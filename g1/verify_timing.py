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
# **[기대 변경 2026-09-02] `9월 3일` → `9.3 목`.**
# **원래 목적**: "이틀 뒤를 '오늘'이나 상대어로 말하지 않는다". 그 목적은 그대로다.
# 바뀐 것은 **표기뿐**이다 — 카드 헤더는 `8.30 일`인데 헤드라인만 `9월 3일`이라
# 한 카드 안에 날짜 표기가 두 종류였다. v1.11i에서 `day_word()`도 헤더와 같은
# `M.D 요일`로 통일했다(pipeline._day_word_at).
# 기대값만 바꾸면 검증이 무뎌지므로 목적을 **세 갈래로 나눠** 지킨다:
#   ① 새 형식과 정확히 같은가  ② 상대어가 섞이지 않았는가(원래 목적 그 자체)
#   ③ 붙인 요일이 **실제 요일**과 맞는가 — 형식만 보면 요일을 틀려도 통과한다
#   ④ 카드 헤더(kst_day_label)와 **같은 문자열**인가 — 통일이 목적이었으므로
#      둘이 갈라지면 고친 이유 자체가 사라진다
_far_day = "2026-09-03"
_far = [mk(League.KBO, _far_day, 18, 30, tz="Asia/Seoul", h="LG", a="OB")]
_fw = P.day_word(_far, send)
check("day_word 이틀 뒤는 날짜로 (헤더와 같은 M.D 요일)", _fw == "9.3 목", _fw)
check("day_word 이틀 뒤에 상대어를 쓰지 않는다",
      not any(w in _fw for w in ("오늘", "내일", "모레", "어제")), _fw)
check("day_word가 붙인 요일이 실제 요일과 같다",
      _fw.endswith(P._WD[datetime.fromisoformat(_far_day).weekday()]), _fw)
check("헤드라인 날짜 표기가 카드 헤더와 같은 형식",
      _fw == kst_day_label(_far, _far_day)[0],
      f"{_fw} vs {kst_day_label(_far, _far_day)[0]}")
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
# **[기대 변경 2026-09-02] `8월 1일` → `8.1 토`.** 위 5-2절과 같은 이유다 —
# 날짜 표기를 카드 헤더와 같은 `M.D 요일` 한 가지로 통일했다(v1.11i).
# **원래 목적**("하루 묵은 카드가 오늘 것으로 보이지 않게 한다")은 표기와 무관하므로
# 형식·상대어·요일을 나눠 확인해 그대로 지킨다.
past_day = "2026-08-01"
past = [mk(League.KBO, past_day, 18, 30, tz="Asia/Seoul", h="OB", a="LG")]
_pw = P.day_word(past, datetime(2026, 9, 1, 12, 0, tzinfo=KST))
check("지난 날짜는 날짜로 말한다", _pw == "8.1 토", _pw)
check("지난 날짜에 상대어를 쓰지 않는다",
      not any(w in _pw for w in ("오늘", "내일", "어제", "그제")), _pw)
check("지난 날짜의 요일도 실제 요일과 같다",
      _pw.endswith(P._WD[datetime.fromisoformat(past_day).weekday()]), _pw)

# (7) 더블헤더 표시
dh = [mk(League.MLB, "2026-08-29", 13, 5, tz="America/New_York", h="NYY", a="BOS",
         status=Status.FINAL, score=Score(6, 0, ScoreUnit.RUNS)),
      mk(League.MLB, "2026-08-29", 19, 5, tz="America/New_York", h="NYY", a="BOS",
         status=Status.FINAL, score=Score(2, 9, ScoreUnit.RUNS))]
dh[0].meta.doubleheader_seq = 1
dh[1].meta.doubleheader_seq = 2
cap_dh = P.caption_result(dh, "2026-08-29")
check("더블헤더 2차전을 표시한다", "(2차전)" in cap_dh, cap_dh)
# v1.11i 육안검수: 전에는 "1차전에는 안 붙인다"를 요구했다. 그런데 2차전에만
# 표시가 있으면 위쪽 줄이 1차전이라는 근거가 카드 어디에도 없다 — 독자가
# "목록이 시간순일 것"이라고 추론해야 알 수 있고, 그 규칙은 카드에 안 적혀 있다.
# 지켜야 할 것은 "붙이지 않는다"가 아니라 **같은 대진 두 줄을 구별할 수 있다**이다.
check("1차전에도 표시한다 — 한쪽만 붙이면 근거가 없다", "(1차전)" in cap_dh, cap_dh)
check("차수 표시가 경기 수만큼만 나온다", cap_dh.count("차전") == 2, cap_dh)
# 더블헤더가 아닌 날에는 붙지 않는다 (표시를 남발하지 않는다)
solo = [mk(League.MLB, "2026-08-30", 13, 5, tz="America/New_York", h="NYY", a="BOS",
           status=Status.FINAL, score=Score(6, 0, ScoreUnit.RUNS))]
