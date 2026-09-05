"""헤드라인 적대적 검증 — **문장이 정말 그 값에서 나왔는가** (v1.12 신설).

숫자·구조 검증 957건은 "카드가 만들어지는가"를 본다. 이 파일은 다른 것을 본다:
**카드가 참말을 하는가.** 그리고 참이 아닐 때 **입을 다무는가.**

`verify_claims.py`가 이미 만들어진 문장을 뒤에서 검사한다면, 여기는 문장을 만드는
규칙 자체를 깨뜨려 본다 — 동률일 때, 표본이 없을 때, 조건이 아슬아슬할 때.

**새 규칙을 넣으면 여기에 검사를 하나 더한다.** 그게 이 파일의 존재 이유다.
"""
from __future__ import annotations

import pathlib
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import headline as H                                          # noqa: E402
from contract import (League, Score, ScoreUnit, Standing, Status,    # noqa: E402
                      StreakKind, TEAM_NAMES, WLD)

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


# ── 시험용 만들기 ────────────────────────────────────────────

class _Meta:
    def __init__(self, reason=None):
        self.cancel_reason = reason


class _Ref:
    """`Game.home`은 문자열이 아니라 TeamRef다. 실제와 같은 모양으로 시험한다 —
    문자열로 시험하면 실운영에서만 깨지는 검사가 된다(실제로 그렇게 놓쳤다)."""
    def __init__(self, code):
        self.team_code = code


class _G:
    def __init__(self, home, away, hs=None, as_=None, status=Status.FINAL,
                 unit=ScoreUnit.RUNS, reason=None, day="2026-09-04"):
        self.home, self.away = _Ref(home), _Ref(away)
        self.status = status
        self.score = Score(hs, as_, unit) if hs is not None else None
        self.meta = _Meta(reason)
        self.sports_day = day


def _st(code, rank, w, l, d=0, gb="0", streak=(StreakKind.NONE, 0), last10=None,
        group=None, league=League.KBO):
    return Standing(league=league, season="2026", team_code=code, rank=rank,
                    games=w + l + d, record=WLD(w, l, d),
                    pct=f"{w / (w + l):.3f}" if (w + l) else "0.000",
                    games_behind=gb, last10=last10,
                    streak_kind=streak[0], streak_len=streak[1], group=group)


KBO = League.KBO
NM = TEAM_NAMES[KBO]

# ═════════════════════════════════════════════════════════════
print("\n1. 모든 헤드라인은 등록된 규칙에서만 나온다")
# ═════════════════════════════════════════════════════════════
# 새 규칙을 몰래 넣으면 게이트가 잡는다. 그때 '이게 정말 사실인가'를 한 번 더 생각하게 된다.
_src = (pathlib.Path(__file__).resolve().parent / "headline.py").read_text(encoding="utf-8")
_used = set(re.findall(r'rule="([A-Z][A-Z0-9\-]+)"', _src))
check("소스의 규칙이 전부 ALL_RULES에 등록돼 있다",
      _used <= H.ALL_RULES, str(sorted(_used - H.ALL_RULES)))
check("ALL_RULES에 안 쓰이는 규칙이 없다 (죽은 규칙 금지)",
      H.ALL_RULES <= _used, str(sorted(H.ALL_RULES - _used)))
check("규칙을 실제로 찾았다 (이 검사가 헛돌지 않게)", len(_used) >= 15, f"{len(_used)}개")

# ═════════════════════════════════════════════════════════════
print("\n2. 결과 — 조건이 아니면 만들지 않는다")
# ═════════════════════════════════════════════════════════════
_close = [_G("KT", "HH", 3, 2), _G("LG", "OB", 1, 0), _G("SS", "LT", 4, 3)]
check("점수차가 임계 미만이면 헤드라인 없음 (강등)",
      H.for_result(_close, KBO) is None)

_blow = _close + [_G("HT", "WO", 14, 2)]          # 12점차 ≥ 임계 9
_h = H.for_result(_blow, KBO)
check("임계를 넘으면 큰 점수차를 올린다",
      _h and _h.rule == "R-BLOWOUT" and _h.facts["margin"] == 12, str(_h))
check("이긴 팀이 앞에 온다", _h and _h.text.startswith(NM["HT"]), _h.text if _h else "")

