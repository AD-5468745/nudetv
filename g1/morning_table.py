#!/usr/bin/env python3
"""아침표 — **채널에 실제로 나간 것**을 리그 × 컨텐츠로 센다 (v1.72 신설).

대표님 지시(2026-09-20): *"아침표 뽑아놔."*

────────────────────────────────────────────────────────────────────
**이 표가 재는 것은 검증 묶음이 재는 것과 다르다.**

`verify_*` 는 *코드가 맞는가* 를 잰다. 이 표는 *채널에 나갔는가* 를 잰다.
둘은 다른 측정이다 — 검사 21묶음이 전부 통과한 채로 카드가 한 장도 안
나간 적이 이 저장소에 여러 번 있었다(게이트 탈락·큐 미등록·조용한 `None`).

★ **첫 수는 '에러 찾기'가 아니라 '한 번도 안 나간 것 찾기'다.**
  발송 대장에는 **보낸 것만** 줄이 생긴다. 안 보낸 것은 줄이 안 생긴다 —
  그래서 대장을 아무리 봐도 빠진 것은 안 보인다. 그래서 이 표는
  **0을 눈에 띄게 찍는 것**이 전부다.

재료는 `state/ledger.jsonl` 하나뿐이다 — 인터넷도, 소스도 안 탄다.
경기 수는 앵커 발송 수로 센다(앵커는 경기당 한 장이다).

돌리는 법:
    PYTHONPATH=g1:. python3 g1/morning_table.py            # 어제+오늘
    PYTHONPATH=g1:. python3 g1/morning_table.py 2026-09-19 # 그날만
"""
from __future__ import annotations

import collections
import json
import pathlib
import sys
from datetime import datetime, timedelta

G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))

import contract as C                                           # noqa: E402
import pipeline as P                                           # noqa: E402
from contract import ContentType, League, ScoreUnit            # noqa: E402

LEDGER = ROOT / "state" / "ledger.jsonl"

# 표에 세로로 세울 컨텐츠 — **경기·리그 단위로 나가는 것만.**
# 채널 전체로 한 장씩 나가는 것(오늘의 경기·나이트)은 리그 칸이 없어
# 아래 따로 센다.
PER_LEAGUE = ["anchor", "analysis", "pregame", "lineup", "kickoff",
              "goal_flash", "period_flash", "boxscore", "final_flash",
              "league_result", "standings", "leaderboard", "morning"]
CHANNEL_WIDE = ["daily_index"]

# ── ★ 오탐을 만들지 않는다 ───────────────────────────────────────
#
# **경기가 끝나야 나가는 것**을 '오늘' 칸에서 0이라고 찍으면 그건 사고가
# 아니라 **아직 시간이 안 된 것**이다. 오탐 하나가 감시를 통째로 꺼뜨린다 —
# 다음부터 아무도 이 표의 ✗를 안 본다(약점 182와 같은 규율).
# 그래서 **오늘 칸에서는 `…`(아직)** 으로 찍고 구멍으로 세지 않는다.
AFTER_GAME = {"final_flash", "league_result", "boxscore", "standings",
              "leaderboard", "goal_flash", "period_flash"}

# **꺼 둔 컨텐츠는 구멍이 아니다.** 목록을 여기 다시 적지 않고 코드가 쓰는
# 표를 그대로 읽는다 — 두 벌을 만들면 꺼 둔 것이 영영 사고로 남는다.
DISABLED = {c.value for c in P.DISABLED_CONTENT_TYPES}

SHORT = {"anchor": "앵커", "analysis": "분석", "pregame": "사전", "lineup": "명단",
         "kickoff": "킥오프", "goal_flash": "득점", "period_flash": "구간",
         "boxscore": "흐름", "final_flash": "종료", "league_result": "결과",
         "standings": "순위", "leaderboard": "리더", "morning": "모닝",
         "daily_index": "오늘의경기", "night_brief": "나이트"}


def duty(content: str, lg: League) -> bool:
    """그 리그에 그 컨텐츠가 **나갔어야 하는가.**

    ★ 조건을 여기서 다시 적지 않는다 — **코드가 쓰는 표를 그대로 읽는다.**
    표를 두 벌 만들면 어긋나고, 어긋난 쪽이 조용히 이긴다.
    """
    unit = C.SCORE_UNIT_BY_LEAGUE.get(lg)
    if content in DISABLED:
        return False                      # 일부러 꺼 둔 것은 의무가 아니다
    if content in ("anchor", "lineup", "kickoff", "final_flash",
                   "league_result", "period_flash", "pregame", "morning"):
        return True                       # 전 리그 공통
    if content == "analysis":
        return lg in P.ANALYSIS_LEAGUES
    if content in ("standings", "leaderboard"):
        return lg in P.RECORD_SOURCE_LEAGUES
    if content == "boxscore":
        return unit in C.BOXSCORE_SPORTS
    if content == "goal_flash":
        return unit is ScoreUnit.GOALS    # 골로 세는 종목 = 축구
    return False


