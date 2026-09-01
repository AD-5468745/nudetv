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


# ── 5-2. 모닝 브리핑도 같은 규칙을 쓰는가 ───────────────────────────────
# 시작 알림만 고치고 모닝 브리핑을 빼먹었다가, 배포 뒤 실채널에서
# "오늘 MLB 15경기"(실제로는 내일 아침 경기)가 나간 것을 보고 찾았다.
# 한 군데를 고치면 같은 병을 앓는 모든 곳을 함께 훑는다.
print("5-2. 모닝 브리핑 — '오늘'을 발송 시점으로 판정하는가")

import re  # noqa: E402

send = datetime(2026, 9, 1, 7, 50, tzinfo=KST).astimezone(timezone.utc)
mlb_next = [mk(League.MLB, "2026-09-01", 18, 40, tz="America/New_York", h="ATL", a="SF"),
            mk(League.MLB, "2026-09-01", 22, 10, tz="America/New_York", h="LAD", a="DET")]
check("MLB 슬레이트가 한국시각 다음날 아침",
      min(g.start_kst for g in mlb_next).strftime("%Y-%m-%d") == "2026-09-02")

html = P.render_morning(mlb_next, "2026-09-01", now=send)
h1 = re.sub("<[^>]+>", "", re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)).strip()
check("모닝 카드가 '오늘'이라 하지 않는다", "오늘" not in h1, h1)
check("모닝 카드가 '내일 아침'이라 한다", h1.startswith("내일 아침"), h1)

cap = P.caption_morning(mlb_next, "2026-09-01", now=send)
check("모닝 캡션도 같은 말을 한다", "내일 아침" in cap.splitlines()[0],
      cap.splitlines()[0])

kbo_today = [mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="LG", a="OB")]
h1k = re.sub("<[^>]+>", "", re.search(
    r"<h1>(.*?)</h1>", P.render_morning(kbo_today, "2026-09-01", now=send), re.S).group(1)).strip()
check("국내 리그 모닝은 '오늘' 그대로", h1k.startswith("오늘 KBO"), h1k)

# day_word 자체의 경계
check("day_word 같은 날", P.day_word(kbo_today, send) == "오늘")
check("day_word 다음날 새벽",
      P.day_word([mk(League.MLB, "2026-09-01", 12, 15, tz="America/New_York", h="ATL", a="SF")],
                 send) == "내일 새벽",
      P.day_word([mk(League.MLB, "2026-09-01", 12, 15, tz="America/New_York", h="ATL", a="SF")], send))
check("day_word 이틀 뒤는 날짜로",
      P.day_word([mk(League.KBO, "2026-09-03", 18, 30, tz="Asia/Seoul", h="LG", a="OB")],
                 send) == "9월 3일")
check("day_word 빈 입력", P.day_word([], send) == "오늘")


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


# ── 7. '종료'를 너무 일찍 믿지 않는가 ───────────────────────────────────
# 2026-09-01 19:18, 18:30에 시작한 KBO 5경기가 전부 '종료 0:0'으로 카드에 실려
# 채널로 나갔다. KBO 일정 페이지가 **진행 중인 경기에도 점수를 채우기** 때문이고,
# 어댑터가 "점수가 있으면 종료"로 읽었기 때문이다.
# 아래 둘을 함께 친다: ① 어댑터가 신호를 제대로 읽는가 ② 계약이 못 믿을 값을 막는가
print("7. '종료' 판정 — 진행 중 경기를 끝났다고 하지 않는가")

from contract import (MIN_GAME_SECONDS, assert_final_not_too_early,  # noqa: E402
                      game_duration_for as _dur)

_start = datetime(2026, 9, 1, 18, 30, tzinfo=KST)


def kbo_at(minutes_after: int, status=Status.FINAL) -> tuple[list, datetime]:
    g = mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
           status=status,
           score=Score(0, 0, ScoreUnit.RUNS) if status is Status.FINAL else None)
    return [g], (_start + timedelta(minutes=minutes_after)).astimezone(timezone.utc)


gs, at = kbo_at(48)
check("시작 48분 뒤 '종료'는 차단", _raised(lambda: assert_final_not_too_early(gs, at)))
gs, at = kbo_at(240)
check("4시간 뒤 '종료'는 통과", not _raised(lambda: assert_final_not_too_early(gs, at)))
gs, at = kbo_at(48, status=Status.LIVE)
check("진행 중(LIVE)은 검사 대상이 아니다",
      not _raised(lambda: assert_final_not_too_early(gs, at)))
gs, at = kbo_at(10, status=Status.CANCELED)
check("취소는 시작 직후여도 통과",
      not _raised(lambda: assert_final_not_too_early(gs, at)))