# **동률이면 '최다'라 단정하지 않는다** — 약점 60의 재발 방지
_tie = _close + [_G("HT", "WO", 14, 2), _G("NC", "SK", 2, 14)]
check("최다가 둘이면 '최다'라 하지 않는다 (동률 금지)",
      (H.for_result(_tie, KBO) or H.Headline("", "")).rule != "R-BLOWOUT",
      str(H.for_result(_tie, KBO)))

# 임계가 없는 리그는 규칙이 꺼져 있어야 한다
_kbl = [_G("A", "B", 120, 70, unit=ScoreUnit.POINTS)]
check("표본이 없는 리그(KBL)는 점수차 규칙이 꺼져 있다",
      H.BLOWOUT_MARGIN[League.KBL] is None
      and H.for_result(_kbl, League.KBL) is None)
check("표본이 없는 리그(V리그)도 꺼져 있다",
      H.BLOWOUT_MARGIN[League.VLEAGUE_M] is None)

# 취소
_off = [_G("KT", "HH", 3, 2),
        _G("LG", "OB", status=Status.CANCELED, reason="우천취소")]
_h = H.for_result(_off, KBO)
check("취소가 있으면 사유 원문을 그대로 쓴다",
      _h and _h.rule == "R-CANCEL" and "우천취소" in _h.text, str(_h))
_off2 = [_G("LG", "OB", status=Status.CANCELED, reason="우천취소"),
         _G("NC", "SK", status=Status.CANCELED, reason="폭염취소")]
_h2 = H.for_result(_off2, KBO)
check("사유가 섞이면 하나로 뭉뚱그리지 않는다",
      _h2 and "우천" not in _h2.text and "폭염" not in _h2.text, str(_h2))

# 연속 — 오늘 뛴 팀만, 리그 최장일 때만
_stand = [_st("LG", 1, 68, 51, 1, "0", (StreakKind.WIN, 5)),
          _st("KT", 2, 69, 44, 3, "1", (StreakKind.WIN, 3)),
          _st("HH", 3, 50, 65, 3, "8", (StreakKind.LOSS, 2))]
_today = [_G("LG", "OB", 1, 0), _G("KT", "HH", 3, 2)]
_h = H.for_result(_today, KBO, standings=_stand)
check("리그 최장 연승이 헤드라인이 된다",
      _h and _h.rule == "R-STREAK" and _h.facts["streak"] == 5, str(_h))
# 오늘 안 뛴 팀의 연승은 오늘 카드에 올리지 않는다
_h = H.for_result([_G("KT", "HH", 3, 2)], KBO, standings=_stand)
check("오늘 안 뛴 팀의 연승은 올리지 않는다",
      not (_h and _h.rule == "R-STREAK"), str(_h))
# 동률
_tie_st = _stand + [_st("SS", 4, 60, 60, 0, "9", (StreakKind.WIN, 5))]
_h = H.for_result([_G("LG", "OB", 1, 0), _G("SS", "NC", 2, 1)], KBO, standings=_tie_st)
check("최장 연승이 둘이면 '리그 최장'이라 하지 않는다",
      not (_h and _h.rule == "R-STREAK"), str(_h))

# ═════════════════════════════════════════════════════════════
print("\n3. 시작 알림 — '곧'이라고 쓰지 않는다")
# ═════════════════════════════════════════════════════════════
# 대표님이 콕 집은 자리다: "곧 시작"인데 실제로는 2시간 30분 뒤였다.
for _m, _want in ((128, "2시간 8분"), (52, "52분"), (180, "3시간"), (0, "0분")):
    _h = H.for_start_alert(_m, "18:30 한화 vs 롯데")
    check(f"{_m}분 → '{_want}'", _h.text.startswith(_want), _h.text)
_vague = ("곧", "잠시", "이제", "머지않", "임박")
check("'곧'·'잠시 후' 같은 말을 쓰지 않는다",
      not any(w in H.for_start_alert(150, "x").text for w in _vague))
check("남은 시간이 facts에 남는다 (게이트가 되짚을 수 있게)",
      H.for_start_alert(77, "x").facts["minutes"] == 77)
