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
from _notices import NoticeMixin

from contract import (GateError, League, LeaderEntry, RecordBook, Standing,
                      StreakKind, UnknownStatus, WLD, assert_recordbook)
from adapters.kbo import TEAM_CODE

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_BASE = "https://www.koreabaseball.com"
_RANK = _BASE + "/Record/TeamRank/TeamRankDaily.aspx"
_TOP5 = _BASE + "/Record/Ranking/Top5.aspx"

# ASP.NET 컨트롤 이름 접두사(이 페이지의 마스터 페이지 중첩 깊이에서 나온다).
_CTL = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$"
_SERIES_REGULAR = "0"          # ddlSeries: 0=정규시즌 · 1=시범경기

# ── `season` 인자에 관한 정정 (v1.11i) ────────────────────────
#
# **전에는 `fetch(season)`이 아무 일도 하지 않았다.** `TeamRankDaily.aspx`는
# 쿼리스트링을 받지 않아서 `fetch(2025)`가 `season="2025"`라는 라벨만 붙이고
# 내용은 2026년 순위였다. 라벨이 틀린 기록은 없는 것보다 나쁘다.
#
# 그래서 **정말로 과거 시즌을 받을 수 있는지 실제 요청으로 확인했다**(2026-09-02):
#   · `TeamRankDaily.aspx?searchYear=2025` → 무시. 여전히 2026.
#   · 같은 페이지에는 연도 선택 자체가 없다(`ddlSeries` 하나뿐).
#   · 연도 선택이 있는 `TeamRank.aspx`로 `ddlYear=2025` 포스트백을 쳐 봤다.
#     드롭다운은 2025로 선택돼 돌아오지만 **표는 그대로 2026이다**
#     (2025·2020 둘 다, 1·2차 포스트백 모두 '삼성 116경기 69승'=2026 진행 중 값).
#   → 이 경로로는 과거 시즌을 받을 수 없다. 인자를 살릴 방법이 없다.
#
# 결론: **인자로 시즌을 고르지 않는다.** 시즌은 페이지가 스스로 말하는 값
# (`hfSearchYear`)을 읽어 붙인다. 호출자가 굳이 시즌을 주면 그것은 '선택'이 아니라
# **확인**으로 쓴다 — 다르면 막는다. 거짓 라벨이 붙을 자리를 아예 없앤다.
#
# `ddlSeries`(정규시즌/시범경기)는 반대로 **포스트백이 먹는다**(series=1을 치면
# 시범경기 12경기 표가 온다). 기본값에 기대지 않고 매번 확인하고, 다르면 바로잡는다.
_HIDDEN = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)

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


