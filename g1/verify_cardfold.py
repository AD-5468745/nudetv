#!/usr/bin/env python3
"""카드가 **접혀서 죽는 것**을 터지기 전에 전부 찾는다.

★ 왜 이 벌이 따로 있나 — **같은 병으로 네 번 죽었다.**
    v1.75   야구 명단      `접힘(2.0줄): 지명타자`     (글씨를 키운 날)
    v1.57b  오늘의 경기    태어난 날부터 한 장도 안 나감
    v2.18   기록실         `접힘(2.3줄): 강백호33호(…)`
    v2.20   사전정보       `접힘(2.1줄): 163 2/3이닝 · 137K`
**네 번 다 "터진 뒤에" 고쳤다.** 대표님이 채널에서 먼저 보신 것도 있다.

카드는 한 줄이 넘치면 게이트가 **그 장을 통째로 거절**하고, 그러면 그
콘텐츠가 조용히 사라진다. 사고의 모양은 늘 같다 —
**칸은 그대로인데 글자가 길어진다.**

그래서 하나씩 기다리지 않고 **전부 최악 조건으로 미리 재운다.**
  · 팀 이름은 그 리그에서 **실제로 가장 긴 것**을 쓴다(표에서 뽑는다)
  · 값은 실제로 온 적 있는 **가장 긴 모양**을 쓴다
  · 접힘이 하나라도 나오면 **실패**다 — 어느 카드 어느 줄인지 이름을 찍는다
"""
import pathlib
import sys

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]
import cards_v5 as C5                                          # noqa: E402
import headline as H                                           # noqa: E402
from contract import League                                    # noqa: E402

PASS, FAIL, SKIP = 0, [], 0


