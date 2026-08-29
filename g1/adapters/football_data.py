"""유럽 6개 대회 수집 어댑터 — football-data.org (v1.11 신설).

EPL · 라리가 · 세리에A · 분데스리가 · 리그1 · 챔피언스리그.
무료 등급이 이 여섯을 전부 커버한다.

**API 키가 필요하다.** 키는 대표님이 직접 발급해 환경변수로 넣는다 —
코드·시트·문서 어디에도 값을 두지 않는다(봇 토큰과 같은 규칙).

    export FOOTBALL_DATA_TOKEN=...        # 대표님이 직접

키가 없으면 이 어댑터는 조용히 비활성이 아니라 **명시적으로 막힌다.**
조용히 0건을 반환하면 "유럽 리그가 오늘 경기가 없구나"로 오해되고,
그 오해가 커버리지 감시까지 통과해버린다.

무료 등급 제약 (공식 문서):
  · 분당 10회. 넘으면 429 + `X-RequestCounter-Reset` 헤더로 대기 시간을 준다
  · 과거 시즌은 제한. 현재 시즌 위주로 쓴다
"""
from __future__ import annotations

import json
import os
import sys
import pathlib
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener

from contract import (GateError, Game, GameMeta, League, Score, ScoreUnit, Status,
                      TeamRef, UnknownStatus)

_API = "https://api.football-data.org/v4"
_OPENER = make_opener()

# football-data 대회 코드 → 우리 리그
COMPETITION = {
    "PL": League.EPL, "PD": League.LALIGA, "SA": League.SERIEA,
    "BL1": League.BUNDESLIGA, "FL1": League.LIGUE1, "CL": League.UCL,
}
LEAGUE_TO_CODE = {v: k for k, v in COMPETITION.items()}

# 소스 상태값 → 계약 상태. 문서화된 도메인 전체를 적는다.
_STATUS = {
    "SCHEDULED": Status.SCHEDULED, "TIMED": Status.SCHEDULED,
    "IN_PLAY": Status.LIVE, "PAUSED": Status.LIVE,
    "FINISHED": Status.FINAL, "AWARDED": Status.FINAL,
    "POSTPONED": Status.POSTPONED, "SUSPENDED": Status.SUSPENDED,
    "CANCELLED": Status.CANCELED, "CANCELED": Status.CANCELED,
}

TOKEN_ENV = "FOOTBALL_DATA_TOKEN"


def load_token() -> str:
    v = os.environ.get(TOKEN_ENV, "").strip()
    if not v:
        raise GateError(
            f"{TOKEN_ENV} 환경변수가 없습니다. football-data.org 무료 키가 필요합니다 "
            f"(대표님이 직접 발급·입력). 키 없이 조용히 0건을 반환하지 않습니다 — "
            f"그러면 '오늘 유럽 경기가 없다'로 오해됩니다.")
    return v


class FootballDataAdapter:
    """대회 하나를 맡는다."""

    def __init__(self, league: League, token: str | None = None) -> None:
        if league not in LEAGUE_TO_CODE:
            raise GateError(f"football-data: 지원하지 않는 리그 {league.value}")
        self.league = league
        self.code = LEAGUE_TO_CODE[league]
        self._token = token or load_token()

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{_API}{path}",
                                     headers={"X-Auth-Token": self._token,
                                              "User-Agent": "nudetv-collector/1.0"})
        raw = _fetch(_OPENER, req, label=f"football-data {self.code}")
        return json.loads(raw)

    def fetch(self, date_from: str, date_to: str) -> list[Game]:
        """date_from/to: 'YYYY-MM-DD'. 무료 등급은 한 번에 최대 10일 범위를 권장한다."""
        d = self._get(f"/competitions/{self.code}/matches"
                      f"?dateFrom={date_from}&dateTo={date_to}")
        matches = d.get("matches")
        if matches is None:
            raise GateError(f"football-data {self.code}: 응답에 matches 없음 "
                            f"(키: {list(d)[:5]}) — 권한 또는 구조 문제")
        return [self._parse(m) for m in matches]

    def _parse(self, m: dict) -> Game:
        raw = str(m.get("status") or "").upper()
        status = _STATUS.get(raw)
        if status is None:
            raise UnknownStatus(f"football-data {self.code}: 미등록 상태 {raw!r}")

        start = datetime.fromisoformat(str(m["utcDate"]).replace("Z", "+00:00"))
        home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        hc, ac = home.get("tla") or home.get("shortName"), away.get("tla") or away.get("shortName")
        if not hc or not ac:
            raise UnknownStatus(f"football-data {self.code}: 팀 코드 없음 "
                                f"{home.get('name')!r} / {away.get('name')!r}")

        ft = ((m.get("score") or {}).get("fullTime") or {})
        hg, ag = ft.get("home"), ft.get("away")
        score = (Score(int(hg), int(ag), ScoreUnit.GOALS)
                 if status in (Status.FINAL, Status.LIVE)
                 and hg is not None and ag is not None else None)

        # UCL 2차전은 합산 승부다. 계약의 aggregate가 없으면 is_draw()가 틀린다.
        agg = None
        full = (m.get("score") or {})
        if full.get("duration") == "EXTRA_TIME" or full.get("penalties"):
            pk = full.get("penalties") or {}
            if pk.get("home") is not None:
                agg = Score(int(pk["home"]), int(pk["away"]), ScoreUnit.GOALS)

        g = Game(
            league=self.league, season=_season(m, start), source_key=str(m["id"]),
            home=TeamRef(self.league, str(hc)), away=TeamRef(self.league, str(ac)),
            start_utc=start.astimezone(timezone.utc),
            home_tz=_TZ.get(self.league, "Europe/London"),
            status=status, score=score,
            venue=m.get("venue") or None,
            meta=GameMeta(season_category=(m.get("stage") or None), penalties=agg),
        )
        g.validate()
        return g


# 홈 현지 시간대. sports_day와 현지시간 병기에 쓴다.
_TZ = {
    League.EPL: "Europe/London", League.LALIGA: "Europe/Madrid",
    League.SERIEA: "Europe/Rome", League.BUNDESLIGA: "Europe/Berlin",
    League.LIGUE1: "Europe/Paris", League.UCL: "Europe/Zurich",
}


def _season(m: dict, start: datetime) -> str:
    s = m.get("season") or {}
    sd = str(s.get("startDate") or "")[:4]
    if sd.isdigit():
        return f"{sd}-{str(int(sd) + 1)[2:]}"
    y = start.year if start.month >= 7 else start.year - 1
    return f"{y}-{str(y + 1)[2:]}"
