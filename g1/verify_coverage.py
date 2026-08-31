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


if __name__ == "__main__":
    print(f"커버리지 검증 — 통과 {PASS} · 실패 {len(FAIL)}")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
