"""점수 외부 대조 게이트 검증 (v1.11j).

`adapters/scoreref.py`가 **막아야 할 것을 막고, 막으면 안 되는 것을 안 막는지** 본다.

이 검증에서 가장 중요한 것은 두 번째다. 안전장치는 통과만 하면 통과하는 것처럼 보여서,
"항상 PASS"가 곧 "아무것도 안 하고 있음"일 수 있다. 그래서
**일부러 틀린 값을 넣어 잡히는지**를 먼저 확인하고, 그 다음에 실측을 붙인다.

  A. 규칙 검증 (네트워크 없음 — 가짜 소스를 주입해 전 분기를 때린다)
  B. 실측 (진짜 소스에 붙어 어제·오늘 일치율을 낸다)
"""
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "adapters"))

from contract import (Game, GameMeta, League, Score, ScoreUnit, Status, TeamRef)
from adapters.scoreref import (
    KBO_TEAMS, KL1_TEAMS, LCK_TEAMS, MLB_TEAMS, NPB_TEAMS, PROVIDERS,
    MismatchKind, NEUTRAL_VENUE_LEAGUES, RefGame, RefStatus, RefUnavailable,
    ScoreReference, Severity, check_results, _espn_status, _naver_status)

ok = fail = skip = 0


def check(n, c, d=""):
    global ok, fail
    if c:
        ok += 1; print(f"  PASS  {n}")
    else:
        fail += 1; print(f"  FAIL  {n}  {d}")


def note_skip(n, why):
    global skip
    skip += 1
    print(f"  SKIP  {n} — {why}")


TMP = pathlib.Path(tempfile.mkdtemp(prefix="scoreref-"))
KST = ZoneInfo("Asia/Seoul")


# ── 표본 만들기 ──────────────────────────────────────────────────
def ours(league=League.KBO, home="KT", away="HH", day="2026-09-01", hh=18, mm=30,
         status=Status.FINAL, score=(0, 0), key=None, season="2026") -> Game:
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm, tzinfo=KST)
    sc = None
    if score is not None and status in (Status.FINAL, Status.LIVE):
        unit = {League.KBO: ScoreUnit.RUNS, League.MLB: ScoreUnit.RUNS,
                League.NPB: ScoreUnit.RUNS, League.KL1: ScoreUnit.GOALS,
                League.KBL: ScoreUnit.POINTS}[league]
        sc = Score(score[0], score[1], unit)
    return Game(league=league, season=season,
                source_key=key or f"{day.replace('-', '')}{away}{home}0",
                home=TeamRef(league, home), away=TeamRef(league, away),
                start_utc=st.astimezone(timezone.utc), home_tz="Asia/Seoul",
                status=status, score=sc, sports_day_fixed=day, meta=GameMeta())


def theirs(home="KT", away="HH", day="2026-09-01", score=(0, 0),
           status=RefStatus.FINAL, raw="RESULT", hh=18, mm=30,
           league=League.KBO, mapped=True) -> RefGame:
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm, tzinfo=KST)
    return RefGame(league=league, sports_day=day,
                   home_code=home if mapped else None,
                   away_code=away if mapped else None,
                   home_score=None if score is None else score[0],
                   away_score=None if score is None else score[1],
                   status=status, raw_status=raw,
                   raw_home=f"raw:{home}", raw_away=f"raw:{away}",
                   start_utc=st.astimezone(timezone.utc), source="가짜")


_seq = {"n": 0}


def ref_with(rows, *, boom=None, cache_dir=None):
    """가짜 소스를 물린 ScoreReference. `rows`는 리스트이거나 예외를 던지는 함수.

    캐시 폴더는 **매번 새로 판다.** 전에는 `id(rows)`로 이름을 지었는데,
    CPython이 해제된 주소를 재사용해서 서로 다른 테스트가 같은 폴더를 물고
    앞 테스트의 응답을 캐시로 읽었다 — 통과하던 검사가 조용히 틀린 값을 보게 된다.
    """
    _seq["n"] += 1
    calls = {"n": 0}

    def provider(day, **kw):
        calls["n"] += 1
        if boom is not None:
            raise boom
        return rows(day) if callable(rows) else list(rows)

    r = ScoreReference(cache_dir=cache_dir or (TMP / f"c{_seq['n']:04d}"),
                       providers={League.KBO: provider, League.MLB: provider,
                                  League.NPB: provider, League.KL1: provider},
                       sleep=lambda s: None)
    return r, calls