check("단일 경기에는 차수를 안 붙인다", "차전" not in P.caption_result(solo, "2026-08-30"))

# (8) 점수 없는 FINAL을 '종료'로 세지 않는다
noscore = [mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="OB", a="LG",
              status=Status.FINAL, score=Score(3, 1, ScoreUnit.RUNS)),
           mk(League.KBO, "2026-08-29", 18, 30, tz="Asia/Seoul", h="SS", a="KT",
              status=Status.LIVE)]
h_ns = _re2.sub("<[^>]+>", " ", P.render_result(noscore, "2026-08-29"))
check("헤드라인과 본문 행 수가 맞는다", "1경기" in h_ns and "미확정" in h_ns, h_ns[:0])


# ── 10. 아직 발행 안 되는 카드들의 거짓말 (정밀진단 잔여분) ─────────────
# 기록·순위·리더보드·맞대결은 아직 큐에 안 오르지만, 켜는 날 그대로 나간다.
# "켤 때 고치자"는 곧 "켜고 나서 사고로 안다"가 된다.
print("10. 미발행 카드 — 켜기 전에 거짓말을 지운다")

from contract import LeaderEntry, RecordBook, Standing, StreakKind, WLD  # noqa: E402


_TC = "LG"


def _le(cat: str, i: int) -> LeaderEntry:
    return LeaderEntry(category=cat, stat_key=cat, player_id=f"p{i}", rank=i + 1,
                       name=f"선수{i}", team_code=_TC, value=f"{20 - i}.0")


def _rb(lg: League, cats) -> RecordBook:
    # 순위표 0건은 게이트가 막으므로 최소 한 팀은 넣는다.
    # 게이트가 리그 정원(팀 수)을 확인하므로 표를 정원만큼 채운다.
    codes = list(C.TEAM_NAMES[lg])
    st = [Standing(league=lg, season="2026", team_code=tc, rank=i + 1, games=15,
                   record=WLD(win=10, loss=5, draw=0), pct=".667",
                   games_behind="0.0", last10=WLD(win=6, loss=4, draw=0),
                   streak_kind=StreakKind.WIN, streak_len=2)
          for i, tc in enumerate(codes)]
    return RecordBook(league=lg, season="2026",
                      collected_utc=datetime.now(timezone.utc), source_url="x",
                      standings=st, h2h={},
                      leaders={c: [_le(c, i) for i in range(5)] for c in cats})


# (1) 리더보드 제목이 카드 내용을 속이지 않는가
kbl_rb = _rb(League.KBL, ("득점", "리바운드", "어시스트", "3점",
                          "스틸", "블록", "자유투", "야투"))
sets = [P.leader_set(kbl_rb, i) for i in range(4)]
check("비야구 리그에 야구 제목을 안 쓴다",
      all("타격" not in t and "투수" not in t for t, _ in sets), str([t for t, _ in sets]))
check("세트마다 다른 부문을 싣는다", sets[0][1] != sets[1][1], str(sets[0][1]))
kbo_rb = _rb(League.KBO, ("타율", "홈런", "타점", "도루",
                          "평균자책점", "승리", "탈삼진", "세이브"))
check("야구 리그는 이름 붙은 세트를 그대로 쓴다",
      P.leader_set(kbo_rb, 0)[0] == "타격 부문", str(P.leader_set(kbo_rb, 0)))

