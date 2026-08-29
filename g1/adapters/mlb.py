"""MLB 수집 어댑터 — 공식 statsapi (v1.11 신설).

KBO 다음으로 붙이는 리그. 새벽·오전 시간대가 통째로 살아난다.

**sports_day가 공짜다.** MLB는 `officialDate`를 준다 — 이게 곧 홈 현지 캘린더 날짜다.
v1.8이 KST 오프셋으로 계산하려다 슬레이트를 반으로 쪼갰던 그 값을, 소스가 직접 준다.
계산하지 않고 그대로 쓴다.

확인한 것 (2026-08-28 실측, 662경기):
  · 상태: F(Final) 455 · S(Scheduled) 204 · D(Postponed) 2 · 'Completed Early' 1
  · 더블헤더 12건 — `doubleHeader`(N/Y/S) + `gameNumber`(1/2)
  · 순위 30팀 — 승·패·승률·게임차·연속·최근10·매직넘버까지 전부 있음
  · 부문 순위 — 타율/홈런/타점/도루, ERA/승/탈삼진/세이브 확인
"""
from __future__ import annotations

import json
import ssl
import sys
import pathlib
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener

from contract import (GateError, Game, GameMeta, League, LeaderEntry, RecordBook,
                      Score, ScoreUnit, Standing, Status, StreakKind, TeamRef,
                      UnknownStatus, WLD, assert_recordbook, parse_status)

_API = "https://statsapi.mlb.com/api/v1"
_UA = "Mozilla/5.0 (compatible; nudetv/1.0)"

# MLB는 팀 약칭을 응답마다 다르게 준다(있을 때도 없을 때도). id → 약칭을 계약처럼 고정한다.
TEAM_BY_ID = {
    147: "NYY", 111: "BOS", 139: "TB", 141: "TOR", 110: "BAL",
    114: "CLE", 142: "MIN", 116: "DET", 118: "KC", 145: "CWS",
    117: "HOU", 136: "SEA", 140: "TEX", 108: "LAA", 133: "ATH",
    144: "ATL", 121: "NYM", 143: "PHI", 146: "MIA", 120: "WSH",
    158: "MIL", 112: "CHC", 138: "STL", 113: "CIN", 134: "PIT",
    119: "LAD", 135: "SD", 137: "SF", 109: "ARI", 115: "COL",
}

# 부문 코드 → 카드에 쓸 한글 이름
HIT_CATS = {"battingAverage": "타율", "homeRuns": "홈런", "runsBattedIn": "타점",
            "stolenBases": "도루", "hits": "안타", "onBasePlusSlugging": "OPS"}
PIT_CATS = {"earnedRunAverage": "평균자책점", "wins": "승리", "strikeouts": "탈삼진",
            "saves": "세이브", "whip": "WHIP", "inningsPitched": "이닝"}
ASCENDING = {"평균자책점", "WHIP"}


_OPENER = make_opener()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    return json.loads(_fetch(_OPENER, req, label=f"MLB {url.split('?')[0].rsplit('/', 1)[-1]}"))


def _team(node: dict) -> str:
    tid = node.get("id")
    code = TEAM_BY_ID.get(tid)
    if not code:
        raise UnknownStatus(f"MLB: 미등록 팀 id={tid} name={node.get('name')!r}")
    return code