print("=" * 62)
print("점수 외부 대조 게이트 검증")
print("=" * 62)

# ─────────────────────────────────────────────────────────────────
print("\n[A-1] 09-01 사고 재현 — 우리 '종료 0:0', 외부 '진행 중'")
# 2026-09-01 19:18에 실제로 나간 카드다. 5경기 전부 18:30 시작, 48분 뒤 '종료 0:0'.
accident_ours = [ours(home=h, away=a, score=(0, 0), status=Status.FINAL)
                 for h, a in (("KT", "HH"), ("NC", "HT"), ("OB", "LG"),
                              ("SS", "LT"), ("WO", "SK"))]
accident_theirs = [theirs(home=h, away=a, score=(0, 0), status=RefStatus.LIVE,
                          raw="STARTED")
                   for h, a in (("KT", "HH"), ("NC", "HT"), ("OB", "LG"),
                                ("SS", "LT"), ("WO", "SK"))]
r, _ = ref_with(accident_theirs)
v = r.check(accident_ours)
check("09-01 사고가 차단된다", v.blocked, f"blocked={v.blocked}")
check("사고 5건이 전부 심각으로 잡힌다", len(v.blocking) == 5, f"{len(v.blocking)}건")
check("종류가 '우리 종료/외부 미종료'다",
      all(m.kind is MismatchKind.WE_FINAL_THEY_NOT for m in v.blocking))
check("차단 사유가 사람이 읽을 수 있다",
      "KT" in v.block_reason and "종료" in v.block_reason, v.block_reason[:80])

# 외부가 '진행 중'이 아니라 '시작 전'이라고 해도 같은 칸으로 잡혀야 한다.
# (소스가 진행 중 코드를 뭐라고 부르든 새지 않게 하려고 한 칸에 묶었다)
r, _ = ref_with([theirs(status=RefStatus.SCHEDULED, raw="BEFORE")])
check("외부가 '시작 전'이어도 차단된다",
      r.check([ours(score=(3, 1))]).blocked)

# ─────────────────────────────────────────────────────────────────
print("\n[A-2] 일부러 틀린 점수를 넣으면 잡히는가")
r, _ = ref_with([theirs(score=(6, 1))])
v = r.check([ours(score=(6, 1))])
check("점수가 같으면 통과", not v.blocked and v.agreed == 1 and v.compared == 1)
for wrong in ((6, 2), (5, 1), (1, 6), (60, 1)):
    r, _ = ref_with([theirs(score=(6, 1))])
    v = r.check([ours(score=wrong)])
    check(f"틀린 점수 {wrong}가 잡힌다",
          v.blocked and v.mismatches[0].kind is MismatchKind.SCORE,
          f"blocked={v.blocked} {[m.kind.value for m in v.mismatches]}")
r, _ = ref_with([theirs(score=(6, 1)),
                 theirs(home="NC", away="HT", score=(7, 2))])
v = r.check([ours(score=(6, 1)), ours(home="NC", away="HT", score=(9, 9))])
check("여러 경기 중 1건만 틀려도 차단(임계 1건)",
      v.blocked and len(v.blocking) == 1 and v.score_compared == 2,
      f"차단 {len(v.blocking)}건 · 점수대조 {v.score_compared}건")

# 홈·원정이 뒤집히면 승패가 뒤집혀 나간다 — 점수 비교 전에 잡아야 한다
r, _ = ref_with([theirs(home="HH", away="KT", score=(1, 6))])
v = r.check([ours(home="KT", away="HH", score=(6, 1))])
check("홈·원정 뒤집힘이 잡힌다",
      v.blocked and v.mismatches[0].kind is MismatchKind.ORIENTATION,
      f"{[m.kind.value for m in v.mismatches]}")

# **중립 구장 리그(LCK)는 예외다.** 실측에서 최근 3주 13경기가 13경기 모두
# '뒤집힘'으로 차단됐다 — LCK에는 홈·원정이 없고 양쪽 소스의 표시 순서가
# 그냥 반대이기 때문이다. 그대로 뒀으면 이 게이트가 LCK를 100% 침묵시켰다.
check("LCK는 NEUTRAL_VENUE_LEAGUES에 들어 있다", League.LCK in NEUTRAL_VENUE_LEAGUES)
lck_ref = ScoreReference(
    cache_dir=TMP / "lckflip", sleep=lambda s: None,
    providers={League.LCK: lambda day, **kw: [
        RefGame(League.LCK, day, "KT", "T1", 1, 3, RefStatus.FINAL, "RESULT",
                "KT", "T1", None, "가짜")]})