# ── 제목 검사 교체 (2026-09-02) ─────────────────────────────────────────
# **[기대 변경] 옛 검사 "제목이 실제 실린 부문과 같다"를 뺐다.**
# 옛 동작은 부문명 4개를 이어 붙인 것을 제목으로 썼다(`득점 · 리바운드 · 어시스트 · 3점`).
# 그것은 제목으로 읽히지 않고 h1 한 줄 폭을 넘겨서, v1.11i에서 **리그별 고정
# 세트명**(`공격 부문`/`수비 부문`, 표가 없으면 `주요 부문`)으로 바꿨다.
# 이제 제목이 부문명을 포함하지 않으므로 옛 검사는 성립할 수 없다.
#
# **원래 목적은 "제목이 카드 내용을 속이지 않는다"**이고, 그 목적은 남는다.
# 제목이 더 이상 내용에서 만들어지지 않으므로, **제목이 거짓이 될 수 있는 경로**를
# 새로 찾아 넷으로 나눠 막는다. 리그마다 부문 수가 다르고(수집이 일부 실패하면
# 더 줄어든다) 세트는 요일로 4번 돈다 — 그 조합 전부를 훑는다.
#   (a) 지어낸 제목이 나오지 않는다
#   (b) **뜻이 있는 제목이 다르면 실린 부문 구성도 다르다**
#       (같은 카드를 제목만 바꿔 네 번 낸 것이 애초에 이 자리를 고친 이유다.
#        `주요 부문`처럼 아무 부문도 지목하지 않는 공용 이름은 비교에서 뺀다 —
#        공용 이름은 무엇도 주장하지 않으므로 거짓이 될 수 없다.)
#   (c) **제목이 특정 부문을 지목하면 그 부문이 실제로 실려 있다**
#       (야구 세트명은 그 세트의 4부문이 전부 있을 때만 쓴다)
#   (d) 제목이 다시 부문명 나열로 돌아가지 않는다 (옛 결함 회귀 방지)

# "뜻이 있는 제목" = 부문 묶음을 지목하는 이름. 공용 이름(`주요 부문`)은 뺀다.
_SPECIFIC_TITLES = ({t for t, _ in P.LEADER_SETS}
                    | {n for v in P._LEADER_SET_NAMES.values() for n in v})

_POOLS = {   # 리그별로 실제로 올 만한 부문 이름 (Top5 페이지 표기 계열)
    League.KBL: ["득점", "리바운드", "어시스트", "3점", "스틸", "블록", "자유투", "야투",
                 "턴오버", "파울"],
    League.LCK: ["KDA", "분당 CS", "분당 데미지", "킬 관여", "퍼스트블러드", "오브젝트",
                 "시야점수", "솔로킬"],
    League.KL1: ["득점", "도움", "슈팅", "유효슈팅", "태클", "인터셉트", "세이브", "클린시트"],
    League.VLEAGUE_M: ["득점", "공격 성공률", "서브", "블로킹", "디그", "리시브", "세트", "범실"],
    League.KBO: ["타율", "홈런", "타점", "도루", "평균자책점", "승리", "탈삼진", "세이브",
                 "출루율", "장타율", "OPS", "안타", "WHIP", "QS", "이닝", "피안타율"],
    League.MLB: ["타율", "홈런", "타점", "도루", "평균자책점", "승리", "탈삼진", "세이브",
                 "출루율", "장타율", "OPS", "안타"],
}

_made_up: list[str] = []      # (a) 어디에도 정의되지 않은 제목
_same_cats: list[str] = []    # (b) 뜻이 다른 제목인데 실린 부문이 똑같다
_unearned: list[str] = []     # (c) 야구 세트명을 달았는데 그 부문이 없다
_listy: list[str] = []        # (d) 제목이 부문명 나열이다

for _lg, _pool in _POOLS.items():
    _allowed = (set(P._LEADER_SET_NAMES.get(_lg, [])) | {P.LEADER_SET_FALLBACK}
                | {t for t, _ in P.LEADER_SETS})
    for _n in range(1, len(_pool) + 1):          # 부문이 일부만 수집된 날까지
        _rbx = _rb(_lg, tuple(_pool[:_n]))
        _s = [P.leader_set(_rbx, i) for i in range(4)]
        for _i, (_t, _c) in enumerate(_s):
            if _t not in _allowed:
                _made_up.append(f"{_lg.value} n={_n} set{_i}: {_t}")
            # 옛 형식은 "실린 부문명 **전부**를 ' · '로 이어 붙인 것"이었다.
            # (고정 세트명이 부문 낱말 하나를 품는 것은 정상이다 —
            #  `제구·이닝 부문`은 나열이 아니라 이름이다.)
            if (_c and all(_cat in _t for _cat in _c)) or " · " in _t:
                _listy.append(f"{_lg.value} n={_n} set{_i}: {_t} ← {_c}")
            for _wt, _wc in P.LEADER_SETS:       # 야구 세트명은 '지목하는' 제목이다
                if _t == _wt and set(_c) != set(_wc):
                    _unearned.append(f"{_lg.value} n={_n} set{_i}: {_t} ← {_c}")
            for _j in range(_i + 1, 4):
                _t2, _c2 = _s[_j]
                if (_t != _t2 and _c == _c2
                        and _t in _SPECIFIC_TITLES and _t2 in _SPECIFIC_TITLES):
                    _same_cats.append(f"{_lg.value} n={_n}: {_t}/{_t2} ← {_c}")

