"""KBO 수집 어댑터 — G0 4차에서 확정한 ASMX 경로.

1차 보고서는 이 경로를 "차단됨"으로 잘못 판정했다.
차단이 아니라 form-urlencoded + 세션 쿠키를 요구했던 것이다.
→ Playwright 불필요. 순수 HTTP로 수집한다.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch
from _notices import NoticeMixin
from contract import (Game, GameMeta, League, Score, ScoreUnit, Status, TeamRef,
                      GateError, UnknownStatus, KBO_NOTE_NORMAL,
                      KBO_KNOWN_CANCEL_REASONS, KBO_RELAY_DONE)

KST = ZoneInfo("Asia/Seoul")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_PAGE = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
_API = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList"

# ── 시리즈(srId) ─────────────────────────────────────────────
#
# **전에는 `_SERIES = "0,9,6"` 하나였고 주석은 "정규시즌(0)·포스트시즌(9)·올스타(6)"라
# 적혀 있었다. 셋 다 틀렸다 (v1.11i 전수조사).**
#
# 2025시즌 전 월(03~11) × srId 0~9 전수 실측 — 실제 도메인:
#     0 정규시즌      804경기   (3~10월)
#     1 시범경기       50경기   (3월)                       → 제외
#     2 퓨처스(2군)     1경기   (9/21 경산, 11:00)          → 제외
#     3 준플레이오프    5경기   (10/09~, SSG-삼성)
#     4 와일드카드      2경기   (10/06~, NC-삼성)
#     5 플레이오프      6경기   (10/17~, 삼성-한화)
#     7 한국시리즈      5경기   (10/26~, LG-한화)
#     8 국가대표 평가전  4경기   (11월, 한국-일본-체코)      → 제외
#                     2026-03에는 47경기(WBC)                → 제외
#     9 올스타전        1경기   (7/12, '나눔' vs '드림')     → 제외(아래 근거)
# 6은 어느 달에도 0건이었다 — **없는 코드를 포스트시즌이라고 부르고 있었다.**
# 즉 `"0,9,6"`은 포스트시즌 18경기(3·4·5·7)를 **한 경기도 안 가져왔다.**
# 정규시즌이 10월 초에 끝나므로 그 뒤로 KBO가 통째로 침묵했다.
#
# **올스타(9)를 넣지 않는 이유** — 팀 자리에 구단이 아니라 '나눔'·'드림'이 온다.
# 이 이름은 `TEAM_CODE`에도 계약의 `TEAM_NAMES[KBO]`에도 없다. 넣으면
# `_parse_row`가 `UnknownStatus`를 던져 **7월 올스타 주간 내내 KBO 전체가 죽는다**
# (MLB 어댑터가 올스타·시범경기를 `_GAME_TYPES`로 제외한 것과 같은 이유다).
# 8(국가대표)도 같다 — 팀 자리에 '한국'·'일본'·'체코'가 온다.
_SERIES_REGULAR = "0"
_SERIES_POSTSEASON = ("4", "3", "5", "7")     # 와일드카드 → 준PO → PO → 한국시리즈
SERIES_IDS: tuple[str, ...] = (_SERIES_REGULAR,) + _SERIES_POSTSEASON

# 사람이 읽는 이름 — 보고에 쓴다.
SERIES_NAMES = {"0": "정규시즌", "3": "준플레이오프", "4": "와일드카드",
                "5": "플레이오프", "7": "한국시리즈"}

# **콤마 목록을 쓰지 않는 이유 (실제 요청으로 확인).**
# 소스는 `srIdList`에 콤마 목록을 받지만 **원소를 잃는다.** 2025-07 실측:
#     '0' = 110 · '9' = 1 · '6' = 0
#     '0,9'   = 111  (정상)      '6,9,0' = 111  (정상)
#     '0,9,6' = 110  ← srId 9의 1건(20250712EAWE0)이 **사라진다**
# 순서만 바꾸면 살아나므로 우리 파싱 문제가 아니라 서버 쪽 목록 처리 문제다.
# 2025-10에서는 '0,3,4,5,7'=27로 개별 합(9+5+2+6+5)과 정확히 맞아 **어떤 목록은
# 멀쩡하다.** 즉 "이 목록은 되고 저 목록은 안 된다"를 코드가 알 방법이 없다.
# 그래서 **시리즈마다 따로 요청해 합친다.** 요청은 월당 5회로 늘지만,
# 조용히 한 시리즈가 통째로 빠지는 것보다 낫다.
# (합친 뒤 `gameId`로 중복을 없앤다 — 시리즈가 겹쳐 오는 경우에 대비.)

# 팀 약칭 매핑 — 소스가 주는 한글명 → 팀 코드
# gameId(20260901LGOB0)에도 같은 코드가 들어 있어 교차 검증이 된다
TEAM_CODE = {
    "LG": "LG", "두산": "OB", "KT": "KT", "SSG": "SK", "NC": "NC",
    "키움": "WO", "KIA": "HT", "롯데": "LT", "삼성": "SS", "한화": "HH",
}
CODE_TEAM = {v: k for k, v in TEAM_CODE.items()}

# 구장 → (좌표, 돔 여부). 돔이면 날씨 블록을 붙이지 않는다.
VENUE = {
    "잠실": (37.5122, 127.0719, False), "고척": (37.4982, 126.8671, True),
    "문학": (37.4370, 126.6932, False), "수원": (37.2997, 127.0097, False),
    "대전": (36.3170, 127.4290, False), "대구": (35.8411, 128.6816, False),
    "광주": (35.1682, 126.8891, False), "사직": (35.1940, 129.0615, False),
    "창원": (35.2225, 128.5823, False), "울산": (35.5320, 129.2656, False),
    "포항": (36.0080, 129.3596, False),
}


# 같은 키가 겹쳤을 때 남길 우선순위. 종료 > 진행 > 예정 > 취소.
_RESOLUTION = {Status.FINAL: 3, Status.LIVE: 2, Status.SCHEDULED: 1}


def _resolution_rank(g: Game) -> tuple[int, str]:
    return (_RESOLUTION.get(g.status, 0), str(g.start_utc))


class KboAdapter(NoticeMixin):
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
        self._warm = False
        # 처음 보는 취소 사유를 모은다. fetch가 끝나면 호출자가 확인한다.
        self.unknown_notes: set[str] = set()

    def _ensure_session(self) -> None:
        """ASMX가 ASP.NET_SessionId를 요구한다. 없으면 401."""
        if self._warm:
            return
        _fetch(self._op, _PAGE, label='KBO 세션')
        if not any(c.name == "ASP.NET_SessionId" for c in self._cj):
            raise GateError("KBO: 세션 쿠키 획득 실패")
        self._warm = True

    def _fetch_month(self, season: int, month: str, series: str) -> list[dict]:
        self._ensure_session()
        body = urllib.parse.urlencode({
            "leId": 1, "srIdList": series, "seasonId": season,
            "gameMonth": month, "teamId": "",
        }).encode()
        req = urllib.request.Request(_API, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": _PAGE, "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.koreabaseball.com",
        })
        raw = _fetch(self._op, req,
                     label=f'KBO 일정 {month} 시리즈{series}').decode("utf-8")
        return json.loads(raw).get("rows", [])

    # ── 파싱 ────────────────────────────────────────────────

    @staticmethod
    def _cells(row: dict) -> list[tuple[str, str]]:
        out = []
        for c in row.get("row", []):
            html = c.get("Text") or ""
            txt = re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", html)).strip()
            out.append((c.get("Class") or "", txt))
        return out

    def _parse_row(self, row: dict, season: int, cur_date: str) -> tuple[str, Game | None]:
        """(날짜, Game). 날짜 셀은 그 날의 첫 행에만 있어 이월해야 한다."""
        cells = self._cells(row)
        raw = json.dumps(row, ensure_ascii=False)
        blob = " ".join(t for _, t in cells)

        for cls, txt in cells:
            if cls == "day" and txt:
                m = re.match(r"(\d{2})\.(\d{2})", txt)
                if m:
                    cur_date = f"{season}{m.group(1)}{m.group(2)}"

        gid = re.search(r"gameId=([0-9A-Za-z]+)", raw)
        play = next((t for c, t in cells if c == "play"), "")
        tm = next((t for c, t in cells if c == "time"), "")
        if not play or " vs " not in play:
            return cur_date, None

        # play 셀 형식: 미래 "LG vs 두산" / 종료 "LG 2 vs 5 두산"
        # 원정은 "팀 점수", 홈은 "점수 팀" 순으로 붙는다
        left, right = [s.strip() for s in play.split(" vs ", 1)]
        m_l = re.match(r"^(.+?)\s*(\d+)?$", left)
        m_r = re.match(r"^(\d+)?\s*(.+?)$", right)
        away_name = (m_l.group(1) if m_l else left).strip()
        home_name = (m_r.group(2) if m_r else right).strip()
        away_sc = int(m_l.group(2)) if (m_l and m_l.group(2)) else None
        home_sc = int(m_r.group(1)) if (m_r and m_r.group(1)) else None
        if away_name not in TEAM_CODE or home_name not in TEAM_CODE:
            raise UnknownStatus(f"KBO: 미등록 팀명 {away_name!r} / {home_name!r}")

        hhmm = re.match(r"(\d{2}):(\d{2})", tm)
        if not hhmm or not cur_date:
            return cur_date, None
        start = datetime(int(cur_date[:4]), int(cur_date[4:6]), int(cur_date[6:8]),
                         int(hhmm.group(1)), int(hhmm.group(2)), tzinfo=KST)

        # 구장 — 팀명·시각·상태를 뺀 나머지 셀에서 찾는다
        venue = next((t for _, t in cells if t in VENUE), None)

        # 상태 — 마지막 '비고' 셀로 판정한다.
        #
        # v1.10 이전에는 행 전체에서 "취소"라는 글자를 찾았다. 그래서 사유가
        # '그라운드사정'인 7경기를 놓쳤고, 그중 하나가 12일 지난 뒤에도 '예정'으로 남아
        # 모닝 브리핑에 실려 채널까지 나갔다. 사유 어휘는 KBO가 늘릴 수 있으므로
        # 키워드를 추가하는 것은 같은 사고를 미루는 것일 뿐이다.
        # 규칙: 비고가 '-'가 아니면 그날 그 경기는 열리지 않았다.
        note = cells[-1][1].strip() if cells else ""
        cancel_reason = None
        if note and note != KBO_NOTE_NORMAL:
            cancel_reason = note
            if note not in KBO_KNOWN_CANCEL_REASONS:
                # 처음 보는 사유. 막지는 않는다 — '열리지 않았다'는 확실하므로.
                # 다만 조용히 넘기지도 않는다. 운영에서는 이 목록이 DM으로 나간다.
                self.unknown_notes.add(note)

        # **점수가 있다고 끝난 것이 아니다 (v1.11g).**
        #
        # KBO 일정 페이지는 **진행 중인 경기에도 점수 칸을 채운다.**
        # 18:30에 시작한 경기가 18:40에 "한화 0 vs 0 KT"로 보인다.
        # 전에는 "점수가 있으면 종료"로 읽어서, 2026-09-01 19:18에
        # **"KBO 5경기 종료 · 전부 0:0 무승부"** 카드가 채널로 나갔다.
        # 야구에서 0:0 종료는 존재하지 않는다 — 명백한 허위 보도였다.
        #
        # 소스에는 정확한 구분 신호가 있다(2026년 8월 130행 실측):
        #     relay='리뷰'    → 종료      (점수 있는 86경기 전부)
        #     relay=''       → 진행 중    (점수 있음)
        #     relay='프리뷰'  → 예정      (점수 없음)
        #     비고 != '-'    → 취소      (점수 없음)
        # '점수의 존재'가 아니라 **소스가 종료라고 말하는지**를 본다.
        relay = next((t for c, t in cells if c == "relay"), "").strip()
        score = None
        if cancel_reason:
            status = Status.CANCELED
        elif home_sc is not None and away_sc is not None:
            if relay == KBO_RELAY_DONE:
                status = Status.FINAL
                score = Score(home_sc, away_sc, ScoreUnit.RUNS)
            else:
                # **진행 중 점수는 실제 점수가 아니다.** KBO는 진행 중 경기에
                # 러닝 스코어가 아니라 `0 vs 0` 자리표시자를 채운다
                # (18:30 시작 5경기를 20:35·20:49 두 번 관측 — 두 번 다 전부 0:0).
                # 그 값을 저장하면 스냅샷에 사실이 아닌 점수가 남는다.
                status = Status.LIVE
                score = None
        else:
            status = Status.SCHEDULED

        # ── 경기 키 ──────────────────────────────────────────
        # 소스가 `gameId`를 주면 그대로 쓴다. 취소·미래 경기에는 안 준다.
        #
        # **생성 키가 충돌한 사례 (v1.11i 실측).** 2025-09-17 창원에서
        #   15:00 SSG vs NC — 우천취소 (gameId 없음 → 생성 키 `20250917SKNC0`)
        #   18:30 SSG 0 vs 4 NC — 종료 (gameId `20250917SKNC0`)
        # 두 행이 **같은 키**를 갖는다. 서로 다른 경기인데 `game_id`가 같아지니
        # 하나가 다른 하나를 덮는다. 어느 쪽이 남는지는 행 순서에 달려 있어서,
        # 하필 취소 행이 남으면 **실제로 열린 경기가 '우천취소'로 발행된다.**
        # 취소 행은 영원히 `gameId`를 받지 못하므로 키가 나중에 바뀔 일도 없다.
        # → 취소 행에만 시각을 붙여 가른다. 정상 경기의 키 형식은 건드리지 않는다
        #   (예정 경기 키를 바꾸면 스냅샷 대조가 한 번 어긋난다).
        if gid:
            game_key = gid.group(1)
        elif cancel_reason:
            game_key = (f"{cur_date}{TEAM_CODE[away_name]}{TEAM_CODE[home_name]}"
                        f"0C{hhmm.group(1)}{hhmm.group(2)}")
        else:
            game_key = f"{cur_date}{TEAM_CODE[away_name]}{TEAM_CODE[home_name]}0"

        # 더블헤더 차수는 `gameId`의 끝자리에만 있다. 생성 키에서 읽으면
        # 시각의 마지막 숫자를 차수로 오해한다.
        tail = gid.group(1)[-1] if gid else ""
        dh_seq = (int(tail) or None) if tail.isdigit() else None

        lat_lon_dome = VENUE.get(venue or "", (None, None, False))
        g = Game(
            league=League.KBO, season=str(season), source_key=game_key,
            home=TeamRef(League.KBO, TEAM_CODE[home_name]),
            away=TeamRef(League.KBO, TEAM_CODE[away_name]),
            start_utc=start.astimezone(ZoneInfo("UTC")),
            home_tz="Asia/Seoul", status=status, score=score, venue=venue,
            meta=GameMeta(is_dome=lat_lon_dome[2],
                          doubleheader_seq=dh_seq,
                          cancel_reason=cancel_reason),
        )
        g.validate()
        return cur_date, g

    # ── 공개 API ────────────────────────────────────────────

    def fetch(self, season: int, months: list[str]) -> list[Game]:
        """월 × 시리즈로 나눠 긁고 `source_key`로 합친다.

        **시리즈를 나눠 부르는 이유는 위 `_SERIES_*` 주석 참조** — 콤마 목록이
        원소를 잃는 것을 실제 요청으로 확인했다. 나눠 부르면 그 위험이 사라지는
        대신 같은 경기가 두 시리즈에서 올 여지가 생기므로, 여기서 한 번 더 합친다.
        """
        self.reset_notices()
        self.unknown_notes.clear()
        by_key: dict[str, Game] = {}
        per_series: dict[str, int] = {}
        for mm in months:
            for sr in SERIES_IDS:
                cur = ""
                n = 0
                for row in self._fetch_month(season, mm, sr):
                    try:
                        cur, g = self._parse_row(row, season, cur)
                    except UnknownStatus:
                        raise        # 미등록 팀 → 게이트 차단 + DM (추측 금지)
                    except GateError:
                        raise
                    if not g:
                        continue
                    n += 1
                    prev = by_key.get(g.source_key)
                    if prev is None:
                        by_key[g.source_key] = g
                        continue
                    # 키가 겹치면 **더 확정된 쪽**을 남긴다. 먼저 온 쪽을 남기면
                    # 행 순서에 따라 결과가 취소로 덮이는 사고가 난다(위 키 주석 참조).
                    keep = max((prev, g), key=_resolution_rank)
                    by_key[g.source_key] = keep
                    self.note("같은 키의 행이 둘 이상 — 확정된 쪽만 남김",
                              f"{g.source_key} {prev.status.value}/{g.status.value}"
                              f" → {keep.status.value}")
                per_series[sr] = per_series.get(sr, 0) + n

        games = sorted(by_key.values(), key=lambda g: (g.start_utc, g.source_key))
        # 포스트시즌이 잡히는지 운영에서 눈으로 확인할 수 있게 시리즈별 건수를 남긴다.
        # (10월에 '정규시즌 0건 · 포스트시즌 0건'이면 그 자체가 사고 신호다.)
        picked = " · ".join(f"{SERIES_NAMES.get(sr, sr)} {per_series.get(sr, 0)}"
                            for sr in SERIES_IDS)
        self.note_text_info("시리즈별 수집", picked)   # 9월엔 포스트시즌 0이 정상
        if self.unknown_notes:
            for note in sorted(self.unknown_notes):
                self.note("처음 보는 취소 사유", note)
        return games