lck_ours = Game(league=League.LCK, season="2026", source_key="LCKFLIP",
                home=TeamRef(League.LCK, "T1"), away=TeamRef(League.LCK, "KT"),
                start_utc=datetime(2026, 9, 1, 8, tzinfo=timezone.utc),
                home_tz="Asia/Seoul", status=Status.FINAL,
                score=Score(3, 1, ScoreUnit.MAPS),
                sports_day_fixed="2026-09-01", meta=GameMeta())
v = lck_ref.check([lck_ours])
check("중립 구장 리그는 표시 순서가 반대여도 차단하지 않는다",
      not v.blocked, f"{[m.line() for m in v.blocking][:1]}")
check("중립 구장 리그는 점수를 맞춰 읽어 일치로 센다",
      v.score_compared == 1 and v.score_agreed == 1,
      f"{v.score_agreed}/{v.score_compared}")
# 맞춰 읽되 **틀린 점수는 여전히 잡아야 한다** (예외가 구멍이 되면 안 된다)
lck_bad = ScoreReference(
    cache_dir=TMP / "lckflip2", sleep=lambda s: None,
    providers={League.LCK: lambda day, **kw: [
        RefGame(League.LCK, day, "KT", "T1", 2, 3, RefStatus.FINAL, "RESULT",
                "KT", "T1", None, "가짜")]})
v = lck_bad.check([lck_ours])
check("중립 구장 예외가 점수 검사까지 무력화하지는 않는다",
      v.blocked and v.mismatches[0].kind is MismatchKind.SCORE,
      f"{[m.kind.value for m in v.mismatches]}")

# ─────────────────────────────────────────────────────────────────
print("\n[A-3] 외부 소스가 죽으면 — **절대 발행을 막지 않는다**")
for name, boom in [("타임아웃", TimeoutError("timed out")),
                   ("연결 끊김", ConnectionResetError("reset by peer")),
                   ("소스 실패(RefUnavailable)", RefUnavailable("HTTP 503")),
                   ("파서가 터짐(KeyError)", KeyError("games")),
                   ("생각지도 못한 예외", RuntimeError("boom"))]:
    r, _ = ref_with([], boom=boom)
    v = r.check([ours(score=(6, 1))])
    check(f"{name} → 차단 안 함", not v.blocked, f"blocked={v.blocked}")
    check(f"{name} → '대조 불가'로 남는다",
          len(v.unverifiable) == 1 and not v.mismatches,
          f"불가 {len(v.unverifiable)} / 불일치 {len(v.mismatches)}")

r, _ = ref_with([])          # 0건
v = r.check([ours(score=(6, 1))])
check("외부 0건 → 차단 안 함", not v.blocked)
check("외부 0건 → '0건은 항상 의심'이 사유에 남는다",
      "0건" in v.unverifiable[0].reason, v.unverifiable[0].reason)

# 구조가 바뀌어 조용히 0건이 되는 길
def _struct_changed(day, **kw):
    raise RefUnavailable("응답에 result.games 없음 (키: ['code', 'msg'])")


r = ScoreReference(cache_dir=TMP / "struct", providers={League.KBO: _struct_changed},
                   sleep=lambda s: None)
v = r.check([ours(score=(6, 1))])
check("응답 구조 변경 → 차단 안 함 + 사유가 구체적",
      not v.blocked and "result.games" in v.unverifiable[0].reason,
      v.unverifiable[0].reason[:70])

check("대조 0건일 때 일치율은 0.0이 아니라 -1.0 (못 함 ≠ 전부 틀림)",
      v.agreement_rate == -1.0, str(v.agreement_rate))

# ─────────────────────────────────────────────────────────────────
print("\n[A-4] 팀 매핑이 안 되면 '어긋남'이 아니라 '대조 불가'")
r, _ = ref_with([theirs(home="KT", away="HH", score=(9, 9), mapped=False)])
v = r.check([ours(home="KT", away="HH", score=(6, 1))])
check("매핑 실패 경기는 차단하지 않는다", not v.blocked)
check("매핑 실패는 불일치 0건", not v.mismatches, f"{[m.kind.value for m in v.mismatches]}")
check("매핑 실패가 '대조 불가'로 남는다", len(v.unverifiable) == 1)
check("사유에 '억지 매칭 금지'가 보인다",
      "억지" in v.unverifiable[0].reason, v.unverifiable[0].reason)
