#!/usr/bin/env python3
"""테마 통일 검사 (v1.69 신설).

대표님 지적(2026-09-18, 채널을 훑으시고): *"너무 보기 힘들고 눈에 잘 안들어와.
전체적인 이미지를 바꿔야겠다."*

원인 1위는 취향이 아니라 **구조**였다 — 테마가 두 개로 갈려 있었다:

    KBO · KBL · V리그 · K리그   →  paper (밝은 크림색)
    MLB · NPB · 유럽 · MLS      →  dark  (남색)

같은 채널을 훑는데 두 세계가 번갈아 나온다. 눈이 기준을 못 잡으니 개별 카드가
아무리 정갈해도 전체가 산만하다. **카드 한 장씩 고쳐서는 절대 안 풀린다.**

돌리는 법:  PYTHONPATH=g1:. python3 g1/verify_theme.py
"""
from __future__ import annotations

import pathlib
import sys

G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))

import contract as C                                          # noqa: E402
from contract import CARD_THEME_BY_LEAGUE, League             # noqa: E402

# 대표님이 고른 방향(2026-09-20): **밝은 하나 + 리그별 색.**
# 어두운 안을 폰에서 보시고: *"어두운테마 폰에서 보기 힘들더라."*
# 리그 구분은 테마가 아니라 `LEAGUE_COLORS` 의 잉크색이 맡는다 — 밝은 바탕
# 대비 5.0~8.5로 이미 실측 검증된 표다.
CHANNEL_THEME = "paper"

ok = fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


print("=" * 62)
print("테마 통일 — 채널 전체가 한 세계인가")
print("=" * 62)

live = [lg for lg in League if C.league_enabled(lg)]
themes = {lg: CARD_THEME_BY_LEAGUE.get(lg) for lg in live}
odd = sorted(lg.value for lg, t in themes.items() if t != CHANNEL_THEME)

check(f"★★★ 발행 중인 {len(live)}개 리그가 **전부 같은 테마**를 쓴다",
      not odd, "다른 테마: " + ", ".join(odd))
check(f"  ↳ 그 테마가 대표님이 고른 `{CHANNEL_THEME}` 이다",
      set(themes.values()) == {CHANNEL_THEME} if not odd else False,
      str(sorted(set(str(t) for t in themes.values()))))

# **표에 빠진 리그가 없어야 한다.** 빠지면 기본값으로 떨어져 조용히 다른
# 모양이 나간다 — 테마가 갈린 것과 같은 사고인데 더 안 보인다.
missing = sorted(lg.value for lg in live if CARD_THEME_BY_LEAGUE.get(lg) is None)
check("★★ 표에 빠진 리그가 없다 (빠지면 조용히 기본값으로 떨어진다)",
      not missing, "빠짐: " + ", ".join(missing))

# ── 리그 로고 (v1.69) ──────────────────────────────────────────
# 대표님: *"각 리그로고를 수집해서 함께 사용하자"* → *"없는리그는 있을 수 없어."*
#
# **주소가 살아 있는지는 여기서 안 본다** — 검사가 인터넷을 타면 비행기
# 모드에서 실패하고, 그럼 아무도 검사를 안 믿게 된다. 여기서 지키는 것은
# 하나다: **발행하는 리그가 표에서 빠지지 않는다.** 빠지면 오류 없이
# 글자 라벨로 조용히 나간다(로고는 장식 보강기라 실패해도 안 멈춘다).
from adapters import logos as _LG                             # noqa: E402

no_logo = sorted(lg.value for lg in live
                 if not _LG.LEAGUE_LOGO_URLS.get(lg.value))
check(f"★★★ 발행 중인 {len(live)}개 리그가 **전부 로고 주소를 갖는다**",
      not no_logo, "로고 없음: " + ", ".join(no_logo))

# ── 변이시험 — 검사가 실제로 듣는가 ────────────────────────────
# 한 리그만 다른 테마로 바꿔 보고 위 검사가 잡는지 확인한다. 안 잡으면
# 이 파일은 통과 도장만 찍어 주는 장식이다.
_probe = live[0]
_saved = CARD_THEME_BY_LEAGUE.get(_probe)
try:
    CARD_THEME_BY_LEAGUE[_probe] = "paper" if CHANNEL_THEME == "dark" else "dark"
    _caught = any(CARD_THEME_BY_LEAGUE.get(lg) != CHANNEL_THEME for lg in live)
    check("★★ (변이) 한 리그만 테마를 바꾸면 검사가 잡는다", _caught)
finally:
    if _saved is not None:
        CARD_THEME_BY_LEAGUE[_probe] = _saved

_pl = live[0]
_saved_u = _LG.LEAGUE_LOGO_URLS.pop(_pl.value, None)
try:
    check("★★ (변이) 한 리그의 로고를 빼면 검사가 잡는다",
          any(not _LG.LEAGUE_LOGO_URLS.get(lg.value) for lg in live))
finally:
    if _saved_u is not None:
        _LG.LEAGUE_LOGO_URLS[_pl.value] = _saved_u

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
