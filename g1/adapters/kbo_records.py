"""KBO 기록·순위 수집 어댑터 (v1.10 신설).

경기 어댑터(kbo.py)는 "언제 누가 붙어서 몇 대 몇"만 준다.
순위표·리더보드·맞대결 분석 카드는 그 위에 얹히는 콘텐츠라 아래 세 표가 필요하다.

  1) 팀 순위        Record/TeamRank/TeamRankDaily.aspx  표0
  2) 상대전적 매트릭스  같은 페이지            표1
  3) 부문별 순위     Record/Ranking/Top5.aspx  (KBO 공식 부문 순위 위젯)

**부문 순위를 Basic1.aspx에서 뽑지 않는 이유** — 그 표는 규정타석 충족자만 담는다.
2026-08-27 실측: 규정 충족 타자 46명, 그 목록의 홈런 1위는 14개인데
KBO 공식 홈런 1위는 39개(김도영)다. 규정 미달자를 빼고 "홈런 1위"라고 쓰면
그 자체로 사실 오류다. Top5.aspx는 KBO가 직접 만드는 부문 순위라 이 함정이 없다.

표기 순서 함정 (실측으로 확인):
  · 홈/방문 "32-1-21"  → 승-무-패
  · 상대전적 "3-8-0"   → 승-패-무
  같은 페이지 안에서 순서가 다르다. 둘 다 WLD(승,패,무)로 정규화한다.
"""
from __future__ import annotations

import http.cookiejar
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch

from contract import (GateError, League, LeaderEntry, RecordBook, Standing,
                      StreakKind, UnknownStatus, WLD, assert_recordbook)
from adapters.kbo import TEAM_CODE

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_BASE = "https://www.koreabaseball.com"
_RANK = _BASE + "/Record/TeamRank/TeamRankDaily.aspx"
_TOP5 = _BASE + "/Record/Ranking/Top5.aspx"

# Top5 페이지에서 '볼넷 TOP5'가 타자·투수 양쪽에 나온다.
# 부문명만으로는 키가 겹치므로 stat_key로 갈라 이름을 붙인다.
_DUP_CATEGORIES = {"볼넷", "삼진"}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", html)).strip()


def _cells(tr_html: str) -> list[str]:
    return [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.S)]


def _rows(table_html: str) -> list[list[str]]:
    body = re.search(r"<tbody[^>]*>(.*?)</tbody>", table_html, re.S)
    if not body:
        return []
    return [_cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S)]


def _wld_dash(raw: str, order: str) -> WLD:
    """'32-1-21' 같은 하이픈 표기를 order('WDL' 또는 'WLD')대로 읽는다."""
    parts = raw.split("-")
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        raise UnknownStatus(f"KBO 기록: 전적 표기 해석 불가 {raw!r}")
    n = [int(p) for p in parts]
    idx = {k: i for i, k in enumerate(order)}
    return WLD(n[idx["W"]], n[idx["L"]], n[idx["D"]])


_LAST10 = re.compile(r"^(\d+)승(\d+)무(\d+)패$")
_STREAK = re.compile(r"^(\d+)([승패무])$")
_STREAK_KIND = {"승": StreakKind.WIN, "패": StreakKind.LOSS, "무": StreakKind.DRAW}