check("매핑 못 한 건수가 운영 보고에 오른다",
      any("매핑 못 함" in k for k in r.skipped_report()),
      str(list(r.skipped_report())))

# 외부에 그 경기 자체가 없는 경우
r, _ = ref_with([theirs(home="NC", away="HT")])
v = r.check([ours(home="KT", away="HH", score=(6, 1))])
check("외부에 그 경기가 없으면 차단 안 함", not v.blocked and len(v.unverifiable) == 1)

# 외부가 취소로 표기 — 어긋남이 아니다
r, _ = ref_with([theirs(status=RefStatus.OTHER, raw="BEFORE/취소", score=None)])
v = r.check([ours(score=(6, 1))])
check("외부가 취소 표기 → 대조 불가", not v.blocked and len(v.unverifiable) == 1)

# 처음 보는 상태 코드 — 막지 않되 조용히 넘기지도 않는다
r, _ = ref_with([theirs(status=RefStatus.UNKNOWN, raw="PLAYING_OT")])
v = r.check([ours(score=(6, 1))])
check("처음 보는 외부 상태 → 차단 안 함", not v.blocked)
check("처음 보는 외부 상태가 운영 보고에 오른다",
      any("처음 보는" in k for k in r.skipped_report()), str(list(r.skipped_report())))

# 대조 소스가 아예 없는 리그
r = ScoreReference(cache_dir=TMP / "none", providers={}, sleep=lambda s: None)
v = r.check([ours(score=(6, 1))])
check("대조 소스 없는 리그 → 차단 안 함 + 대조 불가",
      not v.blocked and v.unverifiable[0].reason.startswith("이 리그는"))

# ─────────────────────────────────────────────────────────────────
print("\n[A-5] 우리가 늦은 경우는 알림이지 차단이 아니다")
r, _ = ref_with([theirs(status=RefStatus.FINAL, score=(5, 4))])
v = r.check([ours(status=Status.LIVE, score=None)])
check("우리 진행 중 / 외부 종료 → 차단 안 함", not v.blocked)
check("그래도 알림으로는 남는다",
      len(v.warnings) == 1 and v.warnings[0].kind is MismatchKind.WE_BEHIND)
check("알림의 심각도는 WARN", v.warnings[0].severity is Severity.WARN)

r, _ = ref_with([theirs(status=RefStatus.FINAL, score=(5, 4))])
v = r.check([ours(status=Status.SCHEDULED, score=None)])
check("우리 예정 / 외부 종료 → 알림만", not v.blocked and len(v.warnings) == 1)

# npb.jp는 매일 이렇게 늦는다. 5건이 한꺼번에 떠도 막으면 안 된다.
r, _ = ref_with([theirs(home=h, away=a, status=RefStatus.FINAL, score=(1, 0),
                        league=League.NPB)
                 for h, a in (("CHU", "HIR"), ("NIP", "SOF"), ("RAK", "ORI"),
                              ("YAK", "HAN"), ("YOG", "DEN"))])
v = r.check([ours(League.NPB, h, a, status=Status.LIVE, score=None)
             for h, a in (("CHU", "HIR"), ("NIP", "SOF"), ("RAK", "ORI"),
                          ("YAK", "HAN"), ("YOG", "DEN"))])
check("NPB처럼 5건이 한꺼번에 늦어도 차단 안 함",
      not v.blocked and len(v.warnings) == 5, f"warn {len(v.warnings)}")

# 진행 중 점수는 비교하지 않는다 (몇 초 차이로 늘 다르다)
r, _ = ref_with([theirs(status=RefStatus.LIVE, raw="STARTED", score=(4, 2))])
v = r.check([ours(status=Status.LIVE, score=(3, 2))])
check("진행 중끼리는 점수 차이를 불일치로 세지 않는다",
      not v.blocked and not v.mismatches and v.agreed == 1)

# ─────────────────────────────────────────────────────────────────
print("\n[A-6] 더블헤더 · 짝짓기")
dh_ours = [ours(home="KT", away="HH", hh=13, score=(1, 0), key="20260901HHKT1"),
           ours(home="KT", away="HH", hh=18, score=(2, 5), key="20260901HHKT2")]