check("제목을 지어내지 않는다 (정의된 세트명·공용 이름 중 하나)",
      not _made_up, "; ".join(_made_up[:3]))
check("뜻이 다른 제목이면 실린 부문 구성도 다르다 (같은 카드를 제목만 바꿔 내지 않는다)",
      not _same_cats, "; ".join(_same_cats[:3]))
check("제목이 지목한 부문이 실제로 실려 있다 (야구 세트명은 4부문이 다 있을 때만)",
      not _unearned, "; ".join(_unearned[:3]))
check("제목이 부문명 나열로 돌아가지 않았다 (h1 한 줄)",
      not _listy, "; ".join(_listy[:3]))
check("제목이 한 줄에 들어가는 길이",
      all(len(P.leader_set(_rb(lg, tuple(p[:8])), i)[0]) <= 12
          for lg, p in _POOLS.items() for i in range(4)))

# 요구 부문이 하나라도 빠지면 그 세트 이름을 쓰지 않는다 —
# "타격 부문"이라 적고 평균자책점을 싣는 것이 곧 제목의 거짓말이다.
_kbo_missing = _rb(League.KBO, ("타율", "홈런", "타점",          # 도루가 빠졌다
                                "평균자책점", "승리", "탈삼진", "세이브"))
_tm, _cm = P.leader_set(_kbo_missing, 0)
check("요구 부문이 빠지면 그 세트 이름을 쓰지 않는다",
      _tm != "타격 부문", f"{_tm} ← {_cm}")
check("이름표가 없는 리그는 공용 이름으로 떨어진다 (야구 이름을 빌려 쓰지 않는다)",
      P.leader_set(_rb(League.MLB, tuple(_POOLS[League.KBL][:8])), 0)[0]
      == P.LEADER_SET_FALLBACK,
      str(P.leader_set(_rb(League.MLB, tuple(_POOLS[League.KBL][:8])), 0)))

# (2) 근거 없는 단정을 카드에 쓰지 않는다.
# 렌더 전체를 돌리려면 순위표·부문값이 게이트를 전부 통과해야 해서(그 자체가 좋은
# 신호다) 여기서는 카드에 박히는 문자열만 확인한다.
_src = pathlib.Path(P.__file__).read_text(encoding="utf-8")
_body = _src.split("def render_leaders", 1)[1].split("\ndef ", 1)[0]
_body_code = "\n".join(l for l in _body.splitlines() if not l.strip().startswith("#"))
check("'규정 미달 선수 포함'이라 단정하지 않는다",
      "규정 미달" not in _body_code, "카드 문구에 남아 있음")

# (3) 순위표 최근10 형식이 열 안에서 통일된다
w_draw = WLD(win=6, loss=3, draw=1)
w_nodraw = WLD(win=8, loss=2, draw=0)
check("무승부가 있는 열은 전부 세 칸",
      P._wld(w_nodraw, True) == "8-2-0" and P._wld(w_draw, True) == "6-3-1",
      f"{P._wld(w_nodraw, True)} / {P._wld(w_draw, True)}")
check("무승부가 없는 리그는 두 칸", P._wld(w_nodraw, False) == "8-2")

# (4) 맞대결이 무승부를 빼고 말하지 않는다 — 검증은 문구 생성부만 본다
check("전적 표기에 무승부가 들어간다", "1" in P._wld(w_draw), P._wld(w_draw))


# ─────────────────────────────────────────────────────────────
# (11) 한글 채널에 못 읽는 표기를 내보내지 않는다 (v1.11m)
# ─────────────────────────────────────────────────────────────
# NPB 부문 순위·주목 선수가 소스 원문(`平良 海馬`·`マルティネス`)으로 나갔다.
# 경기장에서 같은 일이 있었고(`京セラD大阪`), 선수명은 표가 없어 그대로 나갔다.
def _raises(fn) -> bool:
    """게이트는 '통과했다'가 아니라 '깨뜨렸더니 막았다'로만 증명된다."""
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        return True
    return False


