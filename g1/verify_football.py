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

def _match(mid, status, home=("Arsenal FC", "ARS"), away=("Chelsea FC", "CHE"),
           hg=None, ag=None, utc="2026-08-22T14:00:00Z", stage="REGULAR_SEASON",
           extra=None):
    m = {
        "id": mid, "utcDate": utc, "status": status, "stage": stage,
        "season": {"startDate": "2026-08-08", "endDate": "2027-05-23"},
        "homeTeam": {"name": home[0], "tla": home[1], "shortName": home[0]},
        "awayTeam": {"name": away[0], "tla": away[1], "shortName": away[0]},
        "score": {"winner": None, "duration": "REGULAR",
                  "fullTime": {"home": hg, "away": ag}},
        "venue": "Emirates Stadium",
    }
    if extra:
        m["score"].update(extra)
    return m


class FakeAdapter(FootballDataAdapter):
    """네트워크 대신 준비된 응답을 돌려준다. 파싱만 시험한다."""

    def __init__(self, league, payload):
        # load_token()을 건너뛰기 위해 부모 __init__을 우회한다 (키 없이 검증하는 게 목적)
        if league not in LEAGUE_TO_CODE:
            raise GateError(f"지원하지 않는 리그 {league.value}")
        self.league = league
        self.code = LEAGUE_TO_CODE[league]
        self._token = "TEST"
        self._payload = payload

    def _get(self, path):
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
print("\n5. 승부차기 — 무승부처럼 보이지만 승자가 있다")
a = FakeAdapter(League.UCL, {"matches": [
    _match(5, "FINISHED", hg=1, ag=1, stage="SEMI_FINALS",
           extra={"duration": "PENALTY_SHOOTOUT",
                  "penalties": {"home": 4, "away": 3}})]})
g = a.fetch("x", "y")[0]
check("승부차기 점수를 보관", g.meta.penalties is not None
      and (g.meta.penalties.home, g.meta.penalties.away) == (4, 3))
check("단계(stage)를 보관", g.meta.season_category == "SEMI_FINALS")

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
print("\n7. 응답 구조 — 권한 문제를 '경기 없음'으로 읽지 않는다")
expect("matches 키가 없으면 막힌다 (권한·구조 문제)",
       lambda: FakeAdapter(League.EPL, {"errorCode": 403}).fetch("x", "y"))
a = FakeAdapter(League.EPL, {"matches": []})
check("빈 목록은 그대로 0건 (그날 경기가 없을 수 있다)", a.fetch("x", "y") == [])

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

# ── 9) 전 리그 카드 렌더 ──────────────────────────────────────
print("\n9. 카드 — 여섯 대회가 각자 색·아이콘으로 그려지나")
for lg in sorted(want, key=lambda x: x.value):
    a = FakeAdapter(lg, {"matches": [
        _match(100, "FINISHED", hg=2, ag=1, utc="2026-08-22T14:00:00Z"),
        _match(101, "FINISHED", hg=0, ag=0, utc="2026-08-22T16:30:00Z",
               home=("Liverpool FC", "LIV"), away=("Everton FC", "EVE"))]})
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
a = FakeAdapter(League.EPL, {"matches": [
    _match(200 + i, "FINISHED", hg=i % 4, ag=(i + 1) % 3,
           utc=f"2026-08-22T{12 + i % 8:02d}:00:00Z")
    for i in range(10)]})
gs = a.fetch("x", "y")
parts = P.caption_result(gs, gs[0].sports_day, as_parts=True)
check(f"캡션 {len(parts)}파트 · 접고펼치기", "<blockquote expandable>" in parts[0])
check("경기 수가 캡션에 다 들어감",
      sum(p.count(" : ") for p in parts) == len(gs),
      f"{sum(p.count(' : ') for p in parts)}/{len(gs)}")

# ── 11) 묵은 예정 게이트 ──────────────────────────────────────
print("\n11. 묵은 '예정' 게이트가 유럽에도 걸리나")
from datetime import datetime, timezone
NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
a = FakeAdapter(League.EPL, {"matches": [_match(300, "TIMED",
                                                utc="2026-08-22T14:00:00Z")]})
gs = a.fetch("x", "y")
check("22시간 지난 '예정'은 잡힌다", len(stale_unresolved(gs, now_utc=NOW)) == 1)
expect("게이트가 차단한다", lambda: assert_no_stale_scheduled(gs, now_utc=NOW))

print(f"\n결과: {ok} PASS / {fail} FAIL")
print("\n키가 오면: export FOOTBALL_DATA_TOKEN=... 후 verify_leagues.py 실행 →")
print("SKIP 6건이 자동으로 실검증으로 바뀐다.")
sys.exit(1 if fail else 0)