dh_theirs = [theirs(hh=18, score=(2, 5)), theirs(hh=13, score=(1, 0))]
r, _ = ref_with(dh_theirs)
v = r.check(dh_ours)
check("더블헤더가 시작 시각 순으로 짝지어진다",
      not v.blocked and v.compared == 2 and v.agreed == 2,
      f"대조 {v.compared} 일치 {v.agreed} 차단 {len(v.blocking)}")
r, _ = ref_with([theirs(hh=13, score=(1, 0))])
v = r.check(dh_ours)
check("더블헤더 중 외부에 1경기만 있으면 나머지는 대조 불가",
      not v.blocked and v.compared == 1 and len(v.unverifiable) == 1)

# ─────────────────────────────────────────────────────────────────
print("\n[A-7] 요청 예산 — 캐시와 최소 간격")
cd = TMP / "cache1"
r, calls = ref_with([theirs(score=(6, 1))], cache_dir=cd)
r.check([ours(score=(6, 1))])
r.check([ours(score=(6, 1))])
r.check([ours(score=(6, 1))])
check("같은 (리그·날짜)는 캐시로 1회만 요청한다", calls["n"] == 1, f"{calls['n']}회")

# 묵은 캐시는 쓰지 않는다 — 30분 전 스냅샷으로 대조하면 정상 카드를 막는다
r2, calls2 = ref_with([theirs(score=(6, 1))], cache_dir=cd)
r2.cache_ttl = 0.0
r2.check([ours(score=(6, 1))])
check("TTL이 지난 캐시는 다시 받아온다(묵은 대조 금지)", calls2["n"] == 1, f"{calls2['n']}회")

# 예산(시간) 초과 — 대조 때문에 틱이 밀리면 그 자체가 사고다
r, calls = ref_with([theirs(score=(9, 9))], cache_dir=TMP / "budget")
v = r.check([ours(score=(6, 1))], deadline_seconds=-1.0)
check("예산 초과 → 요청하지 않고 대조 불가로 넘긴다",
      not v.blocked and calls["n"] == 0 and "예산" in v.unverifiable[0].reason,
      f"{calls['n']}회 / {v.unverifiable[0].reason if v.unverifiable else ''}")

# ─────────────────────────────────────────────────────────────────
print("\n[A-8] 최후의 그물 — 이 모듈의 버그가 발행을 막지 못하게")


class _Exploding(ScoreReference):
    def check(self, games, **kw):
        raise RuntimeError("대조기 자체가 터졌다")


v = check_results([ours(score=(6, 1))], ref=_Exploding(cache_dir=TMP / "boom"))
check("check_results는 무슨 일이 있어도 예외를 안 던진다", v is not None)
check("대조기가 터져도 blocked=False", not v.blocked)

# ─────────────────────────────────────────────────────────────────
print("\n[A-9] 소스 상태 파싱 — 실측에서 확인한 함정들")
# KBO 취소 69건은 전부 statusCode='BEFORE' + cancel=true 로 온다.
# cancel을 먼저 안 보면 취소 경기가 '아직 시작 안 함'으로 읽힌다.
st, _ = _naver_status({"statusCode": "BEFORE", "cancel": True})
check("네이버: BEFORE + cancel=true → 취소(대조 불가)", st is RefStatus.OTHER, st.value)
# NPB 취소는 statusCode='ENDED' + cancel=true 이고 점수가 실린 건도 있다(20260426JLSF0).
st, _ = _naver_status({"statusCode": "ENDED", "cancel": True,
                       "homeTeamScore": 0, "awayTeamScore": 1})
check("네이버: ENDED + cancel=true(점수 있음) → 취소", st is RefStatus.OTHER, st.value)
st, _ = _naver_status({"statusCode": "RESULT", "cancel": False})
check("네이버: RESULT → 종료", st is RefStatus.FINAL, st.value)
st, _ = _naver_status({"statusCode": "STARTED", "cancel": False})
check("네이버: STARTED → 진행 중", st is RefStatus.LIVE, st.value)
st, _ = _naver_status({"statusCode": "BEFORE", "cancel": False})
check("네이버: BEFORE(비취소) → 예정", st is RefStatus.SCHEDULED, st.value)
st, _ = _naver_status({"statusCode": "WHAT_IS_THIS", "cancel": False})
check("네이버: 처음 보는 코드 → UNKNOWN(추측 금지)", st is RefStatus.UNKNOWN, st.value)