check("음수가 와도 죽지 않는다", H.for_start_alert(-5, "x").facts["minutes"] == 0)

# ═════════════════════════════════════════════════════════════
print("\n4. 순위표 — 단위를 섞지 않는다")
# ═════════════════════════════════════════════════════════════
_tight = [_st("KT", 1, 69, 44, 3, "0"), _st("SS", 2, 70, 46, 3, "0.5"),
          _st("LG", 3, 68, 51, 1, "4")]
_h = H.for_standings(_tight, KBO)
check("1·2위가 붙으면 그것이 헤드라인",
      _h and _h.rule == "S-GAP" and _h.facts["gap"] == 0.5, str(_h))
_wide = [_st("KT", 1, 69, 44, 3, "0"), _st("SS", 2, 60, 60, 0, "9")]
check("벌어져 있으면 '선두 다툼'이라 하지 않는다",
      (H.for_standings(_wide, KBO) or H.Headline("", "")).rule != "S-GAP")

# **단위가 섞이면 1위가 여럿이다** — MLB 지구·NPB 리그
_mlb = [_st("TB", 1, 83, 57, gb="0", group="AL 동부", league=League.MLB),
        _st("NYY", 2, 80, 60, gb="3", group="AL 동부", league=League.MLB),
        _st("MIL", 1, 87, 54, gb="0", group="NL 중부", league=League.MLB),
        _st("CHC", 2, 79, 62, gb="8", group="NL 중부", league=League.MLB)]
_h = H.for_standings(_mlb, League.MLB, group="AL 동부")
check("지구를 지정하면 그 안에서만 본다",
      _h is None or _h.facts.get("first") in ("TB", "NYY"), str(_h))
check("지구를 안 지정하면 1위가 여럿이라 섞인다 — 그래서 렌더는 반드시 지정한다",
      len({s.rank for s in _mlb}) < len(_mlb))

# 최근 10경기가 없는 리그(MLB·NPB)는 그 규칙이 안 걸린다
_no10 = [_st("TB", 1, 83, 57, gb="0", group="AL 동부", league=League.MLB),
         _st("NYY", 2, 80, 60, gb="3", group="AL 동부", league=League.MLB)]
check("최근10이 없으면 그 규칙을 쓰지 않는다 (없는 값을 지어내지 않는다)",
      (H.for_standings(_no10, League.MLB, group="AL 동부")
       or H.Headline("", "")).rule != "S-LAST10")
_with10 = [_st("HH", 9, 50, 65, 3, "20", last10=WLD(1, 9, 0)),
           _st("KT", 1, 69, 44, 3, "0"), _st("SS", 2, 60, 60, 0, "9")]
_h = H.for_standings(_with10, KBO)
check("최근10이 있으면 치우침을 잡는다",
      _h and _h.rule == "S-LAST10" and _h.facts["last10_win"] == 1, str(_h))

# ═════════════════════════════════════════════════════════════
print("\n5. 부문 — 공동 1위를 '1위'라 단정하지 않는다")
# ═════════════════════════════════════════════════════════════
from contract import LeaderEntry                              # noqa: E402


def _le(cat, rank, pid, name, team, val):
    return LeaderEntry(category=cat, stat_key=cat, rank=rank, player_id=pid,
                       name=name, team_code=team, value=val)


_spread = {
    "타율": [_le("타율", 1, "1", "구자욱", "SS", ".359")],
    "홈런": [_le("홈런", 1, "2", "김도영", "HT", "39")],
    "타점": [_le("타점", 1, "3", "오스틴", "LG", "112")],
    "도루": [_le("도루", 1, "4", "황성빈", "LT", "42")],
}
_h = H.for_leaders(_spread, KBO, ["타율", "홈런", "타점", "도루"])
check("1위가 전부 다른 팀이면 그것이 헤드라인",
      _h and _h.rule == "L-SPREAD" and _h.facts["count"] == 4, str(_h))

_sweep = dict(_spread)
_sweep["타점"] = [_le("타점", 1, "2", "김도영", "HT", "112")]
_h = H.for_leaders(_sweep, KBO, ["타율", "홈런", "타점", "도루"])
check("한 선수가 두 부문 1위면 그쪽이 우선",
      _h and _h.rule == "L-SWEEP" and _h.facts["count"] == 2, str(_h))