def check(name: str, ok, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  FAIL  {name}  {detail}")


def longest_names(league: League, n: int = 2) -> list:
    """그 리그에서 **실제로 가장 긴** 팀 이름 n개. 표에서 뽑는다 — 짓지 않는다."""
    out = []
    for code in (getattr(C5, "TEAM_NAMES", {}) or {}).get(league, {}) or {}:
        try:
            out.append(C5._nm(league, type("T", (), {"league": league,
                                                     "team_code": code})()))
        except Exception:                                      # noqa: BLE001
            continue
    out.sort(key=len, reverse=True)
    return out[:n] or ["팀가", "팀나"]


# 실제로 온 적 있는 **가장 긴 모양**들 (짓지 않고 사고 기록에서 가져왔다)
LONG_VALUES = [
    "163 2/3이닝 · 137K",                    # v2.20 사고
    "강백호33호(4회2점 구창모)",              # v2.18 사고
    "지명타자",                               # v1.75 사고
    "2승 1패 · 방어율 3.42",
]


def shell_for(kind: str, league: League, body: str) -> str:
    head = H.Headline(rule="B-TEST", text="가가가가가 5 : 3 나나나나나",
                      sub="시험", facts={"away": 5, "home": 3})
    return C5.shell(kind=kind, league=league, date_label="9.26 토",
                    head=head, body=body, foot_left="구장이름길게")


def cases() -> list:
    """(이름, 카드종류, 리그, 본문) 목록 — 값이 늘어날 수 있는 칸을 전부 덮는다."""
    out = []
    for lg in (League.KBO, League.MLB, League.NPB, League.KBL,
               League.VLEAGUE_M, League.KL1, League.EPL):
        a, h = longest_names(lg)
        # ① 좌우 대비 (분석·사전정보가 쓴다 — v2.20 이 여기서 터졌다)
        for v in LONG_VALUES:
            out.append((f"좌우대비 {lg.value} {v[:12]}", "analysis", lg,
                        C5.body_compare([(v, "항목", v, "")], a, h, "", "")))
        # ② 한 줄 값 (기록실이 쓴다 — v2.18 이 여기서 터졌다)
        for v in LONG_VALUES:
            out.append((f"한줄값 {lg.value} {v[:12]}", "boxscore", lg,
                        '<div class="anh">기록<span>이 경기</span></div>'
                        f'<div class="bar"><span class="k">홈런</span>'
                        f'<span class="v">{C5.esc(v)}</span></div>'))
        # ③ 선발 맞대결 (v2.20 의 실제 자리)
        _p = {"name": a, "pitcher": "가나다라마", "win": 11, "lose": 6,
              "era": "3.42", "whip": "1.28", "inn": "163 2/3", "kk": 137,
              "vs": {"games": 3, "era": "4.09"}}
        _q = dict(_p, name=h, pitcher="바사아자차", inn="152 2/3", kk=161)
        out.append((f"선발맞대결 {lg.value}", "pregame", lg,
                    C5.body_starters(a, h, _p, _q)))
        # ④ 최근 5경기 (직전 한 줄이 길어질 수 있다)
        out.append((f"최근5경기 {lg.value}", "analysis", lg,
                    C5.body_form([(a, ["W", "L", "W", "D", "W"],
                                   "직전 경기에서 연장 끝에 3 : 2 로 이겼습니다"),
                                  (h, ["L", "L", "W", "W", "L"],
                                   "직전 경기에서 9회 역전을 허용했습니다")])))
        # ⑤ 시즌 상대전적
        out.append((f"상대전적 {lg.value}", "analysis", lg,
                    C5.body_h2h(a, h, 12, 11, 3)))
    return out


def height_cases() -> list:
    """리그 단위 카드를 **그 리그의 가장 바쁜 날**로 렌더한다.

    ★ 왜 — 접힘만 재고 **높이**를 안 재서 MLB 정리판이 거의 매일 죽었다
    (실측 2026-09-27: 14경기 2085px > 한계 2000px. 9/22·23·25·26·27 전부
    0장이고 12경기였던 9/24 만 나갔다). 장부에 줄도 안 생겨 아무도 몰랐다.
    **경기가 많은 날은 예외가 아니라 평소다** — MLB 는 보통 14~17경기다.
    """
    import datetime
    import contract as C
    import render_v5 as R
    now = datetime.datetime.now(datetime.timezone.utc)
    # 리그별 (가장 바쁜 날의 경기 수, 팀 코드 몇 개)
    busiest = {League.MLB: 20, League.KBO: 5, League.NPB: 6,
               League.KBL: 5, League.VLEAGUE_M: 4, League.KL1: 6,
               League.EPL: 10}
    out = []
    for lg, n in busiest.items():
        codes = [c for c in ((getattr(C5, "TEAM_NAMES", {}) or {})
                             .get(lg, {}) or {})] or ["AA", "BB"]
        games = []
        for i in range(n):
            games.append(C.Game(
                league=lg, season="2026", source_key=f"h{i}",
                home=C.TeamRef(lg, codes[(i * 2) % len(codes)]),
                away=C.TeamRef(lg, codes[(i * 2 + 1) % len(codes)]),
                start_utc=now - datetime.timedelta(hours=8 + i * 0.2),
                home_tz="Asia/Seoul", status=C.Status.FINAL,
                # ★ **1점차로 만든다** — 그래야 「오늘의 경기」 블록이 실제로
                # 붙는다. v2.23 은 2점차 데이터로 재서 그 블록이 없는
                # **실제보다 짧은 카드**를 통과시켰고, MLB 정리판은 고친 뒤에도
                # 계속 죽었다. 검증 입력을 내가 만들면 이렇게 틀린다.
                score=C.Score(home=4, away=5,
                              unit=C.SCORE_UNIT_BY_LEAGUE[lg])))
        try:
            made = R.result_card(games, lg, games[0].sports_day, now=now)
        except Exception as e:                                 # noqa: BLE001
            out.append((f"정리판 {lg.value} {n}경기", None,
                        f"{type(e).__name__}: {e}", n))
            continue
        if not made:
            out.append((f"정리판 {lg.value} {n}경기", None,
                        "카드가 만들어지지 않았습니다", n))
            continue
        # ── ★★★ **실제 발송 경로로 판정한다** (v2.25) ───────────────────
        # 카드 HTML 만 재면 안 된다. 넘치면 `render_png` 의 사다리가 한 단
        # 줄여 다시 그리므로, **여기서 성공했으면 운영에서도 나간다.**
        import pathlib as _pl
        import tempfile as _tf
        _o = _pl.Path(_tf.mkdtemp()) / "c.png"
        try:
            _r = (R.render_png(made[0], _o, made[1]) if len(made) == 3
                  else R.render_png(made[0], _o))
        except Exception as e:                                 # noqa: BLE001
            out.append((f"정리판 {lg.value} {n}경기", None,
                        f"{type(e).__name__}: {e}", n))
            continue
        if not _r or _r == R.TOO_TALL:
            out.append((f"정리판 {lg.value} {n}경기", None,
                        f"발송 경로에서 거절됨({_r})", n))
            continue
        out.append((f"정리판 {lg.value} {n}경기", None,
                    f"OK {_r[1]}px", n))
    return out


def main() -> int:
    global SKIP
    try:
        from playwright.sync_api import sync_playwright
    except Exception:                                          # noqa: BLE001
        print("  SKIP  렌더 도구가 없어 재지 못했습니다 (통과로 세지 않는다)")
        SKIP += 1
        return 0
    rows = cases()
    print(f"\n카드 접힘 전수 — {len(rows)}가지를 최악 조건으로 렌더합니다")
    bad: list = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": C5.CARD_W, "height": 2400})
        for name, kind, lg, body in rows:
            pg.set_content(shell_for(kind, lg, body))
            pg.wait_for_timeout(60)
            probs = [x for x in pg.evaluate(C5._MEASURE_JS) if "접힘" in x]
            if probs:
                bad.append((name, probs[:2]))
        b.close()
    check(f"★★★ {len(rows)}가지 전부 **한 줄에 들어간다** (접힘 0)",
          not bad,
          "; ".join(f"{n} → {p}" for n, p in bad[:6]))
    if bad:
        print(f"\n  접힌 것 {len(bad)}가지:")
        for n, p in bad:
            print(f"     ✗ {n} → {p}")

    # ── 높이 — **가장 바쁜 날**로 잰다 ────────────────────────────────
    import contract as _C
    lim = _C.CARD_MAX_HEIGHT_PX
    tall: list = []
    hrows = height_cases()
    print(f"\n카드 높이 전수 — 리그 {len(hrows)}개를 가장 바쁜 날로 렌더합니다"
          f" (한계 {lim}px)")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": C5.CARD_W, "height": 4000})
        for name, html, err, n in hrows:
            if html is None:
                if str(err).startswith("OK "):
                    print(f"     ✅ {name:24} {err} (발송 경로 통과)")
                    continue
                tall.append((name, err or "카드가 만들어지지 않았습니다"))
                print(f"     ❌ {name:24} {err}")
                continue
            pg.set_content(html)
            pg.wait_for_timeout(80)
            el = pg.query_selector(".card")
            h = el.bounding_box()["height"] if el else 0
            room = lim - h
            mark = "✅" if room >= 100 else ("⚠ 여유부족" if room >= 0 else "❌")
            print(f"     {mark} {name:24} {h:5.0f}px · 여유 {room:4.0f}px")
            # **딱 들어가는 것을 통과로 세지 않는다** — 머리말이 한 줄
            # 길어지는 날 다시 넘는다. 여유 100px 을 요구한다.
            if room < 100:
                tall.append((name, f"{h:.0f}px · 여유 {room:.0f}px"))
        b.close()
    check(f"★★★ 리그 {len(hrows)}개 정리판이 **가장 바쁜 날에도** 여유 100px 이상",
          not tall, "; ".join(f"{n} ({d})" for n, d in tall[:6]))
    return 0


if __name__ == "__main__":
    main()
    print(f"\n결과: {PASS} PASS / {len(FAIL)} FAIL" + (f" / {SKIP} SKIP" if SKIP else ""))
    sys.exit(1 if FAIL else 0)