check("빈 입력", not _raised(lambda: assert_final_not_too_early([], send)))
check("종목마다 하한이 다르다 (야구 > LoL)",
      MIN_GAME_SECONDS[League.KBO] > MIN_GAME_SECONDS[League.LCK])
check("하한은 통상 소요시간보다 짧다 (콜드게임을 막지 않는다)",
      all(MIN_GAME_SECONDS[lg] < _dur(lg) for lg in MIN_GAME_SECONDS))

# 어댑터가 소스 신호를 제대로 읽는가 — 네트워크 없이 행만 넣어 본다
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "adapters"))
from adapters.kbo import KboAdapter  # noqa: E402


def kbo_row(play: str, relay: str, note: str = "-"):
    cells = [("time", "18:30"), ("play", play), ("relay", relay),
             ("", ""), ("", "SPO-T"), ("", ""), ("", "잠실"), ("", note)]
    return {"row": [{"Class": c, "Text": t} for c, t in cells]}


ad = KboAdapter()
_, g_live = ad._parse_row(kbo_row("LG 0 vs 0 두산", ""), 2026, "20260901")
check("진행 중(중계 칸 빈칸) → LIVE", g_live.status is Status.LIVE,
      g_live.status.value)
# 앞서 이 자리에는 "진행 중이어도 점수는 담는다"가 있었다. 틀린 가정이었다 —
# KBO가 진행 중에 채우는 값은 러닝 스코어가 아니라 0-0 자리표시자다(2회 관측 확인).
check("진행 중 경기에는 점수를 담지 않는다", g_live.score is None, str(g_live.score))
_, g_fin = ad._parse_row(kbo_row("LG 2 vs 5 두산", "리뷰"), 2026, "20260901")
check("종료(중계 칸 '리뷰') → FINAL", g_fin.status is Status.FINAL, g_fin.status.value)
check("종료 점수가 맞다", g_fin.score.away == 2 and g_fin.score.home == 5,
      str(g_fin.score))
_, g_sch = ad._parse_row(kbo_row("LG vs 두산", "프리뷰"), 2026, "20260902")
check("예정('프리뷰') → SCHEDULED", g_sch.status is Status.SCHEDULED, g_sch.status.value)
_, g_cx = ad._parse_row(kbo_row("LG vs 두산", "", "우천취소"), 2026, "20260901")
check("취소는 그대로 CANCELED", g_cx.status is Status.CANCELED, g_cx.status.value)

# 진행 중 경기가 섞이면 결과 카드가 만들어지면 안 된다
mixed = [mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
            status=Status.FINAL, score=Score(3, 1, ScoreUnit.RUNS)),
         mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="SS", a="LT",
            status=Status.LIVE)]
check("한 경기라도 진행 중이면 결과 카드를 안 만든다",
      not P.league_day_settled(mixed, "2026-09-01"))


# ── 8. 결과 카드는 '결과가 확정된 것'만 싣는가 ──────────────────────────
# 어댑터를 고쳐 진행 중 경기를 LIVE로 바로잡았는데, **렌더는 여전히 score만 보고**
# 그렸다. 그래서 상태를 고쳐도 카드에는 "0:0 무승부"가 그대로 실렸다.
# 한쪽만 고치면 사고는 안 막힌다 — 상태·렌더·캡션을 함께 친다.
print("8. 결과 카드 — 진행 중 경기를 결과로 그리지 않는가")

import re as _re2  # noqa: E402

live_day = [
    mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
       status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
    mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="SS", a="LT",
       status=Status.LIVE),
    mk(League.KBO, "2026-09-01", 18, 30, tz="Asia/Seoul", h="KT", a="HH",
       status=Status.LIVE, score=Score(0, 0, ScoreUnit.RUNS)),   # 자리표시자가 남아도
]
html = P.render_result(live_day, "2026-09-01")
txt = _re2.sub("<[^>]+>", " ", html)
check("종료 경기는 실린다", "LG" in txt and "5" in txt)
check("진행 중 경기를 '무승부'로 그리지 않는다", "무승부" not in txt, txt[:0])
check("헤드라인이 종료 경기 수만 센다", "1경기" in txt, txt[:0])
check("빠진 경기 수를 숨기지 않는다", "2경기는 아직 결과 미확정" in txt, txt[:0])

cap = P.caption_result(live_day, "2026-09-01")
check("캡션도 진행 중을 점수로 적지 않는다", "0 : 0" not in cap, cap)
check("캡션이 진행 중 경기를 통째로 빼지도 않는다", cap.count("결과 미확정") == 2, cap)
check("캡션 헤드라인도 종료 경기 수", "결과 1경기" in cap, cap.splitlines()[0])