# ESPN — 이름을 먼저 걸러야 한다. 연기 경기는 state='post'라서
# completed부터 보면 '진행 중'으로 읽힐 여지가 있다.
st, _ = _espn_status({"name": "STATUS_POSTPONED", "state": "post", "completed": False})
check("ESPN: 연기 → 대조 불가", st is RefStatus.OTHER, st.value)
st, _ = _espn_status({"name": "STATUS_SUSPENDED", "state": "post", "completed": False})
check("ESPN: 서스펜디드 → 대조 불가", st is RefStatus.OTHER, st.value)
st, _ = _espn_status({"name": "STATUS_FINAL", "state": "post", "completed": True})
check("ESPN: 종료 → 종료", st is RefStatus.FINAL, st.value)
st, _ = _espn_status({"name": "STATUS_IN_PROGRESS", "state": "in", "completed": False})
check("ESPN: 진행 중 → 진행 중", st is RefStatus.LIVE, st.value)
st, _ = _espn_status({"name": "STATUS_RAIN_DELAY", "state": "in", "completed": False})
check("ESPN: 우천 지연 → 진행 중(종료가 아니다)", st is RefStatus.LIVE, st.value)
st, _ = _espn_status({"name": "STATUS_SCHEDULED", "state": "pre", "completed": False})
check("ESPN: 예정 → 예정", st is RefStatus.SCHEDULED, st.value)
st, _ = _espn_status({"name": "", "state": "", "completed": None})
check("ESPN: 빈 상태 → UNKNOWN", st is RefStatus.UNKNOWN, st.value)

# ─────────────────────────────────────────────────────────────────
print("\n[A-10] NoticeMixin 계약 — 조용히 버린 것이 운영에 보이는가")
r, _ = ref_with([theirs(status=RefStatus.OTHER, raw="BEFORE/취소")])
r.check([ours(score=(6, 1))])
rep = r.skipped_report()
check("skipped_report()가 dict를 준다", isinstance(rep, dict))
check("대조 요약이 보고에 들어간다", any("대조 요약" in k for k in rep), str(list(rep)))
check("notices가 사람이 읽는 줄을 준다",
      isinstance(r.notices, list) and all(isinstance(x, str) for x in r.notices))
check("cache_age_seconds 속성이 있다(읽는 쪽 hasattr 분기 금지)",
      hasattr(r, "cache_age_seconds"))
r2, _ = ref_with([theirs(score=(6, 1))])
r2.check([ours(score=(6, 1))])
check("수집마다 reset_notices로 건수가 누적되지 않는다",
      not any("취소" in k for k in r2.skipped_report()), str(list(r2.skipped_report())))

# 팀 매핑 표 자체의 최소 건전성 (표가 비면 전부 '대조 불가'로 조용히 굳는다)
for name, tbl, n in (("KBO", KBO_TEAMS, 10), ("NPB", NPB_TEAMS, 12),
                     ("K리그1", KL1_TEAMS, 12), ("MLB", MLB_TEAMS, 30),
                     ("LCK", LCK_TEAMS, 10)):
    check(f"{name} 팀 매핑 {len(tbl)}개 ≥ {n}", len(tbl) >= n, f"{len(tbl)}개")
check("KBO 매핑에 올스타(EA·WE)가 섞이지 않았다",
      "EA" not in KBO_TEAMS and "WE" not in KBO_TEAMS)
check("NPB 매핑에 올스타(CL·PL)가 섞이지 않았다",
      "CL" not in NPB_TEAMS and "PL" not in NPB_TEAMS)


# ═════════════════════════════════════════════════════════════════
# B. 실측 — 진짜 소스에 붙는다
# ═════════════════════════════════════════════════════════════════
print("\n" + "=" * 62)
print("B. 실측 대조 (진짜 소스)")
print("=" * 62)

TODAY = datetime.now(KST).date()
# 7일을 본다. 4일이면 K리그1(주 1~2경기)이 통째로 SKIP으로 빠져
# "검증했다"고 말하면서 실제로는 한 번도 안 본 리그가 생긴다.
DAYS = [(TODAY - timedelta(days=d)).isoformat() for d in range(0, 7)]

live = ScoreReference()

