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
from _notices import NoticeMixin

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


# 발행 대상 경기 종류. R=정규 · F/D/L/W/C/P=포스트시즌.
# A(올스타)·E(시범)·S(스프링)·I(인터리그 연습) 등은 제외한다.
#
# **소스가 쓰는 코드 전체를 적는다 (v1.11i).** `/api/v1/gameTypes` 실측 도메인은
#   S(Spring Training) R(Regular Season) F(Wild Card) D(Division Series)
#   L(League Championship Series) W(World Series) C(Championship) P(Postseason)
#   A(All-Star Game) I(Intrasquad) E(Exhibition)
# 열한 개다. 전에는 C·P가 빠져 있었는데, 그 둘도 **포스트시즌 코드**다.
# 소스가 어느 시리즈에 P를 쓰면 그 경기는 `skipped_types`로 조용히 사라진다 —
# 10월에 리그가 통째로 빠지는 방식이라 알아채기도 어렵다.
# (2026시즌 실측 2,869행에는 C·P가 0건이지만, '지금 안 온다'는 '안 온다'가 아니다.
#  실제로 D·L·F·W는 오고 있고 같은 계열의 코드다.)
_GAME_TYPES = frozenset({"R", "F", "D", "L", "W", "C", "P"})

# 소스가 실제로 쓰는 전체 도메인. 여기 없는 코드가 오면 '처음 보는 종류'로 보고한다.
_KNOWN_GAME_TYPES = frozenset({"S", "R", "F", "D", "L", "W", "C", "P", "A", "I", "E"})