# 전부 끝난 날은 예전과 똑같이 나온다 (회귀 방지)
all_done = [mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
               status=Status.FINAL, score=Score(8, 3, ScoreUnit.RUNS)),
            mk(League.KBO, "2026-08-29", 17, 0, tz="Asia/Seoul", h="SS", a="KT",
               status=Status.CANCELED)]
all_done[1].meta.cancel_reason = "우천취소"
h2 = _re2.sub("<[^>]+>", " ", P.render_result(all_done, "2026-08-29"))
check("전부 종결된 날엔 '미확정' 문구가 안 붙는다", "미확정" not in h2, h2[:0])
check("취소 경기는 그대로 실린다", "우천취소" in h2)

# 어댑터: 진행 중이면 점수를 저장하지 않는다 (KBO는 0:0 자리표시자를 준다)
_, g_live2 = ad._parse_row(kbo_row("LG 0 vs 0 두산", ""), 2026, "20260901")
check("진행 중 경기에는 점수를 붙이지 않는다", g_live2.score is None,
      str(g_live2.score))
_, g_fin2 = ad._parse_row(kbo_row("LG 2 vs 5 두산", "리뷰"), 2026, "20260901")
check("종료 경기에는 점수를 붙인다", g_fin2.score is not None)


# ── 9. 정밀진단(2026-09-01)에서 나온 것들 ───────────────────────────────
print("9. 정밀진단 수정 — 시각·문구·안전")

from contract import (demote_impossible_finals,  # noqa: E402
                      shift_out_of_quiet_hours)

# (1) '종료'가 불가능한 경기는 막지 말고 되돌린다 — 리그 전체를 죽이지 않는다
early = [mk(League.KBO, "2026-09-02", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
            status=Status.FINAL, score=Score(0, 0, ScoreUnit.RUNS)),
         mk(League.KBO, "2026-09-02", 18, 30, tz="Asia/Seoul", h="SS", a="KT",
            status=Status.FINAL, score=Score(7, 4, ScoreUnit.RUNS))]
at48 = (datetime(2026, 9, 2, 19, 18, tzinfo=KST)).astimezone(timezone.utc)
fixed, notes = demote_impossible_finals(early, at48)
check("불가능한 '종료'를 LIVE로 되돌린다",
      all(g.status is Status.LIVE for g in fixed), str([g.status.value for g in fixed]))
check("되돌린 경기의 점수는 지운다", all(g.score is None for g in fixed))
check("무엇을 되돌렸는지 사람에게 알린다", len(notes) == 2, str(notes))
check("원본을 바꾸지 않는다", all(g.status is Status.FINAL for g in early))
late = (datetime(2026, 9, 2, 23, 0, tzinfo=KST)).astimezone(timezone.utc)
kept, notes2 = demote_impossible_finals(early, late)
check("정상 종료는 건드리지 않는다",
      all(g.status is Status.FINAL for g in kept) and not notes2)

# (2) 한국시각 심야에는 시작 알림을 울리지 않는다
for hh, expect in ((5, 22), (3, 22), (0, 22), (6, 6), (23, 23), (16, 16)):
    src = datetime(2026, 9, 3, hh, 5, tzinfo=KST).astimezone(timezone.utc)
    got = shift_out_of_quiet_hours(src).astimezone(KST)
    check(f"심야 판정 {hh:02d}시", got.hour == expect, f"{got:%m-%d %H:%M}")
check("심야면 전날로 옮긴다",
      shift_out_of_quiet_hours(
          datetime(2026, 9, 3, 3, 0, tzinfo=KST).astimezone(timezone.utc)
      ).astimezone(KST).day == 2)

# (3) 시작 알림 예약은 첫 경기에 고정된다 (경기가 시작돼도 밀리지 않는다)
slate = [mk(League.MLB, "2026-09-10", 13, 5, tz="America/New_York", h="ATL", a="SF"),
         mk(League.MLB, "2026-09-10", 19, 10, tz="America/New_York", h="NYY", a="BOS")]
first_utc = min(g.start_utc for g in slate)
q1 = P.build_queue(slate, first_utc - timedelta(hours=5), "ch",
                   floor_hours=0, horizon_hours=72)
sa1 = [i for i in q1 if i.content_type is C.ContentType.START_ALERT]
check("시작 알림이 큐에 있다", bool(sa1))
# 첫 경기가 시작돼 FINAL이 되어도 예약 시각은 그대로여야 한다
started = [mk(League.MLB, "2026-09-10", 13, 5, tz="America/New_York", h="ATL", a="SF",
              status=Status.FINAL, score=Score(1, 2, ScoreUnit.RUNS)),
           slate[1]]
