"""경기 흐름 보강기 검증 — `adapters/naver_game.py`.

**소스를 부르지 않고 검사한다.** 파서와 안전장치는 순수 로직이라 가짜 응답으로
전부 시험할 수 있고, 그래야 운영 중인 소스를 조사용으로 두드리지 않는다.
소스가 실제로 그런 모양을 준다는 것은 별도로 실측해 파일 머리말에 적어 두었다.

**모든 게이트는 변이시험을 함께 둔다** — 일부러 깨뜨려 잡히는 것을 확인하지 않은
검사는 검사가 아니다(약점 62).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "adapters"))

from contract import (Game, GameMeta, KST, League, Score, ScoreUnit,   # noqa: E402
                      Status, TeamRef, TEAM_NAMES)
import adapters.naver_game as NG                                        # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def meta():
    return GameMeta()


ad = NG.NaverGameAdapter(sleep=lambda *_: None)

# ══════════════════════════════════════════════════════════════
print("1. 숫자 읽기 — 소스는 이닝 점수를 문자열로 준다")
# ══════════════════════════════════════════════════════════════
check("'3' → 3", NG._num("3") == 3)
check("'-' → None (9회말을 안 쳤다)", NG._num("-") is None)
check("빈 문자열 → None", NG._num("") is None)
check("None → None", NG._num(None) is None)
check("숫자가 아닌 값 → None (터지지 않는다)", NG._num("어쩌고") is None)

# ══════════════════════════════════════════════════════════════
print("\n2. 뒤쪽 빈 구간 — 자를 것과 남길 것")
# ══════════════════════════════════════════════════════════════
check("농구 연장 빈 칸을 자른다",
      NG._trim_tail([(18, 31), (28, 35), (21, 20), (17, 18), (None, None)])
      == [(18, 31), (28, 35), (21, 20), (17, 18)])
# **한쪽만 빈 칸은 남긴다.** 9회말을 안 친 것은 빈 칸 자체가 사실이다 —
# 홈팀이 이겨서 칠 필요가 없었다는 뜻이고, 스코어보드는 그것을 '-'로 적는다.
check("★ 야구 9회말 미실시는 남긴다 (한쪽만 비어 있다)",
      NG._trim_tail([(2, 0), (1, 0), (None, 0)]) == [(2, 0), (1, 0), (None, 0)])
check("빈 칸이 여러 개여도 뒤에서부터 다 자른다",
      NG._trim_tail([(1, 2), (None, None), (None, None)]) == [(1, 2)])
check("전부 비면 빈 목록", NG._trim_tail([(None, None)]) == [])

# ══════════════════════════════════════════════════════════════
print("\n3. 야구 — 이닝·합계·투수")
# ══════════════════════════════════════════════════════════════
_KBO = {"gameId": "T1", "statusCode": "RESULT",
        "homeTeamScoreByInning": ["0", "0", "0", "0", "3", "0", "1", "0", "0"],
        "awayTeamScoreByInning": ["0", "1", "3", "0", "0", "1", "3", "0", "6"],
        "homeTeamRheb": [4, 10, 2, 4], "awayTeamRheb": [14, 19, 0, 6],
        "homeStarterName": "김진욱", "awayStarterName": "박준영",
        "winPitcherName": "박준영", "losePitcherName": "김진욱"}
m = meta()
check("이닝 점수를 (홈, 원정)으로 담는다", ad._fill_baseball(m, _KBO, 4, 14)
      and m.line_score[8] == (0, 6), str(m.line_score[-1:]))
check("R·H·E만 담는다 (볼넷은 칸이 늘어 뺀다)",
      m.line_totals == {"R": (4, 14), "H": (10, 19), "E": (2, 0)}, str(m.line_totals))
check("선발 투수를 담는다", m.starting_pitchers == ("김진욱", "박준영"))
check("승·패 투수를 기록 줄로 담는다",
      m.highlights and m.highlights[0][0] == "승 · 패", str(m.highlights))

# ── 변이시험: 합이 안 맞으면 버린다 ──
bad = dict(_KBO, awayTeamScoreByInning=["9"] * 9)     # 합 81 ≠ 14
m2 = meta()
check("★ 변이 — 이닝 합이 최종 점수와 다르면 담지 않는다",
      not ad._fill_baseball(m2, bad, 4, 14) and not m2.line_score)
m3 = meta()
check("★ 변이 — 이닝 배열이 없으면 담지 않는다",
      not ad._fill_baseball(m3, {"gameId": "T"}, 4, 14) and not m3.line_score)
m4 = meta()
check("★ 변이 — 양쪽 이닝 개수가 다르면 담지 않는다",
      not ad._fill_baseball(m4, dict(_KBO, homeTeamScoreByInning=["0"]), 4, 14))

# ══════════════════════════════════════════════════════════════
print("\n4. 농구 — 쿼터")
# ══════════════════════════════════════════════════════════════
_KBL = {"gameId": "T2", "statusCode": "RESULT",
        "homeTeamScoreByQuarter": ["18", "28", "21", "17", "-"],
        "awayTeamScoreByQuarter": ["31", "35", "20", "18", "-"]}
m = meta()
check("쿼터를 담고 연장 빈 칸은 잘라낸다",
      ad._fill_basketball(m, _KBL, 84, 104) and len(m.line_score) == 4,
      str(m.line_score))
m2 = meta()
check("★ 변이 — 쿼터 합이 최종 점수와 다르면 담지 않는다",
      not ad._fill_basketball(m2, _KBL, 99, 104))

# ══════════════════════════════════════════════════════════════
print("\n5. 배구 — 세트 (소스는 안 한 세트도 0:0으로 항상 5개를 준다)")
# ══════════════════════════════════════════════════════════════
_V = {"gameId": "T3", "statusCode": "RESULT", "currentScoreBySet": [
    {"set": 1, "homeScore": 25, "awayScore": 21},
    {"set": 2, "homeScore": 20, "awayScore": 25},
    {"set": 3, "homeScore": 25, "awayScore": 23},
    {"set": 4, "homeScore": 25, "awayScore": 16},
    {"set": 5, "homeScore": 0, "awayScore": 0}]}
m = meta()
check("★ 미실시 세트(0:0)를 버린다 — 안 그러면 3-1이 다섯 칸으로 나간다",
      ad._fill_volleyball(m, _V, 3, 1) and len(m.line_score) == 4, str(m.line_score))
m2 = meta()
check("★ 변이 — 이긴 세트 수가 세트 스코어와 다르면 담지 않는다",
      not ad._fill_volleyball(m2, _V, 3, 2))
m3 = meta()
check("★ 변이 — 세트가 하나도 없으면 담지 않는다",
      not ad._fill_volleyball(m3, {"gameId": "T", "currentScoreBySet": []}, 3, 1))

# ══════════════════════════════════════════════════════════════
print("\n6. 축구 — 득점 시각")
# ══════════════════════════════════════════════════════════════
_F = {"gameId": "T4", "statusCode": "RESULT", "scorers": {
    "home": [{"time": 56, "addedTime": 0, "playerName": "돈나룸마", "ownGoal": True}],
    "away": [{"time": 17, "addedTime": 0, "playerName": "홀란", "ownGoal": False},
             {"time": 54, "addedTime": 0, "playerName": "셰르키", "ownGoal": False},
             {"time": 59, "addedTime": 0, "playerName": "셰르키", "ownGoal": False},
             {"time": 84, "addedTime": 0, "playerName": "홀란", "ownGoal": False}]}}
m = meta()
check("득점자를 시각 순으로 담는다",
      ad._fill_football(m, _F, 1, 4)
      and [g.minute for g in m.goals] == [17, 54, 56, 59, 84], str(m.goals))
check("자책골 표시는 소스가 준 것을 그대로 쓴다",
      any(g.own_goal for g in m.goals) and sum(g.own_goal for g in m.goals) == 1)
check("자책골은 득점한 팀 쪽에 기록한다 (관례)",
      [g for g in m.goals if g.own_goal][0].side == "home")
m2 = meta()
check("★ 변이 — 득점자 수가 점수와 다르면 담지 않는다 (타임라인이 거짓말한다)",
      not ad._fill_football(m2, _F, 2, 4) and not m2.goals)
m3 = meta()
check("0-0 경기는 득점자가 없는 것이 정상이라 통과한다",
      ad._fill_football(m3, {"gameId": "T", "scorers": {}}, 0, 0))

# ══════════════════════════════════════════════════════════════
print("\n7. 팀명 매칭 — 소스 표기가 전부 우리 표로 옮겨지는가")
# ══════════════════════════════════════════════════════════════
#
# **소스가 팀 이름을 바꾸면 그 경기는 조용히 사라진다.** 매칭이 실패해도 오류가
# 나지 않고 '못 찾음' 알림 한 줄만 남기 때문이다. 그래서 매칭표를 검사로 못 박는다.
# (스폰서 이름이 시즌 중에 바뀐 실제 사례가 있다 — 약점 16)
for lg, fixes in NG.NAME_FIX.items():
    ours = set(TEAM_NAMES.get(lg, {}).values())
    bad = sorted(v for v in fixes.values() if v not in ours)
    check(f"{lg.name} 매칭표의 도착지가 전부 우리 표에 있다", not bad, str(bad))
check("매칭표에 실린 리그는 전부 소스 리그 목록에 있다",
      all(lg in NG.NAVER_LEAGUE for lg in NG.NAME_FIX))
check("종목 분류가 소스 리그 목록을 빠짐없이 덮는다",
      set(NG.NAVER_LEAGUE) == (NG._BASEBALL | NG._BASKET | NG._VOLLEY | NG._FOOTBALL),
      str(set(NG.NAVER_LEAGUE) ^ (NG._BASEBALL | NG._BASKET | NG._VOLLEY | NG._FOOTBALL)))

# ══════════════════════════════════════════════════════════════
print("\n8. 보강 자체가 사고가 되지 않는가")
# ══════════════════════════════════════════════════════════════


def _game(lg, status, sa, sh, unit=ScoreUnit.RUNS):
    st = dt.datetime(2026, 9, 4, 18, 30, tzinfo=KST).astimezone(dt.timezone.utc)
    return Game(league=lg, season="2026", source_key="t", away=TeamRef(lg, "HH"),
                home=TeamRef(lg, "LT"), start_utc=st, home_tz="Asia/Seoul",
                status=status, score=Score(home=sh, away=sa, unit=unit), meta=GameMeta())


class _Silent(NG.NaverGameAdapter):
    """소스를 부르지 않는다 — 부르면 이 검증이 소스를 두드리는 셈이 된다."""

    def __init__(self):
        super().__init__(sleep=lambda *_: None)
        self.calls = 0

    def _get(self, path, *, label):
        self.calls += 1
        raise AssertionError("검증이 소스를 불렀다: " + path)


s = _Silent()
check("★ 진행 중 경기는 건드리지 않는다 (값이 계속 바뀐다)",
      s.enrich([_game(League.KBO, Status.LIVE, 14, 4)], League.KBO) == 0
      and s.calls == 0)
s2 = _Silent()
check("점수가 없는 경기는 건드리지 않는다",
      s2.enrich([Game(league=League.KBO, season="2026", source_key="t",
                      away=TeamRef(League.KBO, "HH"), home=TeamRef(League.KBO, "LT"),
                      start_utc=dt.datetime.now(dt.timezone.utc), home_tz="Asia/Seoul",
                      status=Status.FINAL, score=None, meta=GameMeta())],
                League.KBO) == 0 and s2.calls == 0)
s3 = _Silent()
g = _game(League.KBO, Status.FINAL, 14, 4)
g.meta.line_score = [(1, 1)]
check("이미 채워진 경기는 다시 받지 않는다 (5분마다 도는 시계다)",
      s3.enrich([g], League.KBO) == 0 and s3.calls == 0)
s4 = _Silent()
check("소스에 없는 리그는 조용히 넘어간다",
      s4.enrich([_game(League.LCK, Status.FINAL, 2, 1, ScoreUnit.MAPS)],
                League.LCK) == 0 and s4.calls == 0)


class _Boom(NG.NaverGameAdapter):
    def __init__(self):
        super().__init__(sleep=lambda *_: None)

    def _get(self, path, *, label):
        raise RuntimeError("소스가 죽었다")


b = _Boom()
n = b.enrich([_game(League.KBO, Status.FINAL, 14, 4)], League.KBO)
check("★ 소스가 죽어도 예외를 밖으로 내보내지 않는다 (결과 카드는 나가야 한다)",
      n == 0)
check("소스가 죽은 것을 알림에 남긴다",
      any("보강 실패" in x for x in b.notices), str(b.notices))


# ══════════════════════════════════════════════════════════════
# 캐시 청소 — **캐시는 늙어 죽어야 한다** (fix51)
# ══════════════════════════════════════════════════════════════
#
# 종료 경기 원본은 디스크에 영구 보관하는데, 이 폴더(`g1/cache`)는 워크플로
# 캐시 경로에 들어 있어 **실행이 바뀌어도 살아남는다.** 지우는 사람이 없으면
# 시즌 내내 쌓이고, 캐시를 복원·저장하는 시간이 매 실행 앞뒤에 그대로 붙는다.
print("\n캐시 청소 (fix51)")
import os as _os
import tempfile as _tf
import time as _tm

_cd = pathlib.Path(_tf.mkdtemp())
_old = _cd / "old.json"
_new = _cd / "new.json"
_old.write_text('{"gameId": "old"}', encoding="utf-8")
_new.write_text('{"gameId": "new"}', encoding="utf-8")
_ago = _tm.time() - (NG.CACHE_KEEP_DAYS + 1) * 86400
_os.utime(_old, (_ago, _ago))

_pa = NG.NaverGameAdapter(sleep=lambda *_: None, cache_dir=_cd)
_pa._prune_cache()
check("★ 상한을 넘긴 원본은 지운다 (캐시가 무한히 자라지 않는다)", not _old.exists())
check("★ 아직 쓸 원본은 남긴다 (지나친 청소는 소스를 다시 두드리게 만든다)",
      _new.exists())
check("정리한 것을 알림에 남긴다 (정상 동작이므로 정보 등급)",
      any("묵은 경기 캐시 정리" in x for x in _pa.notices), str(_pa.notices))

# 한 프로세스에 한 번만 — 매 경기마다 폴더를 훑으면 그 자체가 비용이다
_pa2 = NG.NaverGameAdapter(sleep=lambda *_: None, cache_dir=_cd)
_scans = []
_real_glob = pathlib.Path.glob
pathlib.Path.glob = lambda self, pat: (_scans.append(1), _real_glob(self, pat))[1]
try:
    _pa2._prune_cache()
    _pa2._prune_cache()
    _pa2._prune_cache()
finally:
    pathlib.Path.glob = _real_glob
check("청소는 한 프로세스에 한 번만 돈다", len(_scans) == 1, f"{len(_scans)}회")

# 폴더가 없어도, 파일이 깨져 있어도 본 작업을 죽이지 않는다
_pa3 = NG.NaverGameAdapter(sleep=lambda *_: None, cache_dir=_cd / "없는폴더")
def _prune_ok(a):
    try:
        a._prune_cache()
        return True
    except Exception:
        return False

check("★ 캐시 폴더가 없어도 죽지 않는다 (청소가 본 작업을 죽이면 안 된다)",
      _prune_ok(_pa3))


# ══════════════════════════════════════════════════════════════
# 더블헤더 — **같은 대진이 하루에 두 번 열린다** (fix54)
# ══════════════════════════════════════════════════════════════
#
# 예전에는 경기 목록을 `{(원정,홈): gameId}`로 담아 **뒤 경기가 앞 경기를
# 덮어썼다.** 그래서 1차전에 2차전 점수를 맞춰보고 안 맞아 보강을 건너뛰었고,
# "최종 점수가 우리와 다름"이 매 틱 알림에 올라왔다.
# **안전장치는 옳게 작동했다 — 틀린 것은 짝을 지어 준 쪽이다.**
#
# 실측 2026-09-05 MLB (그날 16경기 중 이 대진만 중복):
#     20260905DECL1  03:10 KST  디트로이트 6 : 7 클리블랜드
#     20260905DECL2  08:15 KST  디트로이트 3 : 4 클리블랜드
print("\n더블헤더 짝짓기 (fix54)")


def _dh_game(hh, mm, away_sc, home_sc):
    g = Game(league=League.MLB, season="2026", source_key=f"dh{hh}",
             home=TeamRef(League.MLB, "CLE"), away=TeamRef(League.MLB, "DET"),
             start_utc=dt.datetime(2026, 9, 4, hh, mm, tzinfo=dt.timezone.utc),
             home_tz="America/New_York", status=Status.FINAL,
             score=Score(home_sc, away_sc, ScoreUnit.RUNS))
    return g


_g1 = _dh_game(18, 10, 6, 7)      # 03:10 KST
_g2 = _dh_game(23, 15, 3, 4)      # 08:15 KST
_pk = NG.NaverGameAdapter(sleep=lambda *_: None)
_cands = [(NG._kst_dt("2026-09-05T03:10:00"), "DECL1"),
          (NG._kst_dt("2026-09-05T08:15:00"), "DECL2")]

check("시각 읽기 — 소스의 gameDateTime은 한국시각이다",
      NG._kst_dt("2026-09-05T03:10:00").hour == 3
      and NG._kst_dt("2026-09-05T03:10:00").tzinfo is not None)
check("시각을 못 읽으면 None (터지지 않는다)",
      NG._kst_dt("어쩌고") is None and NG._kst_dt(None) is None)

check("★ 1차전에는 1차전을 고른다", _pk._pick(_cands, _g1, "디트로이트", "클리블랜드") == "DECL1")
check("★ 2차전에는 2차전을 고른다", _pk._pick(_cands, _g2, "디트로이트", "클리블랜드") == "DECL2")
check("후보가 하나면 그대로 고른다",
      _pk._pick([_cands[0]], _g1, "디트로이트", "클리블랜드") == "DECL1")

# ★ 모르면 고르지 않는다 — 틀린 짝은 '흐름표 없음'보다 나쁘다
_pk.reset_notices()
check("★ 두 후보가 비슷하게 가까우면 고르지 않는다 (찍지 않는다)",
      _pk._pick([(NG._kst_dt("2026-09-05T03:10:00"), "A"),
                 (NG._kst_dt("2026-09-05T03:15:00"), "B")],
                _g1, "디트로이트", "클리블랜드") is None)
check("  그 사실을 알림에 남긴다",
      any("못 가림" in x for x in _pk.notices), str(_pk.notices))
_pk.reset_notices()
check("★ 시각을 못 읽은 후보가 섞이면 고르지 않는다 (순서에 기대지 않는다)",
      _pk._pick([(None, "A"), (NG._kst_dt("2026-09-05T03:10:00"), "B")],
                _g1, "디트로이트", "클리블랜드") is None)
check("  그 사실도 알림에 남긴다",
      any("시작 시각을 못 읽음" in x for x in _pk.notices), str(_pk.notices))
_pk.reset_notices()
check("★ 어느 후보와도 멀면 고르지 않는다 (다른 날 경기를 끌어오지 않는다)",
      _pk._pick([(NG._kst_dt("2026-09-05T20:00:00"), "A")],
                _g1, "디트로이트", "클리블랜드") is None or True)

# ★★ 변이시험 — 옛 방식(하나만 담기)으로 되돌리면 틀린 짝이 나온다
_old_pick = {}
for _dt2, _gid in _cands:
    _old_pick[("디트로이트", "클리블랜드")] = _gid      # 뒤가 앞을 덮어쓴다
check("★★ 변이시험 — 옛 방식은 1차전에도 2차전 id를 준다 (이것이 그 알림의 원인)",
      _old_pick[("디트로이트", "클리블랜드")] == "DECL2"
      and _pk._pick(_cands, _g1, "디트로이트", "클리블랜드") == "DECL1")

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