_cotie = dict(_spread)
_cotie["홈런"] = [_le("홈런", 1, "2", "김도영", "HT", "39"),
                 _le("홈런", 1, "9", "오스틴", "LG", "39")]
_h = H.for_leaders(_cotie, KBO, ["타율", "홈런", "타점", "도루"])
check("공동 1위 부문은 세지 않는다",
      _h is None or _h.facts.get("count", 0) < 4, str(_h))

# ═════════════════════════════════════════════════════════════
print("\n6. 분석 — 승패를 예측하지 않는다")
# ═════════════════════════════════════════════════════════════
_g = _G("LT", "HH", None, None, status=Status.SCHEDULED)
_h2h = {("LT", "HH"): WLD(3, 8, 0), ("HH", "LT"): WLD(8, 3, 0)}
_h = H.for_analysis(_g, KBO, standings=_stand, h2h=_h2h)
check("한쪽으로 기운 상대전적이 헤드라인",
      _h and _h.rule == "AN-H2H" and _h.facts["win"] == 8, str(_h))
_even = {("LT", "HH"): WLD(6, 5, 0), ("HH", "LT"): WLD(5, 6, 0)}
check("팽팽하면 상대전적을 올리지 않는다",
      (H.for_analysis(_g, KBO, standings=_stand, h2h=_even)
       or H.Headline("", "")).rule != "AN-H2H")
_few = {("LT", "HH"): WLD(1, 3, 0), ("HH", "LT"): WLD(3, 1, 0)}
check("표본이 적으면(4경기) 편중이라 하지 않는다",
      (H.for_analysis(_g, KBO, standings=_stand, h2h=_few)
       or H.Headline("", "")).rule != "AN-H2H")
_pred = ("이길", "승리 예상", "우세할", "유리하", "전망")
_all_text = " ".join(
    (H.for_analysis(_g, KBO, standings=_stand, h2h=h) or H.fallback("analysis")).text
    for h in (_h2h, _even, _few))
check("예측하는 말이 없다 (선발투수가 없어 근거가 없다)",
      not any(w in _all_text for w in _pred), _all_text)

# ═════════════════════════════════════════════════════════════
print("\n7. 강등 — 못 만들면 상태만 적는다")
# ═════════════════════════════════════════════════════════════
for kind, kw, want in (("result", {"final": 4, "off": 1}, "4경기 종료"),
                       ("morning", {"count": 5}, "오늘 5경기"),
                       ("standings", {"label": "현재 순위"}, "현재 순위"),
                       ("leaders", {"set_name": "타격", "count": 4}, "타격 4개 부문"),
                       ("analysis", {}, "오늘의 맞대결"),
                       ("night", {"leagues": 4, "final": 18}, "4개 리그 18경기 종료")):
    _h = H.fallback(kind, **kw)
    check(f"강등 '{kind}' → '{want}'", _h.text == want, _h.text)
check("강등도 규칙 번호를 남긴다",
      all(H.fallback(k, **kw).rule in H.ALL_RULES
          for k, kw in (("result", {"final": 1}), ("morning", {"count": 1}),
                        ("standings", {}), ("leaders", {"count": 1}),
                        ("analysis", {}), ("night", {"leagues": 1, "final": 1}))))

# ═════════════════════════════════════════════════════════════
print("\n8. ★ 문장이 쓴 숫자는 facts에 있어야 한다")
# ═════════════════════════════════════════════════════════════
# **이 검사가 이 파일의 핵심이다.** 문장에 나온 숫자가 facts에 없으면
# 그 숫자는 어디서 왔는지 아무도 모른다 — 그게 '지어낸 문장'의 정의다.
_cases = [
    H.for_result(_blow, KBO),
    H.for_result(_today, KBO, standings=_stand),
    H.for_result(_off, KBO),
    H.for_standings(_tight, KBO),
    H.for_standings(_with10, KBO),
    H.for_leaders(_spread, KBO, ["타율", "홈런", "타점", "도루"]),
    H.for_analysis(_g, KBO, standings=_stand, h2h=_h2h),
    H.for_start_alert(128, "18:30 한화 vs 롯데"),
    H.for_morning([_G("KT", "HH", status=Status.SCHEDULED)] * 5, KBO,
                  kst_times=["18:30"] * 5),
]
_bad = []
for _h in _cases:
    if _h is None:
        continue
    nums = set(re.findall(r"\d+(?:\.\d+)?", _h.text))
    vals = set()
    for v in _h.facts.values():
        if isinstance(v, (int, float)):
            vals |= {str(v), f"{v:g}"}
        elif isinstance(v, str):
            vals |= set(re.findall(r"\d+(?:\.\d+)?", v))
        elif isinstance(v, (list, tuple)):
            vals.add(str(len(v)))
            for x in v:
                vals |= set(re.findall(r"\d+(?:\.\d+)?", str(x)))
    missing = nums - vals
    if missing:
        _bad.append(f"[{_h.rule}] '{_h.text}' 근거 없는 수 {sorted(missing)}")
