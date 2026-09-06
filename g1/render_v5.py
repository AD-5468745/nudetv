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
                      Status, assert_card_geometry, format_kickoff,
                      kst_day_label, venue_name)

# ── 되돌리는 스위치 ────────────────────────────────────────────
#
# **여기 하나를 False로 두면 그 종류는 즉시 옛 카드로 돌아간다.**
# 새 카드가 실서비스에서 무엇을 할지는 켜 봐야 알고, 되돌리는 길이 짧아야
# 켜 볼 수 있다. 종류별로 나눈 이유도 같다 — 하나가 잘못돼도 나머지는 산다.
USE_V5 = {
    "result": True,          # 경기 결과 — 흐름표가 들어가는 자리
    "morning": True,         # 2026-09-06 대표님: "모든 이미지 카드와 정보는 v5"
    "standings": True,       #   ↳ 그날 지적하신 카드가 이것이다
    "leaders": True,
    "analysis": True,
    "night": True,
    # **시작 알림은 이미지 카드가 아니다.** 텍스트 한 줄로 나간다
    # (tick.py가 render_start_alert의 문자열만 보낸다). 이미지로 바꾸는 것은
    # 디자인이 아니라 발송 방식의 변경이라 대표님 판단이 필요하다 — 그때까지 꺼 둔다.
    "start": False,
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


# ══════════════════════════════════════════════════════════════
# 나머지 5종 (2026-09-06 — 대표님: "모든 이미지 카드와 정보는 v5")
# ══════════════════════════════════════════════════════════════
#
# **시작 알림은 여기 없다.** 그것은 이미지 카드가 아니라 텍스트 한 줄이다
# (`tick.py`가 `render_start_alert`의 문자열만 보낸다). 이미지로 바꾸는 것은
# 디자인이 아니라 발송 방식의 변경이라 이 작업의 범위가 아니다.
#
# 다섯 함수의 규칙은 같다:
#   · 만들 재료가 없으면 **None** — 부르는 쪽이 옛 카드로 떨어진다
#   · 헤드라인은 `headline.py`가 만든다. **여기서 문장을 짓지 않는다**
#   · 캡션은 `C5.caption()`이 만든다. 카드에 없는 것만 덧붙인다

def _day_label(day: str, games: list | None = None) -> str:
    """카드에 찍는 날짜. **묶는 기준(sports_day)과 보여주는 날짜를 가른다.**

    `sports_day`는 홈 **현지** 캘린더 날짜다 — 미국 하루 슬레이트가 KST 06시
    경계에서 반으로 쪼개지지 않게 하려고 그렇게 정했고, 그 정의는 옳다.
    그런데 그 날짜를 그대로 찍으면 한국 구독자에게는 틀린 말이 된다:
    MLB 현지 8/30 슬레이트는 한국시각 8/31 새벽에 열리므로, 8월 31일 오후에
    "8.30" 카드가 도착하면 하루 묵은 것으로 보인다(대표님 지적 2026-08-31).

    처음 배선할 때 나는 여기서 sports_day를 그대로 썼다 — 옛 카드가 이미
    고쳐 놓은 문제를 새 카드에서 되살린 셈이다. 실렌더로 잡았다.
    """
    label, local = kst_day_label(games or [], day)
    return f"{label} · {local}" if local else label


def morning_card(games: list, league: League, day: str, *,
                 now: datetime | None = None) -> tuple[str, list[str]] | None:
    """모닝 브리핑 — 그 리그의 오늘 편성."""
    if not games:
        return None
    ordered = sorted(games, key=lambda g: g.start_utc)
    playable = [g for g in ordered
                if g.status not in (Status.CANCELED, Status.POSTPONED)]
    # **시각 문자열은 카드를 그리는 쪽에서 만든 것을 그대로 넘긴다** (약점 104).
    # 헤드라인이 따로 계산하면 둘이 어긋난다.
    # 한국 날짜 둘에 걸치면 **전 행에 요일**을 붙인다 — 안 붙이면 23:00과 01:00이
    # 같은 날처럼 읽힌다(유럽 주말·일부 MLB 슬레이트). 판정은 옛 파일이 갖고 있다.
    import pipeline as _P0
    _wd = True if _P0.spans_two_kst_days(ordered) else None
    labels = [format_kickoff(g, with_weekday=_wd)[0] for g in ordered]
    kst = [format_kickoff(g, with_weekday=_wd)[0] for g in playable]
    # **'오늘'이라고 단정하지 않는다.** MLB 슬레이트는 한국시각 새벽에 열리고,
    # 유럽 주말은 한국 날짜 둘에 걸친다. 옛 카드가 이미 이 규칙을 갖고 있었는데
    # (`day_word_span`) 새 카드가 안 받아 "오늘 11경기"라고 우길 뻔했다.
    word = _P0.day_word_span(ordered, now)
    head = H.for_morning(ordered, league, kst_times=kst, day_word=word) or \
        H.fallback("morning", count=len(playable), day_word=word)
    body = C5.body_schedule(ordered, league, times=labels)
    off = len(ordered) - len(playable)
    foot = f"{len(ordered)}경기" + (f" · {off}경기 취소·연기" if off else "")
    lab = _day_label(day, ordered)
    html = C5.shell(kind="morning", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    return html, list(C5.caption(kind="morning", league=league, head=head,
                                 date_label=lab))


def standings_card(rb, league: League, day: str, *,
                   group: str | None = None) -> tuple[str, list[str]] | None:
    """팀 순위 — 대표님이 2026-09-06에 지적한 바로 그 카드."""
    rows = [s for s in rb.standings if group is None or s.group == group]
    if len(rows) < 2:
        return None
    head = H.for_standings(rb.standings, league, group=group) or H.fallback(
        "standings", label="현재 순위", group=group)
    body = C5.body_standings(rows, league)
    html = C5.shell(kind="standings", league=league, date_label=_day_label(day),
                    head=head, body=body, foot_left=f"{len(rows)}개 구단",
                    group_label=(f"{C5.LEAGUE_LABEL.get(league, '')} {group}".strip()
                                 if group else ""))
    return html, list(C5.caption(kind="standings", league=league, head=head,
                                 date_label=_day_label(day)))


def leaders_card(rb, league: League, day: str, set_idx: int
                 ) -> tuple[str, list[str]] | None:
    """부문 순위 — 4부문 × TOP5. **제목은 실제로 실린 부문에서 만든다**(약점: v1.11h)."""
    import pipeline as P                       # 세트 정의는 옛 파일이 단일 진실 원천이다
    title, cats = P.leader_set(rb, set_idx)
    if not cats:
        return None
    body = C5.body_leaders(rb.leaders, league, cats)
    if not body:
        return None
    head = H.for_leaders(rb.leaders, league, cats) or H.fallback(
        "leaders", count=len(cats), set_name=title)
    extra = C5.leaders_extra(rb.leaders, league, cats)
    html = C5.shell(kind="leaders", league=league, date_label=_day_label(day),
                    head=head, body=body, foot_left=title)
    return html, list(C5.caption(
        kind="leaders", league=league, head=head, date_label=_day_label(day),
        extra_lines=extra, extra_title="그 밖의 부문 1위" if extra else ""))


def night_card(games: list, day: str) -> tuple[str, list[str]] | None:
    """나이트 브리핑 — 전 리그 통합 1장. **결과를 다시 쓰지 않는다**(색인이다).

    리그가 하나뿐이면 만들지 않는다 — 그날 그 리그 결과 카드와 같은 말이 된다.
    """
    fin = [g for g in games if g.status is Status.FINAL]
    by_lg: dict = {}
    for g in games:
        by_lg.setdefault(g.league, []).append(g)
    if len(by_lg) < 2:
        return None
    rows = []
    for lg, gs in sorted(by_lg.items(), key=lambda kv: kv[0].value):
        done = [g for g in gs if g.status is Status.FINAL]
        off = [g for g in gs
               if g.status in (Status.CANCELED, Status.POSTPONED)]
        bits = f"{len(done)}경기 종료" if done else "종료 경기 없음"
        if off:
            bits += f" · {len(off)}경기 취소"
        pick = ""
        if done:
            # **대표 경기는 '가장 점수차가 큰 것'이다.** 동률이면 고르지 않는다 —
            # 아무거나 집으면 그 순간 카드가 근거 없는 선택을 한 것이 된다.
            diffs = [(abs(g.score.home - g.score.away), g) for g in done if g.score]
            if diffs:
                top = max(d for d, _ in diffs)
                tied = [g for d, g in diffs if d == top]
                if len(tied) == 1:
                    g = tied[0]
                    w, l = ((g.home, g.away) if g.score.home > g.score.away
                            else (g.away, g.home))
                    hi = max(g.score.home, g.score.away)
                    lo = min(g.score.home, g.score.away)
                    pick = (f'<b>{C5.esc(C5._nm(lg, w))} {hi}</b>-{lo} '
                            f'{C5.esc(C5._nm(lg, l))}')
        rows.append((C5.LEAGUE_LABEL.get(lg, lg.value), C5.esc(bits), pick))
    head = H.fallback("night", leagues=len(by_lg), final=len(fin))
    # 나이트는 **한국 날짜**로 묶은 카드다(`night_brief_day`) — 현지 병기가 필요 없다.
    lab = _day_label(day)
    html = C5.shell(kind="night", league=None, date_label=lab,
                    head=head, body=C5.body_index(rows),
                    foot_left=f"{len(by_lg)}개 리그", theme="dark")
    return html, list(C5.caption(kind="night", league=None, head=head,
                                 date_label=lab))


def analysis_card(rb, game, league: League, day: str, *,
                  team_stats: dict | None = None, history: list | None = None,
                  now: datetime | None = None) -> tuple[str, list[str]] | None:
    """경기 분석 — 좌우 대비 + 관전 포인트.

    **승률·확률·추천을 쓰지 않는다.** 우리에겐 모델이 없다. 가진 숫자를 읽어 줄
    뿐이고, 그 문장은 `headline.for_preview`가 만들고 게이트가 되짚는다.
    """
    a, h = game.away.team_code, game.home.team_code
    sa, sh = rb.team(a), rb.team(h)
    if not sa or not sh:
        return None
    na, nh = C5._nm(league, a), C5._nm(league, h)
    as_of = (rb.collected_utc.astimezone(KST).strftime("%m/%d")
             if getattr(rb, "collected_utc", None) else "")

    metrics: list = []
    rows: list = []

    def add(label, av, hv, at, ht, higher, *, compare=True):
        """표에 한 줄 싣는다. `compare=False`면 **우위 계산에서 뺀다.**"""
        m = H.Metric(label=label, away_text=at, home_text=ht,
                     away_val=av, home_val=hv, higher_better=higher,
                     as_of=as_of)
        if compare:
            metrics.append(m)
        w = m.winner()
        rows.append((at, label, ht, "l" if w == "away" else ("r" if w == "home" else "")))

    # **순위는 세지 않는다.** 순위는 다른 항목들의 *결과*라서, 우위 항목으로
    # 세면 승률과 거의 같은 사실을 두 번 세는 것이 된다 — "3개 중 3개를
    # 가져간다"가 실제보다 크게 들린다. 옛 분석 카드도 순위·승률은 안 셌다.
    # 표에는 남긴다: 독자가 가장 먼저 보는 값이다.
    add("순위", -sa.rank, -sh.rank, f"{sa.rank}위", f"{sh.rank}위", True,
        compare=False)
    try:
        add("승률", float(sa.pct), float(sh.pct), sa.pct, sh.pct, True)
    except (TypeError, ValueError):
        pass
    if sa.last10 and sh.last10 and sa.last10.total and sh.last10.total:
        add("최근10", sa.last10.win, sh.last10.win,
            f"{sa.last10.win}-{sa.last10.loss}", f"{sh.last10.win}-{sh.last10.loss}",
            True)
    import pipeline as P                       # 팀 기록 표기는 옛 파일이 단일 진실 원천이다
    for key, label, higher in P.TEAM_STAT_LABELS:
        ta = (team_stats or {}).get(a) or {}
        th = (team_stats or {}).get(h) or {}
        if key not in ta or key not in th:
            continue                          # **빈 칸을 남기지 않는다** — 행을 뺀다
        try:
            add(label, float(ta[key]), float(th[key]), str(ta[key]), str(th[key]),
                higher)
        except (TypeError, ValueError):
            continue
    if len(rows) < 3:
        return None                           # 순위·승률뿐이면 '분석'이 아니다

    h2h_text = ""
    wld = rb.between(a, h)
    if wld is not None and wld.total:
        h2h_text = (f"{na} {wld.win}승 {wld.loss}패"
                    + (f" {wld.draw}무" if wld.draw else ""))
    verdict = H.for_preview(away_name=na, home_name=nh, metrics=metrics,
                            h2h_text=h2h_text)

    # **`h2h`는 표 전체다.** 한 쌍의 `WLD`를 넘기면 `for_analysis`가 `.get()`을
    # 부르다 터진다 — 그리고 그 예외는 폴백이 삼켜서, 분석만 조용히 옛 카드로
    # 나갔을 것이다. 실렌더로 잡았다(검사가 아니라 눈으로 잡은 것이 문제다 →
    # 아래 verify_render_v5에 종류별 '실제로 만들어졌나' 검사를 세웠다).
    head = H.for_analysis(game, league, standings=rb.standings,
                          h2h=getattr(rb, "h2h", None)) or \
        H.fallback("analysis", label=f"{na} vs {nh}")
    # **현지 시각을 함께 적는다.** MLB·NPB는 한국시각만 쓰면 어느 날 경기인지
    # 흐려진다 — 어느 표기를 쓸지는 `format_kickoff`가 리그별로 이미 정해 뒀다.
    kst, loc = format_kickoff(game)
    if loc:
        kst = f"{kst} · 현지 {loc}"
    place = venue_name(game.venue) if game.venue else ""
    # **부제는 순위를 다시 말하지 않는다.** 표 첫 줄이 이미 순위다 —
    # 한 카드가 같은 사실을 두 번 말하면 그만큼 자리가 낭비된다(실렌더에서 잡음).
    def _wld(rec):
        return f"{rec.win}-{rec.loss}" + (f"-{rec.draw}" if rec.draw else "")
    body = C5.body_compare(rows, na, nh, _wld(sa.record), _wld(sh.record))
    if verdict:
        body += C5.body_verdict(verdict)
    foot = " · ".join([x for x in (kst, place) if x]) or day
    lab = _day_label(day, [game])
    html = C5.shell(kind="analysis", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    return html, list(C5.caption(kind="analysis", league=league, head=head,
                                 date_label=lab))



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

    **높이 때문에 떨어질 때는 한 번 더 시도한다 (2026-09-06).**
    대표님이 고른 여백판은 카드를 세로로 늘린다. 경기가 많은 날일수록 길어지므로,
    그대로 두면 **정보가 가장 많은 날에만 새 디자인이 사라진다** — 정확히
    반대로 동작한다. 그래서 높이 게이트에 걸리면 밀도를 한 단계만 내려
    (`cards_v5.relax`) 다시 그린다. 골격도 정보도 그대로고 간격만 좁아진다.
    그래도 안 되면 그때 옛 카드로 떨어진다.
    """
    r = _render_once(card_html, out)
    if r != TOO_TALL:
        return r
    tighter = C5.relax(card_html)
    if tighter is None:
        return None
    print("  ⓘ [v5] 여백판이 높이 상한을 넘어 조임판으로 다시 그립니다")
    r = _render_once(tighter, out)
    return None if r == TOO_TALL else r


# 높이 게이트에 걸렸다는 표시. **`None`과 갈라야 한다** — `None`은 '다시 그려도
# 같다'(렌더 실패·결함)이고, 이것만 밀도를 낮춰 재시도할 값어치가 있다.
TOO_TALL = "too-tall"


def _render_once(card_html: str, out: pathlib.Path):
    """`(w,h,bytes)` 성공 · `TOO_TALL` 높이 초과 · `None` 그 밖의 실패."""
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
        # 폭이 틀린 것은 골격 문제라 다시 그려도 같다. **높이·세로비만** 재시도한다.
        if w == C5.CARD_W:
            print(f"  ⓘ [v5] 카드가 높이 상한을 넘었습니다: {e}")
            return TOO_TALL
        print(f"  ⚠️ [v5] 카드 크기가 계약을 벗어났습니다 — 옛 카드로 대신합니다: {e}")
        return None
    jpg = out.with_suffix(".jpg")
    im.save(jpg, "JPEG", quality=SEND_JPEG_QUALITY, optimize=True)
    return w, h, jpg.stat().st_size
