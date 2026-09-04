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

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