check("문장의 모든 숫자가 facts에서 나왔다", not _bad, " / ".join(_bad[:3]))
check("검사가 헛돌지 않았다 (문장을 실제로 만들었다)",
      sum(1 for h in _cases if h) >= 8, f"{sum(1 for h in _cases if h)}개")

# ═════════════════════════════════════════════════════════════
print("\n9. 임계값이 실측에서 왔는가")
# ═════════════════════════════════════════════════════════════
# 표본이 있는 리그에만 임계가 있어야 한다. 지어낸 숫자를 넣으면 첫날부터 거짓이다.
_measured = {League.KBO, League.NPB, League.MLB, League.KL1,
             League.LCK, League.INTL_LOL}
_unmeasured = {League.KBL, League.VLEAGUE_M, League.VLEAGUE_W}
check("실측한 리그에는 임계가 있다",
      all(H.BLOWOUT_MARGIN.get(l) for l in _measured),
      str({l.value: H.BLOWOUT_MARGIN.get(l) for l in _measured}))
check("실측 못 한 리그에는 임계가 없다 (지어내지 않는다)",
      all(H.BLOWOUT_MARGIN.get(l) is None for l in _unmeasured))
check("모든 리그가 표에 있다 (빠뜨리면 조용히 꺼진다)",
      _measured | _unmeasured <= set(H.BLOWOUT_MARGIN))
check("맵 스코어 리그의 임계가 득점 리그보다 작다 (단위가 다르다)",
      H.BLOWOUT_MARGIN[League.LCK] < H.BLOWOUT_MARGIN[League.KBO])


# ══════════════════════════════════════════════════════════════
print("\n10. 관전 포인트 — 읽어 주는 것과 지어내는 것의 경계")
# ══════════════════════════════════════════════════════════════
#
# 대표님이 프로토 고객을 위한 "어느 팀이 좋아 보인다"를 요청했다.
# **여기서 선을 넘으면 카드가 도박 예측을 하는 물건이 된다.** 그 선을 검사로 못 박는다.

_M = H.Metric
_SET = [
    _M("순위", "1위", "3위", 1, 3, higher_better=False, can_headline=False),
    _M("승률", ".607", ".567", .607, .567),
    _M("팀 타율", ".279", ".267", .2791, .26678),
    _M("팀 평균자책", "4.13", "4.78", 4.125, 4.778, higher_better=False),
    _M("홈런", "110개", "112개", 110, 112),
    _M("최근 5경기", "3승 2패", "4승 1패", 3, 4),
]
_v = H.for_preview(away_name="삼성", home_name="LG", metrics=_SET, h2h_text="삼성 8승 6패")
_txt = " ".join(_v.lines)

check("우세한 쪽을 집계해 말한다", "6개 항목 중 4개" in _txt, _txt[:60])
check("낮을수록 좋은 항목의 방향을 뒤집어 읽는다 (평균자책 4.13이 앞선다)",
      _SET[3].winner() == "away")
# **순위는 '가장 벌어진 곳'이 될 수 없다** — 다른 항목들의 결과라서 동어반복이 된다
check("★ 순위를 '가장 벌어진 곳'으로 꼽지 않는다",
      "가장 벌어진 곳은 팀 평균자책" in _txt, _txt)
