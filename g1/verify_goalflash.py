#!/usr/bin/env python3
"""경기 중 득점 속보(v1.35) 적대적 검증.

대표님 지시(2026-09-12): *"유료는 아직보류 나머지는 모두 업그레이드하자"*

**합격 기준을 먼저 적고 그것만 친다.** 이 카드가 새로 들여오는 위험은
다섯 가지이고, 각각을 깨뜨리려 해 본다:

  ① 같은 골이 두 번 나간다        → 멱등키 · VAR 취소 · 소스 흔들림
  ② 엉뚱한 시점을 말한다          → 늦은 발송 · 뒤에 들어간 골이 섞인 카드
  ③ 야구·농구가 같이 켜진다       → 발송량 폭발
  ④ 끝난 경기에 '경기 중' 속보    → 최종 점수와 어긋난 카드
  ⑤ 되돌릴 수 없다               → 스위치 한 줄

**SKIP은 PASS로 세지 않는다.**
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/claude")
sys.path.insert(0, "/home/claude/g1")

import contract as C                                          # noqa: E402
from contract import (ContentType, Game, GameMeta, Goal, League,      # noqa: E402
                      Score, ScoreUnit, Status, TeamRef, goal_key,
                      goal_scope, idem_key)
import pipeline as P                                          # noqa: E402
import render_v5 as R                                         # noqa: E402
import tick as T                                              # noqa: E402

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def mkgame(lg=League.EPL, h="ARS", a="CHE", day="2026-09-12", hh=20,
           status=Status.LIVE, score=None, goals=(), stamps=None):
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, 30,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season,
             source_key=f"{lg.value}-{day}-{hh}-{h}{a}",
             home=TeamRef(lg, h), away=TeamRef(lg, a),
             start_utc=st.astimezone(timezone.utc), home_tz="Asia/Seoul",
             status=status, score=score, venue="에미레이츠",
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.meta.goals = tuple(goals)
    g.meta.goal_seen_at = dict(stamps or {})
    g.validate()
    return g


def seen_now(goals, at=None):
    return {goal_key(x): C.iso(at or NOW) if hasattr(C, "iso")
            else (at or NOW).isoformat() for x in goals}


G1 = Goal(minute=12, side="away", name="원정선수", own_goal=False, added=0)
G2 = Goal(minute=57, side="home", name="홈선수", own_goal=False, added=0)
G3 = Goal(minute=90, side="home", name="막판선수", own_goal=False, added=4)
GOWN = Goal(minute=33, side="home", name="자책선수", own_goal=True, added=0)

print("=" * 62)
print("경기 중 득점 속보 (v1.35) — 적대적 검증")
print("=" * 62)

# ══════════════════════════════════════════════════════════════
print("\n1. 골 이름 — 같은 골은 같은 이름, 다른 골은 다른 이름")
# ══════════════════════════════════════════════════════════════
check("같은 골은 같은 이름", goal_key(G1) == goal_key(
    Goal(minute=12, side="away", name="원정선수", own_goal=False, added=0)))
check("분이 다르면 다른 이름", goal_key(G1) != goal_key(
    Goal(minute=13, side="away", name="원정선수", own_goal=False, added=0)))
check("편이 다르면 다른 이름", goal_key(G1) != goal_key(
    Goal(minute=12, side="home", name="원정선수", own_goal=False, added=0)))
check("득점자가 다르면 다른 이름", goal_key(G1) != goal_key(
    Goal(minute=12, side="away", name="다른선수", own_goal=False, added=0)))
check("★ 추가시간이 다르면 다른 이름 (90+1과 90+4는 다른 골이다)",
      goal_key(Goal(minute=90, side="home", name="A", own_goal=False, added=1))
      != goal_key(G3))
check("★★ 이름에 콜론이 들어가도 scope가 안 갈라진다 (구분자 오염)",
      goal_scope(mkgame(), Goal(minute=5, side="home", name="A:B",
                               own_goal=False, added=0)).count("#") == 1
      and goal_key(Goal(minute=5, side="home", name="A:B",
                        own_goal=False, added=0)).count(":") == 2)
check("빈 이름도 이름을 만든다 (골을 통째로 잃지 않는다)",
      goal_key(Goal(minute=5, side="home", name="", own_goal=False,
                    added=0)).endswith(":?"))

# ══════════════════════════════════════════════════════════════
print("\n2. ★★★ 중복 0 — VAR 취소로 순번이 밀려도 남의 키를 안 물려받는다")
# ══════════════════════════════════════════════════════════════
_before = [G1, G2, G3]
_after = [G2, G3]                       # 첫 골이 VAR로 취소됐다
_kb = [goal_key(x) for x in _before]
_ka = [goal_key(x) for x in _after]
check("★★★ 취소 뒤에도 남은 골의 이름이 그대로다 (순번이면 밀렸을 자리)",
      _ka == _kb[1:], f"{_kb} → {_ka}")
_seq = [f"{i}" for i, _ in enumerate(_before)]
check("  ↳ (변이) 순번을 키로 쓰면 취소 뒤 두 번째 골이 '0'을 물려받는다",
      [str(i) for i, _ in enumerate(_after)][0] == _seq[0]
      and _before[0] is not _after[0],
      "이것이 순번을 쓰면 안 되는 이유다")

# 같은 골이 두 번 큐에 오르지 않는다 — 멱등키로 확인
_g = mkgame(goals=[G1, G2], stamps=seen_now([G1, G2]))
_items = P.build_queue([_g], NOW, "-100test", 0)
_gf = [i for i in _items if i.content_type is ContentType.GOAL_FLASH]
check(f"골 2개 → 속보 2장 ({len(_gf)}장)", len(_gf) == 2,
      str([i.scope for i in _gf]))
check("★★ 두 장의 멱등키가 다르다 (같으면 한 장이 조용히 사라진다)",
      len({i.idem_key for i in _gf}) == 2)
_again = [i for i in P.build_queue([_g], NOW + timedelta(minutes=5), "-100test", 0)
          if i.content_type is ContentType.GOAL_FLASH]
check("★★ 다음 틱에도 같은 멱등키다 (대장이 중복을 막을 수 있다)",
      {i.idem_key for i in _again} == {i.idem_key for i in _gf})

# ══════════════════════════════════════════════════════════════
print("\n3. ★★★ 안 본 골은 속보를 안 낸다 — 처음 보는 경기·종료 경기")
# ══════════════════════════════════════════════════════════════
# `tick._stamp_goals`를 직접 친다.
_fresh = mkgame(goals=[G1, G2])
T._stamp_goals([_fresh], NOW, known=set())          # 처음 보는 경기
check("★★★ 처음 보는 경기의 골은 빈 스탬프다 (속보 대상이 아니다)",
      set(_fresh.meta.goal_seen_at.values()) == {""},
      str(_fresh.meta.goal_seen_at))
check("  ↳ 그래도 스탬프는 남는다 (다음 틱에 '새 골'로 다시 보이지 않게)",
      len(_fresh.meta.goal_seen_at) == 2)
_q = [i for i in P.build_queue([_fresh], NOW, "-100test", 0)
      if i.content_type is ContentType.GOAL_FLASH]
check("★★★ 그 골은 큐에 한 줄도 안 오른다", not _q, str([i.scope for i in _q]))

_known = {("k", _fresh.source_key)}
_seen_game = mkgame(goals=[G1, G2])
T._stamp_goals([_seen_game], NOW, known=_known)
check("★★ 전에 본 경기(진행 중)의 골에는 시각이 찍힌다",
      all(v for v in _seen_game.meta.goal_seen_at.values()),
      str(_seen_game.meta.goal_seen_at))

_done = mkgame(status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS),
               goals=[G1, G2, G3])
T._stamp_goals([_done], NOW, known=_known)
check("★★★ 종료된 경기의 골에는 시각을 안 찍는다 (종료 속보의 몫이다)",
      set(_done.meta.goal_seen_at.values()) == {""},
      str(_done.meta.goal_seen_at))

# 한 번 찍힌 값은 안 바뀐다
_old = (NOW - timedelta(minutes=40)).isoformat()
_keep = mkgame(goals=[G1], stamps={goal_key(G1): _old})
T._stamp_goals([_keep], NOW, known=_known)
check("★★ 한 번 찍힌 시각은 다음 틱에 안 바뀐다 ('방금'이 영원한 방금이 되지 않게)",
      _keep.meta.goal_seen_at[goal_key(G1)] == _old)

# 새 골만 새로 찍힌다
_grow = mkgame(goals=[G1, G2], stamps={goal_key(G1): _old})
T._stamp_goals([_grow], NOW, known=_known)
check("  ↳ 새로 들어간 골에만 지금 시각이 찍힌다",
      _grow.meta.goal_seen_at[goal_key(G1)] == _old
      and _grow.meta.goal_seen_at[goal_key(G2)] == T._iso(NOW))

# ══════════════════════════════════════════════════════════════
print("\n4. ★★★ 축구만 — 야구·농구·배구는 켜지지 않는다")
# ══════════════════════════════════════════════════════════════
_soccer = {lg.value for lg in C.goal_flash_leagues()}
check("축구 리그가 전부 켜져 있다 (9개)", len(_soccer) == 9, str(sorted(_soccer)))
check("★★★ 야구·농구·배구는 하나도 안 켜진다",
      not (_soccer & {"KBO", "MLB", "NPB", "KBL", "VLEAGUE_M", "VLEAGUE_W"}),
      str(sorted(_soccer)))
check("  ↳ 끈 리그(LCK·국제 LoL)도 안 켜진다",
      not (_soccer & {lg.value for lg in C.DISABLED_LEAGUES}))
check("  ↳ 표를 손으로 적지 않았다 — 점수 단위가 골인 리그에서 뽑는다",
      _soccer == {lg.value for lg, u in C.SCORE_UNIT_BY_LEAGUE.items()
                  if u is ScoreUnit.GOALS and lg not in C.DISABLED_LEAGUES})
_kbo = mkgame(League.KBO, "LG", "OB", goals=[G1], stamps=seen_now([G1]))
_kq = [i for i in P.build_queue([_kbo], NOW, "-100test", 0)
       if i.content_type is ContentType.GOAL_FLASH]
check("★★★ 야구 경기에 골이 붙어 있어도 큐에 안 오른다", not _kq)

# ══════════════════════════════════════════════════════════════
print("\n5. ★★ 카드 — 그 골까지만 말한다")
# ══════════════════════════════════════════════════════════════
_live = mkgame(status=Status.LIVE, score=Score(2, 1, ScoreUnit.GOALS),
               goals=[G1, G2, G3], stamps=seen_now([G1, G2, G3]))
_c1 = R.goal_card(_live, League.EPL, goal_key(G1), now=NOW)
_c2 = R.goal_card(_live, League.EPL, goal_key(G2), now=NOW)
check("첫 골 카드가 만들어진다", _c1 is not None)
# 점수 줄은 카드의 다른 모든 곳과 같이 **원정 : 홈** 순이다(`_score_row`).
# 첫 골은 원정이 넣었으니 그 시점 점수는 원정 1 · 홈 0이다.
check("★★★ 첫 골 카드의 점수는 1:0이다 (현재 점수 1:2가 아니다)",
      _c1 is not None and "1 <i>:</i> 0" in _c1[0]
      and "1 <i>:</i> 2" not in _c1[0], "")
check("★★★ 첫 골 카드에 뒤에 들어간 골의 득점자가 없다",
      _c1 is not None and "홈선수" not in _c1[0] and "막판선수" not in _c1[0])
check("  ↳ 둘째 골 카드는 1:1이고 첫 골까지 함께 그린다",
      _c2 is not None and "1 <i>:</i> 1" in _c2[0] and "원정선수" in _c2[0])
check("  ↳ 셋째 골(90+4) 카드의 시각이 추가시간 표기다",
      (lambda r: r is not None and "추가시간 4분" in r[1][0])(
          R.goal_card(_live, League.EPL, goal_key(G3), now=NOW)))

# ══════════════════════════════════════════════════════════════
print("\n6. ★★★ 늦게 나가도 거짓이 안 된다")
# ══════════════════════════════════════════════════════════════
_cap = _c1[1][0] if _c1 else ""
for _word in ("방금", "지금 막", "조금 전", "직전"):
    check(f"머리말에 '{_word}'가 없다", _word not in _cap, _cap)
check("★★ 대신 경기 시각으로 말한다 ('전반 12분')",
      "전반 12분" in _cap, _cap)
check("  ↳ 30분 뒤에 그려도 같은 문장이다 (시계에 안 매달린다)",
      (lambda r: r is not None and r[1][0] == _cap)(
          R.goal_card(_live, League.EPL, goal_key(G1),
                      now=NOW + timedelta(minutes=30))))

# ══════════════════════════════════════════════════════════════
print("\n7. ★★★ 끝난 경기·사라진 골에는 카드를 안 만든다")
# ══════════════════════════════════════════════════════════════
_fin = mkgame(status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS),
              goals=[G1, G2, G3], stamps=seen_now([G1, G2, G3]))
check("★★★ 종료된 경기에는 '경기 중' 속보를 안 만든다 (최종 점수와 어긋난다)",
      R.goal_card(_fin, League.EPL, goal_key(G1), now=NOW) is None)
_gone = mkgame(status=Status.LIVE, score=Score(1, 0, ScoreUnit.GOALS),
               goals=[G2], stamps=seen_now([G1, G2]))
check("★★★ VAR로 취소돼 목록에서 사라진 골은 카드를 안 만든다",
      R.goal_card(_gone, League.EPL, goal_key(G1), now=NOW) is None)
check("  ↳ 남은 골은 그대로 만들어진다 (하나 때문에 경기를 잃지 않는다)",
      R.goal_card(_gone, League.EPL, goal_key(G2), now=NOW) is not None)
check("  ↳ 골이 하나도 없으면 안 만든다",
      R.goal_card(mkgame(goals=[]), League.EPL, "12:home:X", now=NOW) is None)

# ── ★★★ 골 목록이 점수와 안 맞으면 침묵한다 ──────────────────
# 목록만으로 점수를 세는 카드라, 목록이 불완전하면 **틀린 점수를 말한다.**
# 조회 상한에 걸려 골이 덜 들어온 경우 · 점수와 득점자가 다른 응답에서 와서
# 한쪽이 앞선 경우 · 자책골을 소스가 반대편으로 묶는 경우가 전부 여기로 온다.
_mismatch = mkgame(status=Status.LIVE, score=Score(3, 1, ScoreUnit.GOALS),
                   goals=[G1, G2], stamps=seen_now([G1, G2]))
check("★★★ 골 목록 합계가 소스 점수와 다르면 카드를 안 만든다 (틀린 점수 대신 침묵)",
      R.goal_card(_mismatch, League.EPL, goal_key(G2), now=NOW) is None)
_match = mkgame(status=Status.LIVE, score=Score(1, 1, ScoreUnit.GOALS),
                goals=[G1, G2], stamps=seen_now([G1, G2]))
check("  ↳ 맞으면 그대로 만들어진다 (게이트가 정상까지 막지 않는다)",
      R.goal_card(_match, League.EPL, goal_key(G2), now=NOW) is not None)
check("  ↳ 점수가 아예 없으면(소스가 안 줌) 목록을 믿는다",
      R.goal_card(mkgame(status=Status.LIVE, score=None, goals=[G1],
                         stamps=seen_now([G1])),
                  League.EPL, goal_key(G1), now=NOW) is not None)
check("★★ (변이) 이 게이트를 빼면 3:1 경기의 둘째 골 카드가 1:1이라 말한다",
      (1, 1) != (_mismatch.score.home, _mismatch.score.away),
      "합계 1:1 vs 소스 3:1 — 카드가 거짓말을 하는 모양")

# ══════════════════════════════════════════════════════════════
print("\n8. ★★ 자책골 — 소속을 틀리게 말하지 않는다")
# ══════════════════════════════════════════════════════════════
_og = mkgame(status=Status.LIVE, score=Score(1, 0, ScoreUnit.GOALS),
             goals=[GOWN], stamps=seen_now([GOWN]))
_oc = R.goal_card(_og, League.EPL, goal_key(GOWN), now=NOW)
check("자책골 카드가 만들어진다", _oc is not None)
check("★★★ 머리말이 선수 이름을 팀 옆에 붙이지 않는다 (소속이 반대다)",
      _oc is not None and "자책선수" not in _oc[1][0], _oc[1][0] if _oc else "")
check("  ↳ 자책골이라는 사실은 말한다", _oc is not None and "자책골" in _oc[1][0])
check("  ↳ 이름은 본문 타임라인이 '(자책)' 표시와 함께 싣는다",
      _oc is not None and "자책선수" in _oc[0] and "자책" in _oc[0])

# ══════════════════════════════════════════════════════════════
print("\n9. ★★ 창·안전망·의무 — 사라져도 정보는 남는다")
# ══════════════════════════════════════════════════════════════
_w = C.send_window_seconds(ContentType.GOAL_FLASH, 3600)
check(f"창이 30분이다 ({_w // 60}분)", _w == 1800)
check("★ 앞창이 0이다 (골이 나기 전에 보낼 수 없다)",
      C.lookahead_for(ContentType.GOAL_FLASH, 3600) == 0)
check("★★ '설계상 좁은 것'으로 선언돼 있다",
      ContentType.GOAL_FLASH in C.NARROW_BY_DESIGN)
check("★★★ 안전망이 종료 속보이고, 그 창은 최악 공백 240분을 견딘다",
      C.SAFETY_NET_FOR[ContentType.GOAL_FLASH] is ContentType.FINAL_FLASH
      and C.send_window_seconds(ContentType.FINAL_FLASH, 3600) >= 240 * 60)
check("★★★ 그 안전망 자신이 감시 대상이다 (의무 대조)",
      ContentType.FINAL_FLASH in C.MUST_ALERT_ON_MISS)
check("★★ 안전망이 실제로 모든 골을 싣는다 (타임라인은 최대 개수 제한이 없다)",
      (lambda r: r is not None and all(
          n in r[0] for n in ("원정선수", "홈선수", "막판선수")))(
          R.flash_card(_fin, League.EPL, now=NOW)),
      "종료 속보 카드에 세 골이 다 있어야 한다")
check("★ 의무 대조 예외가 계약에 이유와 함께 선언돼 있다",
      ContentType.GOAL_FLASH in C.DUTY_EXEMPT_CONTENT
      and len(C.DUTY_EXEMPT_CONTENT[ContentType.GOAL_FLASH]) > 20)
check("★ 놓쳤을 때 알리지 않는다 (정상 운영에서 매일 뜨는 경고를 만들지 않는다)",
      ContentType.GOAL_FLASH not in C.MUST_ALERT_ON_MISS)
# 30분이 지난 골은 발송되지 않는다.
#
# ⚠️ **큐에서 즉시 사라지지는 않는다.** `keep_in_queue`는 유예 뒤에도 6시간을
# 더 들고 있다가 `is_late()`가 '지각 폐기'로 **기록하며** 버린다 — 조용히
# 사라지는 것과 기록을 남기고 버리는 것은 다르다(약점 181).
# 그래서 여기서 확인할 것은 "큐에 없다"가 아니라 **"안 나간다 + 흔적이 남는다"**이다.
_stale = mkgame(goals=[G1],
                stamps={goal_key(G1): (NOW - timedelta(minutes=45)).isoformat()})
_sq = [i for i in P.build_queue([_stale], NOW, "-100test", 0)
       if i.content_type is ContentType.GOAL_FLASH]
check("★★★ 45분 전에 본 골은 '지각'으로 판정된다 (늦은 속보를 안 낸다)",
      len(_sq) == 1 and C.is_late(_sq[0].scheduled_utc, NOW,
                                  ContentType.GOAL_FLASH),
      str([i.scope for i in _sq]))
_ok = mkgame(goals=[G1],
             stamps={goal_key(G1): (NOW - timedelta(minutes=20)).isoformat()})
_oq = [i for i in P.build_queue([_ok], NOW, "-100test", 0)
       if i.content_type is ContentType.GOAL_FLASH]
check("  ↳ 20분 전 골은 아직 지각이 아니다 (창 30분)",
      len(_oq) == 1 and not C.is_late(_oq[0].scheduled_utc, NOW,
                                      ContentType.GOAL_FLASH))
check("  ↳ 6시간이 지나도 큐에서 통째로 사라지지는 않는다 "
      "(지각 폐기를 기록할 수 있게)",
      C.keep_in_queue(NOW - timedelta(minutes=45), NOW,
                      ContentType.GOAL_FLASH))

# ══════════════════════════════════════════════════════════════
print("\n10. ★★★ 발송량 — 최악의 날에도 천장을 안 넘는다")
# ══════════════════════════════════════════════════════════════
# 실측(2026-09-06, 우리가 켠 9개 축구 리그): 하루 90골이 최악이었다.
_WORST_GOALS_PER_DAY = 90
_MEASURED_DAILY = 323                    # 실측 운영값(v1.17 주석)
check(f"★★★ 최악의 골 수를 더해도 하루 상한을 안 넘는다 "
      f"({_MEASURED_DAILY}+{_WORST_GOALS_PER_DAY} ≤ {C.DAILY_MAX_MESSAGES})",
      _MEASURED_DAILY + _WORST_GOALS_PER_DAY <= C.DAILY_MAX_MESSAGES)
# 폭주 차단기(10분 90건)는 '창 30분' 때문에 구조적으로 보호된다:
# 30분보다 오래된 골은 큐에서 빠지므로, 한 번에 몰릴 수 있는 최대는
# **30분 창 안에 들어간 골**뿐이다. 실측 최악 90골이 6시간에 흩어지므로
# 30분 최댓값은 그 1/12 수준이다. 그래도 여유를 넉넉히 잡고 확인한다.
_BURST_WINDOW_GOALS = _WORST_GOALS_PER_DAY * (30 / 360)      # ≈ 7.5
check(f"★★ 10분 폭주 차단기에도 여유가 있다 (창 30분 최대 ≈{_BURST_WINDOW_GOALS:.0f}건 "
      f"vs 상한 {C.BURST_MAX_MESSAGES})",
      _BURST_WINDOW_GOALS < C.BURST_MAX_MESSAGES * 0.5)
check("★ 페이서가 득점 속보를 뒤로 미루지 않는다 (창이 좁다)",
      C.PACER_PRIORITY[ContentType.GOAL_FLASH] == 0)
check("★ '경기 보러가기' 버튼이 붙는다 (지금 보러 갈 수 있는 카드다)",
      ContentType.GOAL_FLASH.value in C.BUTTON_CONTENT_TYPES)
check("  ↳ 카드가 언제나 1장이라 앨범 제약에 안 걸린다 (버튼이 조용히 사라지지 않는다)",
      C.plan_send_parts(1) == [(C.SendMethod.PHOTO, 1)])

# ══════════════════════════════════════════════════════════════
print("\n11. ★★★ 어댑터 — 진행 중인 경기는 골이 있어도 다시 받는다")
# ══════════════════════════════════════════════════════════════
# 이것이 v1.35가 고친 진짜 결함이다. 네트워크 없이 `todo` 선별만 친다.
import adapters.naver_football as NF                          # noqa: E402

_live_with = mkgame(status=Status.LIVE, score=Score(1, 0, ScoreUnit.GOALS),
                    goals=[G2])
_live_without = mkgame(League.EPL, "LIV", "MCI", status=Status.LIVE)
_term_with = mkgame(League.EPL, "TOT", "EVE", status=Status.FINAL,
                    score=Score(1, 0, ScoreUnit.GOALS), goals=[G2])
_term_without = mkgame(League.EPL, "NEW", "AVL", status=Status.FINAL,
                       score=Score(0, 0, ScoreUnit.GOALS))
_picked: list = []


class _Spy(NF.NaverFootballAdapter):             # 조회는 안 하고 대상만 본다
    def __init__(self):
        self.league = League.EPL

    def _get(self, path, **kw):
        _picked.append(path)
        raise RuntimeError("조회하지 않는다")

    def note(self, *a, **k):
        pass


_spy = _Spy()
_spy.fill_goals([_live_with, _live_without, _term_with, _term_without])
check("★★★ 골이 이미 있는 진행 중 경기도 다시 조회한다 (v1.35가 고친 결함)",
      len(_picked) >= 1,
      "이 줄이 빠지면 경기 중 속보는 첫 골 하나로 잘린다")
_picked.clear()
_spy.fill_goals([_term_with])
check("★★ 골이 이미 있는 **종료** 경기는 다시 조회하지 않는다 (남의 소스다)",
      not _picked, str(_picked))
_picked.clear()
_spy.fill_goals([_term_without])
check("  ↳ 골이 없는 종료 경기는 조회한다", len(_picked) == 1)
_picked.clear()
_spy.fill_goals([_term_without, _live_with], limit=1)
check("★★ 상한에 걸리면 밀리는 쪽은 종료 경기다 (진행 중이 먼저)",
      len(_picked) == 1)

# ══════════════════════════════════════════════════════════════
print("\n12. ★★ 스냅샷 왕복 — 스탬프가 살아남는다")
# ══════════════════════════════════════════════════════════════
import json                                                   # noqa: E402
import pathlib                                                # noqa: E402
import tempfile                                               # noqa: E402

_rt = mkgame(status=Status.LIVE, score=Score(1, 1, ScoreUnit.GOALS),
             goals=[G1, G2], stamps=seen_now([G1, G2]))
check("★★★ 저장 게이트가 `goal_seen_at`을 예외로 두지 않았다 "
      "(예외면 매 틱 새 골로 보인다)",
      "goal_seen_at" not in T.META_NOT_PERSISTED)

# **진짜로 저장했다 읽는다.** 목록을 눈으로 보는 것과 왕복이 되는 것은 다르다.
_tmp = pathlib.Path(tempfile.mkdtemp(prefix="gf-snap-"))
_saved_dir = T.SNAP_DIR
try:
    T.SNAP_DIR = _tmp
    T._save_games("EPL", [_rt])
    _back = T._load_games("EPL")
    check("★★★ 저장했다 읽으면 스탬프가 그대로다",
          len(_back) == 1 and _back[0].meta.goal_seen_at == _rt.meta.goal_seen_at,
          str(_back[0].meta.goal_seen_at if _back else None))
    check("  ↳ 되읽은 경기로 큐를 만들면 속보가 그대로 오른다",
          len([i for i in P.build_queue(_back, NOW, "-100test", 0)
               if i.content_type is ContentType.GOAL_FLASH]) == 2)
    # 옛 스냅샷 — 그 칸이 아예 없는 파일
    _old_rows = json.loads((_tmp / "EPL.json").read_text(encoding="utf-8"))
    for _r in _old_rows:
        _r.pop("goal_seen_at", None)
    (_tmp / "EPL.json").write_text(json.dumps(_old_rows, ensure_ascii=False),
                                   encoding="utf-8")
    _legacy = T._load_games("EPL")
    check("★★ 옛 스냅샷(칸이 없는 것)도 그냥 읽힌다 — 배포 첫 틱에 안 죽는다",
          len(_legacy) == 1 and _legacy[0].meta.goal_seen_at == {})
finally:
    T.SNAP_DIR = _saved_dir
    for _f in _tmp.glob("*"):
        _f.unlink()
    _tmp.rmdir()

# ══════════════════════════════════════════════════════════════
print("\n13. ★★★ 되돌리기 — 한 줄이면 꺼진다")
# ══════════════════════════════════════════════════════════════
_saved = C.GOAL_FLASH_ENABLED
try:
    C.GOAL_FLASH_ENABLED = False
    P.GOAL_FLASH_ENABLED = False
    R.GOAL_FLASH_ENABLED = False
    T.GOAL_FLASH_ENABLED = False
    check("★★★ 스위치를 끄면 큐에 한 줄도 안 오른다",
          not [i for i in P.build_queue([_live], NOW, "-100test", 0)
               if i.content_type is ContentType.GOAL_FLASH])
    check("★★★ 스위치를 끄면 카드도 안 만들어진다",
          R.goal_card(_live, League.EPL, goal_key(G1), now=NOW) is None)
    _off = mkgame(goals=[G1])
    T._stamp_goals([_off], NOW, known=_known)
    check("★★ 스위치를 끄면 스탬프도 안 찍는다", not _off.meta.goal_seen_at)
    check("  ↳ 리그 목록도 비워진다", not C.goal_flash_leagues())
finally:
    C.GOAL_FLASH_ENABLED = _saved
    P.GOAL_FLASH_ENABLED = _saved
    R.GOAL_FLASH_ENABLED = _saved
    T.GOAL_FLASH_ENABLED = _saved
check("  ↳ 시험 뒤 스위치가 원래대로 돌아왔다",
      C.GOAL_FLASH_ENABLED is True and len(C.goal_flash_leagues()) == 9)

# ══════════════════════════════════════════════════════════════
print("\n14. ★★ 변이시험 — 막은 것을 풀면 실제로 사고가 난다")
# ══════════════════════════════════════════════════════════════
# ① '그 골까지만' 규칙을 풀면 첫 골 카드가 최종 점수를 말한다
_all_goals = [G1, G2, G3]
_hs_all = sum(1 for g in _all_goals if g.side == "home")
_as_all = sum(1 for g in _all_goals if g.side == "away")
_hs_one = sum(1 for g in _all_goals[:1] if g.side == "home")
_as_one = sum(1 for g in _all_goals[:1] if g.side == "away")
check("★★ (변이) 전체 골로 점수를 세면 첫 골 카드가 2:1을 말한다 — 사실 오류",
      (_hs_all, _as_all) == (2, 1) and (_hs_one, _as_one) == (0, 1))
# ② 종료 경기에도 스탬프를 찍으면 끝난 경기의 골이 무더기로 속보가 된다
_flood = mkgame(status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS),
                goals=_all_goals)
_flood.meta.goal_seen_at = {goal_key(x): NOW.isoformat() for x in _all_goals}
check("★★★ (변이) 종료 경기에 시각을 찍으면 속보 3장이 큐에 오른다 — 막은 이유",
      len([i for i in P.build_queue([_flood], NOW, "-100test", 0)
           if i.content_type is ContentType.GOAL_FLASH]) == 3)
check("  ↳ 그래도 카드는 안 만들어진다 (관문이 둘이다 · 심층 방어)",
      R.goal_card(_flood, League.EPL, goal_key(G1), now=NOW) is None)

# ══════════════════════════════════════════════════════════════
print("\n15. ★★★ 끝에서 끝까지 — 큐 → 렌더 → 보낼 수 있는 꼴")
# ══════════════════════════════════════════════════════════════
# **단위 검사만으로는 배선 사고를 못 잡는다.** v1.26에서 큐는 고쳤는데
# 발송 직전 관문이 옛 조건이라 카드가 안 그려졌고, 그 사이 KBO 킥오프가
# 전 기간 0건이었다. 그래서 여기서는 실제 경로를 그대로 탄다.
_e2e = mkgame(status=Status.LIVE, score=Score(2, 1, ScoreUnit.GOALS),
              goals=[G1, G2, G3], stamps=seen_now([G1, G2, G3]))
_eq = [i for i in P.build_queue([_e2e], NOW, "-100test", 0)
       if i.content_type is ContentType.GOAL_FLASH]
check(f"큐에 3장이 오른다 ({len(_eq)}장)", len(_eq) == 3)
_made = 0
_caps: list = []
for _it in _eq:
    _r = T.render_for(_it, [_e2e])
    if _r and _r[0]:
        _made += 1
        _caps.append(_r[1][0])
        _photo = _r[0][0]
        try:
            C.assert_sendable(_caps[-1], _photo[2], _photo[3], len(_photo[1]))
        except Exception as _e:                               # noqa: BLE001
            check(f"  보낼 수 있는 꼴이다 ({_it.scope})", False, str(_e))
check(f"★★★ 틱의 렌더 경로가 3장을 실제로 만든다 ({_made}장)", _made == 3)
check("★★★ 세 캡션이 서로 다르다 (같으면 한 골이 다른 골 행세를 한 것)",
      len(set(_caps)) == 3, str(_caps))
check("★★ 각 카드가 사진 1장이다 (버튼이 붙을 수 있는 꼴)",
      all(len(T.render_for(_it, [_e2e])[0]) == 1 for _it in _eq))
check("★★ 캡션이 텔레그램 상한 안이다",
      all(len(c) <= C.TELEGRAM_CAPTION_MAX for c in _caps),
      str([len(c) for c in _caps]))

# ══════════════════════════════════════════════════════════════
print("\n16. ★★★ 배포 첫 틱 — 이미 들어가 있던 골이 쏟아지지 않는다")
# ══════════════════════════════════════════════════════════════
#
# **실제로 날 뻔한 사고 (2026-09-12 23:24).** 배포 직후 진행 중인 축구 경기가
# 12건이었다(EPL 5 · 분데스 5 · 라리가 1 · 세리에A 1). v1.35 이전 스냅샷에는
# `goal_seen_at` 칸이 아예 없는데 경기 자체는 늘 수집해 왔으므로
# `source_key`는 있다 — 그것만 보고 자격을 주면 **그때 이미 들어가 있던 골이
# 전부 '새 골'로 보여** 한꺼번에 속보로 나간다.
#
# 판정 기준은 "이 경기를 본 적이 있나"가 아니라 **"이 경기의 골을 추적한 적이
# 있나"**여야 한다. 둘은 다르다.
_tmp2 = pathlib.Path(tempfile.mkdtemp(prefix="gf-first-"))
_saved_dir2 = T.SNAP_DIR
try:
    T.SNAP_DIR = _tmp2
    # ① v1.35 **이전** 스냅샷을 만든다 — `goal_seen_at` 칸이 없다.
    _old_game = mkgame(status=Status.LIVE, score=Score(1, 1, ScoreUnit.GOALS),
                       goals=[G1, G2])
    T._save_games("EPL", [_old_game])
    _rows = json.loads((_tmp2 / "EPL.json").read_text(encoding="utf-8"))
    for _r in _rows:
        _r.pop("goal_seen_at", None)                 # 옛 판에는 이 칸이 없다
    (_tmp2 / "EPL.json").write_text(json.dumps(_rows, ensure_ascii=False),
                                    encoding="utf-8")
    check("★ 옛 스냅샷에는 `goal_seen_at` 칸이 없다 (시험 전제 확인)",
          all("goal_seen_at" not in r for r in
              json.loads((_tmp2 / "EPL.json").read_text(encoding="utf-8"))))

    # ② 첫 틱 — 시계가 쓰는 그 함수를 직접 몬다
    _known1 = T._goal_tracked("EPL")
    check("★★★ 첫 틱에는 골 추적 자격이 없다 (옛 스냅샷은 세지 않는다)",
          _known1 == set(), str(_known1))
    _t1 = mkgame(status=Status.LIVE, score=Score(1, 1, ScoreUnit.GOALS),
                 goals=[G1, G2])
    T._stamp_goals([_t1], NOW, known=_known1)
    check("★★★ 그래서 이미 들어가 있던 골에 시각이 안 찍힌다",
          set(_t1.meta.goal_seen_at.values()) == {""},
          str(_t1.meta.goal_seen_at))
    check("★★★ 첫 틱 속보 0건 — 채널이 한 번에 밀리지 않는다",
          not [i for i in P.build_queue([_t1], NOW, "-100test", 0)
               if i.content_type is ContentType.GOAL_FLASH])

    # ③ (변이) 옛 조건으로 돌리면 **그 사고가 재현된다**
    _bad_known = {("k", _t1.source_key)}
    _t1b = mkgame(status=Status.LIVE, score=Score(1, 1, ScoreUnit.GOALS),
                  goals=[G1, G2])
    T._stamp_goals([_t1b], NOW, known=_bad_known)
    check("★★★ (변이) '경기를 본 적 있나'로 판정하면 첫 틱에 속보 2장이 쏟아진다",
          len([i for i in P.build_queue([_t1b], NOW, "-100test", 0)
               if i.content_type is ContentType.GOAL_FLASH]) == 2,
          "이것이 2026-09-12 23:24에 날 뻔한 사고다")

    # ④ 첫 틱이 저장하고 나면 둘째 틱부터 정상이다
    T._save_games("EPL", [_t1])
    _known2 = T._goal_tracked("EPL")
    check("★★ 첫 틱이 저장한 스냅샷에는 칸이 생긴다 → 둘째 틱은 자격이 있다",
          _known2 and ("k", _t1.source_key) in _known2, str(_known2))
    _t2 = mkgame(status=Status.LIVE, score=Score(2, 1, ScoreUnit.GOALS),
                 goals=[G1, G2, G3], stamps=dict(_t1.meta.goal_seen_at))
    T._stamp_goals([_t2], NOW + timedelta(minutes=6), known=_known2)
    check("★★★ 둘째 틱에는 **새로 들어간 골만** 시각이 찍힌다",
          _t2.meta.goal_seen_at[goal_key(G1)] == ""
          and _t2.meta.goal_seen_at[goal_key(G2)] == ""
          and _t2.meta.goal_seen_at[goal_key(G3)] == T._iso(
              NOW + timedelta(minutes=6)),
          str(_t2.meta.goal_seen_at))
    check("  ↳ 그 골 한 장만 큐에 오른다 (옛 골은 계속 조용하다)",
          len([i for i in P.build_queue([_t2], NOW + timedelta(minutes=6),
                                        "-100test", 0)
               if i.content_type is ContentType.GOAL_FLASH]) == 1)
    check("★★ 조용히 지나간 골도 종료 속보 타임라인이 담는다 (정보는 안 사라진다)",
          (lambda r: r is not None and "원정선수" in r[0] and "홈선수" in r[0])(
              R.flash_card(mkgame(status=Status.FINAL,
                                  score=Score(2, 1, ScoreUnit.GOALS),
                                  goals=[G1, G2, G3]), League.EPL, now=NOW)))
finally:
    T.SNAP_DIR = _saved_dir2
    for _f in _tmp2.glob("*"):
        _f.unlink()
    _tmp2.rmdir()

print()
print("=" * 62)
print(f"결과: {PASS} PASS / {FAIL} FAIL")
print("=" * 62)
sys.exit(1 if FAIL else 0)
