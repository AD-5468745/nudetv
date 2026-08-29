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
from contract import (Game, GameMeta, League, Score, ScoreUnit, Status, TeamRef,
                      GateError, UnknownStatus, KBO_NOTE_NORMAL,
                      KBO_KNOWN_CANCEL_REASONS)

KST = ZoneInfo("Asia/Seoul")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_PAGE = "https://www.koreabaseball.com/Schedule/Schedule.aspx"
_API = "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList"

# 정규시즌(0) · 포스트시즌(9) · 올스타(6). D리그·시범경기는 제외된다.
_SERIES = "0,9,6"

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


class KboAdapter:
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

    def _fetch_month(self, season: int, month: str) -> list[dict]:
        self._ensure_session()
        body = urllib.parse.urlencode({
            "leId": 1, "srIdList": _SERIES, "seasonId": season,
            "gameMonth": month, "teamId": "",
        }).encode()
        req = urllib.request.Request(_API, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": _PAGE, "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://www.koreabaseball.com",
        })
        raw = _fetch(self._op, req, label=f'KBO 일정 {month}').decode("utf-8")
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

        # 경기 키 — 소스가 주면 그대로, 없으면 확정 규칙대로 생성
        if gid:
            game_key = gid.group(1)
        else:
            game_key = f"{cur_date}{TEAM_CODE[away_name]}{TEAM_CODE[home_name]}0"

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

        score = None
        if cancel_reason:
            status = Status.CANCELED
        elif home_sc is not None and away_sc is not None:
            status = Status.FINAL
            score = Score(home_sc, away_sc, ScoreUnit.RUNS)
        else:
            status = Status.SCHEDULED

        lat_lon_dome = VENUE.get(venue or "", (None, None, False))
        g = Game(
            league=League.KBO, season=str(season), source_key=game_key,
            home=TeamRef(League.KBO, TEAM_CODE[home_name]),
            away=TeamRef(League.KBO, TEAM_CODE[away_name]),
            start_utc=start.astimezone(ZoneInfo("UTC")),
            home_tz="Asia/Seoul", status=status, score=score, venue=venue,
            meta=GameMeta(is_dome=lat_lon_dome[2],
                          doubleheader_seq=int(game_key[-1]) or None,
                          cancel_reason=cancel_reason),
        )
        g.validate()
        return cur_date, g

    # ── 공개 API ────────────────────────────────────────────

    def fetch(self, season: int, months: list[str]) -> list[Game]:
        games: list[Game] = []
        self.unknown_notes.clear()
        for mm in months:
            cur = ""
            for row in self._fetch_month(season, mm):
                try:
                    cur, g = self._parse_row(row, season, cur)
                except UnknownStatus:
                    raise            # 미등록 팀 → 게이트 차단 + DM (추측 금지)
                except GateError:
                    raise
                if g:
                    games.append(g)
        return games