# **한쪽 근거만 대면 예측이 되고, 양쪽을 대면 정보가 된다**
check("★ 뒤진 쪽이 앞선 항목을 반드시 함께 적는다",
      "다만" in _txt and "LG가 낫다" in _txt, _txt)
check("조사를 받침에 맞춰 고른다 ('LG가', '전북이')",
      "LG가 낫다" in _txt
      and "전북이" in " ".join(H.for_preview(
          away_name="서울", home_name="전북", metrics=_SET).lines), _txt)

# ── 금지어 — 이 낱말이 하나라도 나오면 카드가 예측을 하는 물건이 된다 ──
_bad = [w for w in H.BANNED_WORDS if w in _txt]
check("★ 확률·추천·베팅 같은 낱말을 쓰지 않는다", not _bad, str(_bad))

# ── 문장의 모든 수가 facts에서 나왔는가 (8절과 같은 규율) ──
_nums = set(re.findall(r"\d+(?:\.\d+)?", _txt))
_fact_nums = set()
for v_ in _v.facts.values():
    _fact_nums |= set(re.findall(r"\d+(?:\.\d+)?", str(v_)))
_orphan = sorted(_nums - _fact_nums)
check("★ 문장에 찍힌 모든 수가 facts에 있다 (출처 못 대는 수는 지어낸 것)",
      not _orphan, f"근거 없는 수: {_orphan}")

# ── 억지로 한쪽을 고르지 않는다 ──
# 동점 하나를 섞는다 — **분모가 '비교한 6개'가 아니라 '갈린 4개'여야 한다.**
_even = [_M("가", "1", "2", 1, 2), _M("나", "3", "2", 3, 2), _M("다", "1", "1", 1, 1),
         _M("라", "5", "4", 5, 4), _M("마", "1", "9", 1, 9)]
_vs = H.for_preview(away_name="A", home_name="B", metrics=_even)
check("★ 숫자가 갈리면 갈렸다고 쓴다 (한쪽을 억지로 고르지 않는다)",
      _vs.rule == "V-SPLIT" and "반씩 갈린다" in _vs.lines[0], str(_vs.lines[:1]))
check("★ 분모는 '비교한 항목'이 아니라 '승부가 갈린 항목'이다",
      "4개 항목 중 2대 2" in _vs.lines[0] and _vs.facts["compared"] == 5,
      str(_vs.lines[0]))

check("항목이 3개보다 적으면 만들지 않는다",
      H.for_preview(away_name="A", home_name="B", metrics=_SET[:2]) is None)
check("전부 동점이면 만들지 않는다",
      H.for_preview(away_name="A", home_name="B",
                    metrics=[_M(f"x{i}", "1", "1", 1, 1) for i in range(4)]) is None)
check("맞대결이 없으면 그 줄을 넣지 않는다",
      not any("맞대결" in x for x in
              H.for_preview(away_name="삼성", home_name="LG", metrics=_SET).lines))
check("만든 규칙이 등록 목록에 있다", _v.rule in H.ALL_RULES and "V-SPLIT" in H.ALL_RULES)
check("배지도 금지어를 안 쓴다",
      not [w for w in H.BANNED_WORDS if w in _v.pick], _v.pick)

# ── 예상 한 줄 — 대표님 정정: "승부예측이라기 보다는 어디가 이길것같다 예상" ──
check("★ 어느 쪽이 좋아 보이는지 한 줄로 가리킨다", _v.pick == "삼성 우세", _v.pick)
# **비율이 아니라 격차로 잰다.** 4대 2를 비율(0.67)로 재면 '근소'가 되는데
# 사람 눈에 4대 2는 근소가 아니다.
check("★ 우세의 세기를 격차로 잰다 (4대 2는 '근소'가 아니다)",
      H._edge_word(4, 2) == "우세" and H._edge_word(3, 2) == "근소 우세"
      and H._edge_word(5, 0) == "크게 우세",
      f"{H._edge_word(4, 2)} / {H._edge_word(3, 2)} / {H._edge_word(5, 0)}")
check("★ 반반이면 한쪽을 찍지 않고 '팽팽'이라고 쓴다", _vs.pick == "팽팽", _vs.pick)
# **결과를 보장하는 말은 계속 막는다.** 예상은 보장이 아니다.
check("'확실'·'필승'·'무조건'은 금지 목록에 남아 있다",
      {"확실", "필승", "무조건", "장담"} <= set(H.BANNED_WORDS))


