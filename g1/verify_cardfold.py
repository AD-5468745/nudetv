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
    return 0


if __name__ == "__main__":
    main()
    print(f"\n결과: {PASS} PASS / {len(FAIL)} FAIL" + (f" / {SKIP} SKIP" if SKIP else ""))
    sys.exit(1 if FAIL else 0)