q2 = P.build_queue(started, first_utc + timedelta(hours=3), "ch",
                   floor_hours=0, horizon_hours=72)
sa2 = [i for i in q2 if i.content_type is C.ContentType.START_ALERT]
check("경기가 시작돼도 예약 시각이 밀리지 않는다",
      (not sa2) or sa1[0].scheduled_utc == sa2[0].scheduled_utc,
      f"{sa1[0].scheduled_utc} vs {sa2[0].scheduled_utc if sa2 else None}")

# (4) 결과 카드: 전 경기 취소 / 취소 건수 / 없는 기능 안내 제거
cx_day = [mk(League.KBO, "2026-08-05", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
             status=Status.CANCELED),
          mk(League.KBO, "2026-08-05", 18, 30, tz="Asia/Seoul", h="SS", a="KT",
             status=Status.CANCELED)]
for g in cx_day:
    g.meta.cancel_reason = "폭염취소"
h_cx = _re2.sub("<[^>]+>", " ", P.render_result(cx_day, "2026-08-05"))
check("전 경기 취소된 날은 '전 경기 취소'라 한다", "전 경기 취소" in h_cx, h_cx[:0])
check("'0경기 종료'라는 무의미한 말을 안 한다", "0경기 종료" not in h_cx)
check("없는 기능을 안내하지 않는다", "편성 확정 시 안내" not in h_cx)
check("캡션도 같은 말을 한다",
      "전 경기 취소" in P.caption_result(cx_day, "2026-08-05").splitlines()[0])

mixed2 = cx_day + [mk(League.KBO, "2026-08-05", 18, 30, tz="Asia/Seoul", h="HH", a="NC",
                      status=Status.FINAL, score=Score(3, 5, ScoreUnit.RUNS))]
h_mx = _re2.sub("<[^>]+>", " ", P.render_result(mixed2, "2026-08-05"))
check("결과 카드가 취소 건수를 밝힌다", "2경기 취소" in h_mx, h_mx[:0])

# (5) 카드와 캡션이 같은 경기 수를 말한다
mor = _re2.sub("<[^>]+>", " ", P.render_morning(mixed2, "2026-08-05",
                                                now=datetime(2026, 8, 5, 7, 30, tzinfo=KST)))
cap_m = P.caption_morning(mixed2, "2026-08-05",
                          now=datetime(2026, 8, 5, 7, 30, tzinfo=KST))
check("카드 헤드라인은 열리는 경기 수", "1경기" in mor, mor[:0])
check("캡션도 같은 수를 말한다", "편성 1경기" in cap_m, cap_m.splitlines()[0])
check("캡션이 취소 수를 병기한다", "2경기 취소" in cap_m.splitlines()[0],
      cap_m.splitlines()[0])

# (6) 지난 날짜를 '오늘'이라 하지 않는다
past = [mk(League.KBO, "2026-08-01", 18, 30, tz="Asia/Seoul", h="OB", a="LG")]
check("지난 날짜는 날짜로 말한다",
      P.day_word(past, datetime(2026, 9, 1, 12, 0, tzinfo=KST)) == "8월 1일",
      P.day_word(past, datetime(2026, 9, 1, 12, 0, tzinfo=KST)))

# (7) 더블헤더 표시
dh = [mk(League.MLB, "2026-08-29", 13, 5, tz="America/New_York", h="NYY", a="BOS",
         status=Status.FINAL, score=Score(6, 0, ScoreUnit.RUNS)),
      mk(League.MLB, "2026-08-29", 19, 5, tz="America/New_York", h="NYY", a="BOS",
         status=Status.FINAL, score=Score(2, 9, ScoreUnit.RUNS))]
dh[1].meta.doubleheader_seq = 2
cap_dh = P.caption_result(dh, "2026-08-29")
check("더블헤더 2차전을 표시한다", "(2차전)" in cap_dh, cap_dh)
check("1차전에는 안 붙인다", cap_dh.count("차전") == 1)

# (8) 점수 없는 FINAL을 '종료'로 세지 않는다
noscore = [mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
              status=Status.FINAL, score=Score(3, 1, ScoreUnit.RUNS)),
           mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="SS", a="KT",
              status=Status.LIVE)]
h_ns = _re2.sub("<[^>]+>", " ", P.render_result(noscore, "2026-08-29"))
check("헤드라인과 본문 행 수가 맞는다", "1경기" in h_ns and "미확정" in h_ns, h_ns[:0])


if __name__ == "__main__":
    print()
    print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