class MlbAdapter(NoticeMixin):
    """일정·결과."""
    league = League.MLB

    def __init__(self) -> None:
        self.skipped_types = 0
        self.skipped_unknown: list[str] = []

    def fetch(self, start: str, end: str) -> list[Game]:
        url = (f"{_API}/schedule?sportId=1&startDate={start}&endDate={end}"
               f"&hydrate=team,venue,linescore")
        data = _get(url)
        dates = data.get("dates", [])
        if not dates:
            # 0건은 항상 의심한다 — 권한·파라미터 문제가 200+빈배열로 오는 소스가 있다
            raise GateError(f"MLB: 일정 0건 ({start}~{end}) — 0건은 항상 의심")

        self.reset_notices()
        self.skipped_types = 0
        self.skipped_unknown = []

        raw: list[dict] = []
        for day in dates:
            for g in day.get("games", []):
                # **올스타전·시범경기는 우리 대상이 아니다 (v1.11h).**
                # 전에는 걸러내지 않아 'National League All-Stars'·대학팀 같은
                # 미등록 팀 id에서 `UnknownStatus`가 나고 **MLB 수집 전체가 죽었다**
                # (실측: 7/11~7/17 올스타 주간, 2월 시범경기 기간 전체 결번).
                gt = str(g.get("gameType") or "R")
                if gt in _GAME_TYPES:
                    raw.append(g)
                    continue
                self.skipped_types += 1
                self.note_info("발행 대상 아닌 경기 종류로 건너뜀", f"gameType={gt}")
                if gt not in _KNOWN_GAME_TYPES:
                    # 도메인에 없는 코드다. 막지는 않되(경기 하나 때문에 리그를
                    # 죽이지 않는다) 조용히 넘기지도 않는다.
                    self.note("처음 보는 gameType", f"{gt!r} gamePk={g.get('gamePk')}")

        raw = self._dedupe(raw)

        out: list[Game] = []
        for g in raw:
            try:
                out.append(self._parse(g))
            except UnknownStatus as e:
                # 한 경기의 미등록 값이 리그 전체를 침묵시키지 않게 한다.
                self.skipped_unknown.append(str(e)[:120])
                self.note("미등록 팀·상태로 건너뜀", str(e)[:120])
                continue
        if not out:
            raise GateError(f"MLB: 파싱 후 0건 ({start}~{end}) — 0건은 항상 의심")
        return out

    # ── 같은 경기가 두 번 오는 문제 ────────────────────────────
    #
    # **연기·서스펜디드 경기는 같은 `gamePk`로 두 행이 온다 (v1.11i에서 잡음).**
    #
    # 연기: 소스가 `officialDate`에 **재편성 날짜**를, `gameDate`에 **원래 날짜**를 준다.
    #   · 원래 날짜 행 — status=Postponed, `rescheduleDate`(재편성 시각)를 갖는다
    #   · 재편성 행   — status=Final,     `rescheduledFrom`(원래 시각)을 갖는다
    #   두 행의 `officialDate`가 **같으므로 sports_day도 같다.** 어댑터는
    #   `sports_day_fixed=officialDate`를 쓰니 한 슬레이트에 같은 대진이 두 줄로 들어갔다.
    #   결과 캡션이 '토론토 4 : 5 시카고W'와 '토론토 — 시카고W · 결과 미확정'을 나란히 냈다.
    # 서스펜디드: 중단 행이 `resumeDate`를, 재개 행이 `resumedFrom`을 갖는다.
    #   둘 다 Final이고 점수까지 같아서 '중복인지'조차 눈으로는 안 보인다.
    #
    # 실측 (2026-03-01~11-15, 2,869행 / gamePk 2,841개):
    #   중복 gamePk 28개 = (Final, Postponed) 25 · (Postponed, Scheduled) 2 · (Final, Final) 1
    #   연기 행 27건 전부가 `rescheduleDate`를 갖고 `rescheduledFrom`은 하나도 없다.
    #   반대로 `rescheduledFrom`을 가진 27행 전부가 중복 짝의 다른 쪽이다.
    #   → 소스가 '어느 쪽이 실제로 열린 행인지'를 필드로 말해 준다. 추측할 필요가 없다.
    #
    # **남길 쪽 규칙**: 실제로 열린(또는 열릴) 행을 남긴다.
    #   ① 도착 행(`rescheduledFrom`/`resumedFrom` 보유)이 있으면 그것
    #   ② 없으면 상태 순위(종료·진행 > 예정 > 연기·취소)
    #   ③ 그래도 같으면 나중 `gameDate`
    # **버리는 쪽에 '연기'라는 사실이 남지 않는가**: 남지 않아도 잃는 것이 없다.
    # 버려지는 원래 날짜 행의 `officialDate`는 이미 **재편성 날짜**라서
    # 그 행은 애초에 원래 날짜 슬레이트에 뜨지 않았다(sports_day가 재편성 날짜다).
    # 즉 두 행은 같은 날짜를 두 번 채우던 것이지, 서로 다른 날짜를 채우던 것이 아니다.
    # `game_id`는 `리그:시즌:source_key`라 두 행을 동시에 남길 수도 없다.
    # 대신 몇 건을 어떻게 합쳤는지 `skipped_report()`로 운영에 보고한다.
    @staticmethod
    def _played_rank(g: dict) -> tuple[int, int, str]:
        state = (g.get("status", {}).get("detailedState") or "").strip().lower()
        moved = 1 if (g.get("rescheduledFrom") or g.get("resumedFrom")) else 0
        if state in ("postponed", "cancelled", "canceled"):
            played = 0
        elif state in ("scheduled", "pre-game", "warmup", "delayed start"):
            played = 1
        else:
            played = 2                     # Final · In Progress · Completed Early …
        return (moved, played, str(g.get("gameDate") or ""))

    def _dedupe(self, rows: list[dict]) -> list[dict]:
        by_pk: dict[str, dict] = {}
        for g in rows:
            pk = str(g.get("gamePk"))
            cur = by_pk.get(pk)
            if cur is None:
                by_pk[pk] = g
                continue
            keep, drop = ((g, cur) if self._played_rank(g) > self._played_rank(cur)
                          else (cur, g))
            by_pk[pk] = keep
            self.note_info(
                "같은 경기가 두 행으로 와서 하나로 합침(연기·서스펜디드)",
                f"gamePk={pk} 남김={keep.get('gameDate', '')[:10]}"
                f"/{(keep.get('status') or {}).get('detailedState')} "
                f"버림={drop.get('gameDate', '')[:10]}"
                f"/{(drop.get('status') or {}).get('detailedState')}")
        return list(by_pk.values())

    # ── 상태 판정 (v1.11p) ────────────────────────────────────
    #
    # **`detailedState`는 MLB가 계속 늘리는 값이다.** 실운영 알림에 사흘 동안
    # `'Delayed Start'` · `'Delayed'` · `'Player challenge'` 세 개가 새로 떴고,
    # 그때마다 **그 경기가 통째로 빠졌다**(미등록이면 건너뛰기 때문).
    # 표에 다 적는 것은 불가능하다 — 내일 또 새 값이 온다.
    #
    # 그런데 MLB API는 `abstractGameState`라는 **셋뿐인 상위 분류**를 함께 준다:
    # `Preview`(아직 안 열림) · `Live`(진행 중) · `Final`(끝남).
    # 실측(2026 시즌 넉 달 1,272경기): Final 1,241 · Scheduled 16 ·
    # Postponed 13 · Completed Early 2 — 그리고 **연기 경기의 abstract는 `Final`**이다.
    #
    # 그래서 폴백을 **안전한 방향으로만** 연다:
    #   · Preview → SCHEDULED  : 아직 안 열린 것이 확실하다. 결과 카드에 안 실린다.
    #   · Live    → LIVE       : 진행 중이면 점수를 결과로 쓰지 않는다.
    #   · Final   → **폴백하지 않는다.** 연기(Postponed)도 abstract가 Final이라,
    #                떨어뜨리면 연기 경기가 '점수 없는 종료'로 결과 카드에 실린다.
    #                이건 지금처럼 건너뛰고 사람이 보게 한다.
    # `'Delayed Start'`(Preview) · `'Delayed'`·`'Player challenge'`(Live)가 이 폭에 든다.
    _ABSTRACT_FALLBACK = {"preview": Status.SCHEDULED, "live": Status.LIVE}

    def _status_of(self, st: dict, detailed: str) -> Status:
        try:
            return parse_status(detailed, League.MLB)
        except UnknownStatus:
            key = (st.get("abstractGameState") or "").strip().lower()
            fb = self._ABSTRACT_FALLBACK.get(key)
            if fb is None:
                raise            # Final 계열은 지어내지 않는다 — 건너뛰고 알린다
            # 조용히 넘기지 않는다. 표에 넣을지는 사람이 정한다.
            self.note("처음 보는 상태값 — 상위 분류로 처리",
                      f"{detailed!r} → {key} → {fb.value}")
            return fb

    def _parse(self, g: dict) -> Game:
        st = g.get("status", {})
        detailed = (st.get("detailedState") or "").strip()
        status = self._status_of(st, detailed)

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
            home_tz=_team_tz(_team(home_n["team"])),
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
# **홈 구단의 시간대**. 전에는 구장 id 표를 썼는데, 표가 낡아서
# 2026시즌 정규경기 98건 중 **45건(46%)의 현지 시각이 최대 3시간 어긋났다**
# (에인절 스타디움·글로브 라이프·타깃 필드·카우프만·체이스 필드 …).
# 구장 id는 이전·개보수·임시 홈에서 계속 바뀌지만 **구단의 연고 시간대는 안 바뀐다.**
# 그래서 기준을 구장이 아니라 팀으로 옮긴다.
_TEAM_TZ = {
    # 아메리칸리그 동부
    "BAL": "America/New_York", "BOS": "America/New_York", "NYY": "America/New_York",
    "TB": "America/New_York", "TOR": "America/Toronto",
    # 아메리칸리그 중부
    "CWS": "America/Chicago", "CLE": "America/New_York", "DET": "America/Detroit",
    "KC": "America/Chicago", "MIN": "America/Chicago",
    # 아메리칸리그 서부
    "HOU": "America/Chicago", "LAA": "America/Los_Angeles",
    "ATH": "America/Los_Angeles",          # 새크라멘토 임시 홈 (구 오클랜드)
    "SEA": "America/Los_Angeles", "TEX": "America/Chicago",
    # 내셔널리그 동부
    "ATL": "America/New_York", "MIA": "America/New_York", "NYM": "America/New_York",
    "PHI": "America/New_York", "WSH": "America/New_York",
    # 내셔널리그 중부
    "CHC": "America/Chicago", "CIN": "America/New_York", "MIL": "America/Chicago",
    "PIT": "America/New_York", "STL": "America/Chicago",
    # 내셔널리그 서부
    "ARI": "America/Phoenix",              # 애리조나는 서머타임이 없다
    "COL": "America/Denver", "LAD": "America/Los_Angeles",
    "SD": "America/Los_Angeles", "SF": "America/Los_Angeles",
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


class UnknownTeamTz(GateError):
    """홈 팀의 시간대를 모른다 — 현지 시각을 지어내지 않는다."""


def _team_tz(code: str) -> str:
    """홈 팀 코드로 현지 시간대를 찾는다.

    **모르면 조용히 뉴욕으로 두지 않는다.** 그 폴백 때문에 절반이 틀린 채로
    매일 카드에 찍히고 있었다. 30개 표는 팀이 바뀌지 않는 한 완전하므로,
    빠지면 그것 자체가 사고 신호다."""
    tz = _TEAM_TZ.get(code)
    if not tz:
        raise UnknownTeamTz(f"MLB: 홈 팀 {code!r}의 시간대를 모릅니다 — _TEAM_TZ에 추가하세요")
    return tz


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
