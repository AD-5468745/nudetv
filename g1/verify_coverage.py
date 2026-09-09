"""커버리지 감시 검증 — 특히 '정기 휴식일' 판정 (v1.11e 신설).

이 검증이 지키려는 두 가지는 서로 반대 방향이다:
  1) 월요일처럼 **평소 쉬는 요일**의 0경기로 빨간불을 켜지 않는다 (오탐 제거)
  2) 그렇다고 **진짜 소스 고장**을 쉬는 날로 착각해 넘기지 않는다 (미탐 금지)
둘 중 하나만 통과하는 수정은 실패다. 그래서 양쪽을 같은 파일에서 함께 친다.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import coverage as CV  # noqa: E402
from contract import KST  # noqa: E402

FAIL: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name}{(' — ' + detail) if detail else ''}")


class G:
    """검증용 최소 경기 — 커버리지는 sports_day와 status만 본다."""

    def __init__(self, day: str):
        self.sports_day = day
        self.status = None
        self.start_utc = datetime.fromisoformat(day + "T04:00:00+00:00")


def build(days: dict[str, int]) -> list[G]:
    out: list[G] = []
    for day, n in days.items():
        out += [G(day) for _ in range(n)]
    return out


def season(start: str, weeks: int, per_day: dict[int, int]) -> dict[str, int]:
    """start부터 weeks주간, 요일별 경기 수로 일정을 만든다. 0인 요일은 아예 없다."""
    from datetime import date
    d0 = date.fromisoformat(start)
    out: dict[str, int] = {}
    for i in range(weeks * 7):
        d = d0 + timedelta(days=i)
        n = per_day.get(d.weekday(), 0)
        if n:
            out[d.isoformat()] = n
    return out


# 2026-08-31은 월요일이다. 이 날짜가 흔들리면 검증 전체가 무의미해진다.
from datetime import date as _date  # noqa: E402

check("기준일이 월요일", _date(2026, 8, 31).weekday() == 0)
NOW = datetime(2026, 8, 31, 6, 59, tzinfo=KST).astimezone(timezone.utc)

# ── 1. 오탐 제거: 월요일 휴식 리그 ─────────────────────────────────────────
# KBO 모양 — 화~일 5경기, 월요일 없음. 8주치.
kbo = season("2026-07-06", 8, {1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 5})
f = CV.check_league("KBO", build(kbo), NOW)
check("월요일 휴식 리그도 '사라짐'은 남긴다", any(x.kind == "오늘 편성이 사라짐" for x in f),
      f"찾은 것: {[x.kind for x in f]}")
rest = [x for x in f if x.kind == "오늘 편성이 사라짐"]
check("월요일 휴식은 빨간불이 아니다", rest and rest[0].soft)
check("왜 조용한지가 문구에 보인다", rest and "월요일은 평소 쉬는 날" in rest[0].detail,
      rest[0].detail if rest else "")
check("참고 사유가 '정기 휴식일'", rest and "정기 휴식일" in str(rest[0]), str(rest[0]) if rest else "")

# ── 2. 미탐 금지: 평소 경기하는 요일에 0이면 빨간불 ────────────────────────
# MLB 모양 — 월요일에도 경기가 있다. 그런데 오늘 0이면 소스 고장이다.
mlb = season("2026-07-06", 8, {0: 12, 1: 15, 2: 15, 3: 7, 4: 15, 5: 17, 6: 14})
mlb.pop("2026-08-31", None)          # 오늘만 사라졌다 = 고장
f = CV.check_league("MLB", build(mlb), NOW)
hard = [x for x in f if x.kind == "오늘 편성이 사라짐" and not x.soft]
check("월요일에도 경기하는 리그의 0경기는 빨간불", bool(hard),
      f"soft로 내려갔다: {[str(x) for x in f]}")

# 같은 리그가 화요일에 사라져도 빨간불이어야 한다.
TUE = datetime(2026, 9, 1, 6, 59, tzinfo=KST).astimezone(timezone.utc)
check("기준일이 화요일", _date(2026, 9, 1).weekday() == 1)
kbo_tue = dict(kbo)
kbo_tue.pop("2026-09-01", None)
kbo_tue["2026-08-31"] = 0            # 월요일은 원래 없음
f = CV.check_league("KBO", build({k: v for k, v in kbo_tue.items() if v}), TUE)
hard = [x for x in f if x.kind == "오늘 편성이 사라짐" and not x.soft]
check("휴식일 리그라도 평일 결번은 빨간불", bool(hard),
      f"soft로 내려갔다: {[str(x) for x in f]}")

# 요일마다 경기 수가 다른 리그(MLB 목요일)는 '어제'가 아니라 '평소 그 요일'과 견준다.
THU = datetime(2026, 9, 3, 6, 59, tzinfo=KST).astimezone(timezone.utc)
check("기준일이 목요일", _date(2026, 9, 3).weekday() == 3)
mlb2 = season("2026-07-06", 9, {0: 12, 1: 15, 2: 15, 3: 7, 4: 15, 5: 17, 6: 14})
f = CV.check_league("MLB", build(mlb2), THU)
check("평소 적은 요일을 급감으로 오해하지 않는다",
      not [x for x in f if x.kind == "편성이 급감"], str([str(x) for x in f]))
mlb2["2026-09-03"] = 2               # 목요일 평소 7경기인데 2경기 = 진짜 급감
f = CV.check_league("MLB", build(mlb2), THU)
check("같은 요일 기준으로 급감을 잡는다",
      any(x.kind == "편성이 급감" and not x.soft for x in f), str([str(x) for x in f]))

# ── 3. 표본이 모자라면 판정하지 않는다(안전한 쪽) ──────────────────────────
# 2주치면 지나간 월요일이 2번뿐 — WEEKDAY_MIN_SAMPLE(3) 미만이다.
short = season("2026-08-17", 2, {1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 5})
check("표본 부족이면 쉬는 날로 보지 않는다", CV.rest_weekday(short, "2026-08-31") == "",
      CV.rest_weekday(short, "2026-08-31"))

# ── 4. 쉬는 요일이 아니게 되면 판정도 따라 바뀐다 ──────────────────────────
# 월요일에도 다른 요일의 절반씩 경기가 있으면 더는 '쉬는 날'이 아니다.
half = season("2026-07-06", 8, {0: 3, 1: 5, 2: 5, 3: 5, 4: 5, 5: 5, 6: 5})
check("월요일에 절반씩 하면 쉬는 날이 아니다", CV.rest_weekday(half, "2026-08-31") == "",
      CV.rest_weekday(half, "2026-08-31"))

# 반대로 아주 드물게(다른 요일의 20% 미만) 있으면 여전히 쉬는 날이다 — NPB 모양.
npb = season("2026-07-06", 8, {1: 6, 2: 6, 3: 5, 4: 6, 5: 6, 6: 6})
npb["2026-07-13"] = 4                # 8주 중 월요일 한 번만 경기
check("아주 드문 월요일 경기는 여전히 쉬는 날", CV.rest_weekday(npb, "2026-08-31") != "")

# ── 5. 쉬는 날 판정이 다른 이상까지 덮지 않는다 ────────────────────────────
# 지난 경기의 결과가 안 들어온 것은 요일과 무관하게 빨간불이어야 한다.
stale_games = build(kbo)
for g in stale_games:
    if g.sports_day == "2026-08-29":
        g.status = None              # 종결되지 않은 채로 남았다
f = CV.check_league("KBO", stale_games, NOW)
kinds = {x.kind: x.soft for x in f}
check("쉬는 날이어도 '결과 안 들어옴'은 따로 잡는다",
      "결과가 안 들어온 지난 경기" not in kinds or kinds["결과가 안 들어온 지난 경기"] is False,
      str(kinds))

# ── 6. 빈 입력·깨진 날짜에도 죽지 않는다 ───────────────────────────────────
check("빈 입력", CV.rest_weekday({}, "2026-08-31") == "")
check("오늘 이후 날짜만 있으면 판정 안 함", CV.rest_weekday({"2026-09-05": 3}, "2026-08-31") == "")
check("깨진 날짜 문자열", CV.rest_weekday({"어제": 3}, "2026-08-31") == "")
check("오늘이 수집 첫날", CV.rest_weekday({"2026-08-31": 3}, "2026-08-31") == "")

# ── 7. 비시즌 참고 문구는 그대로다(기존 동작 보존) ─────────────────────────
old = CV.Finding("KBL", "수집이 멈춤", "마지막 성공 9.0시간 전", soft=True)
check("비시즌 참고 문구 그대로", "(비시즌 — 참고)" in str(old), str(old))
check("빨간불 항목엔 괄호가 안 붙는다",
      "참고" not in str(CV.Finding("KBO", "오늘 편성이 사라짐", "어제 5경기 → 오늘 0경기")))

# ── 8. Report.ok가 soft를 빨간불로 세지 않는다 ─────────────────────────────
rep = CV.Report()
rep.findings = [CV.Finding("KBO", "오늘 편성이 사라짐", "…", soft=True, soft_why="정기 휴식일")]
check("참고만 있으면 시계는 초록불", rep.ok)
rep.findings.append(CV.Finding("MLB", "오늘 편성이 사라짐", "…"))
check("빨간불 하나면 시계도 빨간불", not rep.ok)


# ══════════════════════════════════════════════════════════════
# 9. 알림 소음 정리 (v1.23) — 낮추되 지우지 않는다
# ══════════════════════════════════════════════════════════════
#
# 2026-09-09 실측: 매 틱 알림 7~10줄 중 대부분이 **발행하지 않는 리그**와
# **유럽 리그 라운드 공백**이었다. soft로 표시해 두고도 시계가 `lines()`
# (soft 포함 전부)를 알림에 실어서, **표시가 아무 일도 하지 않았다.**

class _G:
    """**가짜를 계약에 맞춘다** — 좁으면 멀쩡한 코드를 틀렸다고 잡는다(약점 144·172).

    `check_league`는 `stale_unresolved()`도 부르는데 그건 `status`·`start_utc`를 본다.
    """

    def __init__(self, day, lg=None, *, done=True):
        from contract import League, Status
        self.sports_day = day
        self.league = lg or League.LALIGA
        # **지난 경기는 끝난 것으로 둔다.** 전부 '예정'으로 두면 이 수정과
        # 무관한 "결과가 안 들어온 지난 경기"가 같이 잡혀 검사가 흐려진다.
        self.status = Status.FINAL if done else Status.SCHEDULED
        self.start_utc = datetime.fromisoformat(day + "T09:30:00+00:00")


def _mk(days, lg=None, today="2026-09-09"):
    return [_G(d, lg, done=d < today)
            for d, n in days.items() for _ in range(n)]


_NOW = datetime(2026, 9, 9, 11, 0, tzinfo=timezone.utc)      # KST 20:00
_TODAY = "2026-09-09"

# ── 9-1. 알림 줄은 hard만 · soft는 개수로 ────────────────────────────────
_r = CV.Report()
_r.findings = [
    CV.Finding("LCK", "수집이 멈춤", "…", soft=True, soft_why="발행 제외 리그"),
    CV.Finding("LALIGA", "오늘 편성이 사라짐", "…", soft=True, soft_why="라운드 공백"),
    CV.Finding("KBO", "오늘 편성이 사라짐", "어제 5경기 → 오늘 0경기"),
]
_al = _r.alert_lines(4)
check("★★ 알림에 hard가 실린다", any("KBO" in x for x in _al), str(_al))
check("★★ soft 본문은 알림에 안 실린다 (소음)",
      not any("LCK" in x or "LALIGA" in x for x in _al), str(_al))
check("★★ 대신 몇 건이 참고로 남았는지 밝힌다 (조용해진 것과 아무 일 없는 것은 다르다)",
      any("참고 2건" in x for x in _al), str(_al))
check("  ↳ 참고 사유도 함께 적는다",
      any("라운드 공백" in x and "발행 제외 리그" in x for x in _al), str(_al))
check("  ↳ health.json에는 그대로 남는다 (lines()는 안 바뀐다)",
      len(_r.lines()) == 3)
check("★ 참고가 하나도 없으면 그 줄을 붙이지 않는다",
      not any("참고" in x for x in
              CV.Report(findings=[CV.Finding("KBO", "x", "y")]).alert_lines()))

# ── 9-2. 발행 제외 리그는 참고로 낮춘다 ──────────────────────────────────
_fl = {"LCK": {"at": None, "error": "ratelimited"},
       "KBO": {"at": None, "error": "구조 변경"}}
_fa = {f.league: f for f in CV.check_snapshot_age(_fl, _NOW)}
check("★★ 발행 제외 리그(LCK)의 수집 실패는 참고로 내린다",
      _fa["LCK"].soft and _fa["LCK"].soft_why == "발행 제외 리그", str(_fa["LCK"]))
check("  ↳ 발행하는 리그(KBO)는 그대로 빨간불이다 (무디게 한 것이 아니다)",
      not _fa["KBO"].soft, str(_fa["KBO"]))
check("  ↳ 목록을 여기 다시 적지 않고 계약에서 읽는다 (두 곳이면 어긋난다)",
      CV._disabled_names() == {lg.value for lg in __import__(
          "contract").DISABLED_LEAGUES})

# ── 9-3. 라운드 공백 — 앞으로 편성이 있으면 참고로 내린다 ─────────────────
#
# 실측 2026-09-09: 라리가 어제 2경기 → 오늘 0경기인데 **앞으로 22경기**가
# 있었다. 소스는 멀쩡한데 하루 181틱씩 빨간불을 켰다.
_past = {"2026-09-02": 2, "2026-09-05": 2, "2026-09-06": 3, "2026-09-08": 2}
_ahead = dict(_past, **{"2026-09-12": 4, "2026-09-13": 6, "2026-09-19": 5})
_miss = lambda fs: [f for f in fs if f.kind == "오늘 편성이 사라짐"]  # noqa: E731
_f1 = _miss(CV.check_league("LALIGA", _mk(_ahead), _NOW))
check("★★★ 앞으로 편성이 있으면 '오늘 0경기'는 참고로 내린다 (라운드 공백)",
      len(_f1) == 1 and _f1[0].soft and _f1[0].soft_why == "라운드 공백",
      str(_f1))
check("  ↳ 앞으로 몇 경기인지 본문에 적는다 (판단 근거를 숨기지 않는다)",
      "앞으로 15경기" in _f1[0].detail, _f1[0].detail)

# ★★★ 여기가 이 수정의 합격 기준이다 — **진짜 고장은 그대로 잡혀야 한다.**
_f2 = _miss(CV.check_league("LALIGA", _mk(_past), _NOW))
check("★★★ 앞으로도 비어 있으면 여전히 빨간불이다 (소스가 죽은 경우)",
      len(_f2) == 1 and not _f2[0].soft, str(_f2))
check("  ↳ (변이) 미래 판정을 빼면 두 경우가 구별되지 않는다",
      _f1 and _f2 and _f1[0].soft != _f2[0].soft,
      "라운드 공백과 진짜 고장이 같은 무게가 되면 이 수정은 값어치가 없다")
_few = dict(_past, **{"2026-09-12": CV.FUTURE_OK_MIN - 1})
_f3 = _miss(CV.check_league("LALIGA", _mk(_few), _NOW))
check("  ↳ 앞으로 한두 경기뿐이면 낮추지 않는다 (연기분만 남은 상태일 수 있다)",
      len(_f3) == 1 and not _f3[0].soft, str(_f3))

# ── 9-4. 정기 휴식일 판정이 먼저다 (기존 동작 보존) ──────────────────────
# 월요일을 쉬는 리그를 3주치로 만든다 — 요일 판정에 표본이 3번은 있어야 한다
_mon = {}
_d = datetime(2026, 8, 11).date()   # 월요일 표본이 3번은 지나가야 판정된다
while _d < datetime(2026, 9, 7).date():
    if _d.weekday() != 0:                       # 월요일은 경기가 없다
        _mon[_d.isoformat()] = 5
    _d += timedelta(days=1)
_f4 = CV.check_league("KBO",
                      _mk(dict(_mon, **{"2026-09-11": 5}),
                          __import__("contract").League.KBO,
                          today="2026-09-07"),
                      datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc))
check("★ 정기 휴식일(월요일)은 라운드 공백보다 먼저 판정한다 (사유가 더 정확하다)",
      all(f.soft_why == "정기 휴식일" for f in _miss(_f4) if f.soft)
      and _miss(_f4), str(_miss(_f4)))


if __name__ == "__main__":
    print(f"커버리지 검증 — 통과 {PASS} · 실패 {len(FAIL)}")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