class MlbAdapter:
    """일정·결과."""
    league = League.MLB

    def fetch(self, start: str, end: str) -> list[Game]:
        url = (f"{_API}/schedule?sportId=1&startDate={start}&endDate={end}"
               f"&hydrate=team,venue,linescore")
        data = _get(url)
        dates = data.get("dates", [])
        if not dates:
            # 0건은 항상 의심한다 — 권한·파라미터 문제가 200+빈배열로 오는 소스가 있다
            raise GateError(f"MLB: 일정 0건 ({start}~{end}) — 0건은 항상 의심")

        out: list[Game] = []
        for day in dates:
            for g in day.get("games", []):
                out.append(self._parse(g))
        return out

    def _parse(self, g: dict) -> Game:
        st = g.get("status", {})
        detailed = (st.get("detailedState") or "").strip()
        status = parse_status(detailed, League.MLB)      # 미등록 상태값이면 UnknownStatus

        home_n, away_n = g["teams"]["home"], g["teams"]["away"]
        hs, as_ = home_n.get("score"), away_n.get("score")
        score = None
        if status in (Status.FINAL, Status.LIVE, Status.SUSPENDED) and \
                hs is not None and as_ is not None:
            score = Score(int(hs), int(as_), ScoreUnit.RUNS)

        start = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00"))
        dh_seq = int(g.get("gameNumber") or 1) if g.get("doubleHeader", "N") != "N" else None

        game = Game(
            league=League.MLB, season=str(g.get("season") or start.year),
            source_key=str(g["gamePk"]),
            home=TeamRef(League.MLB, _team(home_n["team"])),
            away=TeamRef(League.MLB, _team(away_n["team"])),
            start_utc=start.astimezone(timezone.utc),
            home_tz=_venue_tz(g.get("venue", {}).get("id")),
            status=status, score=score,
            venue=g.get("venue", {}).get("name"),
            # MLB가 홈 현지 캘린더 날짜를 직접 준다. 계산하지 않는다.
            sports_day_fixed=g.get("officialDate"),
            meta=GameMeta(doubleheader_seq=dh_seq,
                          cancel_reason=(st.get("reason") or detailed
                                         if status.value in ("canceled", "postponed") else None)),
        )
        game.validate()
        return game


# 구장 → 시간대. 현지시간 병기에만 쓰인다(sports_day는 officialDate를 그대로 쓴다).
_VENUE_TZ = {
    2394: "America/Detroit", 3309: "America/New_York", 15: "America/Denver",
    1: "America/New_York", 2: "America/Chicago", 3: "America/New_York",
    4: "America/New_York", 5: "America/New_York", 7: "America/New_York",
    12: "America/New_York", 13: "America/New_York", 14: "America/Toronto",
    17: "America/Chicago", 19: "America/Chicago", 22: "America/Los_Angeles",
    31: "America/Chicago", 32: "America/New_York", 680: "America/Los_Angeles",
    2392: "America/New_York", 2395: "America/New_York", 2680: "America/Los_Angeles",
    2681: "America/Chicago", 2889: "America/Phoenix", 3289: "America/New_York",
    4169: "America/New_York", 5325: "America/Los_Angeles", 5340: "America/Chicago",
    2602: "America/New_York", 2532: "America/Chicago", 10: "America/Los_Angeles",
}


def _display_name(person: dict, peers: list[dict]) -> str:
    """카드에 쓸 이름.

    MLB 선수 이름은 'Jacob Misiorowski'처럼 길어서 한 줄에 안 들어간다.
    중계도 성으로 부르므로 성만 쓴다. 단 같은 카드 안에서 성이 겹치면
    'C. Sánchez'처럼 이니셜을 붙인다 — 짧게 만들자고 사람을 헷갈리게 할 수는 없다.
    """
    full = (person.get("fullName") or "").strip()
    last = (person.get("lastName") or full.split(" ")[-1]).strip()
    same = [p for p in peers
            if (p.get("lastName") or (p.get("fullName") or "").split(" ")[-1]) == last]
    if len(same) > 1:
        first = (person.get("firstName") or full.split(" ")[0]).strip()
        return f"{first[:1]}. {last}" if first else last
    return last


def _venue_tz(vid) -> str:
    """모르면 뉴욕으로 둔다 — 현지시간 병기가 한 시간 어긋날 뿐 사실이 깨지지 않는다."""
    return _VENUE_TZ.get(vid, "America/New_York")