def _hidden_fields(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _HIDDEN.finditer(html):
        tag = m.group(0)
        n = re.search(r'name="([^"]*)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n:
            out[n.group(1)] = v.group(1) if v else ""
    return out


def _hidden_value(html: str, name: str) -> str:
    m = re.search(r'name="%s"[^>]*value="([^"]*)"' % re.escape(name), html)
    if not m:
        m = re.search(r'%s"[^>]*value="([^"]*)"' % re.escape(name.split("$")[-1]), html)
    return m.group(1).strip() if m else ""


# 기록 캐시 신선 창. 경기가 끝나야 바뀌는 값이라 짧게 잡을 이유가 없다.
RECORD_CACHE_TTL_SECONDS = 30 * 60


class KboRecordAdapter(NoticeMixin):
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

    def _post(self, url: str, html: str, fields: dict[str, str]) -> str:
        form = _hidden_fields(html)
        form.update(fields)
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": url, "User-Agent": _UA})
        return _fetch(self._op, req, label="KBO 기록 포스트백").decode("utf-8", "replace")

    def _rank_html(self) -> tuple[str, str]:
        """(HTML, 페이지가 말하는 시즌). 정규시즌 표임을 확인하고 아니면 바로잡는다."""
        html = self._get(_RANK)
        series = _hidden_value(html, _CTL + "hfSearchSeries")
        if series and series != _SERIES_REGULAR:
            # 기본값이 시범경기로 바뀐 상태. 조용히 그 표를 쓰면 3월에
            # '시범경기 순위'가 정규시즌 순위 자리에 실린다.
            self.note_text_info("순위 표 구분 보정",
                           f"기본값이 ddlSeries={series}였습니다 — 정규시즌으로 다시 요청")
            html = self._post(_RANK, html, {
                "__EVENTTARGET": _CTL + "ddlSeries", "__EVENTARGUMENT": "",
                _CTL + "ddlSeries": _SERIES_REGULAR,
                _CTL + "hfSearchSeries": _SERIES_REGULAR})
            series = _hidden_value(html, _CTL + "hfSearchSeries")
            if series != _SERIES_REGULAR:
                raise GateError(
                    f"KBO 순위: 정규시즌 표를 못 받았습니다 (ddlSeries={series!r}) — "
                    f"시범경기 순위를 정규시즌 순위로 내보내지 않습니다")
        page_season = _hidden_value(html, _CTL + "hfSearchYear")
        if not re.fullmatch(r"\d{4}", page_season):
            raise GateError(
                f"KBO 순위: 페이지가 시즌을 안 알려줍니다 (hfSearchYear={page_season!r}) — "
                f"어느 해 순위인지 모른 채 라벨을 붙이지 않습니다")
        return html, page_season

    # ── 1) 팀 순위 + 2) 상대전적 ────────────────────────────────

    def _fetch_rank_page(self, html: str, season: str) -> tuple[list[Standing], dict]:
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

    def fetch(self, season: "int | str | None" = None, *,
              with_leaders: bool = True) -> RecordBook:
        """소스는 **현재 시즌만** 준다 (이유는 파일 위 `season` 인자 주석 참조).

        `season`은 고르는 값이 아니라 **확인용**이다. 주면 페이지가 말하는 시즌과
        대조해 다르면 막는다. 안 주면 페이지가 말하는 시즌을 그대로 붙인다.
        """
        self.reset_notices()

        # ── 메모리 캐시 (v1.11k) ──────────────────────────────────
        # **시계가 5분마다 도는데 그때마다 4페이지를 긁을 이유가 없다.**
        # 순위·부문 기록은 경기가 끝나야 바뀐다. 실측 수집 4.5초 × 하루 288회는
        # 소스에 대한 예의가 아니고 차단을 부른다.
        # 프로세스 안에서만 사는 캐시로 충분하다 — 틱은 한 번 실행에 여러 콘텐츠가
        # 같은 RecordBook을 요구하고(순위표·부문 순위·분석 카드), 연속 운전이면
        # 한 프로세스가 5.5시간 살면서 5분마다 일한다.
        import time as _time
        _now = _time.time()
        _hit = getattr(self, "_rb_cache", None)
        if _hit and (_now - _hit[0]) < RECORD_CACHE_TTL_SECONDS:
            if season is None or str(season).strip() == _hit[1].season:
                # 신선 창 안의 캐시는 정상 동작이다 — 알림에 싣지 않는다.
                # (`note_cache_age`는 '묵은 데이터로 버티는 중'을 알리는 자리다.
                #  정상 히트까지 실으면 매 틱 같은 줄이 쌓여 진짜 경고가 묻힌다.)
                return _hit[1]

        html, page_season = self._rank_html()
        if season is not None and str(season).strip() != page_season:
            raise GateError(
                f"KBO 기록: {season} 시즌을 요청했지만 소스가 주는 것은 {page_season} 시즌입니다. "
                f"이 페이지에는 연도 선택이 없고 `TeamRank.aspx`의 연도 포스트백도 "
                f"표를 바꾸지 못합니다(실측). 과거 시즌 라벨을 붙이지 않습니다.")

        standings, h2h = self._fetch_rank_page(html, page_season)
        leaders = self._fetch_leaders() if with_leaders else {}
        rb = RecordBook(
            league=League.KBO, season=page_season,
            collected_utc=datetime.now(timezone.utc), source_url=_RANK,
            standings=standings, h2h=h2h, leaders=leaders)
        assert_recordbook(rb)                 # 교차 대조 게이트
        self._rb_cache = (_now, rb)
        return rb
