"""LCK · LoL 국제대회 수집 어댑터 (v1.11 신설).

**경로가 바뀌었다.** G0 4차에서 확정한 라이엇 공식 API(`esports-api.lolesports.com`)는
`x-api-key`를 요구하는데, 그 키는 lolesports.com JS 번들의 상수였다.
2026-08-28 실측: 번들 49개를 훑어도 `esports-api`·`x-api-key` 문자열이 없다.
사이트가 호출을 서버 쪽으로 옮긴 것으로 보인다. → **계약에 미리 정해둔 폴백을 쓴다**
(`SOURCE_FALLBACK[LCK] = "leaguepedia_cargoquery"`).

Leaguepedia(MediaWiki Cargo)는 무인증이지만 **레이트리밋이 있다.**
그리고 리밋에 걸리면 `{"error": {...}}`를 200으로 돌려준다 —
`cargoquery` 키만 꺼내 읽으면 **에러가 '0건'으로 둔갑한다.** 실제로 그렇게 읽었다가
"LCK 0경기"라는 잘못된 결론을 낼 뻔했다. 그래서 `error` 키를 먼저 본다.

**리밋이 시간당 쿼터다 (2026-08-28 실측).** 8초 간격에 315초를 기다려도 계속 걸린다.
`action=query`(siteinfo)는 같은 순간에 통과하므로 IP 차단이 아니라 cargoquery 전용 쿼터다.
운영에서 이걸 못 견디면 그날 LCK가 통째로 빠진다. 그래서 세 가지를 건다:
  · 대회를 나눠 세 번 부르지 않고 **한 번의 OR 쿼리**로 받는다 (호출 1/3)
  · 리밋을 만나면 **기다렸다 다시 친다** (`_http`의 429 재시도는 이걸 못 잡는다 —
    HTTP는 200이고 본문만 에러이기 때문이다)
  · 성공한 응답은 **디스크에 캐시**한다. 리밋에 걸리면 캐시로 버틴다.
    캐시가 있으면 조용히 쓰지 않고 나이를 함께 돌려준다 — 묵은 데이터를
    새 데이터인 척 내보내는 것이 리그가 빠지는 것보다 나쁘다.

**리그 이름을 'LCK'라고 쓰면 0건이 나온다 (2026-08-28에 잡은 결함).**
Leaguepedia의 League 필드 실제 값은 `LoL Champions Korea`다. 'LCK'로 시작하는 값은
`LCK Challengers League`(2군)와 `LCK Academy Series`(아카데미)뿐이라, 부분 일치로 짜면
**2군 경기가 1군 행세를 하고** 정확 일치로 짜면 **0건**이 된다. 둘 다 사실 오류다.
그래서 이름을 정확 일치로 두되, **그 이름이 위키에 실재하는지 먼저 확인하는 게이트**를 건다.
이름이 실재하는데 경기가 0건이면 시즌 오프이고, 이름 자체가 없으면 위키가 바꾼 것이다.

`TBD`는 팀이 아니다. Worlds 미확정 대진의 자리표시자라 그대로 쓰면
"TBD 대 TBD" 카드가 나간다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import pathlib
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (GateError, Game, GameMeta, League, Score, ScoreUnit, Status,
                      TeamRef, UnknownStatus)

_API = "https://lol.fandom.com/api.php"
_OPENER = make_opener()

# 대회 → 우리 리그. **Leaguepedia의 League 필드 실제 값**이어야 한다(2026-08-28 실측).
# 'LCK'·'Worlds'·'MSI'는 이 위키에 존재하지 않는 이름이다 — 그렇게 쓰면 0건이 나온다.
LEAGUE_OF = {
    "LoL Champions Korea": League.LCK,          # ← 'LCK'가 아니다
    "World Championship": League.INTL_LOL,      # ← 'Worlds'가 아니다
    "Mid-Season Invitational": League.INTL_LOL,
}

# 이름이 비슷해서 섞이기 쉬운 하위 대회. 정확 일치를 쓰는 이유를 남겨둔다.
EXCLUDED_LEAGUES = frozenset({
    "LCK Challengers League",    # 2군
    "LCK Academy Series",        # 아카데미
})

# **`League`가 맞아도 우리 대회가 아닌 것이 섞여 온다 (v1.11i).**
# `T.League='World Championship'` 안에 `T.Name='LPL 2026 Regional Finals'`
# 3경기(2026-09-17~19)가 들어 있다. 롤드컵 본선이 아니라 **LPL 지역 선발전**이다
# (MatchId도 `LPL/2026 Season/Regional Finals_…`로 시작한다).
# 지금은 대진이 전부 `TBD`라 자리표시자 필터에 걸려 안 나가지만,
# 9월 중순에 대진이 확정되는 순간 **중국 지역예선이 롤드컵 콘텐츠로 발행된다.**
# 대회 이름(`T.Name`)에 지역 선발전 표시가 있으므로 그것으로 거른다.
EXCLUDED_TOURNAMENT_PATTERNS = (
    "regional finals",       # LPL/LEC/LCS 지역 선발전
    "regional qualifier",
)


def _is_excluded_tournament(name: str) -> bool:
    n = (name or "").strip().lower()
    return any(p in n for p in EXCLUDED_TOURNAMENT_PATTERNS)

# 팀이 아닌 자리표시자. 대진 미확정 상태로 그대로 쓰면 'TBD 대 TBD' 카드가 나간다.
PLACEHOLDER_TEAMS = frozenset({"TBD", "TBA", "?", "-", ""})

# 호출 간 최소 간격(초). Leaguepedia는 몰아서 때리면 ratelimited를 200으로 돌려준다.
_MIN_GAP = 8.0
_last_call = 0.0

# 리밋을 만났을 때 기다릴 시간(초). 시간당 쿼터라 짧은 재시도로는 못 뚫는다.
#
# **v1.11k: 재시도를 줄였다.** 실측에서 104시간(4.3일) 동안 리밋이 안 풀렸는데,
# 원인은 우리가 계속 두드린 것이다. 한 번의 수집에서 30·90·240초를 더 쓰면
# 그 자체가 쿼터를 더 먹고, 틱마다 반복되면 회복할 틈이 없다.
# 한 번만 짧게 다시 치고, 안 되면 **캐시로 버티고 다음 기회를 기다린다**
# (tick이 레이트리밋 실패에 6시간 백오프를 건다).
_RATELIMIT_WAITS = (20,)

# 성공 응답 캐시. 리밋에 걸린 날 리그가 통째로 빠지는 것을 막는다.
CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"
CACHE_MAX_AGE_SECONDS = 36 * 3600      # 이보다 묵으면 캐시로도 안 버틴다


class RateLimited(GateError):
    """레이트리밋. 구조 오류와 구분해야 캐시로 버틸지 판단할 수 있다."""


def _cache_path(key: str) -> pathlib.Path:
    return CACHE_DIR / f"lck_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"


def _cargo_once(**params) -> list[dict]:
    global _last_call
    gap = _MIN_GAP - (time.monotonic() - _last_call)
    if gap > 0:
        time.sleep(gap)
    q = {"action": "cargoquery", "format": "json", "limit": "200"}
    q.update(params)
    url = f"{_API}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "nudetv-collector/1.0 (sports schedule)",
        "Accept": "application/json"})
    raw = _fetch(_OPENER, req, label="Leaguepedia")
    _last_call = time.monotonic()
    data = json.loads(raw.decode("utf-8", "replace"))

    # 레이트리밋·문법 오류가 HTTP 200 + error 키로 온다.
    # 여기서 안 보면 에러가 '0건'이 되어 조용히 리그가 사라진다.
    if "error" in data:
        err = data["error"]
        msg = f"Leaguepedia: {err.get('code')} — {str(err.get('info'))[:120]}"
        # 리밋과 구조 오류를 갈라야 '캐시로 버틸지'를 판단할 수 있다.
        # 구조 오류는 캐시로 덮으면 안 된다 — 위키가 바뀐 것을 못 보고 지나친다.
        if str(err.get("code")) == "ratelimited":
            raise RateLimited(msg)
        raise GateError(msg)
    if "cargoquery" not in data:
        raise GateError(f"Leaguepedia: 응답에 cargoquery 없음 (키: {list(data)[:5]})")
    return [x["title"] for x in data["cargoquery"]]


def _cargo(*, _cache: bool = True, **params) -> list[dict]:
    """리밋을 만나면 기다렸다 다시 치고, 그래도 안 되면 캐시로 버틴다.

    캐시를 쓸 때는 조용히 쓰지 않는다 — `last_cache_age_seconds`에 나이를 남겨
    호출자가 '묵은 데이터'임을 알 수 있게 한다.
    """
    global last_cache_age_seconds
    key = urllib.parse.urlencode(sorted(params.items()))
    path = _cache_path(key)
    err: Exception | None = None
    for wait in (0,) + _RATELIMIT_WAITS:
        if wait:
            time.sleep(wait)
        try:
            rows = _cargo_once(**params)
        except RateLimited as e:
            err = e
            continue
        if _cache:
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(rows, ensure_ascii=False))
            except OSError:
                pass                          # 캐시 실패는 수집 실패가 아니다
        last_cache_age_seconds = 0.0
        return rows

    # 여기까지 왔으면 리밋을 못 뚫었다. 캐시가 있으면 나이를 달고 쓴다.
    if _cache and path.exists():
        age = time.time() - path.stat().st_mtime
        if age <= CACHE_MAX_AGE_SECONDS:
            last_cache_age_seconds = age
            return json.loads(path.read_text())
        raise GateError(
            f"Leaguepedia 리밋이 안 풀렸고 캐시도 {age / 3600:.1f}시간으로 너무 묵었습니다 "
            f"(상한 {CACHE_MAX_AGE_SECONDS // 3600}시간). 묵은 일정을 오늘 것처럼 내보내지 않습니다.")
    raise err or GateError("Leaguepedia: 알 수 없는 실패")


last_cache_age_seconds: float = 0.0


# ── LCK 팀 정규화 ────────────────────────────────────────────────────
# **네이밍 스폰서가 붙으면 소스의 팀 이름이 시즌 중에 바뀐다 (2026-08-28 실측).**
#   LCK Cup 2026 (1~3월): 'BRION' · 'DRX'
#   정규시즌 이후 (4월~) : 'HANJIN BRION' · 'Kiwoom DRX'
# 이름을 그대로 코드로 쓰면 한 팀이 두 팀이 되어 순위표·상대전적이 통째로 틀린다
# (KBL이 카테고리마다 팀 코드를 바꾸던 함정과 같은 계열).
#
# 개명이라는 근거 — 추측이 아니라 데이터로 확인했다:
#   · 두 이름이 같은 대회에 함께 나온 적이 **한 번도 없다**
#   · 대회별 팀 수가 전부 정확히 10팀이다 (LCK는 10팀 리그)
#   · 병합하면 시즌 전체가 정확히 10팀이 된다. 병합 안 하면 12팀이 된다
LCK_TEAMS = {
    "T1": "T1",
    "Gen.G": "GEN",
    "Dplus Kia": "DK",
    "KT Rolster": "KT",
    "Hanwha Life Esports": "HLE",
    "Nongshim RedForce": "NS",
    "BNK FEARX": "BFX",
    "DN SOOPers": "DNS",
    "Kiwoom DRX": "DRX",
    "DRX": "DRX",                  # ← 스폰서 붙기 전 표기
    "HANJIN BRION": "BRO",
    "BRION": "BRO",                # ← 스폰서 붙기 전 표기
}

# 위 표를 병합했을 때 나와야 하는 팀 수. 새 별칭이 생기면 여기서 걸린다.
LCK_TEAM_COUNT = 10


def _team_code(name: str, league: League = League.INTL_LOL) -> str:
    """표시가 아니라 **식별**을 위한 코드. 같은 팀은 반드시 같은 코드가 나와야 한다."""
    n = (name or "").strip()
    if league is League.LCK:
        code = LCK_TEAMS.get(n)
        if code is None:
            # LCK는 10팀 고정이다. 모르는 이름이 오면 개명이거나 승강이다.
            # 조용히 새 코드를 만들면 그 팀이 순위표에서 갈라진다 — 막고 사람이 본다.
            raise UnknownStatus(
                f"LCK: 등록되지 않은 팀 이름 {n!r} — 개명(네이밍 스폰서)일 수 있습니다. "
                f"LCK_TEAMS에 별칭을 추가해야 같은 팀으로 묶입니다.")
        return code
    # 국제대회는 참가팀이 매년 바뀐다. 고정 표를 둘 수 없어 이름에서 만든다.
    return re.sub(r"[^A-Za-z0-9가-힣]", "", n).upper()[:12] or "UNK"


class LckAdapter(NoticeMixin):
    """LCK 및 LoL 국제대회. league_filter로 대회를 고른다."""

    def __init__(self, league: League = League.LCK) -> None:
        if league not in (League.LCK, League.INTL_LOL):
            raise GateError(f"LCK 어댑터: 지원하지 않는 리그 {league.value}")
        self.league = league
        self._names = [k for k, v in LEAGUE_OF.items() if v is league]
        # 조용히 사라지면 안 되는 것들. 호출자가 꺼내 보고 보고한다.
        # (v1.11i부터는 `skipped_report()`·`notices`가 정식 창구다.
        #  아래 두 속성은 기존 검증 스크립트가 읽고 있어 그대로 둔다.)
        self.skipped_placeholder = 0      # TBD 등 대진 미확정으로 건너뛴 수
        self.cache_age_seconds = 0.0      # 0보다 크면 캐시로 버틴 것 = 묵은 데이터

    def fetch(self, since: str, *, limit: int = 500) -> list[Game]:
        """since: 'YYYY-MM-DD'.

        limit 기본값이 200이던 시절 2026 시즌 257경기 중 57경기가 조용히 잘렸다.
        잘림은 '경기가 없다'와 구분이 안 되므로, 상한에 닿으면 막는다.
        """
        self.reset_notices()
        cond = " OR ".join(f"T.League='{n}'" for n in self._names)
        rows = _cargo(
            tables="MatchSchedule=MS,Tournaments=T",
            join_on="MS.OverviewPage=T.OverviewPage",
            fields=("MS.DateTime_UTC,MS.Team1,MS.Team2,MS.Team1Score,MS.Team2Score,"
                    "MS.Winner,MS.BestOf,MS.MatchId,T.League,T.Name"),
            where=f"({cond}) AND MS.DateTime_UTC >= '{since}'",
            order_by="MS.DateTime_UTC", limit=str(limit))
        # 캐시로 버틴 사실은 속성과 보고서 양쪽에 남긴다 — 속성에만 두면
        # 알림에 싣는 것을 잊는다(실제로 48시간 묵은 스냅샷이 그렇게 나갔다).
        self.note_cache_age(last_cache_age_seconds)

        if len(rows) >= limit:
            raise GateError(
                f"LCK: 결과가 상한 {limit}건에 닿았습니다 — 뒷부분이 잘렸을 수 있습니다. "
                f"기간을 좁히거나 상한을 올려야 합니다(조용히 잘리면 '경기 없음'과 구분되지 않습니다).")

        if not rows:
            # 0건은 항상 의심한다. 시즌 오프인지, 리그 이름이 바뀐 것인지 갈라야 한다.
            # 'LCK'라고 썼다가 0건이 나온 적이 있어서(실제 값은 'LoL Champions Korea')
            # 이름 자체가 위키에 실재하는지 확인한다.
            self._assert_league_names_exist()
            return []

        out: list[Game] = []
        self.skipped_placeholder = 0
        for r in rows:
            # 우리가 요청한 이름만 왔는지 확인한다. 2군이 섞이면 여기서 걸린다.
            lg = str(r.get("League") or "").strip()
            if lg not in LEAGUE_OF:
                raise UnknownStatus(f"LCK: 요청하지 않은 대회 {lg!r}가 섞여 왔습니다")
            name = str(r.get("Name") or "").strip()
            if _is_excluded_tournament(name):
                # 지역 선발전은 롤드컵 본선이 아니다(위 주석 참조).
                self.note("우리 대회가 아니라 제외(지역 선발전)",
                          f"{name} — {r.get('MatchId')}")
                continue
            g = self._parse(r)
            if g:
                out.append(g)
        return out

    def _assert_league_names_exist(self) -> None:
        """0건일 때만 부른다. 이름이 위키에 실재하면 시즌 오프, 없으면 위키가 바뀐 것."""
        rows = _cargo(tables="Tournaments=T", fields="T.League",
                      where=" OR ".join(f"T.League='{n}'" for n in self._names),
                      group_by="T.League", limit="20")
        found = {str(r.get("League") or "").strip() for r in rows}
        missing = [n for n in self._names if n not in found]
        if missing:
            raise GateError(
                f"LCK: 대회 이름 {missing}이(가) Leaguepedia에 없습니다 — 위키가 이름을 바꿨습니다. "
                f"이 상태로 두면 매일 조용히 0건이 나갑니다.")

    def _parse(self, r: dict) -> Game | None:
        dt = (r.get("DateTime UTC") or "").strip()
        if not re.match(r"\d{4}-\d{2}-\d{2}", dt):
            raise UnknownStatus(f"LCK: 시각 해석 불가 {dt!r}")
        start = datetime.strptime(dt[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

        t1 = (r.get("Team1") or "").strip()
        t2 = (r.get("Team2") or "").strip()
        # 'TBD'는 팀이 아니라 자리표시자다. Worlds 미확정 대진이 이렇게 온다.
        # 그대로 두면 'TBD 대 TBD' 카드가 나간다 — 실제로 2026 Worlds 대진에 있다.
        if t1.upper() in PLACEHOLDER_TEAMS or t2.upper() in PLACEHOLDER_TEAMS:
            self.skipped_placeholder = getattr(self, "skipped_placeholder", 0) + 1
            self.note("대진 미확정(TBD)이라 건너뜀",
                      f"{dt[:10]} {r.get('Name')} {t1 or '?'} vs {t2 or '?'}")
            return None                       # 대진 미발행(토너먼트 등록만 된 상태)

        # **끝났는지는 Winner만이 안다 (v1.11h).**
        #
        # Leaguepedia는 `Team1Score`/`Team2Score`를 **세트가 끝날 때마다 갱신한다.**
        # 전에는 `winner or 점수있음`으로 판정해서, BO5 1세트가 끝나는 순간
        # "T1 1-0 BNK FEARX 종료"가 성립했다. 실제 캐시에 그 순간이 남아 있다
        # (lck_be5519f46fada1a9.json: Team1Score=1, Team2Score=0, Winner='' —
        #  같은 매치의 최종 결과는 3-2). KBO의 "점수 있으면 종료"와 같은 계열이다.
        #
        # 점수는 결과가 아니라 진행 상황이다. 종결 신호는 Winner 하나뿐이다.
        s1, s2 = r.get("Team1Score"), r.get("Team2Score")
        winner = str(r.get("Winner") or "").strip()
        has_score = s1 not in (None, "") and s2 not in (None, "")
        if winner:
            status = Status.FINAL
        elif has_score:
            status = Status.LIVE          # 세트는 진행됐지만 매치는 안 끝났다
        else:
            status = Status.SCHEDULED
        # ── 홈/원정 자리 (v1.11i에서 바로잡음) ──────────────────
        #
        # **LoL에는 홈이 없다.** LCK는 전 경기가 서울 LoL파크 한 곳에서 열리고
        # 국제대회는 개최지가 옮겨 다닌다. 즉 `home`/`away`는 '연고지'가 아니라
        # **좌우 자리**를 뜻할 수밖에 없다. 그러면 어느 쪽이 왼쪽이어야 하는가.
        #
        # 소스(Leaguepedia)의 `Team1`은 중계·대진표의 **왼쪽** 팀이다.
        # 우리 렌더러(pipeline.render_result / render_morning)는 예외 없이
        # `away`를 왼쪽에, `home`을 오른쪽에 그린다.
        # 전에는 `home=Team1, away=Team2`로 두었기 때문에 좌우가 뒤집혔다 —
        # 2026-09-01 `Team1=Gen.G(3) / Team2=KT Rolster(0)`가 카드에서는
        # "KT 0 : 3 젠지"로 나갔다. 점수-팀 짝은 맞아 사실 오류는 아니지만
        # 중계 화면과 좌우가 반대라 같은 경기를 두 번 보는 사람이 헷갈린다.
        #
        # 그래서 **Team1을 `away`(왼쪽), Team2를 `home`(오른쪽)** 으로 둔다.
        # 점수도 같이 뒤집어야 짝이 유지된다 — `Score(home=Team2Score, away=Team1Score)`.
        # 야구·축구처럼 홈이 실재하는 종목과 달리, 여기서 home/away는
        # **표시 순서 이상의 의미가 없다**는 것을 기억할 것.
        left, right = t1, t2                  # 중계 좌 / 중계 우
        left_score, right_score = s1, s2

        score = None
        if status is Status.FINAL and has_score:
            # LoL은 세트(맵) 스코어다.
            score = Score(int(float(right_score)), int(float(left_score)), ScoreUnit.MAPS)

        bo = r.get("BestOf")
        key = str(r.get("MatchId") or f"{dt}-{t1}-{t2}")

        g = Game(
            league=self.league, season=str(start.year), source_key=key,
            home=TeamRef(self.league, _team_code(right, self.league)),
            away=TeamRef(self.league, _team_code(left, self.league)),
            start_utc=start,
            # 국제대회는 개최지가 옮겨 다닌다. 한국 대회는 KST가 홈 시간대다.
            # **국제 대회 개최지 시간대를 모르면 UTC를 '현지'라고 부르지 않는다.**
            # 전에는 UTC를 넣어 카드에 "현지 07:30"이 찍혔다 — 그 시각은
            # 어느 개최지의 시각도 아니다. 개최지 tz를 못 받으므로
            # 한국 시간대로 두고, 현지 병기 자체를 끈다(needs_local_time이 꺼진다).
            home_tz="Asia/Seoul",
            status=status, score=score, venue=None,
            meta=GameMeta(season_category=r.get("Name") or None,
                          best_of=int(bo) if str(bo).isdigit() else None),
        )
        g.validate()
        return g
