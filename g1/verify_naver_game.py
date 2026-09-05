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

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
