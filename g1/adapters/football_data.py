"""유럽 6개 대회 수집 어댑터 — football-data.org (v1.11 신설 · v1.11i 대수술).

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

────────────────────────────────────────────────────────────────────
v1.11i 전수조사에서 나온 것 — **켜는 순간 여러 층이 동시에 틀린다.**
토큰이 없어 지금은 등록조차 안 되지만, 켜면 즉시 다음이 발생한다.

1) **승부차기가 '무승부'로 나갔다.** `meta.penalties`만 채우고 `decided_by`를
   `PSO`로 바꾸지 않았다. 계약의 `is_draw()`는 `decided_by`를 먼저 보므로
   UCL 녹아웃 1-1이 그대로 "무승부"로 렌더된다. 합산 승부(`aggregate`)도
   안 채워서, 1차전 2-1 → 2차전 1-1(합산 3-2 진출)도 "무승부"가 된다.
2) **0건 게이트가 없었다.** `matches`가 빈 배열이면 조용히 0건.
   NPB·MLB·K리그에는 다 있는 게이트가 여기만 없었다.
3) **상태값 하나가 대회 전체를 죽였다.** 경기 단위 `try/except`가 없어
   `_STATUS`에 없는 값 하나면 그 대회 수집이 통째로 중단된다(MLB에는 있다).
4) **UCL 현지 시각이 틀렸다.** `_TZ[UCL] = "Europe/Zurich"` 고정이었는데
   UCL 홈경기는 리스본(UTC+0)부터 이스탄불(UTC+3)까지 걸쳐 있다 —
   최대 3시간 틀린 "현지 HH:MM"이 카드에 찍힌다. MLB가 구장 표를 버리고
   `_TEAM_TZ`로 옮긴 것과 같은 결함이다. **홈팀 기준으로 바꿨다.**
5) **`TEAM_NAMES`에 유럽 6개 대회가 아예 없다.** 그대로 두면 카드에
   'MCI'·'ARS' 같은 코드가 찍힌다. 그 표는 contract.py 소관이라 여기서 못 채운다.
   대신 **표가 없으면 수집을 막고**, 무엇을 채워야 하는지 예외 메시지에 적는다.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (DecidedBy, GateError, Game, GameMeta, League, Score, ScoreUnit,
                      Status, TEAM_NAMES, TeamRef, UnknownStatus)

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

# 팀 목록(=시간대 표의 원천)과 시즌 전체 일정 캐시. 팀 구성은 시즌 중에 안 바뀌고
# 무료 등급은 분당 10회라, 매 틱 다시 받으면 정작 경기 조회가 429에 걸린다.
CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"
TEAMS_CACHE_SECONDS = 7 * 24 * 3600        # 팀 구성: 주 1회면 충분
SEASON_CACHE_SECONDS = 6 * 3600            # 시즌 일정: 합산 계산용, 6시간


def load_token() -> str:
    v = os.environ.get(TOKEN_ENV, "").strip()
    if not v:
        raise GateError(
            f"{TOKEN_ENV} 환경변수가 없습니다. football-data.org 무료 키가 필요합니다 "
            f"(대표님이 직접 발급·입력). 키 없이 조용히 0건을 반환하지 않습니다 — "
            f"그러면 '오늘 유럽 경기가 없다'로 오해됩니다.")
    return v


# ── 시간대 ───────────────────────────────────────────────────
#
# **대회가 아니라 홈팀이 시간대를 정한다.** 전에는 `_TZ[리그]` 하나로 찍었는데,
# UCL은 한 대회 안에 여러 시간대가 섞여 있어서 구조적으로 맞을 수가 없다.
# 국내 리그도 마찬가지 이유로 같은 경로를 쓴다 — 예외가 생겼을 때
# (스페인 라스팔마스는 카나리아 제도라 마드리드보다 1시간 느리다) 리그 단위
# 표에서는 고칠 자리가 없기 때문이다.
#
# 팀 → 나라는 **소스에서 받는다**(`/competitions/{code}/teams`의 `area.name`).
# 나라 → 시간대만 우리가 표로 갖는다. UEFA 회원국은 늘어나지 않고,
# 한 나라 안에서 시간대가 갈리는 곳은 아래 `_TEAM_TZ_OVERRIDE`로 따로 적는다.
_AREA_TZ = {
    "England": "Europe/London", "Wales": "Europe/London",
    "Scotland": "Europe/London", "Northern Ireland": "Europe/London",
    "Ireland": "Europe/Dublin", "Portugal": "Europe/Lisbon",
    "Spain": "Europe/Madrid", "France": "Europe/Paris",
    "Belgium": "Europe/Brussels", "Netherlands": "Europe/Amsterdam",
    "Germany": "Europe/Berlin", "Switzerland": "Europe/Zurich",
    "Austria": "Europe/Vienna", "Italy": "Europe/Rome",
    "Denmark": "Europe/Copenhagen", "Norway": "Europe/Oslo",
    "Sweden": "Europe/Stockholm", "Czech Republic": "Europe/Prague",
    "Czechia": "Europe/Prague", "Poland": "Europe/Warsaw",
    "Slovakia": "Europe/Bratislava", "Hungary": "Europe/Budapest",
    "Slovenia": "Europe/Ljubljana", "Croatia": "Europe/Zagreb",
    "Serbia": "Europe/Belgrade", "Bosnia and Herzegovina": "Europe/Sarajevo",
    "Greece": "Europe/Athens", "Turkey": "Europe/Istanbul",
    "Türkiye": "Europe/Istanbul", "Cyprus": "Asia/Nicosia",
    "Bulgaria": "Europe/Sofia", "Romania": "Europe/Bucharest",
    "Ukraine": "Europe/Kyiv", "Moldova": "Europe/Chisinau",
    "Belarus": "Europe/Minsk", "Russia": "Europe/Moscow",
    "Finland": "Europe/Helsinki", "Estonia": "Europe/Tallinn",
    "Latvia": "Europe/Riga", "Lithuania": "Europe/Vilnius",
    "Israel": "Asia/Jerusalem", "Azerbaijan": "Asia/Baku",
    "Georgia": "Asia/Tbilisi", "Armenia": "Asia/Yerevan",
    "Kazakhstan": "Asia/Almaty", "Iceland": "Atlantic/Reykjavik",
    "Faroe Islands": "Atlantic/Faroe", "Luxembourg": "Europe/Luxembourg",
    "Malta": "Europe/Malta", "Albania": "Europe/Tirane",
    "North Macedonia": "Europe/Skopje", "Montenegro": "Europe/Podgorica",
    "Kosovo": "Europe/Belgrade", "Andorra": "Europe/Andorra",
    "San Marino": "Europe/Rome", "Gibraltar": "Europe/Gibraltar",
    "Liechtenstein": "Europe/Vaduz",
}

# **국내 리그의 나라는 대회가 곧 정의한다.** 프리미어리그 구단은 전부 잉글랜드에,
# 라리가 구단은 전부 스페인에 있다 — 여기에 추측이 끼어들 자리가 없다.
# 그래서 국내 5개 대회는 이 표로 시간대를 정하고, 팀 목록을 따로 받지 않는다
# (무료 등급 분당 10회를 경기 조회에 쓰는 편이 낫다).
# **UCL만 다르다.** 한 대회 안에 리스본(UTC+0)~이스탄불(UTC+3)이 섞이므로
# 대회 단위로는 어떤 값을 넣어도 최대 3시간 틀린다. UCL은 홈팀의 나라를
# 소스에서 받아 정한다(아래 `_areas`).
_COMPETITION_AREA = {
    "PL": "England", "PD": "Spain", "SA": "Italy",
    "BL1": "Germany", "FL1": "France",
}

# 나라 기본값과 다른 도시. **팀 id 기준**(이름은 스폰서·표기가 바뀐다).
#
# 비워 둔다 — **확인하지 않은 id를 넣지 않는다.** 지금 토큰이 없어 소스에서
# 팀 id를 확인할 수 없기 때문이다. 알려진 후보는 라리가 **UD 라스팔마스**다
# (카나리아 제도, 마드리드보다 1시간 느리다. 승격해 있는 시즌에만 해당).
# 키가 오면 `/v4/competitions/PD/teams`에서 그 팀의 `id`를 확인해
# `{id: "Atlantic/Canary"}`를 넣어야 홈경기 현지 시각이 맞는다.
# 나라 단위가 소스가 주는 최대 해상도라, 이 표 없이는 소스만으로 못 고친다.
_TEAM_TZ_OVERRIDE: dict[int, str] = {}


class UnknownTeamTz(GateError):
    """홈 팀의 시간대를 모른다 — 현지 시각을 지어내지 않는다.

    MLB에서 배운 것과 같다: 모르면 조용히 리그 기본값으로 두지 않는다.
    그 폴백 때문에 MLB 2026시즌 정규경기의 46%가 최대 3시간 틀린 현지 시각을
    매일 카드에 찍고 있었다.
    """


def _cache_path(name: str) -> pathlib.Path:
    return CACHE_DIR / f"fd_{name}.json"


def _cache_read(name: str, max_age: float):
    p = _cache_path(name)
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) <= max_age:
            return json.loads(p.read_text())
    except (OSError, ValueError):
        pass
    return None


def _cache_write(name: str, data) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(name).write_text(json.dumps(data, ensure_ascii=False))
    except OSError:
        pass                                   # 캐시 실패는 수집 실패가 아니다


# 2차전이 있는 라운드. 여기 속한 경기는 **그날 스코어만으로 승부가 갈리지 않는다.**
# 소스는 합산 점수를 주지 않으므로 두 다리를 직접 더한다.
TWO_LEG_STAGES = frozenset({
    "PLAY_OFFS", "PLAYOFFS", "PLAY_OFF_ROUND", "LAST_16", "ROUND_OF_16",
    "QUARTER_FINALS", "SEMI_FINALS",
})


class FootballDataAdapter(NoticeMixin):
    """대회 하나를 맡는다."""

    # 클래스 기본값으로 둔다 — 검증 스크립트가 `__init__`을 우회해 인스턴스를
    # 만들기도 하므로, 인스턴스 속성에만 두면 그때 AttributeError가 난다.
    _team_area: "dict[int, str] | None" = None
    _season_matches: "list[dict] | None" = None

    def __init__(self, league: League, token: str | None = None) -> None:
        if league not in LEAGUE_TO_CODE:
            raise GateError(f"football-data: 지원하지 않는 리그 {league.value}")
        # **팀 이름표가 없으면 수집하지 않는다.**
        # `team_name()`은 표에 없는 코드를 그대로 돌려주므로, 표가 비면
        # 카드에 'MCI'·'ARS'가 찍힌다. 오류도 경고도 없이 시청자만 이상한 이름을 본다.
        # 표를 채우는 것은 contract.py 소관이라 여기서 못 한다 — 그래서 막는다.
        if not TEAM_NAMES.get(league):
            raise GateError(
                f"football-data: contract.TEAM_NAMES[{league.value}]가 비어 있습니다. "
                f"이대로 켜면 카드에 팀 이름 대신 'MCI'·'ARS' 같은 세 글자 코드가 찍힙니다. "
                f"채워야 할 것: 이 대회 참가 구단의 **TLA(세 글자 코드) → 한글 구단명** "
                f"({{'MCI': '맨체스터 시티', 'ARS': '아스널', …}}). TLA 목록은 "
                f"`/v4/competitions/{LEAGUE_TO_CODE[league]}/teams`의 각 팀 `tla`에 있습니다.")
        self.league = league
        self.code = LEAGUE_TO_CODE[league]
        self._token = token or load_token()
        self._team_area: dict[int, str] | None = None
        self._season_matches: list[dict] | None = None

    # ── HTTP ──────────────────────────────────────────────────
    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{_API}{path}",
                                     headers={"X-Auth-Token": self._token,
                                              "User-Agent": "nudetv-collector/1.0"})
        raw = _fetch(_OPENER, req, label=f"football-data {self.code}")
        return json.loads(raw)

    # ── 팀 → 시간대 ───────────────────────────────────────────
    def _areas(self) -> dict[int, str]:
        """팀 id → 나라 이름. 소스에서 받아 디스크에 캐시한다."""
        if self._team_area is not None:
            return self._team_area
        name = f"teams_{self.code}"
        cached = _cache_read(name, TEAMS_CACHE_SECONDS)
        if cached is None:
            d = self._get(f"/competitions/{self.code}/teams")
            teams = d.get("teams")
            if not teams:
                raise GateError(
                    f"football-data {self.code}: 팀 목록 0건 — 홈팀 시간대를 정할 수 없습니다. "
                    f"(0건은 항상 의심: 권한 또는 구조 문제)")
            cached = {str(t["id"]): (t.get("area") or {}).get("name") for t in teams}
            _cache_write(name, cached)
        else:
            self.note_text_info("팀 목록 캐시 사용", f"{name} (최대 {TEAMS_CACHE_SECONDS // 86400}일)")
        self._team_area = {int(k): v for k, v in cached.items() if v}
        return self._team_area

    def _team_tz(self, team: dict) -> str:
        """홈팀의 현지 시간대. 모르면 **막는다** — 지어내지 않는다.

        국내 5개 대회는 대회가 곧 나라라서 표로 정하고(위 `_COMPETITION_AREA`),
        UCL만 홈팀의 나라를 소스에서 받는다. 실패는 `UnknownTeamTz`로 올려
        **그 경기만** 건너뛴다 — 한 팀 때문에 대회 전체가 죽지 않게.
        """
        tid = team.get("id")
        override = _TEAM_TZ_OVERRIDE.get(tid)
        if override:
            return override

        area = _COMPETITION_AREA.get(self.code)
        if area is None:
            # UCL — 홈팀이 어느 나라인지 소스에 물어본다.
            area = (team.get("area") or {}).get("name")     # 응답에 있으면 그대로
            if not area:
                try:
                    area = self._areas().get(tid)
                except GateError as e:
                    raise UnknownTeamTz(
                        f"football-data {self.code}: 팀 목록을 못 받아 홈팀 "
                        f"{team.get('name')!r}의 나라를 모릅니다 — {str(e)[:80]}")
            if not area:
                raise UnknownTeamTz(
                    f"football-data {self.code}: 홈팀 id={tid} "
                    f"{team.get('name')!r}이 대회 팀 목록에 없습니다 — 어느 나라인지 모르면 "
                    f"'현지 시각'을 찍을 수 없습니다(캐시가 낡았을 수 있습니다: "
                    f"{_cache_path('teams_' + self.code)}).")
        tz = _AREA_TZ.get(area)
        if not tz:
            raise UnknownTeamTz(
                f"football-data {self.code}: 나라 {area!r}의 시간대를 모릅니다 — "
                f"_AREA_TZ에 추가하세요(팀 {team.get('name')!r}).")
        return tz

    # ── 수집 ──────────────────────────────────────────────────
    def fetch(self, date_from: str, date_to: str) -> list[Game]:
        """date_from/to: 'YYYY-MM-DD'. 무료 등급은 한 번에 최대 10일 범위를 권장한다."""
        self.reset_notices()
        d = self._get(f"/competitions/{self.code}/matches"
                      f"?dateFrom={date_from}&dateTo={date_to}")
        matches = d.get("matches")
        if matches is None:
            raise GateError(f"football-data {self.code}: 응답에 matches 없음 "
                            f"(키: {list(d)[:5]}) — 권한 또는 구조 문제")
        if not matches:
            # **0건 게이트 (v1.11i 신설).** 다른 어댑터에는 다 있는데 여기만 없었다.
            # 무료 등급은 권한 문제도 200 + 빈 배열로 준다 — 그러면
            # '오늘 이 리그는 경기가 없다'와 구분이 안 된다.
            raise GateError(
                f"football-data {self.code}: {date_from}~{date_to} 경기 0건 — "
                f"0건은 항상 의심(권한·기간·대회 코드 중 하나가 틀렸을 수 있습니다)")

        out: list[Game] = []
        for m in matches:
            # **경기 하나가 대회 전체를 죽이지 않게 한다 (v1.11i).**
            # 전에는 `[self._parse(m) for m in matches]`라, `_STATUS`에 없는
            # 상태값 하나면 그 대회가 통째로 빠졌다. MLB는 이미 이렇게 하고 있다.
            try:
                out.append(self._parse(m))
            except UnknownStatus as e:
                self.note("미등록 값이라 건너뜀", str(e)[:140])
            except UnknownTeamTz as e:
                self.note("홈팀 시간대를 몰라 건너뜀", str(e)[:140])
        if not out:
            raise GateError(f"football-data {self.code}: 파싱 후 0건 "
                            f"({len(matches)}행 전부 건너뜀) — 0건은 항상 의심")
        return out

    # ── 2차전 합산 ────────────────────────────────────────────
    def _season_index(self, season_start_year: str) -> dict[tuple, list[dict]]:
        """(스테이지, 두 팀) → 그 대진의 경기들. 합산 계산에만 쓴다.

        수집 창(보통 -3~+7일)에는 2차전만 들어오고 1차전은 이미 지나가 있다.
        합산을 알려면 시즌 일정을 한 번 더 받아야 한다 — 무료 등급이 분당 10회라
        디스크에 캐시하고, 녹아웃 2차전이 실제로 등장할 때만 받는다.
        """
        if self._season_matches is None:
            name = f"season_{self.code}_{season_start_year}"
            cached = _cache_read(name, SEASON_CACHE_SECONDS)
            if cached is None:
                d = self._get(f"/competitions/{self.code}/matches"
                              f"?season={season_start_year}")
                cached = d.get("matches") or []
                # **빈 목록은 캐시하지 않는다.** 한 번 비어 온 것을 6시간 붙들면
                # 그동안 모든 2차전이 '합산 미확인'이 되어 발행에서 빠진다.
                if cached:
                    _cache_write(name, cached)
            self._season_matches = cached
        idx: dict[tuple, list[dict]] = {}
        for m in self._season_matches:
            h = (m.get("homeTeam") or {}).get("id")
            a = (m.get("awayTeam") or {}).get("id")
            if h is None or a is None:
                continue
            idx.setdefault((str(m.get("stage")), frozenset((h, a))), []).append(m)
        return idx

    def _aggregate(self, m: dict, start: datetime) -> "Score | None":
        """2차전이면 합산 점수를, 아니면 None. 못 구하면 None(호출자가 판단한다)."""
        stage = str(m.get("stage") or "").upper()
        if self.league is not League.UCL or stage not in TWO_LEG_STAGES:
            return None
        season_year = str((m.get("season") or {}).get("startDate") or "")[:4]
        if not season_year.isdigit():
            return None
        try:
            idx = self._season_index(season_year)
        except GateError as e:
            self.note("2차전 합산 확인 실패(시즌 일정을 못 받음)", str(e)[:120])
            return None
        h = (m.get("homeTeam") or {}).get("id")
        a = (m.get("awayTeam") or {}).get("id")
        legs = idx.get((str(m.get("stage")), frozenset((h, a))), [])
        if len(legs) < 2:
            return None                       # 단판이거나 1차전이 아직 없다
        legs = sorted(legs, key=lambda x: str(x.get("utcDate")))
        if str(legs[-1].get("id")) != str(m.get("id")):
            return None                       # 1차전에는 합산이 없다(아직 안 끝났다)
        agg_h = agg_a = 0
        for leg in legs:
            ft = ((leg.get("score") or {}).get("fullTime") or {})
            gh, ga = ft.get("home"), ft.get("away")
            if gh is None or ga is None:
                return None                   # 한 다리라도 결과가 없으면 합산 불가
            if (leg.get("homeTeam") or {}).get("id") == h:
                agg_h += int(gh); agg_a += int(ga)
            else:
                agg_h += int(ga); agg_a += int(gh)
        return Score(agg_h, agg_a, ScoreUnit.GOALS)

    # ── 파싱 ──────────────────────────────────────────────────
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

        full = (m.get("score") or {})
        ft = (full.get("fullTime") or {})
        hg, ag = ft.get("home"), ft.get("away")
        score = (Score(int(hg), int(ag), ScoreUnit.GOALS)
                 if status in (Status.FINAL, Status.LIVE)
                 and hg is not None and ag is not None else None)

        # ── 무승부가 아닌 것을 무승부라고 하지 않는다 ──
        #
        # 계약의 `is_draw()`는 ① decided_by가 AET/PSO면 무승부 아님
        # ② aggregate가 있으면 합산으로 판정 ③ 없으면 그날 스코어로 판정.
        # 전에는 ①②를 아예 안 채워서 승부차기도 합산승부도 '무승부'로 나갔다.
        duration = str(full.get("duration") or "").upper()
        pk = full.get("penalties") or {}
        pk_home, pk_away = pk.get("home"), pk.get("away")
        decided = DecidedBy.REGULAR
        penalties = None
        if duration in ("PENALTY_SHOOTOUT", "PENALTIES") or (
                pk_home is not None and pk_away is not None):
            if pk_home is not None and pk_away is not None:
                decided = DecidedBy.PSO
                penalties = Score(int(pk_home), int(pk_away), ScoreUnit.GOALS)
            else:
                # 승부차기라는 것은 아는데 PK 점수를 안 준다. 계약은 PSO에
                # `penalties`를 요구하므로 PSO로 못 적는다. 그렇다고 REGULAR로
                # 두면 '무승부'가 된다 — **승부차기는 반드시 연장을 거친다**는
                # 사실은 확실하므로 AET로 적고(무승부 아님) 사실을 보고한다.
                decided = DecidedBy.AET
                self.note("승부차기인데 PK 점수를 안 줘서 연장(AET)으로 표기",
                          f"id={m.get('id')} {hc}-{ac}")
        elif duration in ("EXTRA_TIME", "AET"):
            decided = DecidedBy.AET

        aggregate = self._aggregate(m, start) if status is Status.FINAL else None

        # 합산으로 갈리는 2차전인데 합산을 못 구했고 그날 스코어가 동점이면,
        # 그대로 내보내면 "무승부"라는 **사실 오류**가 될 수 있다. 발행하지 않는다.
        if (status is Status.FINAL and score is not None
                and score.home == score.away
                and decided is DecidedBy.REGULAR and aggregate is None
                and self.league is League.UCL
                and str(m.get("stage") or "").upper() in TWO_LEG_STAGES):
            raise UnknownStatus(
                f"football-data {self.code}: 녹아웃 2차전 동점인데 합산을 확인하지 "
                f"못했습니다 (id={m.get('id')} {hc} {hg}-{ag} {ac}) — "
                f"'무승부'로 내보내지 않습니다")

        g = Game(
            league=self.league, season=_season(m, start), source_key=str(m["id"]),
            home=TeamRef(self.league, str(hc)), away=TeamRef(self.league, str(ac)),
            start_utc=start.astimezone(timezone.utc),
            # **대회가 아니라 홈팀이 시간대를 정한다** (파일 머리말 4번 참조).
            home_tz=self._team_tz(home),
            status=status, score=score,
            venue=m.get("venue") or None,
            meta=GameMeta(season_category=(m.get("stage") or None),
                          decided_by=decided, penalties=penalties,
                          aggregate=aggregate),
        )
        g.validate()
        return g


def _season(m: dict, start: datetime) -> str:
    s = m.get("season") or {}
    sd = str(s.get("startDate") or "")[:4]
    if sd.isdigit():
        return f"{sd}-{str(int(sd) + 1)[2:]}"
    y = start.year if start.month >= 7 else start.year - 1
    return f"{y}-{str(y + 1)[2:]}"