class MlbRecordAdapter:
    """순위 · 부문 순위."""
    league = League.MLB

    def fetch(self, season: int, *, with_leaders: bool = True) -> RecordBook:
        standings = self._standings(season)
        leaders = self._leaders(season) if with_leaders else {}
        rb = RecordBook(league=League.MLB, season=str(season),
                        collected_utc=datetime.now(timezone.utc),
                        source_url=f"{_API}/standings",
                        standings=standings, h2h={}, leaders=leaders)
        # MLB는 30팀이라 상대전적 매트릭스를 주지 않는다. 교차 대조는 건너뛴다.
        assert_recordbook(rb, require_h2h=False)
        return rb

    def _standings(self, season: int) -> list[Standing]:
        data = _get(f"{_API}/standings?leagueId=103,104&season={season}"
                    f"&standingsTypes=regularSeason")
        recs = data.get("records", [])
        if not recs:
            raise GateError("MLB 순위: 0건 — 0건은 항상 의심")

        rows = [tr for div in recs for tr in div.get("teamRecords", [])]
        # 리그 전체 순위를 다시 매긴다 — 소스는 디비전 순위를 준다
        rows.sort(key=lambda r: (-float(r["winningPercentage"]), r["team"]["name"]))

        out: list[Standing] = []
        for i, r in enumerate(rows, start=1):
            l10 = next((x for x in r.get("records", {}).get("splitRecords", [])
                        if x.get("type") == "lastTen"), None)
            sc = (r.get("streak") or {}).get("streakCode") or ""
            kind = {"W": StreakKind.WIN, "L": StreakKind.LOSS}.get(sc[:1], StreakKind.NONE)
            slen = int(sc[1:]) if sc[1:].isdigit() else 0

            home = next((x for x in r.get("records", {}).get("splitRecords", [])
                         if x.get("type") == "home"), None)
            away = next((x for x in r.get("records", {}).get("splitRecords", [])
                         if x.get("type") == "away"), None)

            # 게임차는 선두 기준으로 다시 계산한다 — 소스 값은 디비전 기준이라 섞이면 틀린다
            if i == 1:
                gb = "0"
            else:
                top = rows[0]
                v = ((int(top["wins"]) - int(r["wins"])) +
                     (int(r["losses"]) - int(top["losses"]))) / 2
                gb = f"{v:.1f}".rstrip("0").rstrip(".") or "0"

            s = Standing(
                league=League.MLB, season=str(season), team_code=_team(r["team"]),
                rank=i, games=int(r["gamesPlayed"]),
                record=WLD(int(r["wins"]), int(r["losses"]), 0),
                pct=str(r["winningPercentage"]).lstrip("0") or "0",
                games_behind=gb,
                last10=WLD(int(l10["wins"]), int(l10["losses"]), 0) if l10 else None,
                streak_kind=kind, streak_len=slen,
                home=WLD(int(home["wins"]), int(home["losses"]), 0) if home else None,
                away=WLD(int(away["wins"]), int(away["losses"]), 0) if away else None)
            out.append(s)

        if len(out) != 30:
            raise GateError(f"MLB 순위: 팀 {len(out)}개 (기대 30개)")
        return out

    def _leaders(self, season: int) -> dict[str, list[LeaderEntry]]:
        out: dict[str, list[LeaderEntry]] = {}
        for group, cats in (("hitting", HIT_CATS), ("pitching", PIT_CATS)):
            for code, name in cats.items():
                url = (f"{_API}/stats/leaders?leaderCategories={code}&season={season}"
                       f"&sportId=1&limit=5&statGroup={group}")
                try:
                    data = _get(url)
                except GateError:
                    continue                       # 부문 하나가 없어도 카드 전체를 막지 않는다
                lls = data.get("leagueLeaders") or []
                lst = next((x.get("leaders") for x in lls if x.get("leaders")), None)
                if not lst:
                    continue
                entries = []
                raw = []
                for e in lst[:5]:
                    tm = e.get("team") or {}
                    code_t = TEAM_BY_ID.get(tm.get("id"))
                    if not code_t:
                        continue                   # 이적 등으로 팀이 비는 경우가 있다
                    raw.append((e, code_t))
                for e, code_t in raw:
                    entries.append(LeaderEntry(
                        category=name, stat_key=code, rank=int(e["rank"]),
                        player_id=str(e["person"]["id"]),
                        name=_display_name(e["person"], [x[0]["person"] for x in raw]),
                        team_code=code_t, value=str(e["value"])))
                if entries:
                    out[name] = entries
        if not out:
            raise GateError("MLB 부문 순위: 0건 — 0건은 항상 의심")
        return out
