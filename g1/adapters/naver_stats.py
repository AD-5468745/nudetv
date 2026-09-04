"""네이버 스포츠 통계 — MLB·NPB 팀 순위와 부문 순위 (v1.12 신설).

**왜 만들었나 (대표님 지시 2026-09-04).**
"팀명 선수명 등 모두 네이버 기준으로."

그 전까지 두 리그의 부문 순위가 막혀 있었다:
  · NPB — 소스가 선수 이름을 한자·가나 원문으로 준다(`佐藤輝明`·`ダルベック`).
    카드 전체가 선수 이름이라 한국 구독자는 한 줄도 못 읽는다.
  · MLB — 소스가 영문만 준다(`Schwarber`·`Crow-Armstrong`).
**음역은 지어내지 않는다** — 틀린 이름이 못 읽는 이름보다 나쁘다. 그래서 둘 다 보류했었다.

네이버는 이 선수들의 한글 표기를 갖고 있다(실측 2026-09-04):
    佐藤輝明 → 사토 데루아키 · 牧秀悟 → 마키 슈고 · ダルベック → 달벡
    Schwarber → 카일 슈와버 · Crow-Armstrong → 피트 크로우-암스트롱

**이름만 빌려오지 않는다.** 1차 소스(MLB statsapi·npb.jp)의 선수와 네이버 선수를
짝 맞출 근거가 없기 때문이다 — 선수 ID 체계가 완전히 다르고(네이버 `9861` vs
statsapi 6자리), npb.jp는 아예 선수 ID가 없다. 이름으로 맞추면 동명이인·이적에서
조용히 틀린 이름이 나간다. 그건 이 프로젝트가 가장 경계하는 사고다.
→ **부문 순위·팀 순위는 네이버 하나에서 이름·수치·순위를 전부 받는다.**
   같은 응답·같은 시점에서 나오므로 이름과 수치가 어긋나는 것이 구조적으로 불가능하다.
   (경기 일정·결과는 지금대로 공식 소스를 쓴다. 이 파일은 기록 전용이다.)

**표시 팀명은 이 API의 `teamName`을 쓰지 않는다.** `teamId`는 매핑에만 쓰고
표시는 `contract.TEAM_NAMES`를 거친다 — 표기가 바뀌면 사람이 표를 고치는 것이
문자열이 코드 곳곳에 박히는 것보다 안전하다(이 프로젝트가 이미 쓰는 방식).

**요청은 리그당 2건뿐이다.** 한 행에 그 선수의 모든 지표가 들어 있으므로
(hitterHr·hitterRbi·hitterSb가 한 행에 함께 온다) 부문마다 따로 부를 이유가 없다.
타자 한 번, 투수 한 번 받아 우리가 정렬한다. 부문 17개를 따로 부르면 34요청이 된다 —
새 엔드포인트를 그렇게 두드리면 차단을 부르고, 같은 호스트를 쓰는 점수 대조까지 함께 막힌다
(첫 배포에서 Leaguepedia가 정확히 그렇게 막혔다 — 약점 24~26).
"""
from __future__ import annotations

import json
import pathlib
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from contract import (GateError, League, LeaderEntry, RecordBook, Standing,
                      StreakKind, TEAM_NAMES, WLD)

from ._notices import NoticeMixin

BASE = "https://api-gw.sports.naver.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
    "Referer": "https://m.sports.naver.com/",
    "Accept": "application/json",
}
TIMEOUT = 15
CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"
# 기록은 경기가 끝나야 바뀐다. 5분마다 긁을 이유가 없다.
CACHE_MAX_AGE_SECONDS = 30 * 60
# 리밋에 걸려도 이만큼까지는 캐시로 버틴다. 넘으면 만들지 않는다 —
# 묵은 순위를 오늘 것처럼 내보내는 것이 리그가 빠지는 것보다 나쁘다.
CACHE_HOLD_SECONDS = 12 * 3600
REQUEST_GAP_SECONDS = 1.2          # 연속 요청 사이 최소 간격

