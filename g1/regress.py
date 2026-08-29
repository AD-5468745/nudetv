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
                      assert_transition, esc, format_kickoff, idem_key,
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
lo, hi = now + timedelta(hours=6), now + timedelta(hours=30)
# 상한은 그대로다 — 너무 먼 미래 것을 미리 만들지 않는다.
check("지평선 상한 +30h 준수", all(i.scheduled_utc <= hi for i in q))
# 하한은 콘텐츠에 따라 다르다 (v1.11c).
# 결과 카드만 과거 마감을 허용한다: 소스가 결과를 늦게 채우면 마감이 지난 뒤에야
# 카드를 만들 수 있는데, 하한으로 막으면 **그날 결과가 통째로 사라진다.**
# 대신 유예(6시간)를 넘은 것은 is_late()가 버리고, 이미 보낸 것은 멱등키가 막는다.
_past = [i for i in q if i.scheduled_utc < lo]
check("과거 항목은 결과 카드뿐",
      all(i.content_type is ContentType.LEAGUE_RESULT for i in _past),
      str([i.content_type.value for i in _past[:3]]))
check("과거 결과 카드도 유예 안에 있다",
      all((now - i.scheduled_utc).total_seconds() <= C.GRACE_SECONDS[i.content_type]
          for i in _past))
check("나머지는 하한 +6h 준수",
      all(i.scheduled_utc >= lo for i in q if i.content_type is not ContentType.LEAGUE_RESULT))

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
_ka = {i.idem_key for i in _qa}
_kb = {i.idem_key for i in _qb}
check("리그가 다르면 멱등키가 겹치지 않는다", not (_ka & _kb),
      str(sorted(_ka & _kb)[:2]))
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
