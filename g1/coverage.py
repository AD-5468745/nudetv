"""커버리지 감시 — 리그가 조용히 사라지는 것을 잡는다 (v1.11c 신설).

무인 운영에서 가장 위험한 실패는 **에러가 아니라 침묵**이다.
소스가 구조를 바꾸거나 권한이 막히면, 잘 만든 시스템일수록 조용히 0건을 반환하고
아무 일도 없었다는 듯 넘어간다. 며칠 뒤 "요즘 NPB가 안 올라오네"로 알게 된다.

이 프로젝트에서 실제로 그 계열의 사고가 세 번 있었다:
  · NPB 취소 33건을 0건으로 읽음        (구조가 문서와 달랐다)
  · Leaguepedia 레이트리밋을 0건으로 읽음 (에러가 HTTP 200으로 왔다)
  · Leaguepedia 'LCK' 0건               (그 이름이 위키에 없었다)
전부 "0건은 항상 의심한다"로 잡았지만, 그건 **사람이 볼 때** 이야기다.
무인 운영에는 자동으로 의심해줄 눈이 필요하다 — 그게 이 파일이다.

판정은 **어제와 비교**한다. 절대 기준("KBO는 하루 5경기")은 시즌·휴식일·올스타 브레이크에
전부 걸려 오탐만 만든다. 대신 "어제 있던 리그가 오늘 사라졌다"는 시즌과 무관하게 이상하다.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# 스냅샷이 이보다 오래되면 수집이 멈춘 것이다.
# 시계가 매시 도는데 6시간이면 여섯 번을 내리 실패했다는 뜻이다.
SNAPSHOT_MAX_AGE_SECONDS = 6 * 3600

# 경기 수가 어제의 이 비율 밑으로 떨어지면 의심한다.
# 0으로만 판정하면 "10경기 → 1경기"처럼 반쯤 깨진 경우를 놓친다.
DROP_RATIO = 0.4

# 이 일수 안에 한 번도 경기가 없으면 비시즌으로 본다(오탐 방지).
OFFSEASON_DAYS = 14

# --- 정기 휴식일 (v1.11e) ---------------------------------------------------
# KBO·K리그1·LCK는 월요일에 경기를 하지 않는다. NPB도 월요일은 거의 없다.
# "어제와 오늘"만 비교하는 판정에는 요일 개념이 없어서, 매주 월요일마다
# 리그 네 개가 동시에 "오늘 편성이 사라짐"으로 빨간불을 켰다. 하루 종일.
# 그 소음에 진짜 사고가 묻히는 것이 이 파일이 막으려던 바로 그 병이다.
#
# 요일표를 손으로 적지 않는다. 적어둔 표는 리그가 일정을 바꾸면 조용히 틀리고,
# 우리는 그걸 몇 주 뒤에나 안다. 대신 **수집된 실제 경기**에서 계산한다 —
# "이 리그가 이 요일에 지금까지 경기를 했는가"는 데이터가 알고 있다.
WEEKDAY_MIN_SAMPLE = 3      # 그 요일이 과거에 최소 이만큼 지나가야 판정한다
WEEKDAY_QUIET_RATIO = 0.2   # 다른 요일 평균의 이 비율 미만이면 '쉬는 요일'
_WEEKDAY_KO = "월화수목금토일"


@dataclass
class Finding:
    league: str
    kind: str
    detail: str
    # soft=True는 "알려는 주되 빨간불로는 올리지 않는다"는 뜻이다.
    # 비시즌 리그의 수집 실패가 여기 해당한다 — 어차피 내보낼 경기가 없으므로
    # 이걸로 시계를 실패 처리하면, 8월마다 농구·롤이 울어 진짜 사고가 그 소음에 묻힌다.
    soft: bool = False
    # 왜 참고로만 두는지. 기본값은 지금까지의 유일한 사유였던 비시즌이다.
    soft_why: str = "비시즌"

    def __str__(self) -> str:
        mark = f"({self.soft_why} — 참고) " if self.soft else ""
        return f"[{self.league}] {mark}{self.kind} — {self.detail}"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0

    @property
    def hard(self) -> list[Finding]:
        """사람이 손대야 하는 것만."""
        return [f for f in self.findings if not f.soft]

    @property
    def ok(self) -> bool:
        return not self.hard

    def lines(self) -> list[str]:
        return [str(f) for f in self.findings]


def _counts_by_day(games: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for g in games:
        out[g.sports_day] = out.get(g.sports_day, 0) + 1
    return out


def _weekday_stats(by_day: dict[str, int],
                   today: str) -> tuple[list[int], list[int], int] | None:
    """지나간 날들을 '오늘과 같은 요일'과 '나머지'로 갈라 경기 수를 센다.

    by_day에는 경기가 있는 날만 들어 있다. 그래서 키를 훑으면 안 된다 —
    쉬는 요일은 애초에 키가 없어서 표본 0으로 잡히고, 판정이 정반대로 뒤집힌다.
    수집 구간의 **첫날부터 어제까지 하루씩** 걸어가며 없는 날은 0으로 센다.
    """
    from datetime import date

    past = sorted(d for d in by_day if d < today)
    if not past:
        return None
    try:
        start = date.fromisoformat(past[0])
        end = date.fromisoformat(today) - timedelta(days=1)
        wd = date.fromisoformat(today).weekday()
    except ValueError:
        return None
    if end < start:
        return None

    same: list[int] = []
    other: list[int] = []
    day = start
    while day <= end:
        (same if day.weekday() == wd else other).append(
            by_day.get(day.isoformat(), 0))
        day += timedelta(days=1)
    return same, other, wd


def rest_weekday(by_day: dict[str, int], today: str) -> str:
    """오늘과 같은 요일에 이 리그가 평소 경기를 하지 않는가.

    쉬는 요일이면 근거 문구를, 아니면 빈 문자열을 돌려준다.
    표본이 모자라거나 애매하면 **빈 문자열**(= 평소 경기하는 요일)로 본다 —
    잘못 '쉬는 날'로 판정하면 진짜 사고를 못 보고 넘기기 때문이다.
    """
    st = _weekday_stats(by_day, today)
    if not st:
        return ""
    same, other, wd = st
    if len(same) < WEEKDAY_MIN_SAMPLE or not other:
        return ""
    avg_same = sum(same) / len(same)
    avg_other = sum(other) / len(other)
    if avg_other <= 0 or avg_same >= avg_other * WEEKDAY_QUIET_RATIO:
        return ""
    return (f"{_WEEKDAY_KO[wd]}요일은 평소 쉬는 날 — 지난 {_WEEKDAY_KO[wd]}요일 "
            f"{len(same)}번 평균 {avg_same:.1f}경기 "
            f"(다른 요일 평균 {avg_other:.1f}경기)")


def weekday_normal(by_day: dict[str, int], today: str) -> float | None:
    """오늘과 같은 요일의 평소 경기 수. 표본이 모자라면 None.

    이게 없으면 판정 기준이 '어제'뿐인데, 어제가 마침 휴식일이면
    오늘 리그가 통째로 사라져도 "어제 0경기 → 오늘 0경기"라 아무 말도 못 한다.
    KBO·K리그1처럼 월요일을 쉬는 리그의 **화요일**이 정확히 그 구멍이었다.
    """
    st = _weekday_stats(by_day, today)
    if not st:
        return None
    same, _other, _wd = st
    if len(same) < WEEKDAY_MIN_SAMPLE:
        return None
    return sum(same) / len(same)


def _off_season(name: str, now: datetime) -> bool:
    """이 리그가 지금 비시즌인가. 이름을 못 맞히면 '시즌 중'으로 본다(안전한 쪽)."""
    from contract import KST, League, in_season
    lg = next((l for l in League if l.value == name), None)
    return bool(lg) and not in_season(lg, now.astimezone(KST))


def check_snapshot_age(fetch_log: dict, now: datetime,
                       max_age: int = SNAPSHOT_MAX_AGE_SECONDS) -> list[Finding]:
    """수집 자체가 멈췄는지. 내용을 보기 전에 이것부터 본다.

    비시즌 리그의 실패는 **참고로만** 남긴다(soft). 내보낼 경기가 없는 리그 때문에
    시계를 실패로 세우면, 그 소음에 진짜 사고가 묻힌다.
    """
    out = []
    for name, rec in sorted(fetch_log.items()):
        soft = _off_season(name, now)
        at = rec.get("at")
        if not at:
            out.append(Finding(name, "수집 성공 기록 없음",
                               f"마지막 오류: {str(rec.get('error'))[:80]}", soft=soft))
            continue
        age = (now - datetime.fromisoformat(at)).total_seconds()
        if age > max_age:
            out.append(Finding(name, "수집이 멈춤",
                               f"마지막 성공 {age / 3600:.1f}시간 전"
                               + (f" · {str(rec.get('error'))[:60]}" if rec.get("error") else ""),
                               soft=soft))
    return out


def check_league(name: str, games: list, now: datetime) -> list[Finding]:
    """한 리그의 오늘·어제를 비교한다."""
    out: list[Finding] = []
    from contract import KST
    today = now.astimezone(KST).strftime("%Y-%m-%d")
    yesterday = (now.astimezone(KST) - timedelta(days=1)).strftime("%Y-%m-%d")

    by_day = _counts_by_day(games)

    # 비시즌 판정 — 최근 2주에 경기가 하나도 없으면 조용한 게 정상이다
    recent = [(now.astimezone(KST) - timedelta(days=d)).strftime("%Y-%m-%d")
              for d in range(OFFSEASON_DAYS)]
    if not any(by_day.get(d) for d in recent):
        return out

    t, y = by_day.get(today, 0), by_day.get(yesterday, 0)
    why = rest_weekday(by_day, today)

    if why:
        # 정기 휴식일이면 0이 정상이다. 지우지는 않는다 — 참고로 남겨
        # "왜 오늘 조용한지"가 로그에 보이게 한다. 다만 빨간불로는 안 올린다.
        # 어제도 0이었으면 아무도 이상하다 생각하지 않으니 조용히 넘어간다.
        if y and not t:
            out.append(Finding(name, "오늘 편성이 사라짐",
                               f"어제 {y}경기 → 오늘 0경기 · {why}",
                               soft=True, soft_why="정기 휴식일"))
    else:
        # 기준은 '같은 요일의 평소 경기 수'다. 어제만 보면, 월요일을 쉬는 리그가
        # 화요일에 통째로 사라져도 "어제 0 → 오늘 0"이라 침묵한다.
        norm = weekday_normal(by_day, today)
        base = norm if norm is not None else float(y)
        base_txt = (f"평소 이 요일 {norm:.1f}경기" if norm is not None
                    else f"어제 {y}경기")
        if base >= 1 and not t:
            out.append(Finding(name, "오늘 편성이 사라짐",
                               f"{base_txt} → 오늘 0경기"))
        elif base >= 1 and t < base * DROP_RATIO:
            out.append(Finding(name, "편성이 급감",
                               f"{base_txt} → 오늘 {t}경기"))

    # 종결되지 않은 지난 경기 — 소스가 결과를 안 채우고 있다
    from contract import stale_unresolved
    stale = stale_unresolved(games, now_utc=now)
    if stale:
        out.append(Finding(name, "결과가 안 들어온 지난 경기",
                           f"{len(stale)}건 예) {stale[0].sports_day}"))
    return out


def run(snapshots: dict[str, list], fetch_log: dict,
        now: datetime | None = None) -> Report:
    """스냅샷과 수집 로그를 받아 이상을 모은다. 네트워크를 쓰지 않는다."""
    now = now or datetime.now(timezone.utc)
    rep = Report()
    rep.findings += check_snapshot_age(fetch_log, now)
    from contract import KST, League, in_season
    local = now.astimezone(KST)
    for name, games in sorted(snapshots.items()):
        rep.checked += 1
        # 비시즌의 0건은 정상이다. 이걸 경보로 올리면 8월마다 농구·배구가 울고,
        # 그 소음에 진짜 사고가 묻힌다.
        # 스냅샷 이름은 League의 값 그대로다. 대소문자를 흔들어 맞추지 않는다 —
        # 조용히 못 맞히면 비시즌 판정이 통째로 무력해지고, 8월마다 농구가 오탐으로 운다.
        lg = next((l for l in League if l.value == name), None)
        season_now = in_season(lg, local) if lg else True
        if not games:
            if not season_now:
                continue                     # 비시즌 — 조용한 게 정상
            # 스냅샷이 아예 비었다. 수집 로그가 이유를 알고 있으면 위에서 이미 잡혔다.
            if name in fetch_log and fetch_log[name].get("at"):
                rep.findings.append(Finding(name, "시즌 중인데 경기가 0건",
                                            "수집은 성공했는데 한 건도 없다 — "
                                            "소스 구조 변경 의심"))
            continue
        if not season_now:
            continue
        rep.findings += check_league(name, games, now)
    return rep


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    import tick as T

    r = run(T.all_games(), T._fetch_log())
    print(f"커버리지 감시 — 리그 {r.checked}개 점검")
    if not r.findings:
        print("  이상 없음")
    else:
        for line in r.lines():
            print(f"  ⚠️ {line}")
    sys.exit(0 if r.ok else 1)
