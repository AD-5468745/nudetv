"""선발 라인업 · 득점자 검증 (v1.17, 2026-09-08).

대표님 지시 둘이 이 파일의 대상이다:
  · *"경기시작전에 알려줄 출장 라인업이 가능하면 좋아"*   → 라인업 카드
  · *"경기결과 시안은 합격, 저것도 적용하자"*              → 종료 속보의 명단 블록

**네트워크에 기대지 않는다.** 실측으로 확인한 응답 구조를 그대로 본뜬 모의
응답을 넣고, "이 모양으로 오면 옳게 읽는가"를 친다. 소스가 흔들리는 날
검증까지 같이 흔들리면 안 된다.

이 파일이 지키는 사실 넷 — **전부 실측으로 확인한 것이다**(2026-09-08):
  ① 줄 순서가 팀마다 반대다 (원정은 공격수가 첫 줄로 오기도 한다)
  ② 라인업 안의 `goal` 필드는 **전부 0**이다 — 골은 `game.scorers`에서만 온다
  ③ 라인업은 킥오프 약 1시간 전에야 채워진다 (18시간 전엔 전부 빈칸)
  ④ 자책골은 **넣은 팀이 아니라 득점한 팀** 쪽에 기록된다
     (49경기 대조: 골 개수 = 스코어, 자책골 6건 포함, 불일치 0)

마지막 절은 **변이시험**이다 — 일부러 깨뜨려서 위 검사가 실제로 잡는지 본다.
깨뜨려도 통과하는 검사는 검사가 아니다.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "adapters"))

import contract as C
from contract import (ContentType, Game, GameMeta, Goal, League, Score,
                      ScoreUnit, Status, TeamRef)
from adapters.naver_football import NaverFootballAdapter

ok = fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}  {detail}")


def mkgame(*, hh: int = 23, status=Status.SCHEDULED, score=None,
           day: str = "2026-09-08") -> Game:
    from zoneinfo import ZoneInfo
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, 30,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    g = Game(league=League.EPL, season="2026-27",
             source_key=f"EPL-{day}-{hh}-abc123", home=TeamRef(League.EPL, "ARS"),
             away=TeamRef(League.EPL, "CHE"),
             start_utc=st.astimezone(timezone.utc), home_tz="Europe/London",
             status=status, score=score, venue="에미리츠 스타디움",
             meta=GameMeta())
    g.validate()
    return g


def _row(*names) -> list:
    """실제 응답의 선발 한 줄.

    ⚠️ **`substitute`를 빼면 가짜가 계약보다 좁아진다.** 소스는 선발에
    `substitute: "0"`을 주고 우리 코드가 그것으로 선발/교체를 가른다.
    이 필드 없이 만든 표본은 선발을 전부 '교체 명단'으로 읽게 해서,
    검사가 멀쩡한 코드를 틀렸다고 잡는다(실제로 한 번 그렇게 걸렸다).
    """
    return [{"name": n, "playerId": f"p{i}", "goal": 0, "substitute": "0"}
            for i, n in enumerate(names)]


# 4-2-3-1 — GK가 첫 줄인 **정방향** 응답
FWD = [_row("라야"), _row("화이트", "콘사", "마갈량이스", "칼라피오리"),
       _row("라이스", "루이스 스켈리"), _row("사카", "외데고르", "촐리스"),
       _row("하베르츠")]
# 같은 팀을 **뒤집어서** 준 응답 (실측: 원정 팀이 이렇게 온다)
REV = list(reversed(FWD))


class Fake(NaverFootballAdapter):
    """네트워크 대신 미리 정해 둔 응답을 돌려준다."""

    def __init__(self, payloads: dict, league=League.EPL) -> None:
        super().__init__(league)
        self._payloads = payloads
        self.calls: list = []

    def _get(self, path: str, label: str = "") -> dict:      # type: ignore[override]
        self.calls.append(path)
        if path not in self._payloads:
            raise KeyError(path)
        v = self._payloads[path]
        if isinstance(v, Exception):
            raise v
        return v


def lineup_payload(home_rows, away_rows, *, hf="4231", af="4231") -> dict:
    return {"result": {"lineUpData": {"lineup": {
        "home": {"formation": hf, "players": {"lineup": home_rows,
                                              "substitutes": _row("교체1", "교체2")}},
        "away": {"formation": af, "players": {"lineup": away_rows,
                                              "substitutes": _row("교체3")}}}}}}


def game_payload(scorers: dict) -> dict:
    return {"result": {"game": {"scorers": scorers}}}


print("=" * 62)
print("선발 라인업 · 득점자 검증 (v1.17)")
print("=" * 62)

NOW = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)

# ── 1. 줄 방향 — 뒤집혀 와도 GK가 첫 줄이 된다 ────────────────
print("\n1. 줄 방향 — 팀마다 반대로 오는 응답을 바로 세우는가")
_g = mkgame()
_ad = Fake({f"/schedule/games/abc123/lineup": lineup_payload(FWD, REV)})
_ad.fill_lineups([_g], NOW)
check("양 팀 명단이 다 찼다", bool(_g.meta.lineup), str(_g.meta.lineup))
_h = _g.meta.lineup["home"]["rows"]
_a = _g.meta.lineup["away"]["rows"]
check("정방향으로 온 팀은 그대로다 (GK 첫 줄)", _h[0] == ["라야"], str(_h[0]))
check("★ 뒤집혀 온 팀도 GK가 첫 줄이 된다", _a[0] == ["라야"], str(_a[0]))
check("줄 인원이 포메이션과 맞는다 (1-4-2-3-1)",
      [len(r) for r in _a] == [1, 4, 2, 3, 1], str([len(r) for r in _a]))
check("교체 명단은 섞이지 않는다 (선발 11명뿐)",
      sum(len(r) for r in _h) == 11 and "교체1" not in sum(_h, []),
      str(sum(len(r) for r in _h)))

# **둘 다 맞거나 둘 다 안 맞으면 손대지 않는다** — 모를 때 뒤집는 것이 더 나쁘다.
print("\n1-B. 판정할 수 없으면 뒤집지 않는다")
# 1-4-1-4-1 — **뒤집어도 줄 인원이 똑같은** 포메이션이라 방향을 알 수 없다.
_sym = [_row("가"), _row("나", "다", "라", "마"), _row("바"),
        _row("사", "아", "자", "차"), _row("카")]
_g2 = mkgame()
_ad2 = Fake({"/schedule/games/abc123/lineup":
             lineup_payload(_sym, _sym, hf="4141", af="4141")})
_ad2.fill_lineups([_g2], NOW)
check("대칭이라 방향을 알 수 없으면 원래 순서를 지킨다",
      _g2.meta.lineup["away"]["rows"][0] == ["가"],
      str(_g2.meta.lineup["away"]["rows"][0]))
_g3 = mkgame()
_ad3 = Fake({"/schedule/games/abc123/lineup":
             lineup_payload(FWD, REV, hf="", af="")})
_ad3.fill_lineups([_g3], NOW)
check("포메이션이 없으면 추측해서 뒤집지 않는다",
      _g3.meta.lineup["away"]["rows"][0] == ["하베르츠"],
      str(_g3.meta.lineup["away"]["rows"][0]))

# ── 2. 반쯤 찬 명단은 명단이 아니다 ──────────────────────────
print("\n2. 반쯤 찬 명단 — '모른다'와 '없다'를 뭉개지 않는가")
_g4 = mkgame()
_half = [_row("가"), _row("나", "다")]
Fake({"/schedule/games/abc123/lineup": lineup_payload(_half, FWD)}
     ).fill_lineups([_g4], NOW)
check("★ 한쪽이 11명이 안 되면 통째로 안 담는다 (빈칸 카드 방지)",
      _g4.meta.lineup is None, str(_g4.meta.lineup))
_g5 = mkgame()
Fake({"/schedule/games/abc123/lineup": {"result": {"lineUpData": {}}}}
     ).fill_lineups([_g5], NOW)
check("아직 발표 전이면 None이다 (빈 dict가 아니다)", _g5.meta.lineup is None)
_g6 = mkgame()
_ad6 = Fake({"/schedule/games/abc123/lineup": TimeoutError("끊김")})
_ad6.fill_lineups([_g6], NOW)
check("조회가 실패해도 예외로 리그를 죽이지 않는다", _g6.meta.lineup is None)
check("실패를 조용히 넘기지 않고 알림에 남긴다",
      any("라인업" in n for n in _ad6.notices), str(_ad6.notices))

# ── 3. 조회 창 — 볼 값어치 있는 경기만 본다 ──────────────────
print("\n3. 조회 창 — 먼 미래 경기를 헛되이 두드리지 않는가")
_far = mkgame(day="2026-09-20")                       # 12일 뒤
_near = mkgame(day="2026-09-08")                      # 오늘 밤
_pay = {"/schedule/games/abc123/lineup": lineup_payload(FWD, FWD)}
_ad7 = Fake(_pay)
_ad7.fill_lineups([_far], NOW)
check("★ 킥오프 100분 밖 경기는 조회하지 않는다 (실측: 그전엔 있어도 없다)",
      not _ad7.calls, str(_ad7.calls))
_ad8 = Fake(_pay)
_ad8.fill_lineups([_near], NOW)
check("창 안에 든 경기는 조회한다", len(_ad8.calls) == 1, str(_ad8.calls))
# 이미 채운 경기를 다시 두드리면 남의 소스에 요청이 매 틱 쌓인다.
_ad9 = Fake(_pay)
_ad9.fill_lineups([_near], NOW)
check("★ 이미 받은 명단은 다시 조회하지 않는다", not _ad9.calls, str(_ad9.calls))
# 상한 — 한 틱에 몇 경기까지
_many = [mkgame(day="2026-09-08", hh=23) for _ in range(20)]
for _i, _m in enumerate(_many):
    _m.source_key = f"EPL-x-{_i}"
_ad10 = Fake({f"/schedule/games/{_i}/lineup": lineup_payload(FWD, FWD)
              for _i in range(20)})
_ad10.fill_lineups(_many, NOW, limit=3)
check("한 틱 조회 상한을 지킨다", len(_ad10.calls) == 3, str(len(_ad10.calls)))

# ── 4. 득점자 — 라인업의 goal이 아니라 scorers에서 ──────────
print("\n4. 득점자 — 유럽 골이 통째로 비어 있던 자리 (v1.16 결함)")
_fin = mkgame(status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS))
_ad11 = Fake({"/schedule/games/abc123": game_payload({
    "home": [{"time": 25, "addedTime": 0, "playerName": "하베르츠", "ownGoal": False},
             {"time": 90, "addedTime": 6, "playerName": "외데고르", "ownGoal": False}],
    "away": [{"time": 2, "addedTime": 0, "playerName": "로저스", "ownGoal": True}]})})
_ad11.fill_goals([_fin])
check("★ 득점자가 채워진다 (v1.16에서는 한 건도 없었다)",
      len(_fin.meta.goals) == 3, str(_fin.meta.goals))
check("골 개수가 스코어와 맞는다",
      (sum(1 for x in _fin.meta.goals if x.side == "home"),
       sum(1 for x in _fin.meta.goals if x.side == "away")) == (2, 1))
check("추가시간을 버리지 않는다 (90+6)",
      any(x.minute == 90 and x.added == 6 for x in _fin.meta.goals))
check("자책골 표시를 소스가 준 그대로 담는다 (추론하지 않는다)",
      any(x.own_goal for x in _fin.meta.goals))
check("시각 순으로 정렬된다",
      [x.minute for x in _fin.meta.goals] == [2, 25, 90])
_g12 = mkgame(status=Status.FINAL, score=Score(0, 0, ScoreUnit.GOALS))
_ad12 = Fake({"/schedule/games/abc123": game_payload({"home": [], "away": []})})
_ad12.fill_goals([_g12])
check("0:0 경기는 득점이 비어 있다 (없는 것을 지어내지 않는다)",
      not _g12.meta.goals)
_pre = mkgame()
_ad13 = Fake({"/schedule/games/abc123": game_payload({"home": [], "away": []})})
_ad13.fill_goals([_pre])
check("시작 전 경기는 득점을 조회하지 않는다", not _ad13.calls)

# ── 5. 카드 — 킥오프 전에만, 명단이 있을 때만 ───────────────
print("\n5. 카드 — 언제 만들고 언제 안 만드는가")
import render_v5 as R                                          # noqa: E402

_c1 = mkgame(hh=23)
Fake({"/schedule/games/abc123/lineup": lineup_payload(FWD, REV)}
     ).fill_lineups([_c1], NOW)
_r = R.lineup_card(_c1, League.EPL, now=NOW)
check("명단이 있으면 라인업 카드를 만든다", bool(_r))
check("카드에 포메이션이 찍힌다", _r and "4-2-3-1" in _r[0])
check("카드에 선발 22명이 다 들어간다",
      _r and all(n in _r[0] for n in ("라야", "하베르츠", "루이스 스켈리")))

_after = mkgame(hh=23)
_after.meta.lineup = _c1.meta.lineup
_late = _after.start_utc + timedelta(minutes=1)
check("★ 경기가 시작된 뒤에는 라인업 카드를 만들지 않는다",
      R.lineup_card(_after, League.EPL, now=_late) is None)
_noline = mkgame(hh=23)
check("명단이 없으면 만들지 않는다 (빈 카드 금지)",
      R.lineup_card(_noline, League.EPL, now=NOW) is None)
_done = mkgame(hh=23, status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS))
_done.meta.lineup = _c1.meta.lineup
check("끝난 경기에는 '시작 전' 카드를 만들지 않는다",
      R.lineup_card(_done, League.EPL, now=NOW) is None)

print("\n5-B. 종료 속보 — 명단에 득점 분이 붙는가")
_f2 = mkgame(hh=1, status=Status.FINAL, score=Score(2, 1, ScoreUnit.GOALS))
_f2.meta.lineup = _c1.meta.lineup
_f2.meta.goals = (Goal(minute=25, side="home", name="하베르츠"),
                  Goal(minute=50, side="home", name="외데고르"),
                  Goal(minute=2, side="away", name="화이트", own_goal=True))
_fr = R.flash_card(_f2, League.EPL, now=NOW)
check("종료 속보에 명단 블록이 붙는다", bool(_fr) and "하베르츠" in _fr[0])
check("득점한 선발 옆에 분이 붙는다", bool(_fr) and "25′" in _fr[0])
# 자책골은 소스가 **득점한 팀** 쪽에 적는다(실측 49경기 대조). 그래서 그 이름을
# 그 팀 명단에서 찾아 옆에 붙이면 **엉뚱한 선수가 골을 넣은 것**이 된다.
# 블록만 따로 뽑아 확인한다 — 카드 전체에는 득점 타임라인이 같은 분을 또 쓴다.
_blk = R._lineup_body(_f2, League.EPL, with_goals=True)
check("★ 자책골은 이름 옆에 붙이지 않는다 (넣은 팀 쪽에 적히므로 거짓이 된다)",
      "화이트</span>" in _blk.replace("<span class='gl'>", "|"),
      _blk[_blk.find("화이트") - 30:_blk.find("화이트") + 60])
check("그래도 자책골이 사라지지는 않는다 (타임라인이 담는다)",
      bool(_fr) and "화이트" in _fr[0])

# ── 6. 큐 — 예약 시각이 '명단을 본 시각'인가 ────────────────
print("\n6. 큐 — 시계가 아니라 데이터가 예약을 만드는가")
import pipeline as P                                           # noqa: E402

def _queue(game) -> list:
    # floor_hours=0 — 틱의 수집 잡이 부르는 방식과 같게 한다.
    return [i for i in P.build_queue([game], NOW, "-100t", floor_hours=0)
            if i.content_type is ContentType.LINEUP]


_q1 = mkgame(hh=23)
_q1.meta.lineup = _c1.meta.lineup
_q1.meta.lineup_seen_at = NOW.isoformat()
_items = _queue(_q1)
check("명단을 본 경기가 큐에 오른다", len(_items) == 1, str(len(_items)))
check("★ 예약 시각이 곧 '명단을 본 시각'이다",
      _items and abs((_items[0].scheduled_utc - NOW).total_seconds()) < 1)
check("★ 본 그 틱에 바로 발송 대상이 된다 (시계 공백에 안 죽는 이유)",
      C.keep_in_queue(NOW, NOW, ContentType.LINEUP))

_q2 = mkgame(hh=23)
_q2.meta.lineup = _c1.meta.lineup
check("명단만 있고 본 시각이 없으면 큐에 안 오른다", not _queue(_q2))
_q3 = mkgame(hh=23)
_q3.meta.lineup_seen_at = NOW.isoformat()
check("본 시각만 있고 명단이 없으면 큐에 안 오른다", not _queue(_q3))

# 너무 늦게 뜬 명단 — '경기 시작 전 라인업'이라는 이름과 어긋난다
_q4 = mkgame(hh=23)
_q4.meta.lineup = _c1.meta.lineup
_q4.meta.lineup_seen_at = (_q4.start_utc - timedelta(minutes=5)).isoformat()
check("★ 킥오프 5분 전에야 뜬 명단은 카드를 내지 않는다", not _queue(_q4))
_q5 = mkgame(hh=23)
_q5.meta.lineup = _c1.meta.lineup
_q5.meta.lineup_seen_at = (_q5.start_utc - timedelta(minutes=40)).isoformat()
check("킥오프 40분 전에 뜬 명단은 카드를 낸다", len(_queue(_q5)) == 1)

# 멱등 — 같은 경기가 두 번 나가지 않는다
check("멱등키에 경기 식별자가 들어간다",
      _items and _q1.game_id in _items[0].scope, str(_items[0].scope if _items else ""))
_twice = _queue(_q1) + _queue(_q1)
check("★ 같은 경기를 두 번 만들어도 멱등키가 같다",
      len({i.idem_key for i in _twice}) == 1, str({i.idem_key for i in _twice}))

# ── 7. 되돌리는 스위치 ──────────────────────────────────────
print("\n7. 되돌리기 — 한 줄로 v1.16 상태가 되는가")
_saved = C.LINEUP_ENABLED
try:
    C.LINEUP_ENABLED = False
    import importlib
    importlib.reload(P)
    _off = mkgame(hh=23)
    _off.meta.lineup = _c1.meta.lineup
    _off.meta.lineup_seen_at = NOW.isoformat()
    check("★ 스위치를 끄면 큐가 만들어지지 않는다", not _queue(_off))
finally:
    C.LINEUP_ENABLED = _saved
    import importlib
    importlib.reload(P)

# ── 8. 변이시험 — 깨뜨려서 잡히는지 본다 ────────────────────
#
# **깨뜨려도 통과하는 검사는 검사가 아니다.** 위 검사들이 실제로 무엇을
# 붙잡고 있는지를 여기서 확인한다. 각 항목은 코드를 일부러 틀리게 바꾼 뒤
# 위와 같은 상황을 다시 태워, **이번에는 실패해야** 통과로 친다.
print("\n8. 변이시험 — 일부러 깨뜨리면 검사가 잡는가")
import adapters.naver_football as NFmod                        # noqa: E402


def mutate(label: str, target, attr: str, broken, probe) -> None:
    """`target.attr`을 `broken`으로 바꾸고 `probe()`가 **틀린 답**을 내는지 본다.

    ⚠️ **원본은 `__dict__`에서 꺼낸다.** `getattr`로 꺼내면 staticmethod가
    순수 함수로 풀려 나오고, 그것을 되돌려 놓으면 일반 메서드가 되어
    `self`가 첫 인자로 끼어든다 — 변이시험이 원본을 망가뜨리는 셈이다.
    (이 파일을 처음 돌렸을 때 실제로 그렇게 터졌다.)
    """
    missing = object()
    orig = target.__dict__.get(attr, missing) if hasattr(target, "__dict__") \
        else missing
    if orig is missing:
        orig = getattr(target, attr)
    try:
        setattr(target, attr, broken)
        bad = probe()
    finally:
        setattr(target, attr, orig)
    check(f"변이: {label}", bad is False, "깨뜨렸는데도 통과했다 — 검사가 헛돈다")


# ① 방향 판정을 없애면 뒤집힌 팀이 그대로 남는가
def _probe_face():
    g = mkgame()
    Fake({"/schedule/games/abc123/lineup": lineup_payload(FWD, REV)}
         ).fill_lineups([g], NOW)
    return g.meta.lineup["away"]["rows"][0] == ["라야"]


mutate("방향 판정을 끄면 뒤집힌 명단이 그대로 통과한다",
       NFmod.NaverFootballAdapter, "_face_gk_first",
       staticmethod(lambda rows, formation: rows), _probe_face)


# ② 11명 미달 게이트를 없애면 반쪽 명단이 통과하는가
def _probe_half():
    g = mkgame()
    Fake({"/schedule/games/abc123/lineup":
          lineup_payload([_row("가"), _row("나", "다")], FWD)}).fill_lineups([g], NOW)
    return g.meta.lineup is None


_orig_full = NFmod.NaverFootballAdapter._lineup_full


def _no_min_check(self, g):
    """11명 게이트만 뺀 사본 — 나머지는 원본과 같다."""
    gid = g.source_key.rsplit("-", 1)[-1]
    try:
        d = self._get(f"/schedule/games/{gid}/lineup", label="x")
    except Exception:                                          # noqa: BLE001
        return None
    lu = ((d.get("result") or {}).get("lineUpData") or {}).get("lineup")
    if not lu:
        return None
    out = {}
    for side in ("home", "away"):
        rows = self._lineup_rows(lu.get(side))
        fm = str((lu.get(side) or {}).get("formation") or "")
        out[side] = {"formation": fm, "rows": self._face_gk_first(rows, fm)}
    return out


mutate("11명 게이트를 빼면 반쪽 명단이 카드에 실린다",
       NFmod.NaverFootballAdapter, "_lineup_full", _no_min_check, _probe_half)


# ③ 큐의 '킥오프 15분 전' 조건을 없애면 늦은 명단이 카드가 되는가
def _probe_late():
    g = mkgame(hh=23)
    g.meta.lineup = _c1.meta.lineup
    g.meta.lineup_seen_at = (g.start_utc - timedelta(minutes=5)).isoformat()
    import importlib
    importlib.reload(P)
    return not _queue(g)


mutate("마감 조건을 0으로 두면 킥오프 직전 명단도 카드가 된다",
       C, "LINEUP_CARD_MIN_LEAD_SECONDS", 0, _probe_late)
import importlib                                               # noqa: E402
importlib.reload(P)


# ④ 렌더의 '시작 후 금지' 가드를 없애면 지난 경기 카드가 나오는가
def _probe_after():
    g = mkgame(hh=23)
    g.meta.lineup = _c1.meta.lineup
    return R.lineup_card(g, League.EPL,
                         now=g.start_utc + timedelta(minutes=1)) is None


_orig_status = Status.SCHEDULED
mutate("시작 후에도 SCHEDULED로 두면 지난 경기 카드가 나간다",
       R, "lineup_card",
       lambda game, league, *, now: R._lineup_body(game, league, with_goals=False)
       and ("x", ["y"]), _probe_after)

# ── 9. 한국 선수 해외 경기 (v1.17c) ─────────────────────────
#
# 대표님 지시(2026-09-08): *"한국선수 해외리그 출전하는 경기는 꼭 알림이 필요한데"*
#
# 그전까지 한국 선수 표시는 **MLS에만** 붙었다. 유럽은 전 경기를 수집하면서도
# `player_lines`를 아무도 안 채워, 이강인이 뛰어도 카드에 아무 표시가 없었다.
print("\n9. 한국 선수 — 유럽 라인업에서 잡히는가")
import adapters.naver_football as NF                          # noqa: E402

_KR_ROWS = [_row("오블락"), _row("르노르망", "히메네스", "갈란"),
            _row("몰리나", "바리오스", "코케", "이강인", "갈리아르도"),
            _row("그리즈만", "훌리안 알바레스")]
_kg = mkgame()
_kad = Fake({"/schedule/games/abc123/lineup": lineup_payload(_KR_ROWS, FWD,
                                                             hf="4321")})
_kad.fill_lineups([_kg], NOW)
check("★★ 표에 있는 한국 선수가 라인업에서 잡힌다",
      any(pl.name_ko == "이강인" for pl in _kg.meta.player_lines),
      str([pl.name_ko for pl in _kg.meta.player_lines]))
import headline as _H                                          # noqa: E402
check("  ↳ 카드에 실을 한 줄이 만들어진다 ('이강인 선발')",
      "이강인" in _H.korean_player_sub(_kg.meta.player_lines),
      _H.korean_player_sub(_kg.meta.player_lines))
check("★ 조회가 늘지 않는다 (이미 받은 명단을 다시 읽을 뿐)",
      len(_kad.calls) == 1, str(_kad.calls))
check("★ 표에 없는 이름은 잡지 않는다 (이름으로 국적을 추정하지 않는다)",
      all(pl.name_ko in NF.KOREAN_PLAYERS for pl in _kg.meta.player_lines),
      str([pl.name_ko for pl in _kg.meta.player_lines]))
# **저장 왕복** — `player_lines`는 스냅샷에 담지 않는 칸이라, 라인업 안에
# 실어야 살아남는다. 여기서 새면 카드가 되읽을 때 표시가 사라진다(fix49).
_saved = (_kg.meta.lineup or {}).get("korean")
check("★★ 한국 선수가 라인업 칸에 실려 저장된다 (fix49와 같은 자리)",
      bool(_saved) and _saved[0]["name"] == "이강인", str(_saved))
check("  ↳ 카드가 쓰는 값이 다 실린다 (이름 · 축구 칸)",
      bool(_saved) and _saved[0].get("soccer", {}).get("start") is True,
      str(_saved))
# 한국 선수가 없는 경기는 아무 말도 안 한다
_ng = mkgame()
Fake({"/schedule/games/abc123/lineup": lineup_payload(FWD, REV)}
     ).fill_lineups([_ng], NOW)
check("★ 한국 선수가 없으면 아무 말도 안 한다",
      not _ng.meta.player_lines and not (_ng.meta.lineup or {}).get("korean"))

# **변이시험** — 표에서 이름을 빼면 못 잡는가 (표가 진짜 판정 기준인가)
def _probe_kr():
    g = mkgame()
    Fake({"/schedule/games/abc123/lineup":
          lineup_payload(_KR_ROWS, FWD, hf="4321")}).fill_lineups([g], NOW)
    return any(pl.name_ko == "이강인" for pl in g.meta.player_lines)


_orig_tbl = dict(NF.KOREAN_PLAYERS)
try:
    NF.KOREAN_PLAYERS.pop("이강인", None)
    _bad = _probe_kr()
finally:
    NF.KOREAN_PLAYERS.clear()
    NF.KOREAN_PLAYERS.update(_orig_tbl)
check("변이: 표에서 이름을 빼면 못 잡는다 (표가 실제 판정 기준이다)",
      _bad is False, "표에 없는데도 잡았다 — 이름으로 추정하고 있다")
check("  ↳ 변이 뒤 표가 원래대로 돌아왔다", "이강인" in NF.KOREAN_PLAYERS)

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