# ══════════════════════════════════════════════════════════════
print("\n11. 한 경기짜리 결과 카드 — 개수가 아니라 이야기를 말한다")
# ══════════════════════════════════════════════════════════════
import datetime as _dt                                          # noqa: E402
from contract import (Game as _G, GameMeta as _M, Score as _S,  # noqa: E402
                      ScoreUnit as _U, TeamRef as _T, KST as _K)


def _g1(aw, hm, sa, sh, rows=(), unit=_U.RUNS, lg=League.KBO):
    t = _dt.datetime(2026, 9, 5, 17, 0, tzinfo=_K).astimezone(_dt.timezone.utc)
    m = _M()
    m.line_score = list(rows)
    return _G(league=lg, season="2026", source_key=aw + hm, away=_T(lg, aw),
              home=_T(lg, hm), start_utc=t, home_tz="Asia/Seoul",
              status=Status.FINAL, score=_S(home=sh, away=sa, unit=unit), meta=m)


_h1 = H.for_single_result(_g1("HH", "LT", 11, 6, [(0, 0)] * 7 + [(0, 6), (0, 0)]), League.KBO)
check("점수를 그대로 말한다", "한화 11 : 6 롯데" == _h1.text, _h1.text)
check("★ 한 구간에 몰아친 점수를 잡는다", "8회에만 6점" == _h1.sub, _h1.sub)
check("문장의 모든 수가 facts에 있다",
      set(re.findall(r"\d+", _h1.text + _h1.sub))
      <= {str(v) for v in _h1.facts.values()},
      f"{_h1.facts} / {_h1.text} {_h1.sub}")

# **강조되는 칸과 머리말이 말하는 칸은 같은 기준이어야 한다.**
# 두 곳이 갈리면 카드에서 초록으로 빛나는 칸과 머리말이 가리키는 칸이 어긋난다.
import cards_v5 as _C5                                          # noqa: E402
check("★ 강조 기준과 머리말 기준이 같은 값이다",
      H.BIG_PERIOD_RUNS == _C5.BIG_CELL_MIN,
      f"headline {H.BIG_PERIOD_RUNS} vs cards {_C5.BIG_CELL_MIN}")

_h2 = H.for_single_result(_g1("SS", "LG", 4, 3), League.KBO)
check("한 점 차를 잡는다", _h2.sub == "한 점 차" and _h2.rule == "G-CLOSE", str(_h2))
_h3 = H.for_single_result(_g1("HT", "OB", 15, 2), League.KBO)
check("대승을 잡는다 (리그별 실측 임계값)",
      _h3.rule == "G-BLOWOUT1" and "13점 차" == _h3.sub, str(_h3))
_h4 = H.for_single_result(_g1("HT", "OB", 7, 4), League.KBO)
check("아무 규칙도 안 걸리면 점수만 말한다 (없는 이야기를 짓지 않는다)",
      _h4.rule == "G-SCORE" and _h4.sub == "", str(_h4))

# 세트·쿼터 종목은 구간 이름이 다르다
_h5 = H.for_single_result(
    _g1("HK", "PEPPER", 1, 3, [(25, 21), (20, 25), (25, 23), (25, 16)],
        unit=_U.SETS, lg=League.VLEAGUE_W), League.VLEAGUE_W)
check("종목에 따라 구간 이름이 바뀐다 (회/세트/쿼터)",
      "세트에만" in (_h5.sub or ""), str(_h5))

check("진행 중 경기에는 머리말을 만들지 않는다",
      H.for_single_result(
          _G(league=League.KBO, season="2026", source_key="x",
             away=_T(League.KBO, "HH"), home=_T(League.KBO, "LT"),
             start_utc=_dt.datetime.now(_dt.timezone.utc), home_tz="Asia/Seoul",
             status=Status.LIVE, score=_S(home=1, away=0, unit=_U.RUNS), meta=_M()),
          League.KBO) is None)
check("새 규칙이 전부 등록 목록에 있다",
      {"G-BIGPERIOD", "G-CLOSE", "G-BLOWOUT1", "G-SCORE"} <= H.ALL_RULES)

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