class KboRecordAdapter:
    league = League.KBO

    def __init__(self) -> None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self._cj = http.cookiejar.CookieJar()
        self._op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cj),
            urllib.request.HTTPSHandler(context=ctx))
        self._op.addheaders = [("User-Agent", _UA)]

    def _get(self, url: str) -> str:
        return _fetch(self._op, url,
                      label=f"KBO 기록 {url.rsplit('/', 1)[-1]}").decode("utf-8", "replace")

    # ── 1) 팀 순위 + 2) 상대전적 ────────────────────────────────

    def _fetch_rank_page(self, season: int) -> tuple[list[Standing], dict]:
        html = self._get(_RANK)
        tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S)
        if len(tables) < 2:
            raise GateError(f"KBO 순위: 표 {len(tables)}개 (기대 2개 — 페이지 구조 변경)")

        standings: list[Standing] = []
        for c in _rows(tables[0]):
            # 순위|팀명|경기|승|패|무|승률|게임차|최근10경기|연속|홈|방문
            if len(c) < 12:
                raise GateError(f"KBO 순위: 열 {len(c)}개 (기대 12개) {c}")
            name = c[1]
            if name not in TEAM_CODE:
                raise UnknownStatus(f"KBO 순위: 미등록 팀명 {name!r}")

            m10 = _LAST10.match(c[8])
            last10 = WLD(int(m10.group(1)), int(m10.group(3)), int(m10.group(2))) if m10 else None
            if c[8] and not m10:
                raise UnknownStatus(f"KBO 순위: 최근10경기 표기 해석 불가 {c[8]!r}")

            ms = _STREAK.match(c[9])
            if c[9] and c[9] != "-" and not ms:
                raise UnknownStatus(f"KBO 순위: 연속 표기 해석 불가 {c[9]!r}")
            kind = _STREAK_KIND[ms.group(2)] if ms else StreakKind.NONE
            slen = int(ms.group(1)) if ms else 0

            standings.append(Standing(
                league=League.KBO, season=str(season), team_code=TEAM_CODE[name],
                rank=int(c[0]), games=int(c[2]),
                record=WLD(int(c[3]), int(c[4]), int(c[5])),
                pct=c[6], games_behind=c[7],
                last10=last10, streak_kind=kind, streak_len=slen,
                home=_wld_dash(c[10], "WDL"), away=_wld_dash(c[11], "WDL")))

        # 상대전적 매트릭스 — 헤더에서 열 순서를 읽는다(열 순서를 가정하지 않는다)
        head = re.findall(r"<th[^>]*>(.*?)</th>", tables[1], re.S)
        cols: list[str | None] = []
        for h in head[1:]:
            m = re.match(r"^(.+?)\s*\(\s*승\s*-\s*패\s*-\s*무\s*\)$", _text(h))
            if not m:
                cols.append(None)                     # '팀명', '합계' 열
                continue
            nm = m.group(1).strip()
            if nm not in TEAM_CODE:
                raise UnknownStatus(f"KBO 상대전적: 미등록 팀명 {nm!r}")
            cols.append(TEAM_CODE[nm])

        h2h: dict[tuple[str, str], WLD] = {}
        for c in _rows(tables[1]):
            if not c or c[0] not in TEAM_CODE:
                raise UnknownStatus(f"KBO 상대전적: 행 머리 해석 불가 {c[:1]}")
            me = TEAM_CODE[c[0]]
            for i, val in enumerate(c[1:]):
                opp = cols[i] if i < len(cols) else None
                if opp is None or opp == me:
                    continue
                h2h[(me, opp)] = _wld_dash(val, "WLD")
        return standings, h2h

    # ── 3) 부문별 순위 ─────────────────────────────────────────

    def _fetch_leaders(self) -> dict[str, list[LeaderEntry]]:
        html = self._get(_TOP5)
        out: dict[str, list[LeaderEntry]] = {}

        # 부문이 타자/투수 중 어디 것인지는 섹션 제목이 아니라 '전체순위' 링크 경로로 정한다.
        # 섹션 제목에 의존하면 제목 하나를 놓쳤을 때 다음 부문들이 통째로 반대 섹션에 붙는다
        # (실제로 그렇게 투수 볼넷이 타자 볼넷을 덮어썼다).
        expected = len(re.findall(
            r'<div class="title_bar">\s*<span class="title">[^<]+?\s*TOP5</span>', html))
        pat = re.compile(
            r'<div class="title_bar">\s*<span class="title">([^<]+?)\s*TOP5</span>'
            r'.*?href="(/Record/Player/[^"]*?sort=([A-Z0-9_]+))"'
            r'.*?<ol class="rankList">(.*?)</ol>', re.S)
        blocks = list(pat.finditer(html))
        if len(blocks) != expected:
            raise GateError(
                f"KBO 부문 순위: 부문 {expected}개 중 {len(blocks)}개만 파싱 (구조 변경)")

        for m in blocks:
            cat, href, stat_key, ol = m.group(1), m.group(2), m.group(3), m.group(4)
            section = ("투수" if "PitcherBasic" in href else
                       "타자" if ("HitterBasic" in href or "Runner" in href) else "")
            if not section:
                raise UnknownStatus(f"KBO 부문({cat}): 섹션 판별 불가 {href!r}")
            key = f"{section} {cat}" if cat in _DUP_CATEGORIES else cat
            if key in out:
                raise GateError(f"KBO 부문 순위: 키 충돌 {key!r} — 부문이 덮어써진다")
            entries: list[LeaderEntry] = []
            for li in re.findall(r"<li>(.*?)</li>", ol, re.S):
                rk = re.search(r"rank(\d+)", li)
                pid = re.search(r"playerId=(\d+)", li)
                nm = re.search(r'class=[\'"]rank\d+ name[\'"]>\s*<a[^>]*>(.*?)</a>', li, re.S)
                tm = re.search(r'<span class="team">(.*?)</span>', li, re.S)
                vv = re.search(r'<span class="rr">(.*?)</span>', li, re.S)
                if not (rk and pid and nm and tm and vv):
                    raise UnknownStatus(f"KBO 부문({key}): 항목 구조 해석 불가")
                team = _text(tm.group(1))
                if team not in TEAM_CODE:
                    raise UnknownStatus(f"KBO 부문({key}): 미등록 팀명 {team!r}")
                entries.append(LeaderEntry(
                    category=key, stat_key=stat_key, rank=int(rk.group(1)),
                    player_id=pid.group(1), name=_text(nm.group(1)),
                    team_code=TEAM_CODE[team], value=_text(vv.group(1))))
            if entries:
                out[key] = entries
        if not out:
            raise GateError("KBO 부문 순위: 0건 (0건은 항상 의심 — 페이지 구조 변경)")
        return out

    # ── 공개 API ───────────────────────────────────────────────

    def fetch(self, season: int, *, with_leaders: bool = True) -> RecordBook:
        standings, h2h = self._fetch_rank_page(season)
        leaders = self._fetch_leaders() if with_leaders else {}
        rb = RecordBook(
            league=League.KBO, season=str(season),
            collected_utc=datetime.now(timezone.utc), source_url=_RANK,
            standings=standings, h2h=h2h, leaders=leaders)
        assert_recordbook(rb)                 # 교차 대조 게이트
        return rb
