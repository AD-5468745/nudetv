"""v5 카드 렌더 — 시계와 새 카드 골격 사이의 배선 (v1.12 신설).

**pipeline.py를 건드리지 않는다.** 옛 렌더는 그대로 두고 여기에 새 경로를 만든다 —
그래야 되돌리는 길이 상수 하나로 남는다. 새 카드가 실패하면 시계는 옛 카드로
떨어지고, 구독자는 오늘까지 받던 것을 그대로 받는다.

**게이트가 틱을 죽이면 위반보다 나쁘다**(v1.11h). 이 파일의 모든 함수는
못 만들면 `None`을 돌려주고, 부르는 쪽은 그때 옛 렌더를 쓴다.

────────────────────────────────────────────────────────────────────
**시점을 섞지 않는다** (2026-09-05 실측, 약점 123).

네이버 `preview`는 경기 전날 만들어져 굳는다(`generateDate`). 같은 날
`statistics`는 오늘 경기까지 반영한다. 실측:

    preview      삼성 70승 46패 · 2위   (9/4 기준)
    statistics   삼성 71승 46패 · 1위   (지금)

둘 다 맞다 — 시점이 다를 뿐이다. 그런데 한 카드가 둘을 섞으면 카드가 스스로
모순된다. 그래서 **한 카드는 한 시점만 말한다**: 순위·기록은 한 소스에서만
가져오고, 다른 소스에서는 그 소스에만 있는 것(선발 투수 등)만 가져온다.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cards_v5 as C5
import headline as H
from contract import (GateError, KST, League, ScoreUnit, SCORE_UNIT_BY_LEAGUE,
                      Status, assert_card_geometry, venue_name)

# ── 되돌리는 스위치 ────────────────────────────────────────────
#
# **여기 하나를 False로 두면 그 종류는 즉시 옛 카드로 돌아간다.**
# 새 카드가 실서비스에서 무엇을 할지는 켜 봐야 알고, 되돌리는 길이 짧아야
# 켜 볼 수 있다. 종류별로 나눈 이유도 같다 — 하나가 잘못돼도 나머지는 산다.
USE_V5 = {
    "result": True,          # 경기 결과 — 흐름표가 들어가는 자리
    "morning": False,        # 아직 옛 카드
    "start": False,
    "standings": False,
    "leaders": False,
    "analysis": False,
    "night": False,
}

# 흐름표를 넣을 수 있는 경기 수 상한. 넘으면 한 줄 요약(스코어보드)으로 떨어진다 —
# 표가 세 개 넘게 쌓이면 카드가 세로로 무너진다.
FLOW_MAX_GAMES = 3


def _period_labels(league: League, n: int) -> list[str]:
    """구간 이름은 **종목이 정한다**. 골격은 하나이고 이 이름만 갈린다."""
    unit = SCORE_UNIT_BY_LEAGUE.get(league)
    if unit is ScoreUnit.RUNS:
        return [str(i + 1) for i in range(n)]
    if unit is ScoreUnit.SETS:
        return [f"{i + 1}세트" for i in range(n)]
    if unit is ScoreUnit.POINTS:
        # 5구간부터는 연장이다. 'OT'라고 적어야 4쿼터제와 구분된다.
        return [f"{i + 1}Q" if i < 4 else f"OT{i - 3}" for i in range(n)]
    return [str(i + 1) for i in range(n)]


def _total_labels(league: League) -> tuple[list, str]:
    unit = SCORE_UNIT_BY_LEAGUE.get(league)
    if unit is ScoreUnit.SETS:
        return ["승세트"], "세트"
    if unit is ScoreUnit.POINTS:
        return ["합계"], "점"
    return [], "점"


def _flow_body(game, league: League) -> str | None:
    """한 경기의 흐름표. 만들 재료가 없으면 None."""
    meta = getattr(game, "meta", None)
    if meta is None:
        return None
    aw = C5._nm(league, game.away)
    hm = C5._nm(league, game.home)

    if getattr(meta, "goals", ()):                    # 축구 — 구간이 없다
        return C5.body_timeline(
            away_name=aw, home_name=hm,
            away_win=bool(game.score and game.score.away > game.score.home),
            home_win=bool(game.score and game.score.home > game.score.away),
            events=[(g.minute, g.side, g.name, "자책" if g.own_goal else "")
                    for g in meta.goals])

    rows = list(getattr(meta, "line_score", ()) or ())
    if not rows:
        return None
    # `line_score`는 계약상 **(홈, 원정)**이다. 카드는 원정을 먼저 그린다.
    home_vals = [r[0] for r in rows]
    away_vals = [r[1] for r in rows]
    tl, _ = _total_labels(league)
    at: list = []
    ht: list = []
    totals = dict(getattr(meta, "line_totals", {}) or {})
    if totals:
        tl = list(totals)
        ht = [totals[k][0] for k in tl]
        at = [totals[k][1] for k in tl]
    elif tl:                                          # 농구·배구는 합계를 우리가 더한다
        ht = [game.score.home if game.score else sum(v for v in home_vals if v)]
        at = [game.score.away if game.score else sum(v for v in away_vals if v)]
    try:
        body = C5.body_periods(
            labels=_period_labels(league, len(rows)),
            away_name=aw, home_name=hm, away=away_vals, home=home_vals,
            total_labels=tl, away_totals=at, home_totals=ht,
            highlight=SCORE_UNIT_BY_LEAGUE.get(league) is ScoreUnit.RUNS)
    except ValueError:
        return None                                   # 칸이 좁다 — 흐름표를 포기한다
    for label, text in (getattr(meta, "highlights", ()) or ()):
        body += (f'<div class="bar"><span class="k">{C5.esc(label)}</span>'
                 f'<span class="v">{C5.esc(text)}</span></div>')
    return body


def result_card(games: list, league: League, day: str, *,
                now: datetime | None = None) -> tuple[str, list[str]] | None:
    """경기 결과 카드 (HTML, 캡션 파트들). 못 만들면 None.

    경기가 적고 흐름 데이터가 있으면 **흐름표**를, 아니면 지금까지처럼
    한 줄 요약을 그린다. 둘 다 같은 골격을 지난다.
    """
    todays = [g for g in games if g.is_terminal]
    if not todays:
        return None
    # **폴백 인자 이름을 맞춘다.** `count=`로 넘겼더니 "0경기 종료"가 나왔다 —
    # 결과 카드의 폴백은 `final`·`off`를 쓴다. 인자 이름이 틀려도 파이썬은
    # 조용히 기본값 0을 쓰기 때문에 실행은 되고 카드만 거짓말을 한다.
    _fin = sum(1 for g in todays if g.status is Status.FINAL)
    _off = len(todays) - _fin
    # **한 경기짜리 카드는 그 경기의 이야기를 말한다.** "1경기 종료"는
    # 아무것도 알려주지 않는다 — 카드에 경기가 하나뿐인데 개수를 세는 셈이다.
    head = None
    if len(todays) == 1:
        head = H.for_single_result(todays[0], league)
    head = head or H.for_result(todays, league) or H.fallback(
        "result", final=_fin, off=_off)
    date_label = _date_label(todays[0], now)

    flow = None
    if len(todays) <= FLOW_MAX_GAMES:
        parts = [_flow_body(g, league) for g in sorted(todays, key=lambda x: x.start_utc)]
        if all(parts):
            flow = "".join(parts)
    body = flow or C5.body_scoreboard(todays, league)

    foot = _foot(todays, league)
    html = C5.shell(kind="result", league=league, date_label=date_label,
                    head=head, body=body, foot_left=foot)
    # `C5.caption()`은 **이미 리스트**를 돌려준다([0]=사진 캡션, [1:]=이어 보낼 텍스트).
    # 한 번 더 감쌌더니 캡션이 리스트 안의 리스트가 됐다 — 검증이 잡았다.
    parts = C5.caption(kind="result", league=league, head=head, date_label=date_label)
    return html, list(parts)


def _date_label(game, now: datetime | None) -> str:
    d = game.start_utc.astimezone(KST)
    return f"{d.month}.{d.day} {'월화수목금토일'[d.weekday()]}"


def _foot(games: list, league: League) -> str:
    """꼬리말 — **출처를 주장하지 않는다.** 경기장은 사실이고, 그것으로 충분하다.

    (약점 107: 'LCK 공식 결과'라고 적었는데 실제로는 팬 위키였다.)
    """
    if len(games) == 1:
        v = venue_name(games[0].venue) if games[0].venue else ""
        if v:
            return v
    return f"{len(games)}경기"


# ══════════════════════════════════════════════════════════════
# PNG — **옛 렌더 함수를 쓰지 않는다**
# ══════════════════════════════════════════════════════════════
#
# 처음엔 v5 HTML을 `pipeline.render_png()`에 그대로 넘겼다. 그러자 그 함수의
# 타이포그래피 검사가 `null.getBoundingClientRect()`로 터졌다 — 그 검사는
# **옛 카드 골격(`#card`)을 전제**하고, v5는 `.card`이기 때문이다. 게다가
# 옛 함수는 앞에 옛 CSS를 붙이는데 v5 HTML은 이미 완결형이라 이중으로 감싼다.
#
# 골격이 다르면 검사도 다르다. 그래서 여기에 v5 전용 경로를 둔다.
# **검사에 걸리면 None을 돌려준다** — 부르는 쪽이 옛 카드로 떨어지고,
# 깨진 카드는 나가지 않는다(대표님이 두부 카드를 받은 적이 있다).

SEND_JPEG_QUALITY = 88


def render_png(card_html: str, out: pathlib.Path) -> tuple[int, int, int] | None:
    """v5 카드를 그려 PNG로 저장한다. `(폭, 높이, jpg 바이트)` 또는 None.

    None은 '못 그렸다'가 아니라 **'내보내면 안 된다'**는 뜻이다 —
    검사에 걸린 카드는 옛 카드로 대신한다.
    """
    from playwright.sync_api import sync_playwright
    from PIL import Image

    out = pathlib.Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    problems: list = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            # **배율 1이다.** 시안을 볼 때 쓴 `device_scale_factor=2`를 그대로
            # 두면 PNG가 2160px로 나온다 — 계약은 폭 1080px 고정이고, 넘으면
            # 텔레그램 서버가 한 번 더 줄여 화질이 떨어진다. 게이트가 막으려던
            # 바로 그것이다(CARD_WIDTH_PX). 배포 직전 적대적 점검에서 잡았다.
            pg = b.new_page(viewport={"width": C5.CARD_W, "height": 1600})
            pg.set_content(card_html)
            pg.wait_for_timeout(250)
            problems = pg.evaluate(C5._MEASURE_JS)
            el = pg.query_selector(".card")
            if el is None:
                b.close()
                return None
            el.screenshot(path=str(out))
            b.close()
    except Exception as e:                                   # noqa: BLE001
        print(f"  ⚠️ [v5] 렌더 실패: {e.__class__.__name__}")
        return None
    if problems:
        print("  ⚠️ [v5] 카드 결함 — 옛 카드로 대신합니다: " + " | ".join(problems[:3]))
        return None
    im = Image.open(out).convert("RGB")
    w, h = im.size
    # **옛 렌더가 하던 크기 게이트를 여기서도 한다.**
    # 골격이 달라 검사 코드는 갈렸지만, **계약은 하나다** — 폭 1080 고정,
    # 높이 2000 이하, 세로비 1.85 이하. v5만 이 검사를 안 거치고 있었다.
    try:
        assert_card_geometry(w, h)
    except GateError as e:
        print(f"  ⚠️ [v5] 카드 크기가 계약을 벗어났습니다 — 옛 카드로 대신합니다: {e}")
        return None
    jpg = out.with_suffix(".jpg")
    im.save(jpg, "JPEG", quality=SEND_JPEG_QUALITY, optimize=True)
    return w, h, jpg.stat().st_size
