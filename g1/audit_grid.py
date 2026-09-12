#!/usr/bin/env python3
"""리그 × 콘텐츠 격자 — **0인 칸마다 이유를 댄다** (v1.33).

---

## 왜 만들었나 — 같은 방법이 하루에 결함 2개와 거짓 양성 3개를 냈다

2026-09-10, "리그 × 콘텐츠 격자를 만들고 0인 칸을 먼저 본다"(약점 200)가
**전 기간 0건짜리 결함 두 개**를 같은 날 찾아냈다:

  · KBO 킥오프 전 기간 0건   → v1.26
  · NPB 종료 속보 전 기간 0건 → v1.28

그런데 2026-09-12 같은 방법을 다시 돌렸더니 이번엔 **0인 칸 세 개가 전부
거짓 양성**이었다:

  · `start_alert` · `night_brief` 전 리그 0건 → **2026-09-07에 끈 콘텐츠다**
  · MLB·KL1 순위표·리더보드 0건            → **기록 카드를 안 켠 리그다**
  · KBL·V리그 전부 0건                      → **10월 개막, 비시즌이다**

세 가지 답이 **계약(`contract.py`·`pipeline.py`)에 이미 다 적혀 있었다.**
점검이 대장만 읽고 계약을 안 읽어서, 사람이 매번 손으로 가려내야 했다.
그날 세 번을 헛짚었다.

**이 도구는 그 가려내기를 계약에서 읽어 자동으로 한다.**

## 무디게 하는 것이 아니라 무게를 바로잡는다 (§7-139 · v1.23과 같은 처리)

v1.23이 알림에 한 것과 같다: **지우지 않고 내린다.** 사유가 붙은 0은
초록으로 접히지만 표에는 사유와 함께 그대로 남고, **사유를 못 대는 0은
그대로 빨간불이다.** 09-10 아침의 KBO 킥오프 상황을 넣으면 지금도 빨간불이
떠야 한다 — 그게 이 도구의 합격 기준이다(`verify_grid.py`).

## 세 등급으로 나눈다 — '재료 없음'을 초록으로 접지 않는다

|      등급      | 뜻                                             | 색  |
|----------------|------------------------------------------------|-----|
| `설명됨`       | 계약이 답한다 (끔·제외·미대상·비시즌·경기없음) | 초록 |
| `재료없음`     | 경기는 있는데 그 재료가 스냅샷에 없다          | 노랑 |
| `설명못함`     | 아무 사유에도 안 걸린다                        | **빨강** |
| `확인못함`     | 스냅샷이 없어 판정할 수 없다                   | 회색 |

**`재료없음`을 초록으로 접으면 안 된다.** 야구 라인업이 0건인 것은
"소스가 안 준다"가 맞지만 그건 **알려진 결함**이지 정상이 아니다.
접어 버리면 고칠 이유가 사라진다.

**`확인못함`을 초록으로도 빨강으로도 세지 않는다** (v1.32와 같은 규율) —
SKIP을 PASS로 세지 않는다.

## 계약을 못 읽으면 조용해지지 않고 시끄러워진다 (v1.23 §2)

사유 목록을 여기 다시 적지 않는다(§7-45). 전부 계약에서 읽는다.
계약을 못 읽으면 **도구가 죽는다** — 사유 없이 전부 초록으로 접히는 것보다
낫다. 조용해지는 쪽이 아니라 시끄러워지는 쪽이 안전한 실패다.

## 무엇을 안 건드리는가

**발행 경로를 한 줄도 안 고친다.** 읽기 전용이다 —
대장(`state/ledger.jsonl`)과 계약을 읽고 표를 그릴 뿐,
`tick`·`pipeline`·`render`·`sender`·`contract` 어느 것도 수정하지 않는다.
되돌리려면 이 파일 하나를 지우면 된다.

## 쓰는 법

    python3 g1/audit_grid.py                 # 최근 3일
    python3 g1/audit_grid.py --days 7
    python3 g1/audit_grid.py --json          # 예약 점검이 먹기 좋은 꼴
    python3 g1/audit_grid.py --state /path/to/state
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 계약을 못 읽으면 여기서 죽는다 (조용한 실패 금지) ──────────────
try:
    from contract import (  # noqa: E402
        DISABLED_CONTENT_TYPES, DISABLED_LEAGUES, IDEM_SEP, KST,
        QUEUED_CONTENT_TYPES, ContentType, League, in_season,
        player_names_localized,
    )
    from pipeline import ANALYSIS_LEAGUES, RECORD_SOURCE_LEAGUES  # noqa: E402
except Exception as _e:                                   # pragma: no cover
    print(f"✗ 계약을 못 읽었습니다 — 판정할 수 없습니다: {type(_e).__name__}: {_e}",
          file=sys.stderr)
    print("  (사유 없이 전부 초록으로 접는 것보다 여기서 멈추는 쪽이 안전합니다)",
          file=sys.stderr)
    raise SystemExit(2)


# ── 등급 ────────────────────────────────────────────────────────────
OK = "설명됨"
MATERIAL = "재료없음"
UNEXPLAINED = "설명못함"
UNKNOWN = "확인못함"

#: 기록으로 그리는 콘텐츠. 이 둘만 `RECORD_SOURCE_LEAGUES` 관문을 받는다.
RECORD_CONTENT = frozenset({ContentType.STANDINGS, ContentType.LEADERBOARD})

#: 그 리그 스냅샷에 재료가 있어야만 나가는 콘텐츠 → 재료 이름
MATERIAL_OF: dict[ContentType, str] = {
    ContentType.LINEUP: "lineup",
}


def explain_zero(
    league: League,
    ct: ContentType,
    *,
    in_season_flag: bool,
    games: "int | None",
    material: "int | None" = None,
) -> tuple[str, str]:
    """0인 칸 하나를 판정한다. **입출력이 없는 순수 함수** — 검사가 직접 몬다.

    `games`·`material`이 `None`이면 "스냅샷을 못 봤다"는 뜻이다.
    그때는 초록으로도 빨강으로도 세지 않는다(`확인못함`).

    반환: `(등급, 사유)`
    """
    # 가장 정확한 사유부터 — 여러 개에 걸리면 앞의 것이 더 많은 것을 설명한다
    if ct in DISABLED_CONTENT_TYPES:
        return OK, "끈 콘텐츠"
    if league in DISABLED_LEAGUES:
        return OK, "발행 제외 리그"
    if ct in RECORD_CONTENT and league not in RECORD_SOURCE_LEAGUES:
        return OK, "기록 카드 미대상 리그"
    if ct is ContentType.ANALYSIS and league not in ANALYSIS_LEAGUES:
        return OK, "분석 카드 미대상 리그"
    # 리더보드는 카드 전체가 선수 이름이다. 한글 표기표가 없는 리그(NPB)는
    # 아예 만들지 않는다 — v1.11m의 결정이고, 계약이 그 답을 들고 있다.
    if ct is ContentType.LEADERBOARD and not player_names_localized(league):
        return OK, "선수 이름 한글 표기 없음"
    if not in_season_flag:
        return OK, "비시즌"
    if games is None:
        return UNKNOWN, "스냅샷 없음 — 경기 유무를 못 봤다"
    if games == 0:
        return OK, "그날 경기 없음"
    # 경기가 있는데 안 나갔다 — 재료가 있어야 나가는 콘텐츠인지 본다
    need = MATERIAL_OF.get(ct)
    if need:
        if material is None:
            return UNKNOWN, f"스냅샷 없음 — {need} 유무를 못 봤다"
        if material == 0:
            return MATERIAL, f"{games}경기 중 {need} 재료 0건 — 소스가 안 준다"
    return UNEXPLAINED, f"{games}경기가 있는데 0건"


# ── 대장 읽기 ────────────────────────────────────────────────────────
def parse_ledger(path: pathlib.Path, *, days: int, now: "datetime | None" = None
                 ) -> tuple[dict, list[str]]:
    """대장을 (리그, 콘텐츠) → 건수로 접는다. 날짜는 **한국 날짜**."""
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(KST) - timedelta(days=days - 1)).date()
    grid: dict = defaultdict(Counter)
    seen_days: set = set()
    if not path.exists():
        return grid, []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if r.get("state") != "sent":
            continue
        at = r.get("sent_at_utc")
        if not at:
            continue
        try:
            k = datetime.fromisoformat(at).astimezone(KST).date()
        except (ValueError, TypeError):
            continue
        if k < since:
            continue
        seen_days.add(k.isoformat())
        # 콘텐츠는 행에 그대로 있다 — 키를 되짚는 것보다 정확하다
        ct = r.get("content_type") or ""
        # 리그는 scope 앞머리에서 읽는다 (`KBO:2026-09-11`·`MLB:2026-09-11#0`)
        parts = str(r.get("idem_key", "")).split(IDEM_SEP)
        scope = parts[2] if len(parts) >= 3 else ""
        lg = scope.split(":")[0] if ":" in scope else ""
        grid[lg][ct] += 1
    return grid, sorted(seen_days)


def load_snapshot_counts(state: pathlib.Path, *, days: int,
                         now: "datetime | None" = None
                         ) -> "tuple[dict, dict] | tuple[None, None]":
    """스냅샷에서 리그별 (그 기간 경기 수, 재료 수)를 센다.

    스냅샷이 없으면 `(None, None)` — **없는 것을 0으로 세지 않는다.**
    """
    gdir = state / "games"
    if not gdir.is_dir():
        return None, None
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(KST) - timedelta(days=days - 1)).date()
    today = now.astimezone(KST).date()
    games: dict = Counter()
    material: dict = defaultdict(Counter)
    for f in gdir.glob("*.json"):
        lg = f.stem
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, TypeError, OSError):
            continue
        if not isinstance(rows, list):
            continue
        for d in rows:
            if not isinstance(d, dict):
                continue
            day = d.get("sports_day")
            try:
                k = datetime.fromisoformat(str(day)).date()
            except (ValueError, TypeError):
                continue
            if not (since <= k <= today):
                continue
            games[lg] += 1
            for ct, key in MATERIAL_OF.items():
                if d.get(key):
                    material[lg][ct.value] += 1
    return games, material


# ── 판정 ────────────────────────────────────────────────────────────
def audit(grid: dict, *, days: int, games: "dict | None",
          material: "dict | None", now: "datetime | None" = None) -> dict:
    """격자를 판정한다. 0이 아닌 칸은 건드리지 않는다."""
    now = now or datetime.now(timezone.utc)
    cts = [c for c in QUEUED_CONTENT_TYPES]
    cts.sort(key=lambda c: c.value)
    out = {"days": days, "at": now.isoformat(), "cells": [],
           "counts": Counter()}
    for lg in League:
        # 이 기간에 한 달이라도 시즌이면 시즌으로 본다 (달 경계에서 흔들리지 않게)
        flag = any(in_season(lg, now.astimezone(KST) - timedelta(days=i))
                   for i in range(days))
        for ct in cts:
            n = grid.get(lg.value, {}).get(ct.value, 0)
            if n:
                out["counts"]["발행"] += 1
                continue
            g = None if games is None else int(games.get(lg.value, 0))
            m = None if material is None else int(
                material.get(lg.value, {}).get(ct.value, 0))
            verdict, why = explain_zero(lg, ct, in_season_flag=flag,
                                        games=g, material=m)
            out["counts"][verdict] += 1
            cell = {"league": lg.value, "content": ct.value,
                    "verdict": verdict, "why": why}
            if verdict == UNKNOWN:
                # 스냅샷을 못 봐도 **대장은 늘 있다.** 같은 기간에 그 리그가 다른
                # 콘텐츠를 냈다면 경기가 있었을 가능성이 높다 — 판정을 바꾸지는
                # 않지만, 사람이 어느 '확인못함'부터 볼지 정하는 데 쓴다.
                cell["sibling"] = sum(grid.get(lg.value, {}).values())
            out["cells"].append(cell)
    out["counts"] = dict(out["counts"])
    return out


def render(rep: dict, grid: dict) -> str:
    """사람이 읽는 표. 빨간불을 맨 위에 둔다."""
    L = []
    c = rep["counts"]
    L.append("")
    L.append(f"리그 × 콘텐츠 격자 — 최근 {rep['days']}일")
    L.append("=" * 62)
    L.append(f"  발행 {c.get('발행', 0)}칸 · 설명됨 {c.get(OK, 0)}칸 · "
             f"재료없음 {c.get(MATERIAL, 0)}칸 · "
             f"설명못함 {c.get(UNEXPLAINED, 0)}칸 · 확인못함 {c.get(UNKNOWN, 0)}칸")
    L.append("")

    bad = [x for x in rep["cells"] if x["verdict"] == UNEXPLAINED]
    if bad:
        L.append("★★★ 설명 못 하는 0 — 여기부터 본다")
        for x in bad:
            L.append(f"    {x['league']:<12} {x['content']:<14} {x['why']}")
    else:
        L.append("✓ 설명 못 하는 0 없음")
    L.append("")

    mat = [x for x in rep["cells"] if x["verdict"] == MATERIAL]
    if mat:
        L.append("⚠️ 재료가 없어 못 나간 것 — 정상이 아니라 **조사 대상**이다")
        for x in mat:
            L.append(f"    {x['league']:<12} {x['content']:<14} {x['why']}")
        L.append("")

    unk = [x for x in rep["cells"] if x["verdict"] == UNKNOWN]
    if unk:
        L.append(f"· 확인 못 한 칸 {len(unk)}개 — 초록으로도 빨강으로도 세지 않았습니다")
        L.append(f"  ({unk[0]['why']})")
        # 같은 기간에 그 리그가 다른 콘텐츠를 낸 만큼 먼저 본다 (판정은 그대로)
        hot = sorted({(x["league"], x.get("sibling", 0)) for x in unk},
                     key=lambda t: -t[1])
        hot = [t for t in hot if t[1]]
        if hot:
            L.append("  먼저 볼 곳 — 같은 기간에 다른 콘텐츠를 낸 리그"
                     " (경기가 있었을 가능성):")
            for lg, n in hot[:6]:
                miss = [x["content"] for x in unk if x["league"] == lg]
                L.append(f"    {lg:<12} 다른 콘텐츠 {n}건 · 못 본 칸 "
                         f"{', '.join(miss)}")
        L.append("")

    # 격자 본표
    cts = sorted({c.value for c in QUEUED_CONTENT_TYPES})
    why = {(x["league"], x["content"]): x["verdict"] for x in rep["cells"]}
    mark = {OK: "·", MATERIAL: "!", UNEXPLAINED: "✗", UNKNOWN: "?"}
    L.append(f"{'리그':<12}" + "".join(f"{t[:9]:>10}" for t in cts))
    for lg in League:
        row = [f"{lg.value:<12}"]
        for t in cts:
            n = grid.get(lg.value, {}).get(t, 0)
            row.append(f"{n if n else mark.get(why.get((lg.value, t)), '·'):>10}")
        L.append("".join(row))
    L.append("")
    L.append("  숫자=발행 건수 · ·=설명된 0 · !=재료없음 · ✗=설명못함 · ?=확인못함")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="리그 × 콘텐츠 격자 점검")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--state", default=os.environ.get("NUDETV_STATE", "state"))
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.days < 1:
        print("✗ --days는 1 이상이어야 합니다", file=sys.stderr)
        return 2

    state = pathlib.Path(a.state)
    grid, seen = parse_ledger(state / "ledger.jsonl", days=a.days)
    games, material = load_snapshot_counts(state, days=a.days)
    rep = audit(grid, days=a.days, games=games, material=material)
    rep["ledger_days"] = seen

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(render(rep, grid))

    # 설명 못 하는 0이 있으면 실패로 끝낸다 — 예약 점검이 종료코드로 읽는다
    return 1 if rep["counts"].get(UNEXPLAINED, 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
