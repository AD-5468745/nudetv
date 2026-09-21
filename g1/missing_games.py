#!/usr/bin/env python3
"""누락된 경기 — **일정에 있었는데 앵커가 한 장도 안 나간 경기** (v1.75 신설).

대표님 지시(2026-09-22): *"이틀동안 채널에 올라간 글들 전부 확인하고
빠진 컨텐츠, 누락된 경기 체크해."*

────────────────────────────────────────────────────────────────────
★ **아침표(`morning_table`)가 못 보는 사각지대가 바로 이것이다.**

아침표는 경기 수를 **앵커 발송 수**로 센다. 그래서 `KBO 5경기 · 앵커 5`
처럼 언제나 딱 맞아떨어진다 — **앵커가 아예 안 나간 경기는 분모에서도
빠지기 때문이다.** 6경기 중 1경기가 통째로 사라져도 표에는
`5경기 · 앵커 5` 로 찍혀 완벽해 보인다.

그래서 여기서는 **분모를 소스의 실제 일정에서 가져온다.** 저장소가
이미 아는 규칙이다 — *"분모는 큐가 아니라 경기 일정이다"*(약점 181).

돌리는 법:  PYTHONPATH=g1:. python3 g1/missing_games.py 2026-09-20 2026-09-21
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
from contract import KST, League                               # noqa: E402
from tick import fetch_months                                  # noqa: E402
from pipeline import ANCHOR_LEAD_SECONDS                       # noqa: E402

LEDGER = ROOT / "state" / "ledger.jsonl"

# 경기 하나에 **반드시 있어야 하는 것**. 앵커가 없으면 그 경기는 채널에
# 존재한 적이 없다 — 나머지가 다 나가도 댓글로 들어갈 자리가 없다.
PER_GAME_MUST = ["anchor", "kickoff", "final_flash"]


def adapters(day0: str, day1: str) -> dict:
    """리그 → 그 기간 경기 목록을 주는 함수. **시계와 같은 어댑터를 쓴다.**"""
    from adapters.kbo import KboAdapter
    from adapters.mlb import MlbAdapter
    from adapters.kbl import KblAdapter
    from adapters.kovo import KovoAdapter
    from adapters.kleague import KLeagueAdapter
    from adapters.npb import NpbAdapter
    from adapters.naver_football import NaverFootballAdapter, CATEGORY

    d0 = datetime.strptime(day0, "%Y-%m-%d")
    d1 = datetime.strptime(day1, "%Y-%m-%d")
    # ★ **시계가 쓰는 꼴 그대로 넘긴다.** 월을 `[9]`(정수)로 넘겼더니 KBO·NPB·
    #   K리그가 `0건`·`HTTP 404`로 떨어졌다 — 소스는 `['08','09']`(0을 채운
    #   두 자리 문자열)를 받는다. 내가 꼴을 새로 지으면 이렇게 어긋난다.
    y, months = fetch_months(d1.replace(tzinfo=KST))

    jobs: dict = {
        League.KBO: lambda: KboAdapter().fetch(y, months),
        League.NPB: lambda: NpbAdapter().fetch(y, months),
        League.KL1: lambda: KLeagueAdapter().fetch(y, months),
        League.MLB: lambda: MlbAdapter().fetch(
            (d0 - timedelta(days=2)).strftime("%Y-%m-%d"),
            (d1 + timedelta(days=2)).strftime("%Y-%m-%d")),
        League.KBL: lambda: KblAdapter().fetch(
            (d0 - timedelta(days=2)).strftime("%Y%m%d"),
            (d1 + timedelta(days=2)).strftime("%Y%m%d")),
    }
    for lg in CATEGORY:
        jobs[lg] = (lambda _l=lg: NaverFootballAdapter(_l).fetch())
    return jobs


def load_ledger(days: set) -> tuple:
    """원장 → (경기id → {나간 종류}), 리그·날짜별 앵커 수."""
    by_game: dict = collections.defaultdict(set)
    per_day: dict = collections.Counter()      # (리그, 날짜) → 앵커 수
    if not LEDGER.exists():
        print(f"  ✗ 원장이 없습니다: {LEDGER}")
        sys.exit(2)
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except Exception:                                      # noqa: BLE001
            continue
        if d.get("state") != "sent":
            continue
        key = str(d.get("idem_key") or "").split("|")
        if len(key) < 3:
            continue
        ct, scope = key[1], key[2]
        bits = scope.split(":")
        if len(bits) < 3:
            continue                      # 리그·날짜 단위(결과·순위)는 여기서 안 센다
        day = bits[1][:10]
        if day not in days:
            continue
        # `LEAGUE:날짜:` 를 떼면 남는 것이 경기 id다 (뒤의 `#키`는 버린다)
        gid = ":".join(bits[2:]).split("#")[0]
        by_game[(bits[0], day, gid)].add(ct)
        if ct == "anchor":
            per_day[(bits[0], day)] += 1
    return by_game, per_day


def main() -> int:
    days = sys.argv[1:] or [
        (datetime.now(KST) - timedelta(days=d)).strftime("%Y-%m-%d")
        for d in (2, 1)]
    dayset = set(days)
    sent, anchors_per_day = load_ledger(dayset)

    print("=" * 74)
    print(f"누락된 경기 — 일정에 있었는데 채널에 안 나간 것  ({' · '.join(days)})")
    print("=" * 74)
    print("  ※ 분모를 **소스의 실제 일정**에서 가져옵니다 — 아침표는 앵커 수로")
    print("     세기 때문에 통째로 빠진 경기를 볼 수 없습니다.\n")

    jobs = adapters(days[0], days[-1])
    now = datetime.now(KST)
    tot_games = tot_missing = 0
    holes: list = []
    for lg, fn in sorted(jobs.items(), key=lambda x: x[0].value):
        if not C.league_enabled(lg):
            continue
        try:
            games = fn() or []
        except Exception as e:                                 # noqa: BLE001
            print(f"  {lg.value:12} ⚠ 일정 조회 실패 — {e.__class__.__name__}: "
                  f"{str(e)[:60]}  (이 리그는 **확인못함**)")
            continue
        todays = [g for g in games if g.sports_day in dayset]
        # 취소·연기는 편성에서 뺀다 (의무 대조와 같은 규칙)
        todays = [g for g in todays
                  if getattr(getattr(g, "status", None), "value", "")
                  not in C.DUTY_EXEMPT_STATUSES]
        # ★ **아직 때가 안 된 경기를 누락으로 세지 않는다.**
        #   앵커는 킥오프 3시간 전에 예약된다. MLB는 현지 날짜와 한국 날짜가
        #   어긋나 `sports_day`가 어제인데 **내일 아침에 열리는** 경기가 있다
        #   (실측: 9-21 편성인데 9-22 07:35 시작). 그걸 누락이라 찍으면
        #   오탐이고, 오탐 하나가 이 검사를 통째로 못 믿게 만든다.
        _due = now - timedelta(seconds=0)
        _later = [g for g in todays
                  if g.start_utc - timedelta(seconds=ANCHOR_LEAD_SECONDS) > _due]
        todays = [g for g in todays if g not in _later]
        if _later:
            print(f"  {lg.value:12} (아직 때가 안 된 경기 {len(_later)}건은 "
                  f"셈에서 뺍니다 — 앵커는 킥오프 3시간 전)")
        if not todays:
            continue
        if not todays:
            continue
        # ── ★ **날짜별로 수를 먼저 센다 — id로 짝짓지 않는다** ──────────
        #
        # id로 짝지었다가 NPB 8경기를 누락이라고 잘못 보고할 뻔했다.
        # NPB는 npb.jp가 결과 페이지를 올리면 `source_key`가 바뀐다
        #   보낼 때 `20260920-YOG-YAK` → 지금 `scores-2026-0920-g-s-23`
        # **어댑터가 문서로 남긴 정상 동작**이다(npb.py 121~136행).
        # 옛 id와 지금 id를 맞대면 멀쩡한 경기가 전부 '누락'으로 찍힌다.
        #
        # 그래서 **수를 먼저 본다**: 그날 일정이 N경기인데 앵커가 N건이면
        # 빠진 것이 없다. 모자랄 때만 id로 이름을 찾아본다 — 못 찾으면
        # 못 찾았다고 말한다(`이름 확인못함`). 수가 곧 사실이고, 이름은 거들 뿐이다.
        by_day: dict = collections.defaultdict(list)
        for g in todays:
            by_day[g.sports_day].append(g)
        miss_n = 0
        lines: list = []
        for day, gs in sorted(by_day.items()):
            got_n = anchors_per_day.get((lg.value, day), 0)
            short = len(gs) - got_n
            if short <= 0:
                continue
            miss_n += short
            named = [g for g in gs
                     if "anchor" not in sent.get(
                         (lg.value, day, str(g.game_id)), set())]
            if len(named) == short:
                for g in sorted(named, key=lambda x: x.start_utc):
                    lines.append(
                        f"       ✗ {g.start_utc.astimezone(KST):%m-%d %H:%M} "
                        f"{C.team_name(g.away)} vs {C.team_name(g.home)} "
                        f"[{getattr(g.status, 'value', '?')}]")
            else:
                lines.append(f"       ✗ {day} {short}경기 — **이름 확인못함** "
                             f"(일정 {len(gs)} · 앵커 {got_n} · id가 달라 "
                             f"짝을 못 지었습니다)")
        tot_games += len(todays)
        tot_missing += miss_n
        got_all = sum(anchors_per_day.get((lg.value, d), 0) for d in by_day)
        mark = "✅" if not miss_n else f"❌ **{miss_n}경기 누락**"
        print(f"  {lg.value:12} 일정 {len(todays):3}경기 · 앵커 {got_all:3}건  {mark}")
        for ln in lines:
            print(ln)

    print("\n" + "─" * 74)
    if tot_missing:
        print(f"  ❌ 이틀 동안 {tot_games}경기 중 **{tot_missing}경기가 채널에 "
              f"한 장도 안 나갔습니다** ({tot_missing / tot_games * 100:.0f}%)")
    else:
        print(f"  ✅ 이틀 동안 {tot_games}경기 전부 앵커가 나갔습니다 — 누락 0")

    # 앵커는 있는데 **의무 컨텐츠가 빠진** 경기
    print("\n  [앵커는 있는데 빠진 것이 있는 경기]")
    part = collections.Counter()
    for (lgv, day, gid), got in sent.items():
        if "anchor" not in got:
            continue
        for must in PER_GAME_MUST:
            if must not in got:
                part[(lgv, must)] += 1
    if part:
        for (lgv, must), n in sorted(part.items(), key=lambda x: -x[1]):
            print(f"     {lgv:12} {must:12} 빠진 경기 {n}건")
    else:
        print("     없음")
    return 1 if tot_missing else 0


if __name__ == "__main__":
    sys.exit(main())