print("\n11. 못 읽는 표기")
check("한자 이름을 못 읽는 것으로 판정", not C.is_readable_ko("平良 海馬"))
check("가타카나 이름을 못 읽는 것으로 판정", not C.is_readable_ko("マルティネス"))
check("한글 이름은 읽을 수 있다", C.is_readable_ko("곽빈"))
check("라틴 약칭은 읽을 수 있다 (DeNA·SSG)",
      C.is_readable_ko("DeNA") and C.is_readable_ko("SSG"))
check("빈 값은 읽을 수 없는 것으로 본다 — 모르면 안 내보낸다",
      not C.is_readable_ko(""))
check("선수명 한글화 정책: KBO 켜짐 · NPB 꺼짐",
      C.player_names_localized(League.KBO)
      and not C.player_names_localized(League.NPB))
check("정책표에 없는 리그는 꺼진 것으로 본다 (모르면 안 내보낸다)",
      not C.player_names_localized(League.MLB))

# ─────────────────────────────────────────────────────────────
# (12) 승률 표기를 한 가지로 맞춘다 (v1.11m)
# ─────────────────────────────────────────────────────────────
# KBO는 `0.616`, NPB는 `.624`로 준다. 같은 날 두 카드가 같은 것을 다르게 말했다.
print("\n12. 승률 표기 통일")
check("앞의 0을 붙인다", C.pct_text(".624") == "0.624", C.pct_text(".624"))
check("이미 붙어 있으면 그대로", C.pct_text("0.616") == "0.616")
check("값을 바꾸지 않는다", C.pct_text("1.000") == "1.000")
check("빈 값은 빈 값", C.pct_text("") == "" and C.pct_text(None) == "")

# ─────────────────────────────────────────────────────────────
# (13) 상대전적 막대의 팀 이름은 한 줄이어야 한다 (v1.11m)
# ─────────────────────────────────────────────────────────────
# 칸이 142px인데 글자가 42px이라 4자부터 접혔다 — '요미우리'·'히로시마'가
# 실제로 두 줄로 나갔고, 접힘 게이트가 이 클래스를 안 봐서 통과했다.
print("\n13. 상대전적 팀 이름")
check("접힘 게이트가 bn 칸을 본다", "bn" in P.ONE_LINE_CLASSES)
check("3자 이하는 줄이지 않는다", 'class="bn"' in P._bn("한화"))
check("4자는 한 단계 줄인다", "b4" in P._bn("요미우리"), P._bn("요미우리"))
check("5자는 두 단계 줄인다", "b5" in P._bn("소프트뱅크"), P._bn("소프트뱅크"))
check("게이트가 실제로 잡는다 — 일부러 접힌 bn을 넣어본다",
      _raises(lambda: P.assert_no_wrapped_names(
          [{"t": "요미우리", "cls": "bn", "lines": 2, "fs": 42}])))


# ─────────────────────────────────────────────────────────────
# (14) 순위표가 실제로 처리 대상이 되는가 (v1.11n)
# ─────────────────────────────────────────────────────────────
# 순위표 예약 = 결과 카드 예약 + 10분인데, 결과 카드 예약은 그날이 다 끝났으면
# **'지금'**이다. 그래서 순위표는 매 틱 '지금+10분'으로 다시 계산되며 앞으로
# 도망간다. 앞창이 0이면 영원히 따라잡지 못한다 — 실제로 한 장도 못 나갔고,
# 큐에는 매 틱 `+10.0분`으로 얌전히 들어 있어 오류도 경고도 없었다.
from contract import ContentType  # noqa: E402
print("\n14. 순위표가 처리 대상이 되는가")
_lb = C.lookahead_for(ContentType.STANDINGS, 3600)
check("순위표 앞창이 '결과 뒤 오프셋' 이상이다",
      _lb >= P.STANDINGS_AFTER_RESULT_SECONDS,
      f"앞창 {_lb}초 < 오프셋 {P.STANDINGS_AFTER_RESULT_SECONDS}초")

