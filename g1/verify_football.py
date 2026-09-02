"""유럽 6개 대회 어댑터 사전 검증 — **키 없이** 전부 깨본다 (v1.11c).

대표님이 키를 마지막에 발급하시므로, 그때 "꽂기만 하면 되는" 상태를 지금 만든다.
키가 온 뒤에 파싱 결함을 발견하면 그때부터 또 왕복이 시작된다 — 그걸 막는 게 이 파일이다.

실제 호출 대신 **football-data.org v4의 응답 구조를 그대로 본뜬 모의 응답**을 넣는다.
구조는 어댑터 주석에 적힌 공식 문서 기준이고, 여기서 검증하는 것은
"응답이 이 모양으로 오면 우리가 옳게 읽는가"다.

키가 온 뒤에 할 일은 `verify_leagues.py`가 자동으로 처리한다(SKIP → 실검증).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from contract import (GateError, League, Status, TEAM_NAME_MAX_LEN, UnknownStatus,
                      assert_no_stale_scheduled, stale_unresolved, team_name)
from adapters.football_data import (COMPETITION, FootballDataAdapter, LEAGUE_TO_CODE,
                                    TOKEN_ENV, _STATUS, load_token)
import pipeline as P

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def expect(name, fn, exc=(GateError, UnknownStatus)):
    global ok, fail
    try:
        fn()
    except exc as e:
        ok += 1; print(f"  PASS  {name}  → {str(e)[:66]}")
        return
    except Exception as e:                                    # noqa: BLE001
        fail += 1; print(f"  FAIL  {name}  예상 못한 {type(e).__name__}: {str(e)[:50]}")
        return
    fail += 1; print(f"  FAIL  {name}  (막지 않았다)")


# ── 모의 응답 (football-data.org v4 구조) ──────────────────────
#
# **팀에 `id`와 나라 정보를 실는다 (2026-09-02).** v1.11i에서 UCL 홈경기의
# 시간대를 대회 고정값(`Europe/Zurich`)이 아니라 **홈팀의 나라**로 정하게 바뀌었다.
# 소스에서 나라가 오는 자리는 `/v4/competitions/{code}/teams`의 `area.name`이고,
# `/matches`의 팀 객체에는 보통 `id`·`name`·`tla`만 온다. 모의 응답도 그 모양
# 그대로 나눈다 — 경기 응답에 나라를 끼워 넣어 두면, 실제로는 두 번째 요청이
# 필요하다는 사실을 검증이 숨기게 된다.
import tempfile                                                    # noqa: E402
import adapters.football_data as FD                                # noqa: E402


def _tid(tla: str) -> int:
    """TLA로 안정적인 팀 id를 만든다 (경기 응답과 팀 목록이 같은 id를 써야 한다)."""
    return 1000 + sum((i + 1) * ord(c) for i, c in enumerate(tla))


def _team_obj(spec):
    """`/matches`의 팀 객체. spec=(이름, TLA[, 나라]).

    나라를 셋째 칸에 주면 **경기 응답에 area가 실려 오는 경우**를 흉내낸다
    (어댑터는 그때 팀 목록을 다시 받지 않는다).
    """
    name, tla = spec[0], spec[1]
    o = {"id": _tid(tla), "name": name, "tla": tla, "shortName": name}
    if len(spec) > 2 and spec[2]:
        o["area"] = {"id": 2000, "name": spec[2]}
    return o


def _teams(*specs):
    """`/v4/competitions/{code}/teams` 응답 모양. 나라는 `area.name`으로 온다."""
    return {"count": len(specs),
            "teams": [{"id": _tid(t), "name": n, "tla": t, "shortName": n,
                       "area": {"id": 2000 + i, "name": a}}
                      for i, (n, t, a) in enumerate(specs)]}


def _match(mid, status, home=("Arsenal FC", "ARS"), away=("Chelsea FC", "CHE"),
           hg=None, ag=None, utc="2026-08-22T14:00:00Z", stage="REGULAR_SEASON",
           extra=None):
    m = {
        "id": mid, "utcDate": utc, "status": status, "stage": stage,
        "season": {"startDate": "2026-08-08", "endDate": "2027-05-23"},
        "homeTeam": _team_obj(home),
        "awayTeam": _team_obj(away),
        "score": {"winner": None, "duration": "REGULAR",
                  "fullTime": {"home": hg, "away": ag}},
        "venue": "Emirates Stadium",
    }
    if extra:
        m["score"].update(extra)
    return m


class FakeAdapter(FootballDataAdapter):
    """네트워크 대신 준비된 응답을 돌려준다. 파싱만 시험한다.

    `teams=`를 주면 `/competitions/{code}/teams` 요청에 그 응답을 돌려준다.
    안 주면 그 요청은 **GateError로 실패**한다 — 실제 `_http.fetch()`가 실패할 때
    올리는 예외와 같은 종류다(권한 없음·응답 없음).
    """

    def __init__(self, league, payload, teams=None):
        # load_token()을 건너뛰기 위해 부모 __init__을 우회한다 (키 없이 검증하는 게 목적)
        if league not in LEAGUE_TO_CODE:
            raise GateError(f"지원하지 않는 리그 {league.value}")
        self.league = league
        self.code = LEAGUE_TO_CODE[league]
        self._token = "TEST"
        self._payload = payload
        self._teams_payload = teams
        self.asked = []
        # **캐시는 검증마다 새 디렉터리를 본다.** 팀 목록·시즌 일정 캐시는 파일에
        # 남아서(`cache/fd_*.json`) 다음 검증이 앞 검증의 응답을 물려받는다 —
        # 실제로 저장소에 `fd_season_CL_2026.json`이 그렇게 남아 있었다.
        # 검증 결과가 실행 순서와 남은 파일에 따라 달라지면 검증이 아니다.
        FD.CACHE_DIR = pathlib.Path(tempfile.mkdtemp(prefix="fd-cache-"))

    def _get(self, path):
        self.asked.append(path)
        if "/teams" in path:
            if self._teams_payload is None:
                raise GateError(f"football-data {self.code}: 팀 목록 요청 실패(모의)")
            return self._teams_payload
        return self._payload


print("=" * 62)
print("유럽 6개 대회 어댑터 사전 검증 (키 없이)")
print("=" * 62)

# ── 1) 키 정책 ────────────────────────────────────────────────
print("\n1. 키 정책 — 없으면 조용히 0건이 아니라 명시적으로 막힌다")
import os
_saved = os.environ.pop(TOKEN_ENV, None)
expect("키 없으면 GateError", load_token)
os.environ[TOKEN_ENV] = "dummy-key-for-test"
check("키가 있으면 통과", load_token() == "dummy-key-for-test")
os.environ.pop(TOKEN_ENV)
if _saved:
    os.environ[TOKEN_ENV] = _saved

# ── 2) 대회 매핑 ──────────────────────────────────────────────
print("\n2. 대회 매핑 — 6개가 빠짐없이 양방향으로 연결됐나")
check("대회 6개", len(COMPETITION) == 6, str(len(COMPETITION)))
check("양방향 일치", all(LEAGUE_TO_CODE[v] == k for k, v in COMPETITION.items()))
want = {League.EPL, League.LALIGA, League.SERIEA, League.BUNDESLIGA,
        League.LIGUE1, League.UCL}
check("여섯 리그가 정확히 그 여섯", set(COMPETITION.values()) == want,
      str(set(COMPETITION.values()) ^ want))
expect("지원 안 하는 리그는 차단", lambda: FootballDataAdapter(League.KBO, "x"))

# ── 3) 상태 도메인 전체 ───────────────────────────────────────
print("\n3. 상태 도메인 — 문서에 있는 값 전부를 읽는가")
DOMAIN = ["SCHEDULED", "TIMED", "IN_PLAY", "PAUSED", "FINISHED",
          "AWARDED", "POSTPONED", "SUSPENDED", "CANCELLED"]
check(f"문서 상태 {len(DOMAIN)}종 전부 등록", all(s in _STATUS for s in DOMAIN),
      str([s for s in DOMAIN if s not in _STATUS]))
for raw, want_st in (("TIMED", Status.SCHEDULED), ("IN_PLAY", Status.LIVE),
                     ("PAUSED", Status.LIVE), ("FINISHED", Status.FINAL),
                     ("AWARDED", Status.FINAL), ("POSTPONED", Status.POSTPONED),
                     ("SUSPENDED", Status.SUSPENDED), ("CANCELLED", Status.CANCELED)):
    sc = (2, 1) if want_st in (Status.FINAL, Status.LIVE) else (None, None)
    a = FakeAdapter(League.EPL, {"matches": [_match(1, raw, hg=sc[0], ag=sc[1])]})
    g = a.fetch("2026-08-01", "2026-08-31")[0]
    check(f"{raw} → {want_st.value}", g.status is want_st, g.status.value)

expect("미등록 상태는 막힌다 (조용히 예정으로 떨어뜨리지 않는다)",
       lambda: FakeAdapter(League.EPL, {"matches": [_match(9, "GOLDEN_GOAL")]})
       .fetch("2026-08-01", "2026-08-31"))

# ── 4) 점수 ───────────────────────────────────────────────────
print("\n4. 점수 — 있어야 할 때 있고, 없어야 할 때 없다")
a = FakeAdapter(League.EPL, {"matches": [_match(2, "FINISHED", hg=3, ag=1)]})
g = a.fetch("x", "y")[0]
check("종료 경기에 점수", g.score is not None and (g.score.home, g.score.away) == (3, 1))
check("골 단위", g.score.unit.value == "goals", g.score.unit.value)
a = FakeAdapter(League.EPL, {"matches": [_match(3, "TIMED")]})
check("예정 경기엔 점수 없음", a.fetch("x", "y")[0].score is None)
a = FakeAdapter(League.EPL, {"matches": [_match(4, "FINISHED", hg=0, ag=0)]})
g = a.fetch("x", "y")[0]
check("0-0도 점수로 읽는다 (None과 구분)", g.score is not None and g.is_draw())

# ── 5) 승부차기 (UCL) ─────────────────────────────────────────
# **모의 응답에 나라 정보를 넣었다 (2026-09-02).** UCL은 홈팀의 나라로 시간대를
# 정하고, 모르면 그 경기를 건너뛴다. 나라가 없으면 5절 전체가 '파싱 후 0건'으로
# 중단됐다 — 검증이 못 도는 것과 어댑터가 틀린 것은 다르다.
print("\n5. 승부차기 — 무승부처럼 보이지만 승자가 있다")

import re as _re                                                   # noqa: E402
from contract import DecidedBy                                     # noqa: E402

UCL_TEAMS = _teams(("SL Benfica", "BEN", "Portugal"),
                   ("Galatasaray SK", "GAL", "Turkey"),
                   ("Arsenal FC", "ARS", "England"),
                   ("Chelsea FC", "CHE", "England"),
                   ("Liverpool FC", "LIV", "England"),
                   ("Everton FC", "EVE", "England"),
                   ("FC Bayern München", "FCB", "Germany"))
BEN, GAL = ("SL Benfica", "BEN"), ("Galatasaray SK", "GAL")


def _text(html_or_cap: str) -> str:
    return _re.sub("<[^>]+>", " ", html_or_cap)


a = FakeAdapter(League.UCL, {"matches": [
    _match(5, "FINISHED", hg=1, ag=1, stage="SEMI_FINALS", home=BEN, away=GAL,
           extra={"duration": "PENALTY_SHOOTOUT",
                  "penalties": {"home": 4, "away": 3}})]}, teams=UCL_TEAMS)
g = a.fetch("x", "y")[0]
check("승부차기 점수를 보관", g.meta.penalties is not None
      and (g.meta.penalties.home, g.meta.penalties.away) == (4, 3))
check("단계(stage)를 보관", g.meta.season_category == "SEMI_FINALS")

# **[신설] 승부차기로 끝난 경기가 '무승부'로 렌더되지 않는가.**
# 유럽 리그를 켤 때 가장 먼저 터질 자리였다 — v1.11i 전에는 `meta.penalties`만
# 채우고 `decided_by`를 REGULAR로 뒀다. 계약의 `is_draw()`는 decided_by를 먼저
# 보므로 UCL 녹아웃 1-1이 그대로 "무승부"로 카드에 실렸다.
# 저장값(decided_by)·판정(is_draw)·카드·캡션을 **네 층에서 함께** 본다 —
# 한 층만 보면 v1.11h처럼 "상태는 고쳤는데 렌더는 그대로"가 다시 생긴다.
check("결정 방식이 PSO로 남는다", g.meta.decided_by is DecidedBy.PSO,
      g.meta.decided_by.value)
check("승부차기는 무승부가 아니다 (계약 판정)", not g.is_draw())
_pso_html = _text(P.render_result([g], g.sports_day))
check("카드가 승부차기 경기를 '무승부'로 그리지 않는다", "무승부" not in _pso_html,
      _pso_html[:0])
check("승부차기 경기도 카드에서 빠지지는 않는다", "1" in _pso_html and "종료" in _pso_html)
_pso_cap = P.caption_result([g], g.sports_day)
check("캡션도 '무승부'라 하지 않는다", "무승부" not in _pso_cap, _pso_cap)

# PK 점수를 안 주는 응답 — 계약이 PSO에 penalties를 요구하므로 PSO로 못 적는다.
# 그렇다고 REGULAR로 두면 '무승부'가 된다. 연장(AET)으로 적고 사실을 보고한다.
a = FakeAdapter(League.UCL, {"matches": [
    _match(53, "FINISHED", hg=2, ag=2, stage="SEMI_FINALS", home=BEN, away=GAL,
           extra={"duration": "PENALTY_SHOOTOUT"})]}, teams=UCL_TEAMS)
g_nopk = a.fetch("x", "y")[0]
check("PK 점수가 없어도 무승부로 적지 않는다", not g_nopk.is_draw(),
      g_nopk.meta.decided_by.value)
check("PK 점수를 못 구한 사실을 보고한다", bool(a.skipped_report()), str(a.notices))

# 합산으로 갈리는 2차전: 그날 1-1이어도 합산 3-2면 무승부가 아니다.
_legs = [_match(51, "FINISHED", hg=2, ag=1, stage="SEMI_FINALS", home=BEN, away=GAL,
                utc="2026-04-28T19:00:00Z"),
         _match(52, "FINISHED", hg=1, ag=1, stage="SEMI_FINALS", home=GAL, away=BEN,
                utc="2026-05-05T19:00:00Z")]
gs2 = FakeAdapter(League.UCL, {"matches": _legs}, teams=UCL_TEAMS).fetch("x", "y")
_leg2 = [x for x in gs2 if x.source_key == "52"][0]
check("2차전에 합산 점수가 붙는다", _leg2.meta.aggregate is not None,
      str(_leg2.meta.aggregate))
check("합산으로 갈린 2차전은 무승부가 아니다", not _leg2.is_draw())
check("합산 2차전 카드에 '무승부'가 안 나온다",
      "무승부" not in _text(P.render_result([_leg2], _leg2.sports_day)))

# 합산을 못 구했는데 그날 스코어가 동점이면 **발행하지 않는다** —
# 내보내면 "무승부"라는 사실 오류가 된다. 그 경기만 빠지고 사유가 보고된다.
_solo = FakeAdapter(League.UCL, {"matches": [
    _match(54, "FINISHED", hg=1, ag=1, stage="SEMI_FINALS", home=GAL, away=BEN,
           utc="2026-05-05T19:00:00Z"),
    _match(55, "FINISHED", hg=3, ag=0, stage="SEMI_FINALS", home=("Arsenal FC", "ARS"),
           away=("Chelsea FC", "CHE"), utc="2026-05-05T19:00:00Z")]}, teams=UCL_TEAMS)
_kept = _solo.fetch("x", "y")
check("합산 미확인 동점 2차전은 발행하지 않는다",
      [x.source_key for x in _kept] == ["55"], str([x.source_key for x in _kept]))
check("빠뜨린 사실을 보고한다", bool(_solo.skipped_report()), str(_solo.notices))

# ── 6) 팀 코드 ────────────────────────────────────────────────
print("\n6. 팀 — 코드가 없으면 만들어내지 않는다")
expect("팀 코드 없으면 막힌다", lambda: FakeAdapter(League.EPL, {"matches": [{
    "id": 6, "utcDate": "2026-08-22T14:00:00Z", "status": "TIMED",
    "season": {"startDate": "2026-08-08"},
    "homeTeam": {"name": "Unknown"}, "awayTeam": {"name": "Other"},
    "score": {"fullTime": {}}}]}).fetch("x", "y"))
a = FakeAdapter(League.EPL, {"matches": [_match(7, "TIMED")]})
g = a.fetch("x", "y")[0]
check("tla를 팀 코드로", (g.home.team_code, g.away.team_code) == ("ARS", "CHE"),
      f"{g.home.team_code}/{g.away.team_code}")
check("표시명이 카드 폭에 맞음 (등록 전이면 코드 그대로)",
      len(team_name(g.home)) <= TEAM_NAME_MAX_LEN, team_name(g.home))

# ── 7) 응답 구조 ──────────────────────────────────────────────
print("\n7. 응답 구조 — 조용한 0건을 '경기 없음'으로 읽지 않는다")


def _err(fn) -> str:
    """막혔으면 사유 문장을, 안 막혔으면 빈 문자열을 준다."""
    try:
        fn()
    except (GateError, UnknownStatus) as e:
        return str(e)
    return ""


expect("matches 키가 없으면 막힌다 (권한·구조 문제)",
       lambda: FakeAdapter(League.EPL, {"errorCode": 403}).fetch("x", "y"))

# **[기대 변경 2026-09-02] "빈 목록은 그대로 0건" → "빈 목록도 막는다".**
# 옛 기대는 "그날 경기가 없을 수 있다"였다. 그런데 다른 어댑터(MLB·NPB·K리그)에
# 다 있는 **"0건은 항상 의심" 게이트가 여기만 빠져** 있던 것이었고, 무료 등급은
# 권한·기간·대회 코드 문제도 200 + 빈 배열로 돌려준다. 그래서 옛 기대는
# 이 절의 **원래 목적("권한 문제를 '경기 없음'으로 읽지 않는다")을 스스로 어기고**
# 있었다. 목적은 그대로 두고 기대만 새 동작에 맞춘다.
expect("빈 목록도 막는다 (소스가 조용히 준 0건을 '경기 없음'으로 읽지 않는다)",
       lambda: FakeAdapter(League.EPL, {"matches": []}).fetch("x", "y"))

# **'진짜로 경기가 없는 날'과 '소스가 조용히 빈 응답을 준 날'을 어떻게 가르나.**
# 소스가 주는 빈 배열만으로는 둘을 **구분할 수 없다** — 그래서 조용히 넘기지 않고
# 사람에게 올린다. 대신 0건의 **세 갈래를 서로 다른 사유로** 올려, 사람이 어느
# 쪽인지 보고 손을 쓸 수 있게 한다. 셋이 같은 문장으로 뭉개지면 이 구분은 없는 것이다.
_e_nokey = _err(lambda: FakeAdapter(League.EPL, {"errorCode": 403}).fetch("x", "y"))
_e_empty = _err(lambda: FakeAdapter(League.EPL, {"matches": []}).fetch("x", "y"))
_e_allskip = _err(lambda: FakeAdapter(
    League.EPL, {"matches": [_match(70, "GOLDEN_GOAL")]}).fetch("x", "y"))
check("① 응답 구조/권한 문제임을 밝힌다", "권한" in _e_nokey and "matches" in _e_nokey,
      _e_nokey[:70])
check("② 소스가 0건을 준 경우임을 밝힌다 (기간·권한·대회 코드를 짚어 준다)",
      "0건" in _e_empty and "기간" in _e_empty, _e_empty[:70])
check("③ 소스는 줬는데 우리가 다 못 읽은 경우는 따로 밝힌다",
      "파싱 후 0건" in _e_allskip and "건너뜀" in _e_allskip, _e_allskip[:70])
check("0건 세 갈래가 서로 다른 사유로 올라온다",
      len({_e_nokey, _e_empty, _e_allskip}) == 3)

# 게이트는 **0건일 때만** 걸린다 — 경기가 있는 날을 막으면 그게 더 큰 사고다.
check("경기가 1건이라도 있으면 막지 않는다",
      len(FakeAdapter(League.EPL, {"matches": [_match(71, "TIMED")]}).fetch("x", "y")) == 1)
# 소스가 "경기는 있는데 전부 연기/취소"라고 말한 날은 **0건이 아니다.**
# 그날은 그대로 실어야 하고, 소스가 아무 말도 안 한 날은 막아야 한다 — 이 둘을
# 같은 취급으로 뭉개면 '전 경기 취소'를 영원히 못 내보낸다.
_cx = FakeAdapter(League.EPL, {"matches": [
    _match(72, "POSTPONED", utc="2026-08-22T14:00:00Z"),
    _match(73, "CANCELLED", utc="2026-08-22T16:30:00Z",
           home=("Liverpool FC", "LIV"), away=("Everton FC", "EVE"))]}).fetch("x", "y")
check("전부 연기·취소인 날은 0건이 아니라 그대로 실린다", len(_cx) == 2,
      str([g.status.value for g in _cx]))

# ── 8) 시즌·시간대 ────────────────────────────────────────────
print("\n8. 시즌·시간대 — 해를 걸치는 리그")
a = FakeAdapter(League.EPL, {"matches": [_match(8, "TIMED")]})
g = a.fetch("x", "y")[0]
check("시즌 표기 2026-27", g.season == "2026-27", g.season)
check("홈 시간대", g.home_tz == "Europe/London", g.home_tz)
a = FakeAdapter(League.LALIGA, {"matches": [_match(10, "TIMED")]})
check("라리가는 마드리드 시간대", a.fetch("x", "y")[0].home_tz == "Europe/Madrid")
# season 정보가 없을 때의 폴백
m = _match(11, "TIMED", utc="2026-03-14T20:00:00Z")
m.pop("season")
check("시즌 정보 없으면 시작월로 추정 (3월 → 2025-26)",
      FakeAdapter(League.EPL, {"matches": [m]}).fetch("x", "y")[0].season == "2025-26")

# ── 8-2. UCL 홈팀 시간대 (2026-09-02 신설) ────────────────────────────
# **[왜 생겼나]** 전에는 `_TZ[UCL] = "Europe/Zurich"` 고정이었다. UCL 홈경기는
# 리스본(UTC+0)부터 이스탄불(UTC+3)까지 걸쳐 있어서, 대회 단위로는 **어떤 값을
# 넣어도** 최대 3시간 틀린 "현지 HH:MM"이 카드에 찍힌다(MLB가 같은 결함으로
# 정규경기 46%의 현지 시각을 틀리게 찍고 있었다).
# v1.11i에서 **홈팀의 나라**로 정하고, 모르면 조용히 넘기지 않고 그 경기를 막는다.
# 여기서 지키는 것은 둘이다: ① 나라마다 실제로 다른 시각이 나오는가
# ② 모르면 **막는가** — 막지 않고 틀린 현지 시각을 내보내면 이 절은 실패해야 한다.
print("8-2. UCL 홈팀 시간대 — 대회 하나로 찍지 않는다")

from zoneinfo import ZoneInfo as _ZI                               # noqa: E402

_KICK = "2026-04-28T19:00:00Z"
_ucl = FakeAdapter(League.UCL, {"matches": [
    _match(80, "TIMED", home=BEN, away=("Arsenal FC", "ARS"), utc=_KICK),
    _match(81, "TIMED", home=GAL, away=("Chelsea FC", "CHE"), utc=_KICK),
]}, teams=UCL_TEAMS).fetch("x", "y")
_by = {g.source_key: g for g in _ucl}
check("리스본 홈경기는 포르투갈 시간대", _by["80"].home_tz == "Europe/Lisbon",
      _by["80"].home_tz)
check("이스탄불 홈경기는 튀르키예 시간대", _by["81"].home_tz == "Europe/Istanbul",
      _by["81"].home_tz)
check("대회 고정값(Europe/Zurich)으로 찍지 않는다",
      all(g.home_tz != "Europe/Zurich" for g in _ucl), str([g.home_tz for g in _ucl]))


def _local(g):
    return g.start_utc.astimezone(_ZI(g.home_tz)).strftime("%H:%M")


check("같은 킥오프여도 홈팀 나라가 다르면 현지 시각이 다르다 (옛 고정값이면 같았다)",
      _local(_by["80"]) != _local(_by["81"]),
      f'{_local(_by["80"])} vs {_local(_by["81"])}')
check("현지 시각 차이가 실제 시차와 같다 (리스본 20:00 · 이스탄불 22:00)",
      (_local(_by["80"]), _local(_by["81"])) == ("20:00", "22:00"),
      f'{_local(_by["80"])} / {_local(_by["81"])}')
_ask = FakeAdapter(League.UCL, {"matches": [
    _match(82, "TIMED", home=BEN, away=GAL, utc=_KICK)]}, teams=UCL_TEAMS)
_ask.fetch("x", "y")
check("나라가 경기 응답에 없으면 팀 목록을 받아 온다 (지어내지 않는다)",
      any("/teams" in p for p in _ask.asked), str(_ask.asked))

_inline = FakeAdapter(League.UCL, {"matches": [
    _match(83, "TIMED", home=("SL Benfica", "BEN", "Portugal"),
           away=("Galatasaray SK", "GAL"))]}, teams=UCL_TEAMS)
_g_inline = _inline.fetch("x", "y")[0]
check("경기 응답에 나라가 실려 오면 그대로 쓴다 (요청을 늘리지 않는다)",
      _g_inline.home_tz == "Europe/Lisbon"
      and not any("/teams" in p for p in _inline.asked), str(_inline.asked))

# **모르면 막는다.** 세 갈래 전부, 그 경기가 결과에 **없어야** 하고 사실이 보고돼야 한다.
# (결과에 남아 있으면 = 어딘가의 기본값으로 현지 시각을 지어냈다는 뜻 → 실패)
# 나라가 응답에 실려 있어 **항상 풀리는** 경기 하나. 나머지가 살아남는지 함께 본다.
_SAFE = _match(89, "TIMED", home=("Arsenal FC", "ARS", "England"),
               away=("Chelsea FC", "CHE"), utc=_KICK)
for _label, _ad in (
    ("팀 목록을 못 받는다",
     FakeAdapter(League.UCL, {"matches": [
         _match(84, "TIMED", home=BEN, away=GAL, utc=_KICK), _SAFE]}, teams=None)),
    ("팀 목록에 그 팀이 없다",
     FakeAdapter(League.UCL, {"matches": [
         _match(85, "TIMED", home=("FC Novo", "NOV"), away=GAL, utc=_KICK), _SAFE]},
         teams=UCL_TEAMS)),
    ("나라는 아는데 시간대 표에 없다",
     FakeAdapter(League.UCL, {"matches": [
         _match(86, "TIMED", home=("Team Atlantis", "ATL", "Atlantis"), away=GAL,
                utc=_KICK), _SAFE]}, teams=UCL_TEAMS)),
):
    _out = _ad.fetch("x", "y")
    check(f"홈팀 시간대를 모르면 그 경기를 내보내지 않는다 — {_label}",
          [g.source_key for g in _out] == ["89"], str([g.source_key for g in _out]))
    check(f"모른 사실을 보고한다 — {_label}",
          any("시간대" in n for n in _ad.notices), str(_ad.notices))

# 전부 모르면 조용한 0건이 아니라 **막힌다**
expect("홈팀 시간대를 전부 모르면 대회 수집이 막힌다",
       lambda: FakeAdapter(League.UCL, {"matches": [
           _match(87, "TIMED", home=BEN, away=GAL, utc=_KICK)]},
           teams=None).fetch("x", "y"))

# 국내 5개 대회는 대회가 곧 나라다 — 팀 목록을 받지 않는다(무료 등급 분당 10회).
_pl = FakeAdapter(League.EPL, {"matches": [_match(88, "TIMED")]}, teams=None)
check("국내 대회는 팀 목록 요청 없이 시간대를 정한다",
      _pl.fetch("x", "y")[0].home_tz == "Europe/London"
      and not any("/teams" in p for p in _pl.asked), str(_pl.asked))

# ── 9) 전 리그 카드 렌더 ──────────────────────────────────────
print("\n9. 카드 — 여섯 대회가 각자 색·아이콘으로 그려지나")
for lg in sorted(want, key=lambda x: x.value):
    # UCL만 홈팀의 나라를 소스에서 받는다 — 팀 목록 응답을 함께 준다
    # (국내 5개 대회는 대회가 곧 나라라서 팀 목록을 받지 않는다).
    a = FakeAdapter(lg, {"matches": [
        _match(100, "FINISHED", hg=2, ag=1, utc="2026-08-22T14:00:00Z"),
        _match(101, "FINISHED", hg=0, ag=0, utc="2026-08-22T16:30:00Z",
               home=("Liverpool FC", "LIV"), away=("Everton FC", "EVE"))]},
        teams=UCL_TEAMS if lg is League.UCL else None)
    gs = a.fetch("x", "y")
    day = gs[0].sports_day
    try:
        html = P.render_result(gs, day)
        w, h, b = P.render_png(html, pathlib.Path("dryrun") / f"_fd_{lg.value}.png")
        from contract import LEAGUE_COLORS
        ink = LEAGUE_COLORS[lg][0]
        check(f"{lg.value} 카드 {w}x{h} · 리그색 주입",
              w == 1080 and h <= 2000 and f"--lg:{ink}" in html)
    except Exception as e:                                    # noqa: BLE001
        check(f"{lg.value} 카드", False, f"{type(e).__name__}: {str(e)[:60]}")

# ── 10) 캡션 ──────────────────────────────────────────────────
print("\n10. 캡션 — 전체 내용이 텍스트로 붙나")
# 한 라운드처럼 **팀이 겹치지 않는** 10경기로 센다. 전부 같은 대진이면
# "다 들어갔나"를 셀 수가 없다(줄이 똑같아 구분이 안 된다).
_ROUND = [(("Arsenal FC", "ARS"), ("Chelsea FC", "CHE")),
          (("Liverpool FC", "LIV"), ("Everton FC", "EVE")),
          (("Manchester City FC", "MCI"), ("Manchester United FC", "MUN")),
          (("Tottenham Hotspur FC", "TOT"), ("West Ham United FC", "WHU")),
          (("Aston Villa FC", "AVL"), ("Newcastle United FC", "NEW")),
          (("Brighton", "BHA"), ("Brentford FC", "BRE")),
          (("Fulham FC", "FUL"), ("Crystal Palace FC", "CRY")),
          (("Wolverhampton", "WOL"), ("Nottingham Forest FC", "NFO")),
          (("AFC Bournemouth", "BOU"), ("Leeds United FC", "LEE")),
          (("Burnley FC", "BUR"), ("Sunderland AFC", "SUN"))]
a = FakeAdapter(League.EPL, {"matches": [
    _match(200 + i, "FINISHED", home=h, away=aw, hg=i % 4, ag=(i + 1) % 3,
           utc=f"2026-08-22T{12 + i % 8:02d}:00:00Z")
    for i, (h, aw) in enumerate(_ROUND)]})
gs = a.fetch("x", "y")
parts = P.caption_result(gs, gs[0].sports_day, as_parts=True)
check(f"캡션 {len(parts)}파트 · 접고펼치기", "<blockquote expandable>" in parts[0])

# **[기대 변경 2026-09-02] 세는 방법을 바꿨다.** 옛 검사는 `" : "`(공백 포함)
# 개수를 셌는데, v1.11i에서 점수 표기를 카드와 같은 `1:15` 한 가지로 통일하면서
# 그 구분자가 사라졌다. **원래 목적은 "경기가 캡션에서 빠지지 않는다"**이므로,
# 표기가 아니라 **각 경기의 두 팀 이름이 한 줄에 함께 있는가**로 센다 —
# 표기를 또 바꿔도 안 깨지고, "한 경기가 두 줄로 갈리거나 다른 경기와 섞이는"
# 경우까지 잡는다(옛 검사는 구분자만 맞으면 통과했다).
_lines = [_re.sub("<[^>]+>", "", ln).strip()
          for p in parts for ln in p.splitlines() if ln.strip()]
_hit = []
for g in gs:
    _n = sum(1 for ln in _lines
             if team_name(g.home) in ln and team_name(g.away) in ln)
    _hit.append((g.source_key, _n))
check("경기 수가 캡션에 다 들어감 (경기마다 정확히 한 줄)",
      all(n == 1 for _, n in _hit), str([x for x in _hit if x[1] != 1]))
check("캡션 줄 수가 경기 수와 맞는다 (군더더기 줄이 섞이지 않는다)",
      sum(n for _, n in _hit) == len(gs), f"{sum(n for _, n in _hit)}/{len(gs)}")

# ── 11) 묵은 예정 게이트 ──────────────────────────────────────
print("\n11. 묵은 '예정' 게이트가 유럽에도 걸리나")
from datetime import datetime, timezone
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
a = FakeAdapter(League.EPL, {"matches": [_match(300, "TIMED",
                                                utc="2026-08-22T14:00:00Z")]})
gs = a.fetch("x", "y")
check("22시간 지난 '예정'은 잡힌다", len(stale_unresolved(gs, now_utc=NOW)) == 1)
expect("게이트가 차단한다", lambda: assert_no_stale_scheduled(gs, now_utc=NOW))

# ── 12) 경기 하나가 대회 전체를 죽이지 않는가 (2026-09-02 신설) ────────
# **[왜 생겼나]** v1.11i 전에는 `[self._parse(m) for m in matches]`라,
# `_STATUS`에 없는 상태값 **하나**면 그 대회 수집이 통째로 중단됐다(MLB에는
# 이미 경기 단위 try/except가 있었다). 대회가 통째로 빠지는 것은 카드 한 장이
# 빠지는 것과 급이 다르다 — 그날 그 리그는 채널에서 사라진다.
# 지키는 것은 둘: ① 나머지가 살아남는가 ② **건너뛴 사실이 보고되는가**.
# 조용히 살아남으면 소스가 바뀐 것을 아무도 모른 채 매일 경기가 새어 나간다.
print("\n12. 경기 단위 격리 — 하나가 죽어도 나머지는 나가고, 버린 것은 보고된다")

_mixed = FakeAdapter(League.UCL, {"matches": [
    _match(120, "FINISHED", hg=2, ag=1, utc="2026-08-22T14:00:00Z",
           home=("Arsenal FC", "ARS", "England"), away=("Chelsea FC", "CHE")),
    _match(121, "GOLDEN_GOAL", utc="2026-08-22T15:00:00Z",          # 미등록 상태값
           home=("Liverpool FC", "LIV", "England"), away=("Everton FC", "EVE")),
    _match(122, "TIMED", utc="2026-08-22T16:00:00Z",                # 시간대를 모른다
           home=("Team Atlantis", "ATL", "Atlantis"), away=("Chelsea FC", "CHE")),
    {"id": 123, "utcDate": "2026-08-22T17:00:00Z", "status": "TIMED",  # 팀 코드 없음
     "season": {"startDate": "2026-08-08"},
     "homeTeam": {"id": 9, "name": "Unknown"}, "awayTeam": {"id": 8, "name": "Other"},
     "score": {"fullTime": {}}},
    _match(124, "FINISHED", hg=0, ag=3, utc="2026-08-22T18:00:00Z",
           home=("FC Bayern München", "FCB", "Germany"), away=("Arsenal FC", "ARS")),
]}, teams=UCL_TEAMS)
_got = _mixed.fetch("x", "y")
check("망가진 행이 셋 섞여도 멀쩡한 경기는 다 나온다",
      [g.source_key for g in _got] == ["120", "124"], str([g.source_key for g in _got]))
_rep = _mixed.skipped_report()
check("버린 것이 보고서에 남는다", bool(_rep), str(_rep))
check("버린 건수가 실제와 같다 (예시 개수로 축소되지 않는다)",
      sum(v for v in _rep.values() if isinstance(v, int)) == 3, str(_rep))
check("무엇 때문에 버렸는지 갈래별로 남는다 (상태값 / 시간대)",
      any("미등록" in k for k in _rep) and any("시간대" in k for k in _rep), str(_rep))
check("사람이 읽는 줄로도 나온다", len(_mixed.notices) >= 2, str(_mixed.notices))

# 수집을 다시 돌리면 건수가 **누적되지 않는다** — 누적되면 "3건 건너뜀"이
# 며칠 뒤 "300건"이 되고, 그때부터 알림은 소음이 된다.
_mixed.fetch("x", "y")
check("다시 수집해도 건수가 누적되지 않는다",
      sum(v for v in _mixed.skipped_report().values() if isinstance(v, int)) == 3,
      str(_mixed.skipped_report()))

# 한 갈래가 전부를 삼키면(전 행 건너뜀) 그때는 0건 게이트가 막는다 — 위 7절 ③.
expect("전 행을 건너뛰면 조용한 0건이 아니라 막힌다",
       lambda: FakeAdapter(League.EPL, {"matches": [
           _match(125, "GOLDEN_GOAL"), _match(126, "SILVER_GOAL")]}).fetch("x", "y"))

print(f"\n결과: {ok} PASS / {fail} FAIL")
print("\n키가 오면: export FOOTBALL_DATA_TOKEN=... 후 verify_leagues.py 실행 →")
print("SKIP 6건이 자동으로 실검증으로 바뀐다.")
sys.exit(1 if fail else 0)
