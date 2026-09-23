#!/usr/bin/env python3
"""전수조사 — **다섯 컨텐츠가 리그마다 실제로 만들어지는가** (v1.75 신설).

대표님 지시(2026-09-22): *"분석 선발 득점 전후반 알림 결과 하나라도
안 나오는게 있는지 전수조사해야해."*

────────────────────────────────────────────────────────────────────
★ **검증 묶음도, 아침표도 이걸 못 봤다.**

  · `verify_*`  — 대역(가짜 데이터)으로 *코드가 맞는지*를 잰다
  · `아침표`    — 원장으로 *이미 나간 것*을 센다
  · **여기**    — 진짜 소스에서 받아 **지금 만들어지는지**를 잰다

이틀 동안 구간속보·분석·야구타순이 **셋 다 0건**이었는데 앞의 둘은 전부
통과였다. 재료(소스) → 큐 → 카드 → **사진**까지 한 줄로 태워 봐야 잡힌다.

돌리는 법:
    PYTHONPATH=g1:. python3 g1/verify_pipeline_e2e.py            # 전 리그
    PYTHONPATH=g1:. python3 g1/verify_pipeline_e2e.py KBO NPB    # 고른 리그
"""
from __future__ import annotations

import copy
import pathlib
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))

import contract as C                                           # noqa: E402
import pipeline as P                                           # noqa: E402
import render_v5 as R                                          # noqa: E402
import tick as T                                               # noqa: E402
from contract import (ContentType, GameMeta, KST, League,      # noqa: E402
                      Status, period_alert_key)
from tick import fetch_months                                  # noqa: E402

OUT = pathlib.Path(tempfile.mkdtemp(prefix="nudetv-e2e-"))

# 대표님이 물으신 다섯 + 그 짝
WANT = [("분석", ContentType.ANALYSIS),
        ("선발", ContentType.LINEUP),
        ("득점", ContentType.GOAL_FLASH),
        ("전후반", ContentType.PERIOD_FLASH),
        ("결과", ContentType.FINAL_FLASH)]

ok = fail = unknown = 0
_notes: list = []


def say(lg: str, what: str, verdict: str, detail: str = "") -> None:
    global ok, fail, unknown
    mark = {"통과": "  PASS", "실패": "  FAIL", "확인못함": "  ????",
            "해당없음": "  ·   "}[verdict]
    if verdict == "통과":
        ok += 1
    elif verdict == "실패":
        fail += 1
        _notes.append(f"{lg} {what} — {detail}")
    elif verdict == "확인못함":
        unknown += 1
    print(f"{mark}  {lg:11} {what:7} {verdict:6} {detail[:58]}")


def records_for(lg: League):
    """그 리그 기록실. 없으면 None (분석이 기록실을 요구한다)."""
    try:
        if lg is League.KBO:
            from adapters.kbo_records import KboRecordAdapter
            return KboRecordAdapter().fetch()
        if lg is League.NPB:
            from adapters.npb_records import NpbRecordAdapter
            return NpbRecordAdapter().fetch()
    except Exception:                                          # noqa: BLE001
        return None
    return None


def fetch(lg: League):
    """그 리그 경기. 시계와 **같은 어댑터·같은 인자**를 쓴다."""
    y, months = fetch_months(datetime.now(KST))
    today = datetime.now(KST)
    if lg is League.KBO:
        from adapters.kbo import KboAdapter
        return KboAdapter().fetch(y, months)
    if lg is League.NPB:
        from adapters.npb import NpbAdapter
        return NpbAdapter().fetch(y, months)
    if lg is League.KL1:
        from adapters.kleague import KLeagueAdapter
        return KLeagueAdapter().fetch(y, months)
    if lg is League.MLB:
        from adapters.mlb import MlbAdapter
        return MlbAdapter().fetch((today - timedelta(days=3)).strftime("%Y-%m-%d"),
                                  (today + timedelta(days=3)).strftime("%Y-%m-%d"))
    if lg is League.KBL:
        from adapters.kbl import KblAdapter
        return KblAdapter().fetch((today - timedelta(days=7)).strftime("%Y%m%d"),
                                  (today + timedelta(days=14)).strftime("%Y%m%d"))
    from adapters.naver_football import CATEGORY, NaverFootballAdapter
    if lg in CATEGORY:
        return NaverFootballAdapter(lg).fetch()
    return []


