"""발송 시각·표시 날짜 검증 (v1.11f 신설).

대표님이 실채널에서 짚은 세 가지를 고치면서 만든 검증이다:
  ① "오늘 31일인데 30일 카드가 온다"      → 표시 날짜를 한국 기준으로
  ② "경기 결과가 너무 늦게 온다"           → 마감을 마지막 경기 종료에서 계산
  ③ "시작 알림이 이미 시작한 뒤에 온다"     → '오늘'을 발송 시점 한국 날짜로 판정

세 가지 다 **숫자 검증 400여 건이 전부 통과한 채로** 실채널에 나갔다.
카드가 사람에게 무슨 말을 하는지는 숫자가 알려주지 않기 때문이다.
그래서 이 파일은 문구와 시각을 직접 읽는다.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import contract as C  # noqa: E402
import pipeline as P  # noqa: E402
from contract import (KST, Game, GameMeta, League, Score, ScoreUnit,  # noqa: E402
                      Status, TeamRef, assert_result_deadline, game_duration_for,
                      kst_day_label, result_deadline)

FAIL: list[str] = []
PASS = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name}{(' — ' + detail) if detail else ''}")


def _raised(fn) -> bool:
    """게이트가 실제로 막는지 본다. 규칙만 있고 검사기가 없으면 반드시 어긴다."""
    try:
        fn()
    except C.GateError:
        return True
    return False


def mk(lg: League, day: str, hh: int, mm: int = 0, *, tz: str,
       h: str, a: str, status=Status.SCHEDULED, score=None) -> Game:
    """현지 시각으로 경기를 만든다 — sports_day는 현지 캘린더 날짜다."""
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm,
                  tzinfo=ZoneInfo(tz))
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season, source_key=f"{lg.value}-{day}-{hh}{mm}-{h}{a}",
             home=TeamRef(lg, h), away=TeamRef(lg, a),
             start_utc=st.astimezone(timezone.utc), home_tz=tz,
             status=status, score=score, venue=None,
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.validate()
    return g


# ── 실제 슬레이트 재현 ──────────────────────────────────────────────────
# MLB 현지 2026-08-30 (동부): 13:05~21:20 → 한국시각 08-31 02:05~10:20
MLB_DAY = "2026-08-30"
MLB = [mk(League.MLB, MLB_DAY, 13, 5, tz="America/New_York", h="ATL", a="SF",
          status=Status.FINAL, score=Score(2, 3, ScoreUnit.RUNS)),
       mk(League.MLB, MLB_DAY, 19, 10, tz="America/New_York", h="NYY", a="BOS",
          status=Status.FINAL, score=Score(1, 16, ScoreUnit.RUNS)),
       mk(League.MLB, MLB_DAY, 21, 20, tz="America/New_York", h="LAD", a="DET",
          status=Status.FINAL, score=Score(1, 6, ScoreUnit.RUNS))]

KBO_DAY = "2026-08-29"
KBO = [mk(League.KBO, KBO_DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
          status=Status.FINAL, score=Score(3, 8, ScoreUnit.RUNS)),
       mk(League.KBO, KBO_DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
          status=Status.FINAL, score=Score(4, 2, ScoreUnit.RUNS))]

NPB_DAY = "2026-08-30"
NPB = [mk(League.NPB, NPB_DAY, 18, 0, tz="Asia/Tokyo", h="G", a="T",
          status=Status.FINAL, score=Score(5, 2, ScoreUnit.RUNS))]

print("=" * 62)
print("발송 시각·표시 날짜 검증")
print("=" * 62)

# ── 1. 표시 날짜 — 묶는 기준은 현지, 보여주는 날짜는 한국 ────────────────
print("\n1. 표시 날짜 — 한국 기준으로 말하는가")

check("MLB 슬레이트는 한국시각으로 하루 뒤",
      all(g.start_kst.strftime("%Y-%m-%d") == "2026-08-31" for g in MLB),
      str([f"{g.start_kst:%m-%d %H:%M}" for g in MLB]))

lab, loc = kst_day_label(MLB, MLB_DAY)
check("MLB 표시 날짜가 한국 날짜", lab == "8.31 월", lab)
check("MLB는 현지 날짜를 병기", loc == "현지 8.30", loc)

lab, loc = kst_day_label(KBO, KBO_DAY)
check("KBO 표시 날짜", lab == "8.29 토", lab)
check("KBO는 병기하지 않는다", loc == "", loc)

lab, loc = kst_day_label(NPB, NPB_DAY)
check("NPB는 한국과 같은 시간대라 병기 없음", loc == "" and lab == "8.30 일", f"{lab}|{loc}")

# 한 슬레이트가 한국 날짜 둘에 걸치는 경우(유럽 주말)
split = [mk(League.EPL, "2026-08-29", 15, 0, tz="Europe/London", h="ARS", a="CHE"),
         mk(League.EPL, "2026-08-29", 17, 30, tz="Europe/London", h="MCI", a="LIV")]
lab, loc = kst_day_label(split, "2026-08-29")
check("한국 날짜 둘에 걸치면 둘 다 적는다", "~" in lab, lab)

check("빈 입력이어도 죽지 않는다", kst_day_label([], "2026-08-30")[0] == "8.30 일",
      str(kst_day_label([], "2026-08-30")))
check("빈 입력에 날짜도 없으면 빈 문자열", kst_day_label([], None) == ("", ""))

# ── 2. 카드가 실제로 그 날짜를 찍는가 ───────────────────────────────────
print("2. 카드 헤더 — 실제 HTML을 읽는다")

html = P.render_result(MLB, MLB_DAY)
check("결과 카드에 한국 날짜", "8.31 월" in html, html[:0])
check("결과 카드에 현지 날짜 병기", "현지 8.30" in html)
check("결과 카드에 현지 날짜가 주 날짜로 찍히지 않는다",
      '<span class="dt">8.30' not in html)

html_kbo = P.render_result(KBO, KBO_DAY)
check("국내 리그 카드에는 병기가 없다", "현지" not in html_kbo)

html_m = P.render_morning(MLB, MLB_DAY)
check("모닝 브리핑도 같은 규칙", "8.31 월" in html_m and "현지 8.30" in html_m)

# ── 3. 결과 카드 마감 — 마지막 경기가 끝난 뒤인가 ───────────────────────
print("3. 결과 카드 마감 — 마지막 경기를 빠뜨리지 않는가")

dl = result_deadline(MLB)
last_end = max(g.start_utc for g in MLB) + timedelta(seconds=game_duration_for(League.MLB))
check("마감이 마지막 경기 종료 이후", dl >= last_end,
      f"마감 {dl.astimezone(KST):%m-%d %H:%M} < 종료 {last_end.astimezone(KST):%m-%d %H:%M}")
check("마감이 종료 1시간 뒤 (대표님 지시)",
      abs((dl - last_end).total_seconds() - 3600) < 1,
      f"{(dl - last_end).total_seconds()}초")

# 예전 계산식(UTC 자정 + 26h)이었다면 마지막 경기 시작 무렵이 마감이었다.
old = MLB[0].start_utc.replace(hour=0, minute=0) + timedelta(hours=26)
check("옛 계산식은 마지막 경기 종료 전이었다(회귀 방지)", old < last_end,
      f"옛 마감 {old.astimezone(KST):%m-%d %H:%M}")

check("게이트가 이른 마감을 차단한다",
      _raised(lambda: assert_result_deadline(MLB, last_end - timedelta(minutes=1))))
check("게이트가 정상 마감은 통과시킨다",
      not _raised(lambda: assert_result_deadline(MLB, dl)))
check("게이트가 빈 입력에 죽지 않는다",
      not _raised(lambda: assert_result_deadline([], dl)))

# 리그별 소요 시간이 종목에 맞는가 (야구 > 축구)
check("야구가 축구보다 길게 잡혀 있다",
      game_duration_for(League.KBO) > game_duration_for(League.KL1))
check("모르는 리그는 넉넉한 기본값",
      game_duration_for(League.UCL) > 0)

# ── 4. 큐가 그 마감을 쓰는가 ────────────────────────────────────────────
print("4. 큐 — 예약 시각이 실제로 늦춰졌는가")

# 아직 안 끝난 슬레이트: 마지막 경기가 진행 중일 때 결과 카드가 예약되면 안 된다
pending = [mk(League.MLB, "2026-09-01", 13, 5, tz="America/New_York", h="ATL", a="SF",
              status=Status.FINAL, score=Score(2, 3, ScoreUnit.RUNS)),
           mk(League.MLB, "2026-09-01", 21, 40, tz="America/New_York", h="LAD", a="DET")]
now = max(g.start_utc for g in pending) + timedelta(minutes=20)   # 마지막 경기 1회 진행 중
q = P.build_queue(pending, now, "ch", floor_hours=0, horizon_hours=48)
res = [i for i in q if i.content_type is C.ContentType.LEAGUE_RESULT]
check("미종결 슬레이트의 결과 카드가 큐에 있다", bool(res))
if res:
    ends = max(g.start_utc for g in pending) + timedelta(
        seconds=game_duration_for(League.MLB))
    check("예약 시각이 마지막 경기 종료 이후", res[0].scheduled_utc >= ends,
          f"예약 {res[0].scheduled_utc.astimezone(KST):%m-%d %H:%M} < "
          f"종료 {ends.astimezone(KST):%m-%d %H:%M}")
    check("진행 중인데 '지금'으로 앞당기지 않는다", res[0].scheduled_utc > now)

# 전부 종결이면 기다리지 않는다 (대표님 지시: 마지막 경기 종료 후 1시간 이내)
done_now = max(g.start_utc for g in MLB) + timedelta(hours=4)
q = P.build_queue(MLB, done_now, "ch", floor_hours=0, horizon_hours=48)
res = [i for i in q if i.content_type is C.ContentType.LEAGUE_RESULT]
check("전부 끝났으면 즉시 발송", res and res[0].scheduled_utc == done_now,
      str([str(i.scheduled_utc) for i in res]))

# ── 5. 시작 알림 — '오늘'을 발송 시점 한국 날짜로 말하는가 ───────────────
print("5. 시작 알림 — 문구가 실제와 맞는가")

up = [mk(League.MLB, "2026-08-31", 18, 5, tz="America/New_York", h="ATL", a="SF"),
      mk(League.MLB, "2026-08-31", 21, 40, tz="America/New_York", h="LAD", a="DET")]
first_kst = min(g.start_kst for g in up)
check("첫 경기가 한국시각 다음날 오전", first_kst.strftime("%Y-%m-%d") == "2026-09-01",
      f"{first_kst:%m-%d %H:%M}")

night = datetime(2026, 8, 31, 22, 0, tzinfo=KST).astimezone(timezone.utc)
txt = P.render_start_alert(up, night)
check("밤에 보내면 '오늘'이라 하지 않는다", "오늘" not in txt.splitlines()[0],
      txt.splitlines()[0])
check("밤에 보내면 '내일'이라 한다", "내일" in txt.splitlines()[0], txt.splitlines()[0])
check("첫 경기 시각을 적는다", f"{first_kst:%H:%M} 시작" in txt, txt.splitlines()[-1])

# 새벽에 열리는 경기는 '내일 새벽'으로
dawn = [mk(League.MLB, "2026-08-31", 13, 5, tz="America/New_York", h="ATL", a="SF")]
txt2 = P.render_start_alert(dawn, night)
check("한국시각 새벽 경기는 '내일 새벽'", "내일 새벽" in txt2.splitlines()[0],
      txt2.splitlines()[0])

same_day = datetime(2026, 9, 1, 5, 5, tzinfo=KST).astimezone(timezone.utc)
txt3 = P.render_start_alert(up, same_day)
check("같은 날 아침에 보내면 '오늘'", txt3.splitlines()[0].count("오늘") == 1,
      txt3.splitlines()[0])

# 남은 시간은 발송 시각에 따라 달라진다 (박아둔 문자열이 아니다)
check("남은 시간이 발송 시각을 따라간다",
      "9시간" in txt.splitlines()[-1] and "2시간" in txt3.splitlines()[-1],
      f"{txt.splitlines()[-1]} || {txt3.splitlines()[-1]}")

# 국내 리그는 표현이 바뀌지 않는다
kbo_up = [mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="LG", a="OB")]
txt4 = P.render_start_alert(kbo_up, datetime(2026, 8, 29, 16, 30, tzinfo=KST).astimezone(timezone.utc))
check("국내 리그는 '오늘' 그대로", txt4.splitlines()[0].startswith("⚾ <b>오늘 KBO"),
      txt4.splitlines()[0])
check("국내 리그엔 현지 시각 병기 없음", "현지" not in txt4, txt4.splitlines()[-1])


# ── 6. 수집 월 창 — 월말에 내일이 보이는가 ──────────────────────────────
print("6. 수집 월 창 — 월말에 다음 달이 보이는가")

import tick as T  # noqa: E402


def months(day: str) -> tuple[int, list[str]]:
    return T.fetch_months(datetime.fromisoformat(day + "T12:00:00+09:00"))


check("월말에는 다음 달을 함께 긁는다", months("2026-08-31")[1] == ["07", "08", "09"],
      str(months("2026-08-31")))
check("말일 하루 전에도 다음 달이 보인다", "09" in months("2026-08-30")[1],
      str(months("2026-08-30")))
check("평소에는 두 달만 (요청을 늘리지 않는다)", months("2026-09-15")[1] == ["08", "09"],
      str(months("2026-09-15")))
check("1일에는 지난달이 남아 있다 (말일 결과 카드)", "08" in months("2026-09-01")[1],
      str(months("2026-09-01")))
check("12월 말에 다음 해 1월을 긁지 않는다", "01" not in months("2026-12-30")[1],
      str(months("2026-12-30")))
check("1월 1일에 작년 12월을 긁지 않는다", months("2026-01-01")[1] == ["01"],
      str(months("2026-01-01")))
check("연도는 항상 그 해", months("2026-12-31")[0] == 2026)
check("2월 말(28일)에도 3월이 보인다", "03" in months("2026-02-27")[1],
      str(months("2026-02-27")))


if __name__ == "__main__":
    print()
    print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