CATEGORY = {League.MLB: "mlb", League.NPB: "npb"}

# ── 네이버 팀코드 → 우리 코드 ────────────────────────────────
#
# **같은 두 글자가 리그마다 다른 팀이다** — MLB의 `SF`는 샌프란시스코,
# NPB의 `SF`는 소프트뱅크다. 그래서 리그별로 표를 나눈다. 합치면 조용히 뒤바뀐다.
#
# 2026-09-04 실측: 이름 대조로 MLB 29/30 · NPB 10/12가 자동으로 맞았고,
# 나머지 셋(CW·NH·YK)은 표기가 달라 손으로 넣었다. 표기는 네이버를 따른다(대표님 지시).
TEAM_CODE_MAP: dict[League, dict[str, str]] = {
    League.MLB: {
        "AN": "LAA", "AT": "ATL", "AZ": "ARI", "BA": "BAL", "BO": "BOS",
        "CC": "CHC", "CI": "CIN", "CL": "CLE", "CO": "COL", "CW": "CWS",
        "DE": "DET", "FL": "MIA", "HO": "HOU", "KC": "KC", "LA": "LAD",
        "MI": "MIL", "MN": "MIN", "MO": "WSH", "NM": "NYM", "NY": "NYY",
        "OA": "ATH", "PH": "PHI", "PI": "PIT", "SD": "SD", "SE": "SEA",
        "SF": "SF", "SL": "STL", "TB": "TB", "TE": "TEX", "TO": "TOR",
    },
    League.NPB: {
        "CH": "CHU", "HI": "HIR", "HS": "HAN", "JL": "LOT", "JN": "CHU",
        "NH": "NIP", "OX": "ORI", "RT": "RAK", "SE": "SEI", "SF": "SOF",
        "YA": "YAK", "YK": "DEN", "YO": "YOG",
    },
}

# ── 부문 ─────────────────────────────────────────────────────
#
# `qualified=True`인 부문은 **비율 지표**다 — 규정타석·규정이닝을 채운 선수만 센다.
# 이걸 빼먹으면 3타수 2안타가 타율 1위가 된다(약점 2번, KBO에서 이미 앓았다).
# `digits`는 표기 자리수. 소스가 0.32366처럼 주므로 우리가 자른다.
HITTER_CATEGORIES = (
    #  이름       필드            높을수록 좋은가  규정  자리수
    ("타율",     "hitterHra",    True,  True,  3),
    ("홈런",     "hitterHr",     True,  False, 0),
    ("타점",     "hitterRbi",    True,  False, 0),
    ("도루",     "hitterSb",     True,  False, 0),
    ("안타",     "hitterHit",    True,  False, 0),
    ("득점",     "hitterRun",    True,  False, 0),
    ("출루율",   "hitterObp",    True,  True,  3),
    ("장타율",   "hitterSlg",    True,  True,  3),
    ("OPS",     "hitterOps",    True,  True,  3),
)
PITCHER_CATEGORIES = (
    ("평균자책점", "pitcherEra",   False, True,  2),
    ("승리",      "pitcherWin",   True,  False, 0),
    ("세이브",    "pitcherSave",  True,  False, 0),
    ("홀드",      "pitcherHold",  True,  False, 0),
    ("탈삼진",    "pitcherKk",    True,  False, 0),
    ("WHIP",     "pitcherWhip",  False, True,  2),
    ("QS",       "pitcherQs",    True,  False, 0),
)
TOP_N = 5


def _fmt(value, digits: int) -> str:
    """카드에 찍을 문자열. 비율은 앞의 0을 남긴다(`0.316`) — 리그마다 다르면 어긋나 보인다."""
    if value is None:
        raise ValueError("값 없음")
    if digits == 0:
        return str(int(round(float(value))))
    return f"{float(value):.{digits}f}"