def load(day_filter=None) -> tuple:
    """원장 → (그날 발송 맵, 종류별 총계, 종류별 마지막 시각)."""
    if not LEDGER.exists():
        print(f"  ✗ 원장이 없습니다: {LEDGER}")
        sys.exit(2)
    sent: dict = collections.defaultdict(set)   # (day, league, content) → idem
    total: dict = collections.Counter()
    last: dict = {}
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:                        # noqa: BLE001
            continue
        if d.get("state") != "sent":
            continue
        key = str(d.get("idem_key") or "").split("|")
        if len(key) < 3:
            continue
        ct, scope = key[1], key[2]
        at = d.get("sent_at_utc") or ""
        total[ct] += 1
        if at > last.get(ct, ""):
            last[ct] = at
        bits = scope.split(":")
        lgv = bits[0] if bits else ""
        dayv = bits[1][:10] if len(bits) > 1 else ""
        if not dayv and "#" in scope:            # daily_index: `2026-09-20#0005`
            dayv, lgv = scope.split("#")[0][:10], ""
        if day_filter and dayv not in day_filter:
            continue
        sent[(dayv, lgv, ct)].add(d.get("idem_key"))
    return sent, total, last


def kst_day(offset: int = 0) -> str:
    return (datetime.now(C.KST) + timedelta(days=offset)).strftime("%Y-%m-%d")


def main() -> int:
    days = sys.argv[1:] or [kst_day(-1), kst_day(0)]
    sent, total, last = load(set(days))
    now = datetime.now(C.KST)

    print("=" * 72)
    print(f"아침표 — 채널에 실제로 나간 것  ({now:%Y-%m-%d %H:%M} KST 기준)")
    print("=" * 72)

    # ── 표 ① 종류별 총 발송 수 + 마지막 시각 ─────────────────────
    # **점검의 첫 수.** `0건`인 종류와 `마지막이 하루 이상 전`인 종류가
    # 곧 사고 목록이다.
    print("\n① 종류별 총 발송 수 + 마지막 시각  (원장 전체 기간)")
    print(f"   {'종류':14}{'총계':>7}   마지막 발송")
    print("   " + "─" * 52)
    stale = zero = 0
    for ct in [c.value for c in ContentType]:
        n = total.get(ct, 0)
        if n == 0 and ct not in PER_LEAGUE and ct not in CHANNEL_WIDE:
            continue                      # 아직 안 만든 기능은 표에 안 올린다
        if ct in DISABLED:
            at = last.get(ct)
            _t = (f"{datetime.fromisoformat(at).astimezone(C.KST):%m-%d %H:%M}"
                  if at else "—")
            print(f"   {SHORT.get(ct, ct):14}{n:>7}   {_t}   (일부러 꺼 둠)")
            continue
        at = last.get(ct)
        if at:
            t = datetime.fromisoformat(at).astimezone(C.KST)
            age = (now - t).total_seconds() / 3600
            mark = "  ⚠ 하루 넘음" if age > 24 else ""
            if age > 24:
                stale += 1
            when = f"{t:%m-%d %H:%M}  ({age:.0f}시간 전){mark}"
        else:
            when = "  ✗✗ 한 번도 안 나감"
            zero += 1
        print(f"   {SHORT.get(ct, ct):14}{n:>7}   {when}")
    print(f"\n   → 한 번도 안 나간 종류 {zero}개 · 마지막이 하루 넘은 종류 {stale}개")

    # ── 표 ② 리그 × 컨텐츠 ───────────────────────────────────────
    live = [l for l in League if C.league_enabled(l)]
    for day in days:
        # 경기 수 = 그날 그 리그의 앵커 발송 수(앵커는 경기당 한 장)
        ngames = {l.value: len(sent.get((day, l.value, "anchor"), ())) for l in live}
        rows = [l for l in live if ngames.get(l.value)]
        today = (day == kst_day(0))
        print(f"\n② {day} — 리그 × 컨텐츠  "
              f"(경기가 있었던 리그 {len(rows)}개)")
        if not rows:
            print("   (그날 앵커가 한 장도 없습니다 — 경기가 없었거나"
                  " 앵커 자체가 안 나갔습니다)")
            continue
        head = "   " + f"{'리그':12}{'경기':>4} " + " ".join(
            f"{SHORT.get(c, c):>6}" for c in PER_LEAGUE)
        print(head)
        print("   " + "─" * (len(head) - 3))
        holes = []
        for l in rows:
            cells = []
            for ct in PER_LEAGUE:
                if not duty(ct, l):
                    cells.append(f"{'·':>6}")          # 의무 아님
                    continue
                n = len(sent.get((day, l.value, ct), ()))
                if n:
                    cells.append(f"{n:>6}")
                elif today and ct in AFTER_GAME:
                    cells.append(f"{'…':>6}")          # 아직 시간이 안 됐다
                else:
                    cells.append(f"{'✗':>6}")
                    holes.append((l.value, ct))
            print(f"   {l.value:12}{ngames[l.value]:>4} " + " ".join(cells))
        print("\n   · = 그 리그 의무 아님   ✗ = **의무인데 0건**"
              + ("   … = 오늘이라 아직 시간이 안 됨" if today else ""))
        if holes:
            byct = collections.Counter(ct for _, ct in holes)
            print(f"   → 구멍 {len(holes)}칸: " + " · ".join(
                f"{SHORT.get(ct, ct)} {n}개 리그" for ct, n in byct.most_common()))
        else:
            print("   → 구멍 없음")

        # 채널 전체 한 장짜리
        ch = {ct: len(sent.get((day, "", ct), ())) for ct in CHANNEL_WIDE}
        print("   채널 전체: " + " · ".join(
            f"{SHORT.get(k, k)} {v}건" + ("  ✗" if v == 0 else "")
            for k, v in ch.items()))

    print("\n" + "=" * 72)
    print("이 표는 **나간 것**만 셉니다. 검증 묶음(코드가 맞는가)과 다른 측정입니다.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