print(f"\n[B-1] 소스가 살아 있고 응답 구조가 그대로인가 ({DAYS[1]}·{DAYS[0]})")
alive: dict[League, list] = {}
for lg in (League.KBO, League.MLB, League.NPB, League.KL1, League.LCK):
    rows = []
    errs = []
    for day in DAYS:
        try:
            rows += live.games_for(lg, day)
        except RefUnavailable as e:
            errs.append(f"{day}: {str(e)[:60]}")
        except Exception as e:                                    # noqa: BLE001
            errs.append(f"{day}: {type(e).__name__} {str(e)[:50]}")
    alive[lg] = rows
    if rows:
        unk = [r for r in rows if r.status is RefStatus.UNKNOWN]
        unmapped = [r for r in rows if not r.mapped]
        print(f"  [{lg.value}] {len(rows)}건 "
              f"({', '.join(sorted({r.status.value for r in rows}))})"
              + (f" · 매핑 실패 {len(unmapped)}건" if unmapped else "")
              + (f" · 비어 있던 날 {len(errs)}" if errs else ""))
        check(f"{lg.value} 대조 데이터 확보", True)
        check(f"{lg.value} 처음 보는 상태 코드 0건", not unk,
              f"{sorted({r.raw_status for r in unk})[:4]}")
        # 매핑 실패는 올스타·이벤트 대진만이어야 한다
        bad = [r for r in unmapped
               if not {r.raw_home, r.raw_away} & {
                   "EA(드림)", "WE(나눔)", "CL(센트럴리그)", "PL(퍼시픽리그)", "TBD"}]
        check(f"{lg.value} 매핑 실패는 올스타·미정 대진뿐", not bad,
              f"{[(r.raw_home, r.raw_away) for r in bad][:3]}")
    else:
        note_skip(f"{lg.value} 대조 데이터", f"{len(errs)}일 전부 실패 — "
                                            f"{errs[0] if errs else '사유 불명'}")

print("\n[B-2] 어제·오늘 실측 일치율")
try:
    from adapters.kbo import KboAdapter
    from adapters.mlb import MlbAdapter
    from adapters.npb import NpbAdapter
    from adapters.kleague import KLeagueAdapter
    import adapters.lck as _L
    # 검증은 빨라야 한다. 리밋이면 기다리지 말고 바로 LCK 캐시로 떨어진다
    # (캐시도 없으면 RateLimited → SKIP. '검증 못 함'과 '깨짐'을 가른다).
    _L._RATELIMIT_WAITS = ()
    from adapters.lck import LckAdapter, RateLimited
except Exception as e:                                            # noqa: BLE001
    print(f"  SKIP  수집 어댑터 import 실패 — {type(e).__name__} {e}")
    KboAdapter = None                                             # type: ignore

MONTHS = sorted({d[5:7] for d in DAYS})
YEAR = int(DAYS[0][:4])

JOBS = [
    ("KBO", League.KBO, lambda: KboAdapter().fetch(YEAR, MONTHS)),
    ("MLB", League.MLB, lambda: MlbAdapter().fetch(DAYS[-1], DAYS[0])),
    ("NPB", League.NPB, lambda: NpbAdapter().fetch(YEAR, MONTHS)),
    ("K리그1", League.KL1, lambda: KLeagueAdapter().fetch(YEAR, MONTHS)),
    # LCK는 시즌 시작일로 부른다 — verify_leagues.py와 같은 인자라서
    # 리밋에 걸려도 같은 캐시 키로 떨어진다(날짜를 매일 바꾸면 캐시가 절대 안 맞는다).
    ("LCK", League.LCK, lambda: LckAdapter(League.LCK).fetch(f"{YEAR}-01-01")),
]