# 그날 경기가 전부 끝난 순간을 만들어, 그 틱에서 실제로 due가 되는지 본다.
_day = "2026-09-03"
_end = datetime(2026, 9, 3, 21, 30, tzinfo=KST).astimezone(timezone.utc)
_gs = [mk(League.KBO, _day, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
          status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
       mk(League.KBO, _day, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
          status=Status.FINAL, score=Score(2, 4, ScoreUnit.RUNS))]
_now = _end + timedelta(minutes=30)          # 마지막 경기가 끝나고 30분 뒤
_q = P.build_queue(_gs, _now, "chtest", floor_hours=0)
_st = [i for i in _q if i.content_type is ContentType.STANDINGS]
check("경기가 다 끝난 뒤 순위표가 큐에 오른다", len(_st) == 1, f"{len(_st)}건")
if _st:
    _due = _st[0].scheduled_utc <= _now + timedelta(
        seconds=C.lookahead_for(ContentType.STANDINGS, 3600))
    check("그 틱에서 실제로 처리 대상(due)이 된다", _due,
          f"예약 {(_st[0].scheduled_utc - _now).total_seconds() / 60:+.1f}분 · 앞창 {_lb // 60}분")
    # 5분 뒤 틱에서도 계속 due여야 한다(도망가지 않는지)
    _now2 = _now + timedelta(minutes=5)
    _q2 = P.build_queue(_gs, _now2, "chtest", floor_hours=0)
    _st2 = [i for i in _q2 if i.content_type is ContentType.STANDINGS]
    check("다음 틱에서도 계속 처리 대상이다 (앞으로 도망가지 않는다)",
          bool(_st2) and _st2[0].scheduled_utc <= _now2 + timedelta(
              seconds=C.lookahead_for(ContentType.STANDINGS, 3600)))

# 아직 안 끝난 날에는 일찍 열리지 않는다 — 앞창을 연 대가가 없어야 한다.
_gs_open = [mk(League.KBO, _day, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
               status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
            mk(League.KBO, _day, 20, 0, tz="Asia/Seoul", h="SS", a="KT")]
_now3 = datetime(2026, 9, 3, 19, 0, tzinfo=KST).astimezone(timezone.utc)
_q3 = P.build_queue(_gs_open, _now3, "chtest", floor_hours=0)
_st3 = [i for i in _q3 if i.content_type is ContentType.STANDINGS]
check("아직 안 끝난 날은 순위표가 일찍 열리지 않는다",
      not _st3 or _st3[0].scheduled_utc > _now3 + timedelta(
          seconds=C.lookahead_for(ContentType.STANDINGS, 3600)),
      "경기가 남았는데 순위표가 처리 대상이 됐다")


# ─────────────────────────────────────────────────────────────
# (15) 시작 알림 — 카드가 말하는 시각 = 큐가 예약한 시각 (v1.11n)
# ─────────────────────────────────────────────────────────────
# 대표님 지적 2026-09-04: "한참 전에 다음날 경기시간을 보내주는데
# 텍스트에는 2시간 전 알림이라고 적혀 있어."
# 원인: 큐는 심야 회피까지 계산해 예약하는데, 카드는 설정값(120분)만 보고
# "2시간 전"이라 적었다. MLB 실측 — 첫 경기 03:10, 실제 예약 전날 22:00.
print("\n15. 시작 알림 — 카드와 큐가 같은 시각을 말하는가")

def _sa_case(name, gs, now):
    at = C.start_alert_at(gs)
    q = [i for i in P.build_queue(gs, now, "chtest", floor_hours=0)
         if i.content_type is ContentType.START_ALERT]
    check(f"{name}: 큐에 오른다", len(q) == 1, f"{len(q)}건")
    if q:
        check(f"{name}: 카드 계산 = 큐 예약", at == q[0].scheduled_utc,
              f"{at} vs {q[0].scheduled_utc}")
        check(f"{name}: 꼬리말에 그 시각이 있다",
              at.astimezone(KST).strftime("%H:%M") in C.start_alert_notice(gs, now),
              C.start_alert_notice(gs, now))

_n = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)
_sa_case("낮 경기", [mk(League.KBO, "2026-09-04", 18, 30, tz="Asia/Seoul", h="LG", a="OB")], _n)
_sa_case("심야 회피", [mk(League.MLB, "2026-09-04", 14, 10, tz="America/New_York",
                        h="CLE", a="DET")], _n)

# **첫 경기가 취소돼도 카드와 큐가 갈리지 않는다.**
# 큐는 예약 시각이 흔들리지 않도록 그날 전 경기 기준으로 잡는다(약점 64번).
# 카드가 '열리는 경기'만 보면 첫 경기 취소일에 둘이 어긋난다.
_cx = [mk(League.KBO, "2026-09-04", 17, 0, tz="Asia/Seoul", h="SS", a="KT",
          status=Status.CANCELED),
       mk(League.KBO, "2026-09-04", 18, 30, tz="Asia/Seoul", h="LG", a="OB")]
check("첫 경기가 취소된 날도 카드와 큐가 같은 시각",
      C.start_alert_at(_cx)
      == [i for i in P.build_queue(_cx, _n, "chtest", floor_hours=0)
          if i.content_type is ContentType.START_ALERT][0].scheduled_utc)
_mhtml = P.render_morning(_cx, "2026-09-04", now=_n)
check("그 날 모닝 카드도 같은 시각을 적는다",
      C.start_alert_at(_cx).astimezone(KST).strftime("%H:%M") in _mhtml)

# ─────────────────────────────────────────────────────────────
# (16) 시간표 목록 — 한국시각이 주, 현지시각을 매 줄에 (v1.11n)
# ─────────────────────────────────────────────────────────────
# 대표님 지시: "경기 시작시간은 한국시간을 메인으로, 작게 현지시각도."
# 전에는 목록이 KST뿐이고 현지는 맨 아래 첫 경기에만 붙었다 —
# MLB 16경기 중 15경기는 현지 몇 시인지 알 수 없었다.
print("\n16. 시간표 목록의 현지시각 병기")
# **표본이 실제로 갈리는 조합이어야 한다.** 처음엔 동부 19:10 · 중부 19:10을 썼는데
# 그건 현지시각이 같아(둘 다 19:10) 한 그룹에 들어오지도 않았다 —
# 변이시험에서 '뭉개기' 변이를 못 잡아 표본이 잘못된 것을 알았다.
# 같은 한국시각(09:10)에 동부 20:10과 중부 19:10이 함께 열리는 조합을 쓴다.
_mlb = [mk(League.MLB, "2026-09-04", 20, 10, tz="America/New_York", h="NYM", a="SF"),
        mk(League.MLB, "2026-09-04", 19, 10, tz="America/Chicago", h="HOU", a="ARI")]
_kk = {C.format_kickoff(g)[0] for g in _mlb}
_locs = {C.format_kickoff(g)[1] for g in _mlb}
check("표본이 유효하다 — 한국시각은 같고 현지시각은 다르다",
      len(_kk) == 1 and len(_locs) == 2, f"KST {_kk} · 현지 {_locs}")
_txt = P.render_start_alert(_mlb, _n, all_games=_mlb)
check("현지시각이 목록에 들어간다", "현지" in _txt, _txt[:160])
# `.index()`는 없으면 예외를 던져 **검사 전체를 죽인다**(결과 줄조차 안 찍힌다).
# 게이트가 예외로 죽으면 통과도 실패도 아니다 — 안전한 비교로 쓴다.
check("한국시각이 먼저 나온다 (현지가 앞서지 않는다)",
      "현지" in _txt and _txt.find("◆") < _txt.find("현지"),
      _txt[:160])
# 같은 한국시각이라도 구장 시간대가 다르면 현지시각이 다르다 —
# 그룹 헤더에 한 번만 쓰면 한쪽이 틀린다. 두 값이 다 나와야 한다.
check("시간대가 섞인 그룹은 현지시각을 각각 적는다 (한 값으로 뭉개지 않는다)",
      all(l and l in _txt for l in _locs), f"{_locs} / {_txt[:300]}")
# 국내 리그·NPB는 KST와 오프셋이 같아 병기하면 같은 숫자가 두 번 나온다
_kbo = [mk(League.KBO, "2026-09-04", 18, 30, tz="Asia/Seoul", h="LG", a="OB")]
check("시차가 없는 리그는 병기하지 않는다",
      "현지" not in P.render_start_alert(_kbo, _n, all_games=_kbo))
# 꼬리말이 같은 말을 두 번 하지 않는다
check("꼬리말은 현지시각을 되풀이하지 않는다",
      "시작 (" in _txt and "현지" not in _txt.split("시작 (")[-1], _txt[-140:])


if __name__ == "__main__":
    print()
    print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
