"""회귀 점검 — 기록·순위 추가가 기존 기능을 깨뜨리지 않았는지.

'손댄 것'이 아니라 '전체 기능 목록'을 다시 훑는다.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone

import contract as C
from contract import (ContentType, Game, GameMeta, GateError, KST, League, Score,
                      ScoreUnit, Status, TeamRef, UnknownStatus,
                      SOURCE_RESULTLESS_CATEGORIES, assert_no_stale_scheduled,
                      assert_transition, esc, format_kickoff, idem_key, is_late,
                      assert_league_color_contrast, assert_team_names,
                      STALE_GRACE_BY_LEAGUE, stale_grace_for,
                      parse_status, plan_send_parts, quote,
                      stale_unresolved, day_schedule_scope, start_alert_bucket)
from adapters.kbo import KboAdapter, CODE_TEAM
import pipeline as P

ok = fail = 0
def check(n, c, d=""):
    global ok, fail
    if c: ok += 1; print(f"  PASS  {n}")
    else: fail += 1; print(f"  FAIL  {n}  {d}")
def gate(n, fn, exc=(GateError, UnknownStatus, ValueError)):
    global ok, fail
    try: fn()
    except exc: ok += 1; print(f"  PASS  {n}"); return
    fail += 1; print(f"  FAIL  {n} 통과시킴")

print("1. 계약 불변식")
check("리그 15개", len(League) == 15, len(League))
check("완전성 assert 통과 (임포트 성공)", True)
check("멱등키 구분자 |", "|" in idem_key("c", ContentType.MORNING, "KBO:2026:x"))
check("멱등키 game_id 콜론 보존",
      idem_key("c", ContentType.MORNING, "KBO:2026:x").count(":") == 2)
check("plan_send_parts(11) 균등", [n for _, n in plan_send_parts(11)] == [6, 5])
check("plan_send_parts(1) 단건", len(plan_send_parts(1)) == 1)
gate("전이 final→live 차단", lambda: assert_transition(Status.FINAL, Status.LIVE),
     (C.IllegalTransition,))
check("live→canceled 허용(노게임)",
      Status.CANCELED in C.ALLOWED_TRANSITIONS[Status.LIVE])
gate("미등록 상태값", lambda: parse_status("???", League.NPB))
check("esc HTML", esc("<b>&") == "&lt;b&gt;&amp;")
check("quote 중첩 차단", "<blockquote" in quote(["a"]) )
check("리스 < 유예 (전 콘텐츠)",
      all(C.LEASE_SECONDS[c] < C.GRACE_SECONDS[c] for c in ContentType))
check("v1.10 신설 딕셔너리 완전성",
      set(C.REGULAR_SEASON_GAMES) == set(League) and set(C.LEAGUE_TEAM_COUNT) == set(League))

print("\n2. KBO 경기 수집 (실데이터)")
games = KboAdapter().fetch(2026, ["08", "09"])
check(f"경기 {len(games)}건 수집", len(games) > 100, len(games))
check("전 경기 validate 통과", all(g.validate() is None for g in games))
cx = [g for g in games if g.status is Status.CANCELED]
fin = [g for g in games if g.status is Status.FINAL]
sch = [g for g in games if g.status is Status.SCHEDULED]
print(f"        종료 {len(fin)} · 취소 {len(cx)} · 예정 {len(sch)}")
check("상태 3종 모두 존재", all([fin, cx, sch]))
check("종료 경기는 전부 점수 있음", all(g.score for g in fin))
check("예정 경기는 점수 없음", all(g.score is None for g in sch))
check("game_id 유일", len({g.game_id for g in games}) == len(games))
check("sports_day 형식", all(len(g.sports_day) == 10 for g in games))

print("\n3. 큐 생성")
now = datetime.now(timezone.utc)
q = P.build_queue(games, now, "-100test")
check(f"큐 {len(q)}건 생성", len(q) >= 0)
check("큐 시각 정렬", all(q[i].scheduled_utc <= q[i+1].scheduled_utc for i in range(len(q)-1)))
check("멱등키 유일", len({i.idem_key for i in q}) == len(q))
hi = now + timedelta(hours=30)
# 상한은 그대로다 — 너무 먼 미래 것을 미리 만들지 않는다.
check("지평선 상한 +30h 준수", all(i.scheduled_utc <= hi for i in q))

# ── 하한 검사 — 2026-09-03에 '열거'에서 '성질'로 다시 썼다 ──────────
#
# **옛 검사(3종 시절).** `_floor_free = {LEAGUE_RESULT, MORNING}`라는 열거를 두고
#   ① "하한(+6h)을 벗어난 항목은 결과 카드·모닝뿐"
#   ② "시작 알림은 하한 +6h를 지킨다" — 이름과 달리 `not in _floor_free` 전부를 봤다
# 를 확인했다. 콘텐츠가 3종 → 7종이 되면서 standings·leaderboard·night_brief·
# analysis가 열거에 없어 둘 다 실패했다. **열거를 늘리면 8종째에 또 고쳐야 한다.**
#
# **원래 지키려던 것은 두 가지였다.**
#   ① 지나간 항목이 큐에 남아 있어도 그건 '유예 안'이어야 한다
#      — 아무 근거 없이 옛 항목이 쌓이면 그게 언젠가 옛 카드로 나간다.
#   ② 시작 알림은 충분히 미래에 잡힌다
#      — 경기가 시작된 뒤에 나가는 "곧 시작" 알림은 거짓말이다.
# 둘 다 콘텐츠 목록이 아니라 **성질**이므로 성질로 적는다. 이렇게 두면
# 콘텐츠가 더 늘어도 이 검증을 다시 안 고쳐도 된다.
#
# 그리고 이 파일 자신이 남긴 교훈("시각에 따라 결과가 달라지는 검증은 통과가
# 증거가 못 된다")에 따라, 아래 성질은 **24시각 전부에서** 성립함을 확인했다
# (실데이터 KBO 238경기 · 24시각 × 매시 :00/:17/:41 = 72개 시점, 2026-09-03).
#
# ① '유예 안'의 정의는 계약의 keep_in_queue()다 — 큐 생성부가 쓰는 바로 그 함수로 본다.
#    (v1.11i: 큐는 유예 + 보고여유까지 남기고, 버림 판정·기록은 is_late() 한 곳에서 한다.
#     그래서 GRACE_SECONDS로 직접 재던 옛 검사 "지나간 항목은 각자의 유예 안에 있다"도
#     같이 다시 썼다 — 그 검사는 실측 24시각 중 **17시각에서 실패**한다(06시~자정).
#     지금 통과하고 있던 것은 '하필 그 시각에 안 돌려서'였다.)
_stragglers = [i for i in q if not C.keep_in_queue(i.scheduled_utc, now,
                                                   i.content_type)]
check("큐에 남은 항목은 전부 계약이 남기라고 한 것뿐 (keep_in_queue)",
      not _stragglers,
      str([f"{i.content_type.value} {i.scheduled_utc}" for i in _stragglers[:3]]))
# ② 남기는 쪽(keep_in_queue)과 버리는 쪽(is_late)의 경계가 어긋나면 안 된다.
#    큐가 먼저 잘라내면 is_late()는 영원히 참이 되지 않고, 사라진 발행이
#    로그에도 알림에도 안 남는다 — v1.11i에서 실제로 38,283건 중 0건이었다.
#    그래서 **유예를 막 넘긴 항목은 아직 큐에 남아 있어야** 한다.
_past = [i for i in q if i.scheduled_utc < now]
_report_gap = []
for _ct in sorted(C.QUEUED_CONTENT_TYPES, key=lambda c: c.value):
    _at = now - timedelta(seconds=C.GRACE_SECONDS[_ct] + 60)
    if not (is_late(_at, now, _ct) and C.keep_in_queue(_at, now, _ct)):
        _report_gap.append(_ct.value)
check(f"유예를 막 넘긴 항목은 큐에 남아 is_late()가 기록한다 (지나간 항목 {len(_past)}건)",
      not _report_gap, str(_report_gap))
# ③ 시작 알림만 따로 — 그 시간표의 첫 경기보다 리드타임만큼 앞서 잡힌다.
#    (심야 회피로 더 앞당겨질 수는 있어도 뒤로 밀리지는 않는다.)
_sa = [i for i in q if i.content_type is ContentType.START_ALERT]
_sa_late = []
for i in _sa:
    _first = min(g.start_utc for g in games if day_schedule_scope(g) == i.scope)
    if i.scheduled_utc > _first - timedelta(minutes=C.START_ALERT_LEAD_MINUTES):
        _sa_late.append(f"{i.scope} 예약 {i.scheduled_utc} · 첫 경기 {_first}")
check(f"시작 알림({len(_sa)}건)은 첫 경기보다 리드타임만큼 앞서 잡힌다",
      bool(_sa) and not _sa_late, str(_sa_late[:2]))

# ── 예약 시각 — 새 4종이 약속한 시각에 잡히는가 (2026-09-03 신설) ──
# 여기가 틀어지면 아무 오류 없이 엉뚱한 시각에 나가고, 창·유예 계산이
# 전부 다른 이야기를 하게 된다.
# **약속한 숫자를 여기에 직접 적는다.** 파이프라인 상수로 검사하면 상수를 바꾸는
# 순간 검사도 따라가서 아무것도 못 잡는다 — 검증이 코드를 되풀이해 읽을 뿐이 된다.
_NB_HOUR, _LB_HOUR, _AN_LEAD_H = 23, 12, 3
check("파이프라인 상수가 약속과 같다 (23시 · 12시 · -3시간)",
      (P.NIGHT_BRIEF_HOUR_KST, P.LEADERBOARD_HOUR_KST, P.ANALYSIS_LEAD_HOURS)
      == (_NB_HOUR, _LB_HOUR, _AN_LEAD_H),
      f"{P.NIGHT_BRIEF_HOUR_KST}/{P.LEADERBOARD_HOUR_KST}/{P.ANALYSIS_LEAD_HOURS}")
_at_kst = lambda i: i.scheduled_utc.astimezone(KST)
_nb = [i for i in q if i.content_type is ContentType.NIGHT_BRIEF]
check(f"나이트 브리핑은 23:00 KST ({len(_nb)}건)",
      bool(_nb) and all((_at_kst(i).hour, _at_kst(i).minute) == (_NB_HOUR, 0)
                        for i in _nb),
      str([f"{_at_kst(i):%H:%M}" for i in _nb[:3]]))
_lb = [i for i in q if i.content_type is ContentType.LEADERBOARD]
check(f"리더보드는 12:00 KST ({len(_lb)}건)",
      bool(_lb) and all((_at_kst(i).hour, _at_kst(i).minute) == (_LB_HOUR, 0)
                        for i in _lb),
      str([f"{_at_kst(i):%H:%M}" for i in _lb[:3]]))
_an = [i for i in q if i.content_type is ContentType.ANALYSIS]
_an_bad = []
for i in _an:
    _t = P.pick_analysis_game([g for g in games if g.sports_day == i.sports_day])
    if _t is None or i.scheduled_utc != _t.start_utc - timedelta(hours=_AN_LEAD_H):
        _an_bad.append(f"{i.sports_day} {i.scheduled_utc}")
check(f"분석 카드는 주목 경기 시작 -{_AN_LEAD_H}시간 ({len(_an)}건)",
      bool(_an) and not _an_bad, str(_an_bad[:2]))

# ── 발송 창 — 새 4종이 실측 최악 시계(240분)를 견디는가 ────────────
# 창이 시계 간격보다 좁으면 **오류 없이 조용히** 아무것도 안 나간다.
# 실측 최악 간격은 240분(깃허브 자동 시계)이다.
_WORST_TICK_SECONDS = 240 * 60
_DEFAULT_LOOKAHEAD = 60 * 60                   # tick.LOOKAHEAD_SECONDS 기본값
_narrow = [f"{ct.value} {C.send_window_seconds(ct, _DEFAULT_LOOKAHEAD) // 60}분"
           for ct in sorted(C.QUEUED_CONTENT_TYPES, key=lambda c: c.value)
           if C.send_window_seconds(ct, _DEFAULT_LOOKAHEAD) < _WORST_TICK_SECONDS]
check("큐에 오르는 7종 전부가 실측 최악 간격(240분)을 견딘다", not _narrow, str(_narrow))
check("새로 켠 4종도 예외가 아니다",
      all(C.send_window_seconds(ct, _DEFAULT_LOOKAHEAD) >= _WORST_TICK_SECONDS
          for ct in (ContentType.STANDINGS, ContentType.LEADERBOARD,
                     ContentType.NIGHT_BRIEF, ContentType.ANALYSIS)))

# ── 정확도 미루기 (contract.defer_for_precision) ───────────────────
# 앞창을 넓힌 대가로 카드가 목표보다 일찍 나갈 수 있다. 그 정확도를 되찾는 규칙인데,
# **두 방향 모두 틀리면 위험하다** — 너무 잘 미루면 뜸한 시계에서 영영 안 나가고,
# 전혀 안 미루면 앞창을 넓힌 대가만 치른다.
_DN = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
_DA = _DN + timedelta(minutes=60)
check("시계가 촘촘하면 미룬다 (5분 시계 · 목표 60분 뒤)",
      C.defer_for_precision(_DA, _DN, ContentType.ANALYSIS, 5 * 60, 10 * 60))
check("시계가 뜸하면 안 미룬다 (240분 시계 — 미루면 그대로 유실이다)",
      not C.defer_for_precision(_DA, _DN, ContentType.ANALYSIS, 240 * 60, 240 * 60))
check("목표를 이미 지났으면 안 미룬다",
      not C.defer_for_precision(_DN - timedelta(minutes=1), _DN,
                                ContentType.ANALYSIS, 5 * 60, 10 * 60))
check("시계를 모르면(0) 안 미룬다 — 모를 때는 보내는 쪽이 안전하다",
      not C.defer_for_precision(_DA, _DN, ContentType.ANALYSIS, 0, 0))
check("앞창이 잠긴 콘텐츠(모닝·결과·순위·나이트)는 미루지 않는다",
      not any(C.defer_for_precision(_DA, _DN, ct, 5 * 60)
              for ct in (ContentType.MORNING, ContentType.LEAGUE_RESULT,
                         ContentType.STANDINGS, ContentType.NIGHT_BRIEF)))

print("\n4. 카드 렌더 (기존 2종)")
day = max(g.sports_day for g in fin)
today = [g for g in games if g.sports_day == day]
w, h, b = P.render_png(P.render_result(today, day), pathlib.Path("dryrun/result.png"))
check(f"결과 카드 {w}x{h} {b//1024}KB", w == 1080 and h <= 1280)
nxt = min((g.sports_day for g in sch), default=day)
w, h, b = P.render_png(P.render_morning([g for g in games if g.sports_day == nxt], nxt),
                       pathlib.Path("dryrun/morning.png"))
check(f"모닝 카드 {w}x{h} {b//1024}KB", w == 1080 and h <= 1280)

print("\n5. 텍스트 알림")
# 시작 알림은 '그 리그의 하루 시간표' 한 통이다 (v1.11c).
# 전에는 같은 시각 경기만 묶어 시각마다 보냈고, 실측 하루 26건이 나왔다.
bk = day_schedule_scope(sch[0])
same = [g for g in sch if day_schedule_scope(g) == bk]
txt = P.render_start_alert(same)
check(f"시작 알림 {len(same)}경기 한 통", "<blockquote" in txt and len(txt) < 4096)
check("경기가 빠짐없이 들어감",
      all(C.team_name(g.home) in txt and C.team_name(g.away) in txt for g in same))
check("첫 경기까지 남은 시간을 적는다", "시작" in txt)
check("하루 한 통 — 시각이 여럿이어도 scope는 하나",
      len({day_schedule_scope(g) for g in same}) == 1)
kst, loc = format_kickoff(sch[0])
check("KBO는 현지시간 병기 안 함", loc is None)

print("\n6. 묵은 '예정' 격리 (v1.11c — KBL EASL 13건 사고)")
# 사고: KBL이 EASL 결과를 관리하지 않아 10개월 지난 경기가 계속 '예정'으로 남았다.
# 그대로 두면 묵은 경기가 오늘의 모닝 카드에 실린다 (KBO 취소 7건 누락과 같은 계열).
# 고친 뒤 두 가지를 동시에 지켜야 한다:
#   ① 지난 경기는 격리한다      → 사실 오류 차단
#   ② 미래 편성은 그대로 내보낸다 → 정보 누락 금지 (여기가 더 놓치기 쉽다)
_NOW = datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)

def _mk(hours_ago, status=Status.SCHEDULED, cat="EA"):
    sc = Score(88, 77, ScoreUnit.POINTS) if status in (Status.FINAL, Status.LIVE) else None
    g = Game(league=League.KBL, season="2025-26", source_key=f"R{hours_ago}-{status.value}",
             home=TeamRef(League.KBL, "SK"), away=TeamRef(League.KBL, "LG"),
             start_utc=_NOW - timedelta(hours=hours_ago), home_tz="Asia/Seoul",
             status=status, score=sc, venue=None, meta=GameMeta(season_category=cat))
    g.validate()
    return g

check("유예 안(5시간 전)은 통과", not stale_unresolved([_mk(5)], now_utc=_NOW))
check("유예 밖(7시간 전)은 잡힘", len(stale_unresolved([_mk(7)], now_utc=_NOW)) == 1)
check("미래 경기는 통과", not stale_unresolved([_mk(-10)], now_utc=_NOW))
check("종료·취소·연기는 오래돼도 통과",
      not stale_unresolved([_mk(9999, Status.FINAL), _mk(9999, Status.CANCELED),
                            _mk(9999, Status.POSTPONED)], now_utc=_NOW))
check("10개월 묵은 '진행중'도 잡힘",
      len(stale_unresolved([_mk(7000, Status.LIVE)], now_utc=_NOW)) == 1)
try:
    assert_no_stale_scheduled([_mk(7)], now_utc=_NOW)
    check("묵은 경기는 GateError로 차단", False, "안 막았다")
except GateError:
    check("묵은 경기는 GateError로 차단", True)
check("KBL EASL이 결과 미제공 구간으로 등록됨",
      "EA" in SOURCE_RESULTLESS_CATEGORIES.get(League.KBL, frozenset()))
# NPB는 '영원히 안 채워짐'이 아니라 '늦게 채워짐'이다. 둘을 같이 다루면
# 매일 밤 NPB 6경기가 경보로 떠서 진짜 결함이 소음에 묻힌다 (2026-08-28 실측).
_npb = Game(league=League.NPB, season="2026", source_key="NPBSTALE",
            home=TeamRef(League.NPB, "YOG"), away=TeamRef(League.NPB, "HAN"),
            start_utc=_NOW - timedelta(hours=7), home_tz="Asia/Tokyo",
            status=Status.SCHEDULED, score=None, venue=None, meta=GameMeta())
_npb.validate()
check("NPB는 7시간 뒤에도 경보 아님 (소스 익일 갱신)",
      not stale_unresolved([_npb], now_utc=_NOW))
check("NPB도 19시간이면 경보",
      len(stale_unresolved([_npb], now_utc=_NOW + timedelta(hours=12))) == 1)
check("리그별 유예가 기본값과 다름",
      stale_grace_for(League.NPB) > stale_grace_for(League.KBO))
check("수집된 KBO 경기에 묵은 '예정' 없음", not stale_unresolved(games))

print("\n7. 리그 확장 안전 (v1.11c — 렌더러 매핑 누락 사고)")
# 사고: LEAGUE_COLORS에 15개 리그 색을 정의해두고 _card()가 리그를 안 넘겨서
# 전 리그 카드가 KBO 색으로 나갔다. 배지 아이콘도 야구공 하나가
# 농구·배구·축구·e스포츠에 전부 박혔다. **표가 있어도 쓰지 않으면 없는 것과 같다.**
try:
    P.assert_league_render_maps()
    check("전 리그가 렌더러 매핑(라벨·색·아이콘)에 등록됨", True)
except GateError as e:
    check("전 리그가 렌더러 매핑에 등록됨", False, str(e)[:80])
try:
    assert_team_names()
    check("팀 표시명이 카드 폭에 맞음", True)
except GateError as e:
    check("팀 표시명이 카드 폭에 맞음", False, str(e)[:80])
try:
    # 승자 색이 리그색이 된 뒤로, 리그를 추가할 때마다 '그 색이 읽히는가'가 위험이 된다
    assert_league_color_contrast()
    check("전 리그 색이 카드 면에서 읽힘 (대비 3.0+)", True)
except GateError as e:
    check("전 리그 색이 카드 면에서 읽힘", False, str(e)[:80])
# 실제로 리그가 카드에 반영되는지 — 표 등록만으로는 부족하다(위 사고가 그것이었다)
_lg_html = P.render_result([g for g in games if g.status is Status.FINAL][:2],
                           [g for g in games if g.status is Status.FINAL][0].sports_day)
check("카드에 리그색이 실제로 주입됨", "--lg:" in _lg_html and "--lg-ink:" in _lg_html)

print("\n8. 다중 리그 멱등키 (v1.11c — 리그가 서로를 덮어쓰던 사고)")
# 사고: 멱등키에 리그가 없어서 아홉 리그의 모닝 브리핑이 전부 같은 키였다.
# 하나가 나가면 나머지 여덟은 '이미 보냄'으로 버려진다 →
# **KBO만 나가고 MLB·NPB·K리그·LCK는 영영 안 나간다.**
# 검증 215건이 못 잡은 이유: 큐를 한 리그로만 시험했기 때문이다.
_QNOW = datetime(2026, 8, 28, 20, 0, tzinfo=timezone.utc)

def _mkgame(lg, code_h, code_a, hh, day="2026-08-29"):
    from zoneinfo import ZoneInfo
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, 30,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    # 시즌 표기는 리그마다 다르다 (계약이 강제한다: 야구는 '2026', 농구·배구는 '2026-27')
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season, source_key=f"{lg.value}-{day}-{hh}",
             home=TeamRef(lg, code_h), away=TeamRef(lg, code_a),
             start_utc=st.astimezone(timezone.utc), home_tz="Asia/Seoul",
             status=Status.SCHEDULED, score=None, venue=None,
             # V리그는 남녀부 혼입을 계약이 막는다 — 리그에 맞는 gender를 넣어야 한다
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.validate()
    return g

_a = [_mkgame(League.KBO, "LG", "OB", 18)]
_b = [_mkgame(League.KBL, "SK", "LG", 18)]
_qa = P.build_queue(_a, _QNOW, "-100test", floor_hours=0)
_qb = P.build_queue(_b, _QNOW, "-100test", floor_hours=0)
check("두 리그 모두 큐가 생김", len(_qa) > 0 and len(_qb) > 0, f"{len(_qa)}/{len(_qb)}")
# ── 2026-09-03 갱신 (콘텐츠 3종 → 7종) ────────────────────────────
# **옛 기대:** "리그가 다르면 멱등키가 하나도 안 겹친다".
# **왜 못 쓰게 됐나:** 나이트 브리핑은 **전 리그 통합 1건**이라 scope가 `ALL:날짜`다.
# build_queue는 리그별로 불리므로 KBO도 KBL도 **일부러** 같은 키를 만들고,
# 대장(ledger)이 키로 접어 실제 발송은 1회다. 여기서의 겹침은 사고가 아니라 설계다.
#
# **원래 지키려던 것(위 사고):** 리그가 서로를 '이미 보냄'으로 덮어쓰지 않는다.
# 기대를 그냥 뒤집으면 그 사고가 되살아나므로, 통합 카드와 리그 카드를 갈라서
# 각각 **더 강한** 조건을 건다 — 리그 카드는 겹침 0, 통합 카드는 **완전히 동일**.
_nb_a = {i.idem_key for i in _qa if i.content_type is ContentType.NIGHT_BRIEF}
_nb_b = {i.idem_key for i in _qb if i.content_type is ContentType.NIGHT_BRIEF}
_ka = {i.idem_key for i in _qa} - _nb_a
_kb = {i.idem_key for i in _qb} - _nb_b
check("나이트 브리핑을 뺀 멱등키는 리그가 다르면 겹치지 않는다", not (_ka & _kb),
      str(sorted(_ka & _kb)[:2]))
# 여기가 진짜 위험이다 — 통합 카드 키가 리그마다 조금이라도 다르면
# 대장이 못 접어서 **리그 수만큼** 같은 카드가 채널에 나간다(아홉 리그면 하루 아홉 번).
check("나이트 브리핑은 리그가 달라도 완전히 같은 키 (다르면 리그 수만큼 발송된다)",
      bool(_nb_a) and _nb_a == _nb_b,
      f"KBO={sorted(_nb_a)} KBL={sorted(_nb_b)}")
# 같은 리그·같은 날 안에서도 종류가 다르면 키가 달라야 한다. 안 그러면
# 결과 카드가 나간 뒤 순위표가 '이미 보냄'으로 조용히 사라진다.
_slot: dict = {}
for i in _qa + _qb:
    _slot.setdefault((i.league, i.sports_day), {}) \
         .setdefault(i.content_type, set()).add(i.idem_key)
_ct_clash = [(str(lg), d, ca.value, cb.value)
             for (lg, d), m in _slot.items()
             for ca in m for cb in m
             if ca.value < cb.value and (m[ca] & m[cb])]
check("같은 리그·같은 날이어도 콘텐츠 종류가 다르면 키가 다르다",
      not _ct_clash, str(_ct_clash[:2]))
# ── 기록이 없는 리그에는 기록 콘텐츠를 올리지 않는다 (2026-09-03 신설) ──
# 순위·리더보드·분석은 RecordBook이 있어야 그려진다. 기록 어댑터가 없는 리그에
# 올리면 render_for가 매 틱 None을 돌려주고 로그에 "만들 내용 없음"이 쌓인다 —
# 진짜 결함이 그 소음에 묻힌다. (KBL은 기록 소스가 없다.)
_RECORD_ONLY = {ContentType.STANDINGS, ContentType.LEADERBOARD, ContentType.ANALYSIS}
check("기록 소스가 있는 리그는 KBO뿐 (표가 늘면 아래 검사도 함께 넓혀야 한다)",
      P.RECORD_SOURCE_LEAGUES == frozenset({League.KBO}),
      str(sorted(l.value for l in P.RECORD_SOURCE_LEAGUES)))
check("기록이 없는 리그(KBL)에는 순위·리더보드·분석이 큐에 오르지 않는다",
      not [i for i in _qb if i.content_type in _RECORD_ONLY],
      str(sorted({i.content_type.value for i in _qb if i.content_type in _RECORD_ONLY})))
# 반대 방향도 고정한다 — 위 검사는 '아무것도 안 만들면' 저절로 통과하기 때문이다.
# (실데이터 KBO 큐 q에는 세 가지가 실제로 올라와 있어야 한다.)
check("기록이 있는 리그(KBO)에는 세 가지가 실제로 오른다",
      _RECORD_ONLY <= {i.content_type for i in q},
      str(sorted(c.value for c in (_RECORD_ONLY - {i.content_type for i in q}))))
_ma = [i for i in _qa if i.content_type is ContentType.MORNING]
_mb = [i for i in _qb if i.content_type is ContentType.MORNING]
check("같은 날 모닝 브리핑도 리그별로 다른 키",
      bool(_ma) and bool(_mb) and _ma[0].idem_key != _mb[0].idem_key)
check("모닝 키에 리그가 들어있다",
      bool(_ma) and League.KBO.value in _ma[0].idem_key, _ma[0].idem_key if _ma else "")
# 같은 시각에 시작하는 다른 리그 경기가 한 알림으로 섞이면 안 된다
check("같은 날이어도 알림 범위는 리그별로 분리",
      day_schedule_scope(_a[0]) != day_schedule_scope(_b[0]),
      day_schedule_scope(_a[0]))
# 섞인 목록을 넘기면 막아야 한다 (실수로 합쳐 부르면 키가 엉킨다)
gate("리그를 섞어 부르면 차단",
     lambda: P.build_queue(_a + _b, _QNOW, "-100test", floor_hours=0))
# 경기가 없는 날은 모닝을 만들지 않는다 (V리그가 7개월 뒤 시즌인데 매일 잡혔다)
_far = [_mkgame(League.VLEAGUE_M, "HC", "KAL", 14, day="2027-03-31")]
check("그날 경기가 없으면 모닝 브리핑을 만들지 않는다",
      not [i for i in P.build_queue(_far, _QNOW, "-100test", floor_hours=0)
           if i.content_type is ContentType.MORNING])

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