total_cmp = total_agree = total_sc = total_sa = 0
if KboAdapter is not None:
    for tag, lg, fn in JOBS:
        try:
            games = [g for g in fn() if g.sports_day in DAYS]
        except RateLimited as e:                                  # noqa: F821
            note_skip(f"{tag} 실측", f"1차 소스 레이트리밋 — {str(e)[:50]}")
            continue
        except Exception as e:                                    # noqa: BLE001
            note_skip(f"{tag} 실측", f"1차 수집 실패 {type(e).__name__} {str(e)[:50]}")
            continue
        if not games:
            note_skip(f"{tag} 실측", f"최근 {len(DAYS)}일에 경기가 없다")
            continue
        v = live.check(games, deadline_seconds=180.0)
        total_cmp += v.compared
        total_agree += v.agreed
        total_sc += v.score_compared
        total_sa += v.score_agreed
        rate = f"{v.agreement_rate * 100:.1f}%" if v.compared else "대조 0건"
        srate = (f"{v.score_agreement_rate * 100:.1f}%" if v.score_compared
                 else "점수 대조 0건")
        print(f"  [{tag}] 우리 {len(games)}경기 · 대조 {v.compared} · 일치 {v.agreed} "
              f"({rate}) · 점수 대조 {v.score_compared}건 {srate} "
              f"· 차단 {len(v.blocking)} · 알림 {len(v.warnings)} "
              f"· 불가 {len(v.unverifiable)}")
        for m in v.blocking:
            print(f"        차단 → {m.line()}")
        for m in v.warnings[:3]:
            print(f"        알림 → {m.line()}")
        if v.unverifiable[:1]:
            print(f"        불가 → {v.unverifiable[0].reason}")
        # **여기서 FAIL이 나면 진짜로 무언가 어긋난 것이다.** 실측이므로
        # 소스가 잠깐 늦는 것만으로도 걸릴 수 있다 — 그때는 다시 돌려 본다.
        check(f"{tag} 실측 대조에서 차단 사유 없음", not v.blocked,
              v.block_reason[:110])
        check(f"{tag} 대조 성사 1건 이상", v.compared > 0)

    if total_cmp:
        print(f"\n  ── 실측 종합: {total_cmp}건 대조 · {total_agree}건 일치 "
              f"({total_agree / total_cmp * 100:.1f}%)"
              + (f" · 점수 대조 {total_sc}건 중 {total_sa}건 일치 "
                 f"({total_sa / total_sc * 100:.1f}%)" if total_sc else ""))
        # **품질 지표는 '점수 일치율'이다.** 전체 일치율은 npb.jp의 갱신 지연
        # (우리가 늦음 = 사실 오류가 아님)에 끌려가므로 합격선으로 쓰지 않는다.
        # 점수는 다르다 — 양쪽이 모두 '종료'라고 말한 경기의 점수가 다르면
        # 둘 중 하나는 틀린 것이고, 1건이라도 있으면 봐야 한다.
        check("점수 대조 100% 일치", total_sc > 0 and total_sa == total_sc,
              f"{total_sa}/{total_sc}")
        check("실측 전체 일치율 90% 이상(참고 지표)",
              total_agree / total_cmp >= 0.90, f"{total_agree}/{total_cmp}")
    else:
        note_skip("실측 종합 일치율", "대조 성사 0건")

print("\n[B-3] 실측 데이터에 09-01 사고를 심으면 진짜 소스로도 잡히는가")
# 가짜 소스가 아니라 **방금 받아온 진짜 응답**에 대고 우리 쪽 값만 오염시킨다.
seeded = False
for lg in (League.KBO, League.NPB, League.MLB, League.KL1):
    rows = [r for r in alive.get(lg, []) if r.mapped and r.status is RefStatus.FINAL]
    if not rows:
        continue
    r0 = rows[0]
    unit = {League.KBO: ScoreUnit.RUNS, League.MLB: ScoreUnit.RUNS,
            League.NPB: ScoreUnit.RUNS, League.KL1: ScoreUnit.GOALS}[lg]
    fake = Game(league=lg, season=str(YEAR), source_key="SEEDED",
                home=TeamRef(lg, r0.home_code), away=TeamRef(lg, r0.away_code),
                start_utc=r0.start_utc or datetime.now(timezone.utc),
                home_tz="Asia/Seoul", status=Status.FINAL,
                score=Score((r0.home_score or 0) + 7, (r0.away_score or 0) + 7, unit),
                sports_day_fixed=r0.sports_day, meta=GameMeta())
    v = live.check([fake])
    check(f"{lg.value} 진짜 응답에 틀린 점수를 심으면 차단된다",
          v.blocked and v.mismatches and v.mismatches[0].kind is MismatchKind.SCORE,
          f"blocked={v.blocked} {[m.kind.value for m in v.mismatches]}")
    seeded = True
if not seeded:
    note_skip("실측 오염 주입", "종료된 대조 데이터가 없다")

print("\n[B-4] 소스가 죽은 척 — 진짜 인스턴스가 발행을 막지 않는가")
dead = ScoreReference(cache_dir=TMP / "dead",
                      providers={lg: (lambda day, **kw: (_ for _ in ()).throw(
                          TimeoutError("연결 시간 초과"))) for lg in PROVIDERS},
                      sleep=lambda s: None)
sample = [ours(score=(6, 1))]
v = dead.check(sample)
check("전 리그 타임아웃에도 blocked=False", not v.blocked)
check("그 사실이 sources에 남는다",
      any("실패" in s for s in v.sources.values()), str(v.sources))

print(f"\n결과: {ok} PASS / {fail} FAIL / {skip} SKIP")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
