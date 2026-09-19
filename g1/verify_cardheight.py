#!/usr/bin/env python3
"""카드 높이 실측 (v1.69 신설) — **글씨를 키운 대가를 재는 자리.**

대표님 지시(2026-09-19): *"사이즈도 더 키우고 폰트도 전부 조금더 키우자."*
그래서 v1.69에서 모든 글자를 `TYPE_BOOST`(1.25배)만큼 키웠다.

글씨를 키우면 **카드가 길어진다.** 그리고 카드가 상한(2000px)을 넘으면
`render_png`가 `None`을 돌려주고 — **그 컨텐츠는 오류 한 줄 없이 사라진다.**
이 저장소에서 가장 자주 났던 사고 유형 그대로다(조용한 실패).

그래서 **가장 긴 판**을 실제로 그려 본다. 코드가 통과하는 것과 그림이
나오는 것은 다른 측정이다 — 여기서는 진짜 브라우저로 그린다.

돌리는 법:  PYTHONPATH=g1:. python3 g1/verify_cardheight.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))

import contract as C                                           # noqa: E402
import cards_v5 as C5                                          # noqa: E402
import render_v5 as R5                                         # noqa: E402
from adapters import logos as LG                               # noqa: E402
from contract import (GameMeta, League, Score, ScoreUnit,      # noqa: E402
                      Standing, Status, StreakKind, TeamRef, WLD)

# 로고는 인터넷을 탄다 — 검사가 남의 서버 사정에 흔들리면 아무도 안 믿는다.
# 로고가 없을 때가 **오히려 더 긴 판**이다(글자가 로고보다 자리를 먹는다).
LG.LOGOS_ENABLED = False

KST = C.KST
NOW = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
MAX = C.CARD_MAX_HEIGHT_PX
# 상한에 아슬아슬하면 **다음 한 글자에 사라진다.** 여유를 함께 본다.
SNUG = int(MAX * 0.90)

ok = fail = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


# ── 가장 긴 판을 만드는 재료 ─────────────────────────────────────
# 이름은 **실제로 채널에 나가는 것 중 가장 긴 것들**을 쓴다.
LONG = ["브라이턴 앤 호브 알비온", "울버햄튼 원더러스", "토트넘 홋스퍼",
        "보루시아 묀헨글라트바흐", "파리 생제르맹", "맨체스터 유나이티드"]


class _G:
    def __init__(self, lg, a, h, *, venue="", meta=None, score=None,
                 status=Status.SCHEDULED, hh=10):
        self.away = TeamRef(league=lg, team_code=a)
        self.home = TeamRef(league=lg, team_code=h)
        self.status, self.score, self.venue = status, score, venue
        self.meta = meta or GameMeta()
        self.start_utc = datetime(2026, 9, 20, hh, 0, tzinfo=timezone.utc)
        self.start_kst = self.start_utc.astimezone(KST)
        self.start_local = self.start_kst
        self.sports_day = "2026-09-20"
        self.game_id = f"2026-09-20-{a}-{h}"
        self.is_terminal = status is Status.FINAL

    def is_draw(self):
        return False


def _st(lg, code, rank, w, l, d, sk, sn, group=None):
    return Standing(league=lg, season="2026", team_code=code, rank=rank,
                    games=w + l + d, record=WLD(w, l, d),
                    pct=f"0.{600 - rank * 7:03d}", games_behind=str(rank - 1),
                    last10=WLD(7 - rank % 7, 3 + rank % 7, 0),
                    streak_kind=sk, streak_len=sn, group=group)


class _RB:
    """순위·맞대결을 주는 최소 기록실."""
    def __init__(self, rows, between=None):
        self.standings = rows
        self._b = between
        self._by = {r.team_code: r for r in rows}

    def team(self, code):
        return self._by.get(code)

    def between(self, a, h):
        return self._b


_OUT = pathlib.Path(tempfile.mkdtemp(prefix="nudetv-height-"))


def measure(name: str, built, *, snug: bool = True) -> None:
    """실제로 그려 보고 높이를 잰다. `built`는 (html, 캡션) 또는 None."""
    if built is None:
        check(f"★★ {name} — 카드가 만들어진다", False, "None이 돌아왔습니다")
        return
    html = built[0] if isinstance(built, tuple) else built
    png = _OUT / f"{name}.png"
    r = R5.render_png(html, png)
    if r is None:
        check(f"★★★ {name} — 상한 {MAX}px 안에 그려진다", False,
              "높이 게이트에 걸려 **통째로 사라집니다**")
        return
    w, h, _ = r
    check(f"★★★ {name} — 그려졌다 ({w}×{h}px)", True)
    if snug:
        check(f"  ↳ 여유가 있다 (≤{SNUG}px · 다음 한 글자에 안 사라진다)",
              h <= SNUG, f"{h}px — 상한까지 {MAX - h}px밖에 안 남았습니다")


print("=" * 62)
print(f"카드 높이 실측 — 글씨 {C5.TYPE_BOOST}배가 상한을 넘기지 않는가")
print("=" * 62)

try:
    from playwright.sync_api import sync_playwright   # noqa: F401
except Exception:                                     # noqa: BLE001
    print("  SKIP  playwright가 없습니다 — **SKIP은 PASS가 아닙니다**")
    print("\n결과: 0 PASS / 0 FAIL / 전부 SKIP")
    sys.exit(0)

# ── ① 앵커 — 비교줄 다섯이 다 찬 판 (v1.69에서 가장 길어진 카드) ──
_lg = League.EPL
_rows = [_st(_lg, "AAA", 1, 20, 3, 5, StreakKind.WIN, 6),
         _st(_lg, "BBB", 18, 4, 18, 6, StreakKind.LOSS, 5)]
_anchor = _G(_lg, "AAA", "BBB", venue="브라이턴 앤 호브 커뮤니티 스타디움",
             meta=GameMeta(lineup={"away": {"formation": "4231", "rows": []},
                                   "home": {"formation": "352", "rows": []}}))
C5.NAME_OVERRIDE = getattr(C5, "NAME_OVERRIDE", {})
measure("앵커(비교줄 5줄·긴 이름)",
        R5.anchor_card(_anchor, _lg, rb=_RB(_rows, WLD(12, 9, 4)), now=NOW))

# ── ② 경기 결과 — 하루치가 한 장에 다 들어가는 판 ────────────────
_games = [_G(League.KBO, f"A{i}", f"B{i}", status=Status.FINAL,
             score=Score(away=i, home=9 - i, unit=ScoreUnit.RUNS))
          for i in range(5)]
measure("경기 결과(5경기)", R5.result_card(_games, League.KBO, "2026-09-20"))

# ── ③ 순위표 — **실제로 순위표를 내는 리그 전부**, 그 리그의 구단 수로 ──
#
# 처음에는 '가장 구단이 많은 리그'로 30개짜리를 그려 봤는데 3505px가 나왔다.
# 그런데 시계(`tick.py`)는 순위표를 `RECORD_SOURCE_LEAGUES`에만 내고 그 표에는
# 30개짜리 리그가 없다 — **안 나가는 카드를 실패로 세는 것은 거짓 경보**다.
# 그래서 **계약이 정한 대상 전부를, 계약이 정한 구단 수로** 돈다. 나중에 그
# 표에 큰 리그가 들어오면 이 검사가 **자동으로 그 리그를 집어** 실패한다 —
# 그게 정확히 그때 알아야 할 사실이다(실제로 그러면 카드가 통째로 사라진다).
import pipeline as _P                                          # noqa: E402

for _slg in sorted(_P.RECORD_SOURCE_LEAGUES, key=lambda x: x.value):
    _n = C.LEAGUE_TEAM_COUNT.get(_slg) or 12
    _tbl = [_st(_slg, f"T{i:02d}", i + 1, 80 - i * 3, 55 + i * 3, 0,
                StreakKind.WIN if i % 2 else StreakKind.LOSS, 1 + i % 4)
            for i in range(_n)]
    measure(f"순위표 {_slg.value}({_n}개 구단)",
            R5.standings_card(_RB(_tbl), _slg, "2026-09-20"))

# ── ④ 선발 라인업 — 양 팀 11명씩 + 긴 이름 ───────────────────────
_fb_rows = [["골키퍼이름길다"], ["수비수하나", "수비수둘", "수비수셋", "수비수넷"],
            ["미드하나", "미드둘", "미드셋"], ["공격수하나", "공격수둘", "공격수셋"]]
_lu = _G(_lg, "AAA", "BBB", hh=12,
         meta=GameMeta(lineup={"away": {"formation": "433", "rows": _fb_rows},
                               "home": {"formation": "4231", "rows": _fb_rows}}))
measure("선발 라인업(11+11)",
        R5.lineup_card(_lu, _lg, now=_lu.start_utc - timedelta(minutes=40)))

# ── ⑤ 구간 속보 ──────────────────────────────────────────────────
_pd = _G(League.KBO, "AAA", "BBB", status=Status.LIVE,
         meta=GameMeta(live_score=(7, 6)))
measure("구간 속보", R5.period_card(_pd, League.KBO, label="연장 진입", now=NOW))

print(f"\n결과: {ok} PASS / {fail} FAIL")
print(f"(그림은 {_OUT} 에 있습니다 — 임시 폴더라 재부팅하면 사라집니다)")
sys.exit(1 if fail else 0)
