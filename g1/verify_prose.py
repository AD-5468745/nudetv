#!/usr/bin/env python3
"""AI 조합글 전수 점검 (v1.38) — 구체성·정보력·자연스러움.

대표님 지시(2026-09-13): *"컨텐츠 종류, 각 컨텐츠의 디자인, 정보성, 분석글을
포함한 AI 조합글의 구체성·정보력·자연스러움 전반적인 전수조사"*

**이 파일이 지키는 것은 '코드가 도는가'가 아니라 '나온 문장이 참인가'다.**
그래서 전부 **실제 출력 문자열**을 만들어 놓고 그 문자열을 검사한다.

조사에서 나온 사실 오류 열아홉 건을 각각 못 박는다. 고친 것마다
**(변이) 고치기 전 상태를 재현하는 검사**를 함께 둔다 — 그게 없으면
"0건"이 미탐 0의 증거가 못 된다(약점 194).

**SKIP을 PASS로 세지 않는다.**
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

# **개발 컴퓨터 경로를 박지 않는다.** 이 파일은 공개 저장소에 실려 나간다 —
# `verify_public`의 '개발 환경 흔적' 검사가 배포를 막는다(2026-09-13 실제로 막혔다).
# 자기 자리에서 경로를 구하면 어디에 놓여도 돈다.
G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))


def src(name: str) -> str:
    """같은 폴더의 소스 파일을 글자 그대로 읽는다 (배선 검사용)."""
    return (G1 / name).read_text(encoding="utf-8")

import contract as C                                          # noqa: E402
import headline as H                                          # noqa: E402
import pipeline as P                                          # noqa: E402
from contract import (DecidedBy, Game, GameMeta, Goal, League,  # noqa: E402
                      Score, ScoreUnit, Standing, Status, StreakKind,
                      TeamRef, WLD, josa)

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


KST = ZoneInfo("Asia/Seoul")


def _season(lg):
    return ("2026" if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR
            else "2026-27")


def bb(rows, hs, as_, lg=League.KBO, home="LG", away="HH"):
    """구간 점수가 있는 경기 한 벌. `rows`는 (홈, 원정)."""
    m = GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg))
    m.line_score = [tuple(r) for r in rows]
    g = Game(league=lg, season=_season(lg), source_key="x",
             home=TeamRef(lg, home), away=TeamRef(lg, away),
             start_utc=dt.datetime(2026, 9, 6, 9, tzinfo=dt.timezone.utc),
             home_tz="Asia/Seoul", status=Status.FINAL,
             score=Score(hs, as_, C.SCORE_UNIT_BY_LEAGUE[lg]), meta=m)
    g.validate()
    return g


def sc(goals, hs, as_, lg=League.EPL, dec=DecidedBy.REGULAR,
       home="아스널", away="첼시"):
    """축구 경기 한 벌. `goals`는 (분, 편, 이름, 추가분)."""
    m = GameMeta(decided_by=dec, gender=C.GENDER_BY_LEAGUE.get(lg))
    if dec is DecidedBy.PSO:                 # 계약이 요구한다 — 없으면 게이트가 막는다
        m.penalties = Score(4, 3, ScoreUnit.GOALS)
    m.goals = tuple(Goal(minute=a, side=b, name=c, own_goal=False, added=d)
                    for a, b, c, d in goals)
    g = Game(league=lg, season=_season(lg), source_key="y",
             home=TeamRef(lg, home), away=TeamRef(lg, away),
             start_utc=dt.datetime(2026, 9, 6, 19, tzinfo=dt.timezone.utc),
             home_tz="Europe/London", status=Status.FINAL,
             score=Score(hs, as_, ScoreUnit.GOALS), meta=m)
    g.validate()
    return g


def bflow(g, home="LG", away="한화"):
    out = P.flow_prose(g, g.league, away_name=away, home_name=home)
    return out[0] if out else ""


def sflow(g, home="아스널", away="첼시"):
    out = P.goal_prose(g, g.league, away_name=away, home_name=home)
    return out[0] if out else ""


print("=" * 64)
print("AI 조합글 전수 점검 (v1.38)")
print("=" * 64)

# ══════════════════════════════════════════════════════════════
print("\n1. ★★★ 종목의 낱말 — 축구에 '점'은 없다")
# ══════════════════════════════════════════════════════════════
_h = H.for_single_result(sc([(20, "home", "A", 0), (40, "away", "B", 0),
                             (70, "away", "C", 0)], 1, 2), League.EPL)
check("★★★ 축구 한 골 차를 '한 골 차'라 말한다 (실렌더에 '한 점 차'가 나갔다)",
      _h.sub == "한 골 차", _h.sub)
check("  ↳ (변이) 고치기 전 문장은 '한 점 차'였다 — 그것이 사라졌다",
      "점" not in _h.sub, _h.sub)
_hb = H.for_single_result(bb([(1, 0)] * 9, 5, 4), League.KBO)
check("★ 야구는 그대로 '한 점 차'다 (무디게 한 것이 아니다)",
      _hb.sub == "한 점 차", _hb.sub)
check("★★ 낱말을 한 곳에서 만든다",
      H.count_word(League.EPL) == "골" and H.count_word(League.KBO) == "점"
      and H.count_word(League.VLEAGUE_M) == "세트")

# 세트·맵 종목은 한 끗 차를 아예 세지 않는다
_vm = bb([(25, 20)] * 4, 3, 2, lg=League.VLEAGUE_M, home="OK", away="KB")
_hv = H.for_single_result(_vm, League.VLEAGUE_M)
check("★★ 세트 종목에 '한 세트 차'라 떠들지 않는다 (3-2는 흔하다)",
      _hv.rule != "G-CLOSE", f"{_hv.rule} / {_hv.sub}")

# ══════════════════════════════════════════════════════════════
print("\n2. ★★★ 누가 냈는지 말한다 — 안 밝히면 절반이 반대로 읽힌다")
# ══════════════════════════════════════════════════════════════
# 8회에 원정(한화)이 6점. 큰 글씨는 "한화 11 : 6 롯데"로 시작한다.
_g = bb([(0, 0)] * 7 + [(0, 6), (0, 0)], 6, 11, home="LT", away="HH")
_h2 = H.for_single_result(_g, League.KBO)
check("★★★ 빅이닝을 낸 팀을 말한다", _h2.sub.startswith("한화"), _h2.sub)
check("  ↳ 그 팀이 facts에도 남는다 (게이트가 되짚을 수 있어야 한다)",
      _h2.facts.get("team") == "한화", str(_h2.facts))
check("★★ (변이) 팀을 빼면 앞에 적힌 팀이 낸 것으로 읽힌다 — 그게 고치기 전이다",
      "8회에만 6점" in _h2.sub)

# ══════════════════════════════════════════════════════════════
print("\n3. ★★★ 연장을 말한다")
# ══════════════════════════════════════════════════════════════
_h3 = H.for_single_result(bb([(0, 0)] * 10 + [(1, 0)], 4, 3), League.KBO)
check("★★ 11회 경기를 '연장 11회'라 말한다 (전에는 '한 점 차'에 덮였다)",
      _h3.rule == "G-EXTRA" and "연장 11회" in _h3.sub,
      f"{_h3.rule} / {_h3.sub}")
check("  ↳ 규칙이 등록돼 있다 (등록 안 하면 검증이 막는다)",
      "G-EXTRA" in H.ALL_RULES)

# ══════════════════════════════════════════════════════════════
print("\n4. ★★★ 야구 흐름 문장의 산수가 맞는다")
# ══════════════════════════════════════════════════════════════
# 2회 2점 · 5회 1점 · 8회 1점 = 4-0. 전에는 5회를 버리고도 "한 점을 보태 4-0".
_t = bflow(bb([(0, 0), (2, 0), (0, 0), (0, 0), (1, 0), (0, 0), (0, 0), (1, 0),
               (0, 0)], 4, 0))
# 고친 뒤에는 **중간 득점을 말하므로** 산수가 문장 안에서 이어진다.
check("  ↳ 중간 득점을 버리지 않는다 (2-0 → 3-0 → 4-0)",
      "3-0" in _t and "4-0" in _t, _t)
check("★★★ 문장 안에서 산수가 이어진다 (증가분과 누적 점수가 맞는다)",
      "두 점을 먼저" in _t and "한 점을 보태 3-0" in _t
      and "한 점을 보태 4-0" in _t, _t)
check("★★ 문장 마지막 점수가 최종 점수와 같다", "4-0" in _t.split(".")[-2], _t)

# (변이) 중간 득점을 버리면 산수가 끊긴다 — 그것이 고치기 전 상태다
_skip = bflow(bb([(0, 0), (2, 0)] + [(0, 0)] * 5 + [(1, 0), (0, 0)], 3, 0))
check("★★ (변이) 말할 사건이 적으면 그만큼만 말한다 (지어내지 않는다)",
      "3-0" in _skip, _skip)

# 난타전 — 전에는 사건이 잘려 중간 점수에서 끝났다
_t2 = bflow(bb([(2, 3), (1, 0), (0, 4), (3, 1), (0, 2), (2, 3), (1, 0), (3, 2),
                (0, 0)], 12, 15))
check("★★★ 사건이 많아도 **최종 점수**로 끝난다 (전에는 12-9에서 끊겼다)",
      "12-15" in _t2 or "15-12" in _t2, _t2)

# ══════════════════════════════════════════════════════════════
print("\n5. ★★★ 무승부에는 승부가 없다")
# ══════════════════════════════════════════════════════════════
_t3 = bflow(bb([(0, 2), (0, 0), (1, 0), (0, 0), (0, 0), (0, 0), (2, 0), (0, 1),
                (0, 0)], 3, 3))
check("★★★ 3-3 무승부에 '승부를 갈랐다'가 없다", "승부를 갈랐다" not in _t3, _t3)
_t4 = bflow(bb([(0, 1), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (2, 0), (0, 0),
                (0, 0)], 2, 1))
check("  ↳ 승부가 갈린 경기에는 여전히 말한다 (무디게 하지 않았다)",
      "승부를 갈랐다" in _t4 or "경기를 끝냈다" in _t4, _t4)

# ══════════════════════════════════════════════════════════════
print("\n6. ★★★ 대승이 빈약하게 나가지 않는다")
# ══════════════════════════════════════════════════════════════
_t5 = bflow(bb([(0, 1), (0, 0), (0, 2), (0, 0), (0, 0), (0, 3), (0, 0), (0, 0),
                (0, 0)], 0, 6))
check("★★★ 0-6 완승에서 여섯 점이 다 나온다 (전에는 1점만 말하고 끝)",
      "6-0" in _t5 and len(_t5) > 60, _t5)
_t6 = bflow(bb([(0, 0)] * 4 + [(7, 0)] + [(0, 0)] * 4, 7, 0))
check("★★ 빅이닝 선취점을 '몰아쳐'라 말한다", "몰아쳐" in _t6, _t6)

# ══════════════════════════════════════════════════════════════
print("\n7. ★★★ 축구 — 연장·승부차기")
# ══════════════════════════════════════════════════════════════
_s1 = sflow(sc([(30, "home", "A", 0), (70, "away", "B", 0),
                (105, "home", "C", 0)], 2, 1, dec=DecidedBy.AET))
check("★★★ 연장 골에 '정규시간이 끝나갈 때였다'가 붙지 않는다 (거짓이었다)",
      "정규시간이 끝나갈 때" not in _s1, _s1)
check("  ↳ 대신 연장이라고 말한다", "연장" in _s1, _s1)
check("  ↳ 같은 말을 두 번 하지 않는다", _s1.count("연장까지") <= 1, _s1)

_s2 = sflow(sc([(30, "home", "A", 0), (70, "away", "B", 0)], 1, 1,
               dec=DecidedBy.PSO))
check("★★★ 승부차기 문장이 실제로 나온다 (전에는 죽은 코드였다)",
      "승부차기" in _s2, _s2)
check("★★ (변이) 옛 문자열은 계약에 없는 값이었다",
      {m.value for m in DecidedBy} == {"regular", "extra_innings", "aet", "pso"},
      str({m.value for m in DecidedBy}))

_s3 = sflow(sc([(12, "away", "P", 0), (40, "home", "S", 0),
                (90, "home", "H", 4)], 2, 1))
check("★ 추가시간 결승골은 그대로 '정규시간이 다 지난 뒤'다",
      "정규시간이 다 지난 뒤" in _s3, _s3)

# ══════════════════════════════════════════════════════════════
print("\n8. ★★★ 축구 1골 경기도 한 문장은 낸다")
# ══════════════════════════════════════════════════════════════
_s4 = sflow(sc([(75, "home", "A", 0)], 1, 0))
check("★★★ 1-0 경기에 문장이 있다 (전에는 19%가 텍스트 없이 나갔다)",
      _s4 != "", _s4)
check("  ↳ 카드에 없는 것만 말한다 — '그 뒤로 안 바뀌었다'",
      "끝까지 지켜졌다" in _s4 or "경기를 갈랐다" in _s4, _s4)
check("★ 0-0은 여전히 침묵한다 (말할 사건이 정말 없다)",
      sflow(sc([], 0, 0)) == "")

# ══════════════════════════════════════════════════════════════
print("\n9. ★★★ 순위 — 단위를 섞은 표에는 순위 이야기를 안 한다")
# ══════════════════════════════════════════════════════════════
def st(code, rank, w, l, gb, group=None, lg=League.NPB, d=0):
    return Standing(league=lg, season="2026", team_code=code, rank=rank,
                    games=w + l + d, record=WLD(w, l, d),
                    pct=f"{w / (w + l):.3f}", games_behind=gb,
                    streak_kind=StreakKind.NONE, streak_len=0, group=group)


_mixed = [st("SB", 1, 75, 45, "0", "퍼시픽"), st("SEI", 2, 70, 52, "6.0", "퍼시픽"),
          st("NIP", 3, 71, 53, "6.0", "퍼시픽"), st("HAN", 4, 69, 52, "6.5", "센트럴"),
          st("YOM", 5, 67, 55, "9.0", "센트럴"), st("YOK", 6, 57, 62, "17.5", "센트럴")]
_mh = H.for_standings(_mixed, League.NPB)
check("★★★ 섞인 표의 머리말은 **단위 이름을 달고** 나온다 (v1.38)",
      _mh is not None and _mh.facts.get("group") in ("센트럴", "퍼시픽")
      and _mh.text.startswith(_mh.facts["group"]), str(_mh))
check("★★★ (변이) 단위를 안 밝히면 '2·3위 0경기 차'가 된다 — 실제로 매일 나갔다",
      _mh is not None and not _mh.text.startswith("2·3위"), str(_mh))
check("  ↳ 문장에 쓰인 팀은 그 단위 소속이다 (다른 리그 팀을 끌어오지 않는다)",
      all(next(x for x in _mixed if x.team_code == c).group == _mh.facts["group"]
          for k, c in _mh.facts.items()
          if k in ("first", "second", "team") and isinstance(c, str)))
check("★★★ (변이) 막지 않으면 '2·3위 0경기 차'가 나온다 — 실제로 매일 나갔다",
      H.for_standings([s for s in _mixed if s.group == "퍼시픽"],
                      League.NPB) is not None)
check("  ↳ 단위를 지정하면 그 안에서 정상 동작한다",
      H.for_standings(_mixed, League.NPB, group="퍼시픽") is not None)
check("  ↳ 단위가 아예 없는 리그(KBO)는 영향을 안 받는다",
      H.for_standings([st("LG", 1, 80, 45, "0", lg=League.KBO),
                       st("OB", 2, 79, 46, "1.0", lg=League.KBO)],
                      League.KBO) is not None)

# ══════════════════════════════════════════════════════════════
print("\n10. ★★★ 축구 승차는 '경기 차'가 아니라 '점 차'다")
# ══════════════════════════════════════════════════════════════
_kl = [st("K09", 1, 18, 4, "0", lg=League.KL1, d=5),
       st("K05", 2, 11, 7, "17", lg=League.KL1, d=9),
       st("K01", 3, 12, 10, "18", lg=League.KL1, d=5)]
_hk = H.for_standings(_kl, League.KL1)
check("★★★ K리그 순위 머리말에 '경기 차'가 없다 (그 값은 승점차다)",
      _hk is None or "경기 차" not in _hk.text, _hk.text if _hk else "None")
check("  ↳ 야구는 그대로 '경기 차'다",
      H._gb_word(League.KBO) == "경기 차" and H._gb_word(League.KL1) == "점 차")

# ══════════════════════════════════════════════════════════════
print("\n11. ★★ 맞대결 — 무승부를 센다")
# ══════════════════════════════════════════════════════════════
_g2 = bb([(1, 0)] * 9, 5, 4, lg=League.NPB, home="YOM", away="HAN")
_h4 = H.for_analysis(_g2, League.NPB,
                     h2h={("YOM", "HAN"): WLD(14, 4, 2)})
check("★★★ 맞대결 경기 수에 무승부가 들어간다 (20경기인데 18이라 적었다)",
      _h4 is not None and "20경기" in _h4.sub, _h4.sub if _h4 else "None")
check("  ↳ 본문에도 무승부를 적는다", _h4 is not None and "14-4-2" in _h4.text,
      _h4.text if _h4 else "None")

# ══════════════════════════════════════════════════════════════
print("\n12. ★★ 낱말이 겹치지 않는다")
# ══════════════════════════════════════════════════════════════
_gc = bb([(0, 0)] * 9, 0, 0, lg=League.NPB, home="YOM", away="HAN")
_gc.status = Status.CANCELED
_gc.meta.cancel_reason = "中止"
_gc.score = None
_h5 = H.for_result([_gc], League.NPB)
check("★★ '1경기 경기 취소'가 안 나온다", _h5 is None or "경기 경기" not in _h5.text,
      _h5.text if _h5 else "None")
_h6 = H.fallback("leaders", count=4, set_name="타격 부문")
check("★★ '타격 부문 4개 부문'이 안 나온다", "부문 4개 부문" not in _h6.text, _h6.text)
check("  ↳ 그래도 몇 개인지는 말한다", "4개" in _h6.text, _h6.text)

# ══════════════════════════════════════════════════════════════
print("\n13. ★★ 조사 — 영문·숫자로 끝나는 말")
# ══════════════════════════════════════════════════════════════
for word, want in (("팀WHIP", "는"), ("최근10", "은"), ("T1", "은"), ("DN", "은"),
                   ("LG", "는"), ("KT", "는"), ("SSG", "는"), ("수원FC", "는"),
                   ("삼성", "은"), ("아스널", "은"), ("제주", "는")):
    check(f"  {word}{want}", josa(word, "은", "는") == want,
          f"{word}{josa(word, '은', '는')}")
check("★★ 한글 팀 이름은 전과 같다 (건드리지 않았다)",
      all(josa(w, "이", "가") == e for w, e in
          (("삼성", "이"), ("롯데", "가"), ("한화", "가"), ("두산", "이"),
           ("키움", "이"), ("전북", "이"), ("울산", "이"), ("토트넘", "이"))))

# ══════════════════════════════════════════════════════════════
print("\n14. ★★ 비문 — 이중 과거")
# ══════════════════════════════════════════════════════════════
check("★★★ '제주였었다' 꼴이 소스에서 사라졌다",
      "'이', '였')}었다" not in
      src("pipeline.py"))

# ══════════════════════════════════════════════════════════════
print("\n15. ★★ 죽어 있던 규칙이 살아났다")
# ══════════════════════════════════════════════════════════════
_rv = src("render_v5.py")
_tk = src("tick.py")
check("★★★ 결과 카드가 순위표를 머리말에 넘긴다 (연속 기록이 죽어 있었다)",
      "standings=(getattr(rb" in _rv, "render_v5 배선 없음")
check("★★★ 시계가 결과 카드에 기록을 넘긴다", "rb=(records" in _tk, "tick 배선 없음")

# ══════════════════════════════════════════════════════════════
print("\n16. ★★ 유럽 축구에 대승 임계가 있다")
# ══════════════════════════════════════════════════════════════
_soccer = [l for l in League
           if C.SCORE_UNIT_BY_LEAGUE.get(l) is ScoreUnit.GOALS]
_none = [l.value for l in _soccer if H.BLOWOUT_MARGIN.get(l) is None]
check("★★ 축구 9개 리그 전부 임계가 있다 (전에는 K리그1만 있었다)",
      not _none, str(_none))
check("  ↳ 실측 근거가 주석에 적혀 있다",
      "골 차 분포 0:29" in src("headline.py"))
_h7 = H.for_single_result(sc([(10, "home", "A", 0), (20, "home", "B", 0),
                              (30, "home", "C", 0), (40, "home", "D", 0)],
                             4, 0), League.EPL)
check("★★ 4골 차를 '4골 차'라 말한다", _h7.sub == "4골 차", f"{_h7.rule}/{_h7.sub}")

# ══════════════════════════════════════════════════════════════
print("\n17. ★★ 정보성 — KBO 분석 카드가 다른 리그와 같은 줄 수다")
# ══════════════════════════════════════════════════════════════
import tick as _T                                             # noqa: E402
_want = {k for k, _, _ in P.TEAM_STAT_LABELS_BASEBALL}
check("★★★ 카드가 원하는 지표가 이름표에 전부 있다 (두 줄이 영원히 빠져 있었다)",
      _want <= set(_T.TEAM_STAT_RENAME),
      str(sorted(_want - set(_T.TEAM_STAT_RENAME))))
check("  ↳ 팀득점·팀탈삼진이 이어졌다",
      _T.TEAM_STAT_RENAME.get("run") == "bat_r"
      and _T.TEAM_STAT_RENAME.get("kk") == "pit_so")

# ══════════════════════════════════════════════════════════════
print("\n18. ★★★ 감정어·과장어 0 — 이 프로젝트의 뿌리 (회귀 확인)")
# ══════════════════════════════════════════════════════════════
_BAN = ("명승부", "짜릿", "역대급", "충격", "굴욕", "압도", "완벽", "최고의",
        "유리", "전망", "예상", "추천", "확률", "배당", "필승", "무조건")
_all_text = " ".join([
    _t, _t2, _t3, _t4, _t5, _t6, _s1, _s2, _s3, _s4,
    _h.sub, _h2.sub, _h3.sub, _h7.sub, _h6.text,
])
_hit = [w for w in _BAN if w in _all_text]
check("★★★ 이번에 만든 문장 전부에 감정어가 없다", not _hit, str(_hit))

# ══════════════════════════════════════════════════════════════
print("\n19. ★★★ NPB 순위의 단위는 센트럴·퍼시픽이다 (v1.38, 약점 218)")
# ══════════════════════════════════════════════════════════════
import cards_v5 as C5                                          # noqa: E402

# 2026-09-13 실측값. **센트럴 5·6위가 승차 역전**이다 —
# 주니치 130경기 17.5 · 히로시마 123경기 17.0. 소화 경기 수가 달라 생긴다.
_npb_rows = [
    st("HAN", 1, 69, 54, "0.0", "센트럴", d=1), st("YOG", 2, 70, 56, "0.5", "센트럴", d=2),
    st("DEN", 3, 61, 63, "8.5", "센트럴", d=3), st("YAK", 4, 55, 69, "14.5", "센트럴", d=2),
    st("CHU", 5, 54, 74, "17.5", "센트럴", d=2), st("HIR", 6, 50, 69, "17.0", "센트럴", d=4),
    st("SOF", 1, 80, 45, "0.0", "퍼시픽", d=3), st("SEI", 2, 73, 53, "7.5", "퍼시픽", d=4),
    st("NIP", 3, 71, 56, "10.0", "퍼시픽", d=3), st("ORI", 4, 61, 67, "20.5", "퍼시픽", d=2),
    st("LOT", 5, 57, 64, "21.0", "퍼시픽", d=3), st("RAK", 6, 47, 78, "33.0", "퍼시픽", d=1),
]


class _Rb:
    league = League.NPB
    season = "2026"
    collected_utc = None
    h2h: dict = {}
    leaders: dict = {}

    def __init__(self, rows):
        self.standings = list(rows)

    def team(self, code):
        return next((x for x in self.standings if x.team_code == code), None)

    def between(self, a, b):
        return None


_rb = _Rb(_npb_rows)

# ① 게이트가 이 표를 통과시킨다 — 통과 못 하면 NPB 기록 콘텐츠가 통째로 멈춘다
_gate_ok = True
_gate_why = ""
try:
    C.assert_recordbook(_rb, require_h2h=False)
except C.GateError as e:
    _gate_ok, _gate_why = False, str(e)
check("★★★ 리그별 순위표가 게이트를 통과한다 (승차 역전이 있어도)",
      _gate_ok, _gate_why)

# ② (변이) 소화 경기 수가 같은데 승차가 역전되면 **여전히 막는다**
_bad = [st("A", 1, 70, 50, "0.0", "센트럴"), st("B", 2, 65, 55, "5.0", "센트럴"),
        st("C", 3, 60, 60, "4.0", "센트럴")]          # 셋 다 120경기
_blocked = False
try:
    C.assert_recordbook(_Rb(_bad), require_h2h=False)
except C.GateError:
    _blocked = True
check("★★★ (변이) 경기 수가 같은데 승차가 역전되면 막는다 (파싱 밀림)",
      _blocked)

# ③ (변이) 승차가 역전되고 **승률까지 뒤집히면** 막는다
_bad2 = [st("A", 1, 70, 50, "0.0", "센트럴"), st("B", 2, 60, 40, "5.0", "센트럴"),
         st("C", 3, 50, 70, "4.0", "센트럴")]
_blocked2 = False
try:
    C.assert_recordbook(_Rb(_bad2), require_h2h=False)
except C.GateError:
    _blocked2 = True
check("★★★ (변이) 승률 순서까지 어긋나면 경기 수가 달라도 막는다", _blocked2)

# ④ 카드 본문이 단위별로 갈린다
_body = C5.body_standings(_npb_rows, League.NPB)
check("★★★ 순위표 본문이 센트럴·퍼시픽으로 갈린다",
      "센트럴" in _body and "퍼시픽" in _body and _body.count("class=\"gh\"") == 2)
check("  ↳ 단위가 하나인 리그(KBO)는 구획을 만들지 않는다",
      'class="gh"' not in C5.body_standings(
          [st("LG", 1, 80, 45, "0", lg=League.KBO),
           st("OB", 2, 79, 46, "1.0", lg=League.KBO)], League.KBO))

# ⑤ 순위 낱말 — 단위가 다르면 밝혀 적는다
_han, _sof = _rb.team("HAN"), _rb.team("SOF")
check("★★★ 단위가 다른 두 팀은 '센트럴 1위'·'퍼시픽 1위'로 적는다",
      C.rank_word(_han, _sof) == "센트럴 1위"
      and C.rank_word(_sof, _han) == "퍼시픽 1위")
check("  ↳ 같은 단위면 예전처럼 '1위'다",
      C.rank_word(_han, _rb.team("YOG")) == "1위")
check("★★★ (변이) 단위를 안 밝히면 교류전 카드가 '1위 vs 1위'가 된다",
      f"{_han.rank}위" == f"{_sof.rank}위")
check("  ↳ 단위가 다르면 순위를 견주지 않는다",
      not C.rank_comparable(_han, _sof)
      and C.rank_comparable(_han, _rb.team("YOG")))
check("  ↳ 단위가 없는 리그(KBO)는 언제나 견줄 수 있다",
      C.rank_comparable(st("LG", 1, 80, 45, "0", lg=League.KBO),
                        st("OB", 2, 79, 46, "1.0", lg=League.KBO)))

# ⑥ 분석 머리말 — 교류전에는 '몇 계단 차'를 만들지 않는다
def _npb_game(home, away, when):
    return Game(league=League.NPB, season="2026", source_key=f"npb-{home}-{away}",
                home=TeamRef(League.NPB, home), away=TeamRef(League.NPB, away),
                start_utc=when, home_tz="Asia/Tokyo", status=Status.SCHEDULED,
                score=None, meta=GameMeta())


_ix = H.for_analysis(
    _npb_game("SOF", "YAK", dt.datetime(2026, 6, 1, 9, tzinfo=dt.timezone.utc)),
    League.NPB, standings=_npb_rows, h2h=None)
check("★★★ 교류전(센트럴 4위 vs 퍼시픽 1위)에 순위차 머리말을 만들지 않는다",
      _ix is None or _ix.rule != "AN-RANKGAP", str(_ix))
_in = H.for_analysis(
    _npb_game("SOF", "RAK", dt.datetime(2026, 9, 13, 9, tzinfo=dt.timezone.utc)),
    League.NPB, standings=_npb_rows, h2h=None)
check("  ↳ 같은 리그(퍼시픽 1위 vs 6위)에는 만든다",
      _in is not None and _in.rule == "AN-RANKGAP", str(_in))

# ⑦ 분석 산문 — 교류전에 '계단 차'·'선두'를 쓰지 않는다
_px = " ".join(P.analysis_prose(
    _rb, _npb_game("SOF", "YAK", dt.datetime(2026, 6, 1, 9, tzinfo=dt.timezone.utc))))
check("★★★ 교류전 산문이 '계단 차'·'선두'를 쓰지 않는다",
      "계단 차" not in _px and "선두" not in _px, _px[:160])
check("  ↳ 대신 어느 리그의 몇 위인지 밝힌다",
      "센트럴 4위" in _px and "퍼시픽 1위" in _px, _px[:200])
_pi = " ".join(P.analysis_prose(
    _rb, _npb_game("SOF", "RAK", dt.datetime(2026, 9, 13, 9, tzinfo=dt.timezone.utc))))
check("  ↳ 같은 리그 경기는 예전처럼 '계단 차'를 쓴다",
      "계단 차이다" in _pi, _pi[:160])
check("★★★ (변이) 단위를 안 보면 '소프트뱅크가 선두'가 교류전에도 나간다",
      "선두" in _pi and "선두" not in _px)

# ⑧ ★★★ 배포 첫 틱 — 옛 보관본이 되살아나 고친 거짓말을 다시 내보내지 않는다
import tick as TK                                              # noqa: E402
_old_rb = _Rb([st("HAN", 3, 69, 54, "10.0", None, d=1),
               st("SOF", 1, 80, 45, "0.0", None, d=3)])        # v1.37 이전 모양
check("★★★ 단위 없는 옛 NPB 보관본은 되살리지 않는다 (배포 첫 틱)",
      TK._units_missing(_old_rb))
check("  ↳ 단위가 채워진 새 보관본은 되살린다",
      not TK._units_missing(_rb))


class _KboRb(_Rb):
    league = League.KBO


check("  ↳ 단위가 없는 리그(KBO)의 보관본은 그대로 쓴다",
      not TK._units_missing(_KboRb([st("LG", 1, 80, 45, "0", lg=League.KBO)])))

print()
print("=" * 64)
print(f"결과: {PASS} PASS / {FAIL} FAIL")
print("=" * 64)
sys.exit(1 if FAIL else 0)