def _streak(text: str | None) -> tuple[StreakKind, int]:
    """`'2패'` · `'1승'` · `'3무'` → (종류, 횟수). 못 읽으면 NONE — 지어내지 않는다."""
    s = (text or "").strip()
    if not s or len(s) < 2 or not s[:-1].isdigit():
        return StreakKind.NONE, 0
    kind = {"승": StreakKind.WIN, "패": StreakKind.LOSS, "무": StreakKind.DRAW}.get(s[-1])
    if kind is None:
        return StreakKind.NONE, 0
    return kind, int(s[:-1])


class NaverStatsAdapter(NoticeMixin):
    """한 리그의 팀 순위 + 부문 순위. 요청 2건, 30분 캐시."""

    def __init__(self, league: League, *, opener=None, sleep=time.sleep):
        if league not in CATEGORY:
            raise GateError(f"네이버 통계는 MLB·NPB만 지원합니다 (요청: {league.value})")
        self.league = league
        self.category = CATEGORY[league]
        self._opener = opener or self._urlopen
        self._sleep = sleep
        self._last_request = 0.0

    # ── 요청 ──────────────────────────────────────────────
    @staticmethod
    def _urlopen(url: str) -> dict:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as f:
            return json.loads(f.read().decode("utf-8"))

    def _cache_path(self, key: str) -> pathlib.Path:
        return CACHE_DIR / f"naverstats_{self.category}_{key}.json"

    def _get(self, path: str, params: dict, key: str) -> dict:
        """캐시가 신선하면 그대로. 아니면 받아서 저장. 실패하면 묵은 캐시로 버틴다."""
        cache = self._cache_path(key)
        if cache.exists():
            age = time.time() - cache.stat().st_mtime
            if age <= CACHE_MAX_AGE_SECONDS:
                try:
                    return json.loads(cache.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass                       # 깨진 캐시는 없는 것과 같게 다룬다
        gap = REQUEST_GAP_SECONDS - (time.time() - self._last_request)
        if gap > 0:
            self._sleep(gap)
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        try:
            data = self._opener(url)
            self._last_request = time.time()
        except Exception as e:                               # noqa: BLE001
            if cache.exists():
                age = time.time() - cache.stat().st_mtime
                if age <= CACHE_HOLD_SECONDS:
                    self.note_cache_age(age)
                    try:
                        return json.loads(cache.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        pass
                raise GateError(
                    f"네이버 통계({self.category}/{key}) 실패이고 캐시도 "
                    f"{age / 3600:.1f}시간으로 너무 묵었습니다 "
                    f"(상한 {CACHE_HOLD_SECONDS // 3600}시간)") from e
            raise GateError(f"네이버 통계({self.category}/{key}) 실패: "
                            f"{type(e).__name__} {str(e)[:90]}") from e
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                               # 캐시 실패는 수집 실패가 아니다
        return data

    # ── 팀코드 ────────────────────────────────────────────
    def _team(self, naver_id: str | None) -> str:
        """네이버 팀코드 → 우리 코드. 모르면 예외 — 카드에 코드가 그대로 찍히면 안 된다."""
        code = TEAM_CODE_MAP[self.league].get((naver_id or "").strip().upper())
        if not code:
            raise GateError(
                f"네이버 팀코드를 우리 코드로 매핑 못 함 ({self.league.value}: "
                f"{naver_id!r}) — 표를 고쳐야 합니다")
        if code not in TEAM_NAMES[self.league]:
            raise GateError(f"매핑표가 계약에 없는 팀 코드를 가리킵니다: {code}")
        return code

    # ── 팀 순위 ───────────────────────────────────────────
    def fetch_standings(self, season: int) -> list[Standing]:
        data = self._get(f"/statistics/categories/{self.category}/seasons/{season}/teams",
                         {}, f"teams_{season}")
        rows = ((data or {}).get("result") or {}).get("seasonTeamStats") or []
        # **0건은 항상 의심한다.** 시즌 경계·구조 변경이면 조용히 빈 카드가 나간다.
        if not rows:
            raise GateError(f"네이버 팀 순위 0건 ({self.category} {season}) — "
                            f"시즌 경계이거나 응답 구조가 바뀌었습니다")
        expect = 30 if self.league is League.MLB else 12
        if len(rows) != expect:
            self.note("팀 수가 예상과 다름", f"{len(rows)}팀 (예상 {expect})")

        out: list[Standing] = []
        for r in rows:
            w = int(r.get("winGameCount") or 0)
            l = int(r.get("loseGameCount") or 0)
            d = int(r.get("drawnGameCount") or 0)
            kind, ln = _streak(r.get("continuousGameResult"))
            gb = r.get("gameBehind")
            out.append(Standing(
                league=self.league,
                season=str(season),
                team_code=self._team(r.get("teamId")),
                rank=int(r.get("ranking") or 0),
                games=int(r.get("gameCount") or (w + l + d)),
                record=WLD(win=w, loss=l, draw=d),
                pct=_fmt(r.get("wra"), 3),
                # 그룹 1위는 소스가 0.0을 준다. 표시할 때 "—"로 바꾸는 것은 렌더의 몫 —
                # 여기서 문자를 넣으면 게이트가 숫자로 못 읽는다.
                games_behind=_fmt(gb if gb is not None else 0, 1).rstrip("0").rstrip("."),
                # **최근 10경기는 이 소스에 없다.** `lastFiveGames`("WWLLW")는 5경기다 —
                # 10경기 칸에 5경기 값을 넣으면 카드가 거짓말을 한다. 없는 대로 둔다.
                last10=None,
                streak_kind=kind,
                streak_len=ln,
                home=None,
                away=None,
                group=self._group(r),
            ))
        # 그룹 안에서 순위순, 그룹끼리는 이름순. 표시 단위를 나누는 것은 렌더의 몫이다.
        out.sort(key=lambda s: (s.group or "", s.rank))
        self._assert_groups(out)
        return out

    # ── 순위 단위 ─────────────────────────────────────────
    #
    # **1위가 여럿인 이유가 여기 있다.** MLB는 지구 6개, NPB는 리그 2개가 각자
    # 1위를 갖는다. 이걸 모르고 한 줄로 세우면 승차가 뒤죽박죽이 된다.
    _MLB_DIV = {"EAST": "동부", "CENT": "중부", "CENTRAL": "중부", "WEST": "서부"}
    _NPB_LEAGUE = {"CL": "센트럴", "PL": "퍼시픽"}

    def _group(self, row: dict) -> str:
        if self.league is League.MLB:
            lg = (row.get("league") or "").strip().upper()
            dv = self._MLB_DIV.get((row.get("division") or "").strip().upper())
            if not lg or not dv:
                raise GateError(
                    f"MLB 팀의 지구를 못 읽었습니다 ({row.get('teamId')!r}: "
                    f"league={row.get('league')!r} division={row.get('division')!r}) — "
                    f"지구를 모르면 순위·승차의 뜻이 사라집니다")
            return f"{lg} {dv}"
        lg = (row.get("league") or "").strip().upper()
        name = self._NPB_LEAGUE.get(lg)
        if not name:
            raise GateError(f"NPB 팀의 리그를 못 읽었습니다 ({row.get('teamId')!r}: {lg!r})")
        return name

    def _assert_groups(self, rows: list[Standing]) -> None:
        """단위마다 팀 수가 맞는지 본다. **0건은 항상 의심**의 그룹판이다."""
        want = 5 if self.league is League.MLB else 6
        counts: dict = {}
        for s in rows:
            counts[s.group] = counts.get(s.group, 0) + 1
        expect_groups = 6 if self.league is League.MLB else 2
        if len(counts) != expect_groups:
            raise GateError(f"{self.league.value}: 순위 단위가 {len(counts)}개 "
                            f"(기대 {expect_groups}개) — {sorted(counts)}")
        bad = {g: n for g, n in counts.items() if n != want}
        if bad:
            raise GateError(f"{self.league.value}: 단위별 팀 수가 다릅니다 {bad} "
                            f"(각 {want}팀이어야 합니다)")

    # ── 부문 순위 ─────────────────────────────────────────
    def _players(self, season: int, kind: str, sort_field: str, size: int) -> list[dict]:
        data = self._get(
            f"/statistics/categories/{self.category}/seasons/{season}/players",
            {"playerType": kind, "sortField": sort_field, "sortDirection": "DESC",
             "page": 1, "pageSize": size},
            f"{kind.lower()}_{season}")
        rows = ((data or {}).get("result") or {}).get("seasonPlayerStats") or []
        if not rows:
            raise GateError(f"네이버 {kind} 0건 ({self.category} {season})")
        return rows

    def _rank_one(self, rows: list[dict], name: str, field: str,
                  desc: bool, qualified: bool, digits: int) -> list[LeaderEntry]:
        """한 부문의 Top5. 값이 없거나 규정 미달인 선수는 아예 빼고 센다."""
        pool = []
        for r in rows:
            if qualified and not r.get("isQualified"):
                continue
            v = r.get(field)
            if v is None:
                continue
            try:
                pool.append((float(v), r))
            except (TypeError, ValueError):
                continue
        if not pool:
            return []
        pool.sort(key=lambda x: (-x[0] if desc else x[0], str(x[1].get("playerName"))))

        out: list[LeaderEntry] = []
        prev_val = None
        rank = 0
        for i, (v, r) in enumerate(pool):
            # **동률은 같은 순위다.** 순번을 그대로 쓰면 같은 값에 1·2위가 붙는다.
            if prev_val is None or v != prev_val:
                rank = i + 1
                prev_val = v
            if rank > TOP_N:
                break
            pid = str(r.get("playerId") or "").strip()
            pname = (r.get("playerName") or "").strip()
            if not pid or not pname:
                self.note("선수 이름이나 ID가 비어 건너뜀", f"{name} {pid or '?'}")
                continue
            try:
                team = self._team(r.get("teamId"))
            except GateError as e:
                self.note("부문 선수의 팀을 매핑 못 해 건너뜀", str(e)[:80])
                continue
            out.append(LeaderEntry(category=name, stat_key=field, rank=rank,
                                   player_id=pid, name=pname, team_code=team,
                                   value=_fmt(v, digits)))
        return out

    def fetch_leaders(self, season: int) -> dict[str, list[LeaderEntry]]:
        """부문별 Top5. **요청 2건**으로 전 부문을 만든다."""
        out: dict[str, list[LeaderEntry]] = {}
        size = 400 if self.league is League.MLB else 220
        for kind, cats, sort_field in (
                ("HITTER", HITTER_CATEGORIES, "hitterHra"),
                ("PITCHER", PITCHER_CATEGORIES, "pitcherEra")):
            rows = self._players(season, kind, sort_field, size)
            self.note_info(f"{kind} 표본", f"{len(rows)}명")
            for name, field, desc, qual, digits in cats:
                entries = self._rank_one(rows, name, field, desc, qual, digits)
                if entries:
                    out[name] = entries
                else:
                    # 소스에 없는 부문은 **만들지 않는다.** 빈 부문을 카드에 그리면
                    # 독자에게는 '망가진 표'로 읽힌다(약점 94).
                    self.note_info("이 소스에 없는 부문이라 제외", name)
        if not out:
            raise GateError(f"네이버 부문 순위가 통째로 비었습니다 ({self.category})")
        return out

    # ── 한 번에 ───────────────────────────────────────────
    def fetch(self, season: int) -> RecordBook:
        self.reset_notices()
        standings = self.fetch_standings(season)
        leaders = self.fetch_leaders(season)
        return RecordBook(
            league=self.league,
            season=str(season),
            collected_utc=datetime.now(timezone.utc),
            source_url=f"{BASE}/statistics/categories/{self.category}/seasons/{season}",
            standings=standings,
            # 팀간 상대전적은 이 API에 없다. 없는 것을 만들지 않는다 —
            # 맞대결 블록은 KBO에만 나간다.
            h2h={},
            leaders=leaders,
        )