def _render(item, games, rb) -> tuple:
    """`render_for` 를 태우고 (됐나, 설명)."""
    try:
        out = T.render_for(item, games, records=({lg_v: rb} if rb else {}),
                           all_games=games)
    except Exception as e:                                     # noqa: BLE001
        return False, f"예외 {e.__class__.__name__}: {str(e)[:40]}"
    if not out:
        fb = R.take_fallbacks()
        return False, (fb[0][:60] if fb else "render_for가 None (이유를 안 남김)")
    R.take_fallbacks()
    return True, f"{out[0][0][2]}x{out[0][0][3]}"


print("=" * 78)
print(f"전수조사 — 다섯 컨텐츠가 지금 실제로 만들어지는가  "
      f"({datetime.now(KST):%m-%d %H:%M} KST)")
print("=" * 78)

_args = [a.upper() for a in sys.argv[1:]]
_leagues = [l for l in League if C.league_enabled(l)
            and (not _args or l.value in _args)]

for lg in _leagues:
    lg_v = lg.value
    try:
        games = fetch(lg) or []
    except Exception as e:                                     # noqa: BLE001
        say(lg_v, "수집", "확인못함", f"{e.__class__.__name__}: {str(e)[:40]}")
        continue
    if not games:
        say(lg_v, "수집", "확인못함", "경기 0건 (비시즌이거나 소스 변화)")
        continue
    now = datetime.now(timezone.utc)
    rb = records_for(lg)
    fin = sorted([g for g in games if g.is_terminal and g.score],
                 key=lambda g: g.start_utc, reverse=True)
    up = sorted([g for g in games if not g.is_terminal and g.start_utc > now],
                key=lambda g: g.start_utc)

    for name, ct in WANT:
        # ── 그 리그에 그 컨텐츠가 의무인가 ──────────────────────
        unit = C.SCORE_UNIT_BY_LEAGUE.get(lg)
        if ct is ContentType.ANALYSIS and lg not in P.ANALYSIS_LEAGUES:
            say(lg_v, name, "해당없음", "분석 대상 리그가 아님"); continue
        if ct is ContentType.GOAL_FLASH and unit is not C.ScoreUnit.GOALS:
            say(lg_v, name, "해당없음", "골로 세는 종목이 아님"); continue

        try:
            if ct is ContentType.ANALYSIS:
                if rb is None:
                    say(lg_v, name, "확인못함",
                        "기록실을 못 받음 (유럽은 토큰이 있어야 함)"); continue
                if not up:
                    say(lg_v, name, "확인못함", "예정 경기가 없음"); continue
                q = P.build_queue(games, now, "e2e", floor_hours=0, horizon_hours=30)
                its = [i for i in q if i.content_type is ct]
                if not its:
                    say(lg_v, name, "실패", "큐에 한 건도 안 오름"); continue
                good, why = _render(its[0], games, rb)
                say(lg_v, name, "통과" if good else "실패", why)

            elif ct is ContentType.LINEUP:
                # 예정 경기가 없으면 **가장 최근 끝난 경기를 킥오프 2시간 전으로
                # 되돌려** 본다. 명단은 경기 전에만 나가므로 그 시각으로 재야
                # 카드가 만들어진다 — 안 그러면 밤에는 늘 `확인못함` 이다.
                # ★ **바깥 `now` 를 건드리지 않는다.** 처음엔 여기서 `now` 를
                #   되감았는데, `확인못함` 으로 `continue` 하는 길에서 되돌리지
                #   못해 **뒤이어 도는 결과 카드가 전부 None** 이 됐다
                #   (유럽 5리그가 한꺼번에 '실패'로 찍혔다 — 없는 사고였다).
                #   되감은 시각은 이 칸 안에서만 산다.
                _rewound = False
                when = now
                if not up:
                    if not fin:
                        say(lg_v, name, "확인못함", "예정도 끝난 경기도 없음")
                        continue
                    # ★★ **복사본을 쓴다.** 처음엔 `fin[0]` 을 그대로 잡아
                    #   `status` 를 예정으로 덮었는데, 그 객체를 **뒤이어 도는
                    #   득점·결과 시험이 다시 쓴다.** 그래서 멀쩡한 두 카드가
                    #   한꺼번에 '실패'로 찍혔다 — 없는 사고를 내가 만들었다.
                    #   시험이 대상을 더럽히면 그 뒤 판정은 전부 거짓이다.
                    g = copy.deepcopy(fin[0])
                    g.status = Status.SCHEDULED
                    when = g.start_utc - timedelta(hours=2)
                    _rewound = True
                else:
                    g = up[0]
                if unit is C.ScoreUnit.RUNS:
                    T._enrich_baseball_lineup(lg, [g], when)
                else:
                    ad = None
                    try:
                        from adapters.naver_football import NaverFootballAdapter
                        ad = NaverFootballAdapter(lg)
                    except Exception:                          # noqa: BLE001
                        pass
                    if ad is not None and hasattr(ad, "fill_lineups"):
                        try:
                            ad.fill_lineups([g])
                        except Exception:                      # noqa: BLE001
                            pass
                # ── ★★★ **`확인못함` 으로 끝내지 않는다** (2026-09-23) ──────
                #
                # 예정 경기를 골랐는데 아직 명단이 안 나왔으면 여기서 포기했다.
                # 그래서 **낮 시간에는 야구 선발이 늘 `확인못함`** 이었고,
                # 대표님이 열흘 넘게 "경기를 기다려야 한다"는 말을 들었다.
                # *"이렇게 작업 미완성 시킨 채로 계속 갈 수 없어."*
                #
                # 그런데 소스는 **끝난 경기의 미리보기를 계속 준다**(실측
                # 2026-09-23: KBO 12/12 · MLB 11/12 · K리그 12/12 · NPB 0/12).
                # 그러니 기다릴 이유가 없다 — **가장 최근 끝난 경기를 킥오프
                # 2시간 전으로 되돌려 다시 잰다.** 그래도 재료가 없으면
                # 그때야 `확인못함` 이고, 그건 **소스에 없다는 뜻**이다.
                if not (g.meta and g.meta.lineup) and not _rewound and fin:
                    g = copy.deepcopy(fin[0])
                    g.status = Status.SCHEDULED
                    when = g.start_utc - timedelta(hours=2)
                    _rewound = True
                    if unit is C.ScoreUnit.RUNS:
                        T._enrich_baseball_lineup(lg, [g], when)
                    else:
                        try:
                            from adapters.naver_football import NaverFootballAdapter
                            _ad2 = NaverFootballAdapter(lg)
                            if hasattr(_ad2, "fill_lineups"):
                                _ad2.fill_lineups([g])
                        except Exception:                      # noqa: BLE001
                            pass
                if not (g.meta and g.meta.lineup):
                    say(lg_v, name, "확인못함",
                        "예정에도 지난 경기에도 명단이 없음 "
                        "(소스가 이 리그 명단을 안 주는지 보세요)"); continue
                card = R.lineup_card(g, lg, now=when)
                if not card:
                    say(lg_v, name, "실패", "카드가 None"); continue
                r = R.render_png(card[0], OUT / f"{lg_v}-lineup.png")
                say(lg_v, name, "통과" if r else "실패",
                    (f"{r[0]}x{r[1]}" + (" (지난 경기로 되돌려 잼)" if _rewound else ""))
                    if r else "사진 게이트에 걸림")

            elif ct is ContentType.GOAL_FLASH:
                # ★ **재료를 직접 채워 본다.** 그냥 `fin` 을 훑으면 득점자가
                #   비어 있어 늘 `확인못함` 이 나온다 — 그건 조사가 아니라
                #   포기다. 시계가 쓰는 그 창구로 골을 받아 온다.
                src = next((g for g in fin
                            if g.meta and getattr(g.meta, "goals", ())), None)
                if src is None:
                    for cand in fin[:4]:
                        try:
                            from adapters.naver_football import (
                                CATEGORY, NaverFootballAdapter)
                            if lg in CATEGORY:
                                _a = NaverFootballAdapter(lg)
                                if hasattr(_a, "fill_goals"):
                                    _a.fill_goals([cand])
                            else:
                                from adapters.naver_game import NaverGameAdapter
                                NaverGameAdapter().enrich_live(
                                    [cand], lg, want_goals=True)
                        except Exception:                      # noqa: BLE001
                            pass
                        if cand.meta and getattr(cand.meta, "goals", ()):
                            src = cand
                            break
                if src is None:
                    say(lg_v, name, "확인못함",
                        "끝난 경기 4건을 받아 봤지만 득점자가 없음")
                    continue
                # `goal_card(game, league, goal_id, *, now)` — **인자 이름을
                # 지어내지 않는다.** `goal_id` 는 `contract.goal_key` 가 만든
                # 이름이고 순번이 아니다(VAR 취소로 앞 골이 사라져도 안 밀린다).
                # ★ **끝난 경기에는 일부러 안 만든다** — 늦게 도착한 속보가
                #   `1:0` 이라고 말하는 동안 채널엔 이미 최종 점수가 있다.
                #   그러니 끝난 경기로 재면 언제나 `None` 이고, 그건 결함이
                #   아니라 **내 시험이 틀린 것**이다. 복사본을 경기 중으로
                #   되돌려 잰다(원본은 뒤 시험이 다시 쓴다 — 더럽히지 않는다).
                # `is_terminal` 은 `status` 에서 나오는 **읽기 전용**이다 —
                # 직접 못 바꾼다(고치려다 AttributeError 를 봤다). 상태만 바꾼다.
                live = copy.deepcopy(src)
                live.status = Status.LIVE
                gl = live.meta.goals[0]
                card = R.goal_card(live, lg, C.goal_key(gl), now=now)
                if card is None:
                    say(lg_v, name, "실패", "goal_card 가 None"); continue
                r = R.render_png(card[0], OUT / f"{lg_v}-goal.png")
                say(lg_v, name, "통과" if r else "실패",
                    f"{r[0]}x{r[1]}" if r else "사진 게이트에 걸림")

            elif ct is ContentType.PERIOD_FLASH:
                base = (up[0] if up else fin[0])
                probe = C.replace(base, meta=GameMeta(
                    period=1 if unit is C.ScoreUnit.GOALS else 6,
                    period_state="종료" if unit is C.ScoreUnit.GOALS else "초",
                    live_score=(1, 2))) if hasattr(C, "replace") else None
                g2 = probe or base
                if probe is None:
                    g2.meta = GameMeta(
                        period=1 if unit is C.ScoreUnit.GOALS else 6,
                        period_state="종료" if unit is C.ScoreUnit.GOALS else "초",
                        live_score=(1, 2))
                g2.status = Status.LIVE
                key, label = period_alert_key(lg, g2.meta.period,
                                              g2.meta.period_state, False)
                if not key:
                    say(lg_v, name, "해당없음", "이 종목엔 알릴 구간이 없음"); continue
                card = R.period_card(g2, lg, label=label, now=now)
                if not card:
                    say(lg_v, name, "실패", "카드가 None"); continue
                r = R.render_png(card[0], OUT / f"{lg_v}-period.png")
                say(lg_v, name, "통과" if r else "실패",
                    f"{r[0]}x{r[1]}" if r else "사진 게이트에 걸림")

            else:   # 결과 (종료 속보)
                if not fin:
                    say(lg_v, name, "확인못함", "끝난 경기가 없음"); continue
                g = fin[0]
                try:
                    from adapters.naver_game import NaverGameAdapter
                    NaverGameAdapter().enrich([g], lg, limit=1)
                except Exception:                              # noqa: BLE001
                    pass
                card = R.flash_card(g, lg, now=now, rb=rb)
                if not card:
                    say(lg_v, name, "실패", "카드가 None"); continue
                r = R.render_png(card[0], OUT / f"{lg_v}-flash.png")
                say(lg_v, name, "통과" if r else "실패",
                    f"{r[0]}x{r[1]}" if r else "사진 게이트에 걸림")
        except Exception as e:                                 # noqa: BLE001
            say(lg_v, name, "실패",
                f"{e.__class__.__name__}: {str(e)[:44]}")
            traceback.print_exc(limit=1)

print("\n" + "=" * 78)
print(f"통과 {ok} · 실패 {fail} · 확인못함 {unknown}")
if _notes:
    print("\n[실패 목록]")
    for n in _notes:
        print("   ✗", n)
print("※ `확인못함`을 통과로 세지 않습니다.")
print(f"(사진: {OUT})")
sys.exit(1 if fail else 0)
