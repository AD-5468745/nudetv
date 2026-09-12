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
from dataclasses import replace
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import cards_v5 as C5
import headline as H
from contract import (GateError, KST, League, ScoreUnit, SCORE_UNIT_BY_LEAGUE,
                      Status, assert_card_geometry, format_kickoff,
                      kst_day_label, morning_label, venue_name,
                      cancel_reason_text, foreign_script_chars,
                      LINEUP_ENABLED, is_upcoming, record_asof_note,
                      GOAL_FLASH_ENABLED, goal_flash_enabled_for,
                      goal_key, goal_sort_key)

# ── 되돌리는 스위치 ────────────────────────────────────────────
#
# **여기 하나를 False로 두면 그 종류는 즉시 옛 카드로 돌아간다.**
# 새 카드가 실서비스에서 무엇을 할지는 켜 봐야 알고, 되돌리는 길이 짧아야
# 켜 볼 수 있다. 종류별로 나눈 이유도 같다 — 하나가 잘못돼도 나머지는 산다.
USE_V5 = {
    "result": True,          # 리그 결과 요약 — 하루를 닫는 한 장
    # 경기별 2종 (v1.14) — 애초에 v5로만 만든다. 옛 카드에 대응물이 없다.
    "kickoff": True,         # 그 경기 시작 10~1분 전
    "flash": True,           # 그 경기 종료 직후 — 흐름표가 실제로 보이는 자리
    "lineup": True,          # v1.17: 그 경기 선발 명단 — 옛 카드에 대응물이 없다
    "goal": True,            # v1.35: 경기 중 득점 속보 — 옛 카드에 대응물이 없다
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
            away_name=aw, home_name=hm, league=league,
            away_win=bool(game.score and game.score.away > game.score.home),
            home_win=bool(game.score and game.score.home > game.score.away),
            # ★ 다섯째 자리가 **추가시간**이다 (v1.34). 이 한 자리가 빠져 있어서
            #   90+6에 들어간 결승골이 카드에 `90′`로 그려졌다.
            events=[(g.minute, g.side, g.name, "자책" if g.own_goal else "",
                     getattr(g, "added", 0) or 0)
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
                now: datetime | None = None,
                extra_body: str = "") -> tuple[str, list[str]] | None:
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
    head = _with_korean_player(head, todays)
    date_label = _date_label(todays[0], now)

    flow = None
    if len(todays) <= FLOW_MAX_GAMES:
        parts = [_flow_body(g, league) for g in sorted(todays, key=lambda x: x.start_utc)]
        if all(parts):
            flow = "".join(parts)
    if flow:
        body = flow
    elif len(todays) == 1:
        # 한 경기인데 흐름표가 없다 — 스코어보드는 머리말과 같은 말이다.
        _g = todays[0]
        _k, _loc = format_kickoff(_g)
        _ex = []
        if _g.status in (Status.CANCELED, Status.POSTPONED):
            # 원문이 아니라 번역표를 거친 문구를 쓴다(일본어 유출 방지).
            _ex.append(("상태", cancel_reason_text(
                _g.meta.cancel_reason if _g.meta else None, _g.status)))
        body = C5.body_gameinfo(
            kst=_k, local=_loc or "",
            venue=(venue_name(_g.venue) or "") if _g.venue else "", extra=_ex)
    else:
        # ── **정리판** — 번호 + 시각 + 전 경기 (2026-09-07 대표님 확정) ──
        #
        # 이 카드는 그 리그의 하루를 닫는 한 장이다. 각 경기 종료 속보는 이미
        # 경기마다 나갔고(`FINAL_FLASH`), 이건 마지막 속보 30분 뒤에 나가는
        # 정리다. 그래서 **몇 번째 경기였는지·몇 시 경기였는지**를 함께 적는다:
        # *"몇번째 경기라고 표기해주면 좋을것같고 경기시간도 같이 알려주면 좋겠다"*.
        body = C5.body_scoreboard(todays, league, with_time=True, numbered=True)

    # ── 오늘의 경기 (대표님: "베스트 경기를 뽑아 간단히 코멘트") ──
    #
    # 흐름표를 그린 날(경기 서넛)에는 붙이지 않는다 — 흐름표가 이미 그 경기의
    # 이야기를 하고 있어서 같은 말이 두 번 된다.
    if not flow:
        body += C5.body_best(H.best_games(todays, league), league)

    # v1.17 — 부르는 쪽이 얹는 블록(속보 카드의 선발 명단). 기본은 빈 문자열이라
    # 옛 호출부는 그대로 동작한다.
    body += extra_body

    foot = _foot(todays, league)
    html = C5.shell(kind="result", league=league, date_label=date_label,
                    head=head, body=body, foot_left=foot)
    # `C5.caption()`은 **이미 리스트**를 돌려준다([0]=사진 캡션, [1:]=이어 보낼 텍스트).
    # 한 번 더 감쌌더니 캡션이 리스트 안의 리스트가 됐다 — 검증이 잡았다.
    # ── ★ v1.27 — 이닝 흐름을 문장으로 (대표님 우선순위 1번) ─────────
    #
    # ⛔ **카드에 있는 걸 텍스트에 또 쓰지 않는다.** 이닝 표와 결승타는 카드가
    #    이미 그렸다. 여기 붙는 것은 **표가 못 말하는 것** 하나다 —
    #    언제 갈렸고 어디가 승부처였나.
    #
    # **한 경기짜리(종료 속보)에만 붙인다.** 정리판은 여러 경기를 담는 카드라
    # 경기마다 문단을 붙이면 캡션이 통째로 후속 텍스트로 밀려난다(대표님
    # 2026-09-07: 정리판은 그 리그의 하루를 닫는 한 장이다).
    _extra: list[str] = []
    _title = ""
    try:
        import pipeline as _Pf                     # 순환 import를 피해 함수 안에서
        if len(todays) == 1:
            _g1 = todays[0]
            _an = C5._nm(league, _g1.away)
            _hn = C5._nm(league, _g1.home)
            # 야구는 이닝 표를, 축구는 득점 타임라인을 카드가 그린다.
            # **둘 다 "그래서 언제 갈렸나"는 안 말한다** — 그것이 텍스트의 몫이다.
            _extra = (_Pf.flow_prose(_g1, league,
                                     away_name=_an, home_name=_hn)
                      or _Pf.goal_prose(_g1, league,
                                        away_name=_an, home_name=_hn))
            _title = "경기 흐름"
        else:
            # ── ★ v1.29 — 정리판: **그날이 어떤 하루였나** ─────────────
            #
            # ⛔ 카드(`body_scoreboard`)가 번호·시각·점수를 그리고 '오늘의 경기'까지
            #    붙는다. 그래서 여기서는 **점수를 쓰지 않는다.** 텍스트가 맡는 것은
            #    표를 한 줄씩 읽어서는 안 보이고 **세어야 보이는 것**이다 —
            #    몇 경기가 접전이었나, 역전이 몇 번 나왔나, 연장이 있었나.
            _extra = _Pf.wrapup_lines(
                games, league, name_of=lambda t: C5._nm(league, t))
            _title = "오늘의 하루"
    except Exception:                              # noqa: BLE001
        _extra = []                                # 문장 하나 때문에 카드를 잃지 않는다
    parts = C5.caption(kind="result", league=league, head=head,
                       date_label=date_label,
                       extra_lines=_extra or None,
                       extra_title=_title if _extra else "")
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
    # 번호를 붙인다 — **정리판과 같은 모양**이다(대표님: 시작 흐름을 종료
    # 흐름과 같게). 두 카드가 같은 규칙으로 읽히면 채널 전체가 한 결이 된다.
    body = C5.body_schedule(ordered, league, times=labels, numbered=True)
    off = len(ordered) - len(playable)
    foot = f"{len(ordered)}경기" + (f" · {off}경기 취소·연기" if off else "")
    lab = _day_label(day, ordered)
    # **첫 경기 뒤에 나가면 '예고'라고 우기지 않는다** (2026-09-07).
    # 예고는 이제 첫 경기 30분 전에 잡히지만, 시계가 밀리면 그 뒤에 나갈 수 있다.
    _first = min(g.start_utc for g in ordered)
    _kind_label = morning_label(now or datetime.now(timezone.utc), _first)
    html = C5.shell(kind="morning", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot,
                    kind_label=_kind_label)
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
    # v1.31 — **이 숫자가 언제 것인지 밝힌다.** 기록은 30분에 한 번 긁는다.
    return html, list(C5.caption(kind="standings", league=league, head=head,
                                 date_label=_day_label(day),
                                 note=record_asof_note(rb)))


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
        extra_lines=extra, extra_title="그 밖의 부문 1위" if extra else "",
        note=record_asof_note(rb)))                       # v1.31


def night_card(games: list, day: str
               ) -> tuple[str, str, list[str]] | None:
    """나이트 브리핑 — 전 리그 통합 1장. 하루를 닫는 카드다.

    ⚠️ **이 함수만 세 값을 돌려준다**: `(카드HTML, 짧은판HTML, 캡션들)`.
    다른 카드는 `(카드HTML, 캡션들)` 둘이다.

    왜 여기만 다른가 — 이 카드만 **길이를 미리 알 수 없다.** 다른 카드는 한
    리그의 하루치라 상한이 있지만(MLB 18경기), 나이트는 그날 열린 모든 리그가
    겹친다. 리그 넷이면 40경기가 되고 어떤 밀도로도 2000px에 안 담긴다.
    그래서 **더 짧은 판을 같이 만들어** 넘겨준다(`render_png`의 마지막 단).
    짧은판을 여기서 만드는 이유는 줄이는 방법이 데이터에 달렸기 때문이다 —
    렌더러는 HTML만 보므로 "전 경기를 리그별 건수로 접는" 판단을 못 한다.
    """
    fin = [g for g in games if g.status is Status.FINAL]
    by_lg: dict = {}
    for g in games:
        by_lg.setdefault(g.league, []).append(g)
    # **리그가 하나뿐인 날에도 만든다 (2026-09-07 수정).**
    # 어제 여기에 `len(by_lg) < 2`를 두었다 — "리그가 하나면 그날 결과 카드와
    # 같은 말이 된다"는 이유였고, 의도는 맞았다. 그런데 **"안 만든다"가
    # "옛 카드로 나간다"가 됐다** — 부르는 쪽이 None을 받으면 옛 렌더로
    # 떨어지기 때문이다. 월요일(국내 리그 전부 휴식, MLB만 열림)에 실제로
    # 옛 v4 나이트 카드가 나갔다. 약점 133과 같은 뿌리:
    # **폴백이 있는 자리에서 'None'은 '안 함'이 아니라 '옛것으로 함'이다.**
    if not by_lg:
        return None
    # ── 본문 — 리그별로 묶고 **그 안에 전 경기를 싣는다** (2026-09-07) ──
    #
    # 대표님 지적 둘이 여기서 만난다:
    #   ① *"왜 15경기 종료라고 표기되고 한경기 결과만 보여줘?"*
    #      제목이 15이라 말하고 본문은 1경기만 보여주면 그건 요약이 아니라 결함이다.
    #   ② *"대표경기라는 기준이 사람들마다 다를텐데"*
    #      맞다. '가장 점수차가 큰 경기'는 **일방적이었다**는 뜻이지 볼 만했다는
    #      뜻이 아니다. 우리가 정한 기준을 카드가 사실인 양 내세우고 있었다.
    #      **고를 수 없으면 고르지 않는다** — 전부 싣는다.
    #
    # 길이는 `render_png`의 사다리가 맡는다: 여백판 → 조임판 → **짧은판**(리그별
    # 건수만) → 조임. 리그 서넛이 겹친 날에도 옛 카드로 떨어지지 않는다.
    groups = []
    compact = []
    counts = []
    for lg, gs in sorted(by_lg.items(), key=lambda kv: kv[0].value):
        done = [g for g in gs if g.status is Status.FINAL]
        off = [g for g in gs
               if g.status in (Status.CANCELED, Status.POSTPONED)]
        bits = f"{len(done)}경기 종료" if done else "종료 경기 없음"
        if off:
            bits += f" · {len(off)}경기 취소"
        label = C5.LEAGUE_LABEL.get(lg, lg.value)
        # 취소·연기도 싣는다 — 그날 있었던 일이고, 빼면 숫자가 안 맞는다.
        listed = [g for g in gs
                  if g.status in (Status.FINAL, Status.CANCELED, Status.POSTPONED)]
        groups.append((label, C5.esc(bits),
                       C5.body_scoreboard(listed, lg,
                                          with_time=True, numbered=True)))
        compact.append((label, C5.esc(bits),
                        [C5.compact_result(g, lg)
                         for g in sorted(listed, key=lambda x: x.start_utc)]))
        counts.append((label, C5.esc(bits)))
    # 리그가 하나뿐이면 그 리그 머리줄을 본문에 또 얹지 않는다 —
    # 머리말(`group_label`)이 이미 그 리그를 말하고 있다.
    _only = len(by_lg) == 1
    _body = (groups[0][2] if _only else C5.body_night_grouped(groups))
    # ── 오늘의 경기 (2026-09-07 대표님: "베스트 경기를 뽑아 간단히 코멘트") ──
    #
    # **아침에 없앤 '대표 경기'와 다른 것이다.** 없앤 것은 근거 없이 한 경기를
    # 올리던 것이고, 이건 **왜 골랐는지를 함께 적는다** — "9회 역전"·"1점 차"는
    # 데이터에서 읽히는 사실이지 우리 감상이 아니다(`headline.best_games`).
    # 규칙에 걸리는 경기가 없으면 이 칸은 아예 생기지 않는다.
    _best = (C5.body_best(H.best_games(games, next(iter(by_lg))),
                          next(iter(by_lg))) if _only else "")
    _body += _best
    # 짧은판은 두 단이다: **빽빽판**(전 경기를 흘려 담음) → 그래도 안 되면
    # 건수만. 대표님이 요청한 '전 경기'를 마지막까지 버티며 지킨다.
    # **'오늘의 경기'는 어느 단에서도 남긴다** — 카드가 짧아진 날에 골라 둔
    # 경기가 사라지면 그 기능은 가장 바쁜 날에만 없는 셈이 된다.
    _short = C5.body_night_compact(compact) + _best
    _shortest = C5.body_index(counts) + _best
    # 리그가 하나뿐이면 **그 리그 이름**을 넘긴다 — 헤드라인이 "1개 리그"
    # 대신 리그 이름을 머리말에 두고 제목은 숫자만 말한다(2026-09-07 대표님 지적).
    _one_label = C5.LEAGUE_LABEL.get(next(iter(by_lg)), "") if _only else ""
    head = H.fallback("night", leagues=len(by_lg), final=len(fin),
                      league_label=_one_label)
    # 나이트는 **한국 날짜**로 묶은 카드다(`night_brief_day`) — 현지 병기가 필요 없다.
    lab = _day_label(day)
    _shell_kw = dict(
        kind="night", league=None, date_label=lab, head=head,
        # 리그가 하나뿐이면 머리말이 "전 리그" 대신 그 리그를 말한다 —
        # 리그 하나짜리 카드에 "전 리그"는 틀린 말이다.
        group_label=_one_label,
        # 꼬리말도 같다 — 머리에서 "1개 리그"를 없애고 발치에 남겨 두면
        # 어색함이 그대로 남는다.
        foot_left=_one_label or f"{len(by_lg)}개 리그", theme="dark",
        # **여러 리그가 섞인 유일한 카드다.** `league=None`이라 껍데기가 스스로
        # 판단할 수 없으므로 실린 리그를 직접 넘긴다 — 유럽 경기가 하나라도
        # 실리면 소스 표기가 따라붙어야 한다.
        credit_for=list(by_lg))
    html = C5.shell(body=_body, **_shell_kw)
    # 짧은판은 **같은 머리·꼬리에 본문만 줄인 것**이다. 여기서 다른 문장을 쓰면
    # 사다리를 내려간 날에만 카드가 다른 말을 하게 된다.
    short_html = C5.shell(body=_short, **_shell_kw)
    shortest_html = C5.shell(body=_shortest, **_shell_kw)
    return (html, [short_html, shortest_html],
            list(C5.caption(kind="night", league=None, head=head,
                            date_label=lab)))


def _h2h_of(rb, a: str, h: str, na: str, history) -> tuple[str, str, bool]:
    """맞대결 (표기, 최근 흐름, **시즌 전체인가**).

    **소스가 주면 그것, 없으면 우리가 센다.** 한 값을 두 곳에서 받으면 어긋날 때
    판정할 곳이 없다 — 그래서 **먼저 소스, 없을 때만 계산** 순서를 못박는다(약점 74).

    ★ **셋째 값이 중요하다.** 공식 기록(KBO·NPB)은 시즌 전체라 "올해 맞대결"이
    참말이지만, 우리가 센 것은 **가진 스냅샷 범위**일 뿐이다 —
    실측 2026-09-07: KBO·NPB·K리그는 두 달치, **MLB는 일주일치**다.
    그걸 "올해"라고 부르면 카드가 그 자리에서 거짓말을 한다.
    """
    import pipeline as P
    wld = rb.between(a, h)
    if wld is not None and wld.total:
        return (f"{na} {wld.win}승 {wld.loss}패"
                + (f" {wld.draw}무" if wld.draw else ""), "", True)
    if history:
        got = P.h2h_from_games(history, a, h)
        if got:
            w, recent = got
            return (f"{na} {w.win}승 {w.loss}패"
                    + (f" {w.draw}무" if w.draw else ""), recent, False)
    return "", "", False


def _analysis_row(rb, game, league: League, no: int,
                  team_stats: dict | None, history=None) -> dict | None:
    """여러 경기 분석 카드의 한 경기 블록. 순위표에 없는 팀이면 None."""
    import pipeline as P
    a, h = game.away.team_code, game.home.team_code
    sa, sh = rb.team(a), rb.team(h)
    if not sa or not sh:
        return None                      # 지어내지 않는다 — 그 경기를 뺀다
    na, nh = C5._nm(league, a), C5._nm(league, h)
    kst, _loc = format_kickoff(game)

    # 비교 한 줄 — **값이 없는 항목은 통째로 뺀다**(약점 94·135).
    bits: list[str] = []
    metrics: list = []
    as_of = (rb.collected_utc.astimezone(KST).strftime("%m/%d")
             if getattr(rb, "collected_utc", None) else "")

    def _cmp(label, av, hv, at, ht, higher):
        metrics.append(H.Metric(label=label, away_text=at, home_text=ht,
                                away_val=av, home_val=hv, higher_better=higher,
                                as_of=as_of))
        bits.append(f'{C5.esc(label)} <b>{C5.esc(at)}</b> : <b>{C5.esc(ht)}</b>')

    try:
        # ★ **축구에 '승률'이라 쓰면 거짓이다** — 무승부가 있어 승/(승+패)가
        # 아니다. 어댑터는 승점률(승점 ÷ 최대승점)을 담는다. 라벨을 종목에
        # 맞춘다(약점 95: 낱말 하나가 두 가지로 읽히면 반드시 헷갈린다).
        _pl = "승점률" if league is League.KL1 else "승률"
        _cmp(_pl, float(sa.pct), float(sh.pct), sa.pct, sh.pct, True)
    except (TypeError, ValueError):
        pass
    if sa.last10 and sh.last10 and sa.last10.total and sh.last10.total:
        _cmp("최근10", sa.last10.win, sh.last10.win,
             f"{sa.last10.win}-{sa.last10.loss}",
             f"{sh.last10.win}-{sh.last10.loss}", True)
    for key, label, higher in P.team_stat_labels(league):
        ta = (team_stats or {}).get(a) or {}
        th = (team_stats or {}).get(h) or {}
        if key not in ta or key not in th:
            continue
        try:
            _cmp(label, float(ta[key]), float(th[key]),
                 P.format_team_stat(key, ta[key]), P.format_team_stat(key, th[key]),
                 higher)
        except (TypeError, ValueError):
            continue

    h2h_text, _h2h_recent, _h2h_full = _h2h_of(rb, a, h, na, history)
    # 한 줄 평은 **규칙 엔진이 만든 첫 줄만** 쓴다. 카드가 말을 짓지 않는다.
    v = H.for_preview(away_name=na, home_name=nh, metrics=metrics,
                      h2h_text=h2h_text, h2h_recent=_h2h_recent,
                      h2h_full=_h2h_full, ranks=(sa.rank, sh.rank))
    verdict = (v.lines[0] if (v and v.lines) else "")
    _hl = "올해 맞대결" if _h2h_full else "최근 맞대결"
    if h2h_text and verdict:
        verdict = f"{verdict} · {_hl} {h2h_text}"
    elif h2h_text:
        verdict = f"{_hl} {h2h_text}"
    return {"no": no, "time": kst, "away": na, "home": nh,
            "away_sub": f"{sa.rank}위", "home_sub": f"{sh.rank}위",
            "keys": " · ".join(bits), "verdict": verdict}


def analysis_cards(rb, games: list, league: League, day: str, *,
                   batch: int = 0, team_stats: dict | None = None,
                   history: list | None = None,
                   now: datetime | None = None) -> tuple[str, list[str]] | None:
    """분석 — **그날 여러 경기를 한 장에** (v1.15f · 대표님 지시).

    `batch`는 `analysis_batches()`가 나눈 묶음의 번호(0부터)다.
    큐가 묶음마다 한 건을 올리고, 여기서 그 묶음만 그린다.
    """
    import pipeline as _P
    batches = _P.analysis_batches(games)
    if batch >= len(batches):
        return None
    gs = batches[batch]
    # 번호는 **그날 전체 기준**이다 — 2장째가 다시 1번부터 세면 카드가 거짓말을 한다.
    base = batch * max(1, _P.ANALYSIS_PER_CARD)
    rows = []
    for i, g in enumerate(gs):
        r = _analysis_row(rb, g, league, base + i + 1, team_stats,
                          history=history)
        if r:
            rows.append(r)
    if not rows:
        return None
    head = H.fallback("analysis",
                      label=(f"{len(rows)}경기 분석" if len(batches) == 1
                             else f"{len(rows)}경기 분석 ({batch + 1}/{len(batches)})"))
    lab = _day_label(day, gs)
    html = C5.shell(kind="analysis", league=league, date_label=lab, head=head,
                    body=C5.body_analysis_multi(rows),
                    foot_left=f"{len(rows)}경기")

    # ── ★ 분석 산문을 캡션에 싣는다 (v1.24) ──────────────────────
    #
    # **여기가 '밋밋함'의 원인이었다.** 2026-09-07에 분석이 '한 경기 카드' →
    # '여러 경기 한 장'으로 바뀌면서 이 경로가 머리줄 한 줄짜리 캡션만
    # 돌려주게 됐다. `caption_analysis()`가 만들던 풍부한 텍스트는 **옛 카드로
    # 떨어질 때만** 쓰이게 됐고, 정상 경로에서는 한 번도 안 불렸다
    # (약점 78·151: 새 경로가 옛 경로의 기능을 빠뜨리고 폴백이 그것을 가린다).
    #
    # 경기마다 소제목을 달고 문단 사이를 빈 줄로 띄운다 — 대표님 지시
    # *"읽기편하도록 줄띄움도 맞춰서"*. 넘치면 `C5.caption`이 이어보낸다.
    _extra: list[str] = []
    for _g in gs:
        try:
            _pp = _P.analysis_prose(rb, _g, team_stats=team_stats,
                                    history=history)
        except Exception:                                    # noqa: BLE001
            _pp = []          # 한 경기가 막혀도 나머지 산문은 나간다
        if not _pp:
            continue
        if _extra:
            _extra.append("")
        _extra.append(f"■ {C5._nm(league, _g.away.team_code)} vs "
                      f"{C5._nm(league, _g.home.team_code)}")
        for _q in _pp:
            _extra.append("")
            _extra.append(_q)
    return html, list(C5.caption(kind="analysis", league=league, head=head,
                                 date_label=lab, extra_lines=_extra or None,
                                 extra_title="경기 분석" if _extra else "",
                                 note=record_asof_note(rb)))     # v1.31


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
        add("승점률" if league is League.KL1 else "승률",
            float(sa.pct), float(sh.pct), sa.pct, sh.pct, True)
    except (TypeError, ValueError):
        pass
    if sa.last10 and sh.last10 and sa.last10.total and sh.last10.total:
        add("최근10", sa.last10.win, sh.last10.win,
            f"{sa.last10.win}-{sa.last10.loss}", f"{sh.last10.win}-{sh.last10.loss}",
            True)
    import pipeline as P                       # 팀 기록 표기는 옛 파일이 단일 진실 원천이다
    for key, label, higher in P.team_stat_labels(league):
        ta = (team_stats or {}).get(a) or {}
        th = (team_stats or {}).get(h) or {}
        if key not in ta or key not in th:
            continue                          # **빈 칸을 남기지 않는다** — 행을 뺀다
        try:
            # 표기는 **옛 파일의 함수 하나**를 쓴다 — 두 화면이 같은 값을
            # 다르게 찍는 것이 이 프로젝트에서 가장 자주 난 사고다(약점 45·93·110).
            add(label, float(ta[key]), float(th[key]),
                P.format_team_stat(key, ta[key]), P.format_team_stat(key, th[key]),
                higher)
        except (TypeError, ValueError):
            continue
    # ── ③ 최근 n경기 폼 · ④ 시즌 상대전적 (v1.15c) ──────────────
    #
    # **옛 v4 카드는 이 둘을 갖고 있었는데 v5에는 없었다.** 그래서 비교표가
    # 세 줄이 안 되는 리그(NPB — 팀 기록 수집이 KBO 전용이라 순위·승률뿐)는
    # v5가 `None`을 내고 **폴백이 조용히 옛 카드로 떨어뜨렸다.**
    # 대표님이 "이미지 카드는 전부 v5"로 알고 계신 상태에서 NPB 분석만
    # 분홍색 v4로 나가고 있었다 — 약점 132(새 경로가 옛 경로의 수정을 버린다)와
    # 133(폴백이 결함을 숨긴다)이 겹친 자리다.
    #
    # `recent_form`·`_team_result`는 옛 파일의 순수 계산 함수를 그대로 쓴다.
    # **같은 것을 두 벌 만들지 않는다**(약점 45·110).
    form_rows: list = []
    form_title = "최근 5경기"
    if history:
        _n = 5
        _shown: list[int] = []
        for code, nm_ in ((a, na), (h, nh)):
            gs5 = P.recent_form(history, code, game.start_utc, _n)
            if not gs5:
                continue
            _shown.append(len(gs5))
            last = gs5[-1]
            opp = last.home if last.away.team_code == code else last.away
            mine = (last.score.home if last.home.team_code == code
                    else last.score.away)
            yours = (last.score.away if last.home.team_code == code
                     else last.score.home)
            word = {"W": "승", "L": "패", "D": "무"}[P._team_result(last, code)]
            ld = last.start_utc.astimezone(KST)
            form_rows.append((
                nm_, [P._team_result(g, code) for g in gs5],
                f"직전 {ld.month}.{ld.day} "
                f"{C5._nm(league, opp.team_code)}전 {mine}-{yours} {word}"))
        # **제목이 실제 도트 수와 같아야 한다** (v1.11p — 옛 카드가 고친 것).
        if form_rows:
            _mx = max(_shown)
            form_title = (f"최근 {_mx}경기" if len(set(_shown)) <= 1
                          else f"최근 최대 {_mx}경기")

    # 맞대결 — **소스가 주면 그것, 없으면 우리가 센다** (MLB·K리그는 소스가 없다)
    h2h_text, _h2h_recent, _h2h_full = _h2h_of(rb, a, h, na, history)
    wld = rb.between(a, h)
    if (wld is None or not wld.total) and h2h_text:
        wld = True                        # 블록 수 세기용 — 표기는 위에서 끝났다

    # ── 만들 자격 — **표 줄 수가 아니라 블록 수로 센다** ────────────
    #
    # 전에는 `len(rows) < 3`이었다. 그 판정은 "비교표가 빈약하다"는 뜻이지
    # "분석할 재료가 없다"는 뜻이 아니다 — 최근 폼과 상대전적이 있으면
    # 그것만으로도 옛 카드가 만들던 분석이 된다(옛 카드의 기준도
    # `ANALYSIS_MIN_CORE_BLOCKS = 2`, 즉 블록 수였다).
    _blocks = ((1 if len(rows) >= 3 else 0)
               + (1 if form_rows else 0)
               + (1 if h2h_text else 0))
    if _blocks < 2:
        return None                        # 순위·승률뿐이면 '분석'이 아니다
    # ── 근거를 여러 각도로 (v1.16 — 대표님: *"분석글을 상세하게"*) ──
    # 확률은 만들지 않는다. **가진 값의 각도를 늘린다.**
    _form = None
    if history:
        _fa = P.form_record(history, a, game.start_utc)
        _fh = P.form_record(history, h, game.start_utc)
        if sum(_fa) and sum(_fh):
            _form = (_fa, _fh)
    _streak = tuple((nm_, st_.streak_kind, st_.streak_len)
                    for nm_, st_ in ((na, sa), (nh, sh))
                    if getattr(st_, "streak_len", 0))
    verdict = H.for_preview(away_name=na, home_name=nh, metrics=metrics,
                            h2h_text=h2h_text, h2h_recent=_h2h_recent,
                            h2h_full=_h2h_full, form=_form,
                            streak=_streak or None, ranks=(sa.rank, sh.rank))

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
    if form_rows:
        body += C5.body_form(form_rows, title=form_title)
    _w2 = rb.between(a, h)
    if (_w2 is None or not _w2.total) and history:
        _got = P.h2h_from_games(history, a, h)
        _w2 = _got[0] if _got else None
    if _w2 is not None and getattr(_w2, "total", 0):
        body += C5.body_h2h(na, nh, _w2.win, _w2.loss, _w2.draw)
    if verdict:
        body += C5.body_verdict(verdict)
    foot = " · ".join([x for x in (kst, place) if x]) or day
    lab = _day_label(day, [game])
    html = C5.shell(kind="analysis", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    return html, list(C5.caption(kind="analysis", league=league, head=head,
                                 date_label=lab,
                                 note=record_asof_note(rb)))     # v1.31



# ══════════════════════════════════════════════════════════════
# 경기별 2종 (v1.14 — 대표님: "한경기당 1개씩")
# ══════════════════════════════════════════════════════════════

def _with_korean_player(head, games):
    """한국 선수 출전이 있으면 헤드라인 **부제**에 얹는다 (v1.15).

    **왜 부제인가.** 큰 글씨는 그 카드의 사실(점수·남은 시간)이고, 이건
    "이 경기를 왜 골랐는가"다. 둘을 한 줄에 섞으면 사실이 흐려진다.

    부제가 이미 있으면 **덮지 않고 뒤에 붙인다** — 원래 부제도 소스가 준
    사실이라 지울 이유가 없다.
    """
    lines = [pl for g in games for pl in (g.meta.player_lines if g.meta else [])]
    sub = H.korean_player_sub(lines)
    if not sub:
        return head                       # 라인업을 못 봤다 — 아무 말도 안 한다
    return replace(head, sub=f"{head.sub} · {sub}" if head.sub else sub)


def kickoff_card(games, league: League, *, now: datetime, rb=None
                 ) -> tuple[str, list[str]] | None:
    """경기 시작 10~1분 전 알림. **같은 시각 경기는 한 장에** (2026-09-07).

    대표님 지시: *"같은시간에 시작하는 경기는, 묶어서 시작 직전 알림카드 보내자"*.
    경기마다 한 장이면 동시 시작이 그대로 도배가 된다 — 유로파 18경기가 04:00에
    함께 시작하고 KBO 5경기는 전부 17:00이다.

    경기 하나만 넘겨도 된다(옛 호출부 호환). 그때는 대진 한 줄로 그린다.

    **남은 시간은 지금 기준으로 계산한다.** 큐에 담긴 예약 시각이 아니라
    부르는 순간의 `now`를 쓴다 — 페이서가 몇 분 미루면 예약 기준 값은 곧
    거짓이 된다(약점 104, `REJUDGE_AT_SEND`).
    """
    gs = [games] if not isinstance(games, (list, tuple)) else list(games)
    # **아직 시작 안 한 경기만** 남긴다. 묶음 안의 한 경기가 이미 시작했거나
    # 취소됐어도 나머지는 알려야 한다 — 옛 코드는 하나만 보고 전부 포기했다.
    #
    # ⚠️ v1.26 — 여기 `g.status is Status.SCHEDULED`가 있었고, 그것이 KBO
    # 킥오프가 **전 기간 0건**이었던 마지막 관문이다. v1.21이 큐를 고쳐도
    # 카드가 여기서 안 그려졌다. 판정은 이제 계약 한 곳에 있다.
    gs = [g for g in gs if is_upcoming(g, now)]
    if not gs:
        return None                        # **경기 시작 이후에는 절대 안 만든다**
    gs.sort(key=lambda g: g.start_utc)
    # 남은 시간은 **가장 이른 경기** 기준이다 — 묶음은 같은 시각이라 사실상 하나다.
    left = (gs[0].start_utc - now).total_seconds()
    head = _with_korean_player(H.for_kickoff(round(left / 60)), gs)
    if len(gs) == 1:
        g = gs[0]
        kst, loc = format_kickoff(g)
        body = C5.body_matchup(
            away_name=C5._nm(league, g.away), home_name=C5._nm(league, g.home),
            kst=kst, local=loc or "",
            venue=(venue_name(g.venue) or "") if g.venue else "")
        foot = C5.LEAGUE_LABEL.get(league, "")
    else:
        # 여러 경기 — **예고판·정리판과 같은 목록 골격**을 쓴다.
        # 시각은 다 같으므로 번호만 붙인다(시각 열은 같은 값이 반복돼 소음이다).
        _wd = True if _P_spans(gs) else None
        body = C5.body_schedule(
            gs, league, numbered=True,
            times=[format_kickoff(g, with_weekday=_wd)[0] for g in gs])
        foot = f"{C5.LEAGUE_LABEL.get(league, '')} · {len(gs)}경기"
    lab = _day_label(gs[0].sports_day, gs)
    html = C5.shell(kind="kickoff", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    # ── ★ v1.29 — 카드가 못 담는 것만 텍스트로 (킹카 대비 우선순위 2번) ────
    #
    # ⛔ 카드(`body_schedule`)가 그리는 것은 **시각 · 대진 · 장소**다.
    #    그래서 여기 붙는 것은 **순위 · 최근 흐름 · 맞대결** — 카드에 한 글자도
    #    없는 것들이다. 팀 이름은 어느 경기 이야기인지 가리키는 지시어로만 쓴다.
    #
    # **기록이 없으면 아무 말도 안 한다** — 기록은 30분에 한 번 긁는데 킥오프는
    # 매 틱 나갈 수 있다. 그때 카드는 그대로 나가고 문장만 빠진다.
    _extra: list[str] = []
    if rb is not None:
        try:
            import pipeline as _Pk               # 순환 import를 피해 함수 안에서
            _extra = _Pk.preview_lines(rb, gs, league,
                                       name_of=lambda t: C5._nm(league, t))
        except Exception:                        # noqa: BLE001
            _extra = []                          # 문장 하나 때문에 카드를 잃지 않는다
    return html, list(C5.caption(kind="kickoff", league=league, head=head,
                                 date_label=lab,
                                 extra_lines=_extra or None,
                                 extra_title="맞대결 참고" if _extra else ""))


def _P_spans(games) -> bool:
    """한국 날짜 둘에 걸치는가. 옛 파일이 판정을 갖고 있다."""
    import pipeline as _P0
    return bool(_P0.spans_two_kst_days(games))


# ── 선발 라인업 (v1.17) ───────────────────────────────────────
#
# 재료 하나로 카드 두 장을 만든다 — 대표님이 두 안을 다 승인하셨다
# (2026-09-08: *"경기시작전에 알려줄 출장 라인업"* + *"경기결과 시안은 합격"*).
#   · 킥오프 전 `lineup_card`  — 명단만
#   · 종료 후 `flash_card`     — 명단 + 누가 언제 넣었는지
# **블록을 만드는 곳은 여기 하나다.** 두 벌로 짜면 한쪽만 고치는 사고가 난다.

def _lineup_body(game, league: League, *, with_goals: bool) -> str | None:
    """양 팀 선발 명단 블록. 재료가 없으면 None.

    `with_goals`면 **선발 선수 이름 옆에 그가 넣은 골의 분**을 붙인다.
    교체 선수가 넣은 골은 선발 명단에 없으므로 여기 안 붙는다 —
    그 골은 같은 카드의 득점 타임라인이 이미 전부 담는다(빠지지 않는다).
    """
    lu = getattr(getattr(game, "meta", None), "lineup", None)
    if not lu:
        return None
    scored: dict = {}
    if with_goals:
        for g in (getattr(game.meta, "goals", ()) or ()):
            if g.own_goal:
                continue          # 자책골은 넣은 팀 쪽에 적히므로 이름 옆에 붙이면 거짓이 된다
            mark = f"{g.minute}+{g.added}" if g.added else str(g.minute)
            scored.setdefault((g.side, g.name), []).append(mark)
    teams = []
    for side, ref in (("away", game.away), ("home", game.home)):
        d = lu.get(side) or {}
        rows = d.get("rows") or []
        if not rows:
            return None           # 한쪽만 있는 명단은 명단이 아니다
        fm = str(d.get("formation") or "")
        teams.append({
            "name": C5._nm(league, ref),
            # "4231" → "4-2-3-1". 숫자가 아니면 소스가 준 그대로 둔다.
            "formation": "-".join(fm) if fm.isdigit() else fm,
            "rows": [[(nm, scored.get((side, nm), [])) for nm in line]
                     for line in rows]})
    return C5.body_lineup(teams)


def lineup_card(game, league: League, *, now: datetime
                ) -> tuple[str, list[str]] | None:
    """경기 시작 전 선발 라인업. 경기 하나당 한 장.

    **경기가 시작된 뒤에는 절대 만들지 않는다** — 킥오프 카드와 같은 규칙이다.
    큐가 이미 '킥오프 15분 전까지 관측된 것'만 담지만, 페이서가 미루는 동안
    시작해 버릴 수 있으므로 보내는 순간 다시 판정한다(약점 104, `REJUDGE_AT_SEND`).
    """
    if not LINEUP_ENABLED:
        return None
    # v1.26 — 킥오프와 같은 판정을 쓴다. 옛 조건(`status is SCHEDULED`)은
    # 소스가 시작 전에 `LIVE`를 주는 리그에서 명단 카드를 통째로 막았다.
    if not is_upcoming(game, now):
        return None
    left = (game.start_utc - now).total_seconds()
    body = _lineup_body(game, league, with_goals=False)
    if not body:
        return None
    kst, loc = format_kickoff(game)
    aw, hm = C5._nm(league, game.away), C5._nm(league, game.home)
    head = H.for_lineup(aw, hm, round(left / 60))
    head = _with_korean_player(head, [game])
    body = C5.body_matchup(away_name=aw, home_name=hm, kst=kst,
                           local=loc or "",
                           venue=(venue_name(game.venue) or "")
                           if game.venue else "") + body
    lab = _day_label(game.sports_day, [game])
    foot = C5.LEAGUE_LABEL.get(league, "")
    html = C5.shell(kind="kickoff", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    return html, list(C5.caption(kind="kickoff", league=league, head=head,
                                 date_label=lab))


def goal_card(game, league: League, goal_id: str, *,
              now: datetime | None = None) -> tuple[str, list[str]] | None:
    """경기 중 득점 속보 (v1.35). **골 하나당 한 장.**

    대표님 지시(2026-09-12): *"유료는 아직보류 나머지는 모두 업그레이드하자"*.
    킹카티비 대비 실질 격차로 판정한 유일한 항목이다(그쪽은 텍스트 + 채널
    바로가기로만 알린다 — 카드는 없다).

    `goal_id`는 `contract.goal_key(goal)`이 만든 이름이다. **순번이 아니다** —
    VAR 취소로 앞 골이 사라져도 남은 골의 이름이 밀리지 않는다.

    ── 이 함수가 지키는 것 ────────────────────────────────────
    ① **그 골까지만 그린다.** 머리말이 "후반 12분"이라고 말하는데 본문 점수가
       후반 35분 것이면 한 화면이 두 시점을 말한다(약점 67).
    ② **경기가 끝났으면 안 만든다.** 늦게 도착한 속보가 "1 : 0"이라고 말하는
       동안 채널에는 이미 최종 2 : 1이 올라가 있다. 그 골은 종료 속보의
       타임라인이 싣는다(`SAFETY_NET_FOR`) — 사라지지 않는다.
       보내는 순간 다시 판정한다(`REJUDGE_AT_SEND` · 약점 104).
    ③ **소스가 준 것만 쓴다.** 골이 목록에서 사라졌으면(VAR 취소) 만들지 않는다.
    """
    if not (GOAL_FLASH_ENABLED and goal_flash_enabled_for(league)):
        return None
    # ② 종료된 경기에는 '경기 중' 속보가 없다.
    if getattr(game, "is_terminal", False):
        return None
    goals = list((game.meta.goals if game.meta else ()) or ())
    if not goals:
        return None
    goals.sort(key=lambda g: goal_sort_key(getattr(g, "minute", 0),
                                           getattr(g, "added", 0)))
    idx = next((i for i, g in enumerate(goals) if goal_key(g) == goal_id), None)
    if idx is None:
        return None                        # ③ 취소됐거나 사라진 골
    # ── ★ 골 목록이 점수와 맞는지 먼저 확인한다 (v1.35) ──────────
    #
    # 이 카드는 **골 목록만으로 점수를 센다**(그 골 시점의 점수여야 하므로).
    # 그러려면 목록이 완전해야 하는데, 완전하다는 보장이 어디에도 없다:
    #   · 어댑터가 한 틱에 조회하는 경기 수에 상한이 있다(`GOALS_MAX_PER_TICK`)
    #   · 점수와 득점자는 **서로 다른 응답**에서 온다 — 한쪽이 앞설 수 있다
    #   · 자책골을 소스가 어느 편으로 묶는지 우리는 실측한 적이 없다
    #     (실측 2026-09-12: 보관된 스냅샷에 자책골 표본 0건)
    #
    # 그래서 **맞춰 보고, 안 맞으면 안 만든다.** 전부 더한 값이 소스가 준
    # 점수와 같을 때만 이 카드의 산수를 믿는다. 안 맞으면 침묵한다 —
    # 틀린 점수를 말하는 것보다 아무 말도 안 하는 편이 낫다. 그 골은
    # 스탬프가 남아 있으므로 목록이 맞춰지면 창(30분) 안에서 다시 시도되고,
    # 끝내 못 맞춰도 종료 속보가 최종 점수와 타임라인을 싣는다.
    _hs_all = sum(1 for g in goals if getattr(g, "side", None) == "home")
    _as_all = sum(1 for g in goals if getattr(g, "side", None) == "away")
    _sc = getattr(game, "score", None)
    if _sc is not None and (_hs_all, _as_all) != (_sc.home, _sc.away):
        return None
    upto = goals[:idx + 1]
    this = goals[idx]
    # 그 골 시점의 점수 — **현재 점수가 아니다**(①).
    hs = sum(1 for g in upto if getattr(g, "side", None) == "home")
    as_ = sum(1 for g in upto if getattr(g, "side", None) == "away")
    aw, hm = C5._nm(league, game.away), C5._nm(league, game.home)
    is_home = getattr(this, "side", None) == "home"
    team = hm if is_home else aw
    import pipeline as _Pg                  # 순환 import를 피해 함수 안에서
    when = _Pg._goal_when(getattr(this, "minute", 0),
                          getattr(this, "added", 0) or 0, league)
    leader = "" if hs == as_ else (hm if hs > as_ else aw)
    head = H.for_goal(scorer=(getattr(this, "name", "") or "").strip() or "득점",
                      team_name=team, when=when,
                      own_goal=bool(getattr(this, "own_goal", False)),
                      away_score=as_, home_score=hs,
                      tied=(hs == as_), leader=leader)
    body = C5.body_goal(
        away_name=aw, home_name=hm, away_score=as_, home_score=hs,
        league=league,
        events=[(g.minute, g.side, g.name, "자책" if g.own_goal else "",
                 getattr(g, "added", 0) or 0) for g in upto])
    lab = _day_label(game.sports_day, [game])
    foot = ((venue_name(game.venue) or "") if game.venue
            else C5.LEAGUE_LABEL.get(league, ""))
    html = C5.shell(kind="goal", league=league, date_label=lab,
                    head=head, body=body, foot_left=foot)
    return html, list(C5.caption(kind="goal", league=league, head=head,
                                 date_label=lab))


def flash_card(game, league: League, *, now: datetime | None = None
               ) -> tuple[str, list[str]] | None:
    """경기 종료 직후 결과 속보. 경기 하나당 한 장.

    **결과 카드와 같은 골격을 쓴다** — 경기가 하나뿐이라 `result_card`가
    이미 그 경기의 이야기(흐름표 + 한 경기용 머리말)를 그린다. 여기서 골격을
    새로 짜면 같은 것이 두 벌이 되고, 한쪽만 고치는 사고가 난다(약점 45·110).

    v1.17 — 축구면 **선발 명단을 아래에 얹는다**(대표님 승인). 정리판에는
    붙이지 않는다: 그 카드는 여러 경기를 담아서 22명씩 들어갈 자리가 없다.
    """
    if not game.is_terminal:
        return None
    extra = _lineup_body(game, league, with_goals=True) if LINEUP_ENABLED else None
    return result_card([game], league, game.sports_day, now=now,
                       extra_body=extra or "")


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

# ── v5가 옛 카드로 떨어진 기록 (v1.17b, 2026-09-08) ──────────────
#
# **이 사고의 본질은 결함이 아니라 그 결함이 조용했다는 것이다.**
# 게이트에 걸린 v5 카드는 오류를 내지 않고 옛 v4 카드로 **조용히** 대체된다.
# 그래서 검증 1,450건이 전부 통과하는 동안에도 채널에는 옛 디자인이 나갔고,
# 그것을 잡은 것은 우리 감시가 아니라 **대표님 눈**이었다
# (2026-09-08: *"디자인 변경이 아직 안된 것 같던데"*).
#
# 폴백은 **있어야 하는 장치다** — 깨진 카드를 내보내는 것보다 낫다. 다만
# 그것이 일어났다는 사실은 반드시 사람에게 닿아야 한다. 여기 쌓아 두면
# 틱이 매번 거둬 운영 알림에 싣는다(`tick._drain_v5_fallbacks`).
_FALLBACKS: list = []


def note_fallback(why: str) -> None:
    """v5 → 옛 카드 대체를 기록한다. 화면 출력은 로그에만 남고 아무도 안 본다."""
    _FALLBACKS.append(why)


def take_fallbacks() -> list:
    """쌓인 기록을 **비우면서** 돌려준다 — 안 비우면 다음 틱에 또 알린다."""
    out = list(_FALLBACKS)
    _FALLBACKS.clear()
    return out


def render_png(card_html: str, out: pathlib.Path,
               shorter_html: str | None = None) -> tuple[int, int, int] | None:
    """v5 카드를 그려 PNG로 저장한다. `(폭, 높이, jpg 바이트)` 또는 None.

    None은 '못 그렸다'가 아니라 **'내보내면 안 된다'**는 뜻이다 —
    검사에 걸린 카드는 옛 카드로 대신한다.

    **높이 때문에 떨어질 때는 한 번 더 시도한다 (2026-09-06).**
    대표님이 고른 여백판은 카드를 세로로 늘린다. 경기가 많은 날일수록 길어지므로,
    그대로 두면 **정보가 가장 많은 날에만 새 디자인이 사라진다** — 정확히
    반대로 동작한다. 그래서 높이 게이트에 걸리면 밀도를 한 단계만 내려
    (`cards_v5.relax`) 다시 그린다. 골격도 정보도 그대로고 간격만 좁아진다.
    그래도 안 되면 그때 옛 카드로 떨어진다.

    **여백을 다 줄여도 안 되면 내용을 한 단 줄인다 (2026-09-07).**
    `shorter_html`은 같은 카드의 **더 짧은 판**이다(부르는 쪽이 만든다).
    나이트 브리핑이 이걸 쓴다: 리그 서넛이 겹친 날 전 경기를 다 실으면
    40줄이 되어 어떤 밀도로도 안 담긴다. 그때 리그별 요약으로 내려간다 —
    **옛 카드로 통째로 떨어지는 것보다 한 단 낮은 v5가 낫다**(약점 134).
    """
    # ── ★ 외국 문자 유출 감시 (2026-09-08 대표님 지시) ────────────────
    #
    # 대표님: *"이미지카드에 한자, 일본어 들어가지 않도록 해"*
    # 실제로 나갔다 — `히로시마 中止 한신`. 원인은 v5 카드 세 곳이
    # `cancel_reason_text()`(번역표)를 건너뛰고 소스 원문을 그대로 쓴 것이었다
    # (약점 132: 새 경로가 옛 경로의 수정을 되살린다).
    #
    # 세 곳은 고쳤다. 이건 **다음에 또 새 경로가 생겼을 때** 잡는 그물이다.
    # **막지는 않는다** — 결과 카드 한 장에 그날 여섯 경기가 들어 있어서,
    # 한 글자 때문에 카드를 버리면 그날 결과가 통째로 사라진다. 대신 알린다.
    # (검증 `verify_cards`에서는 같은 검사를 실패로 처리한다.)
    _cjk = foreign_script_chars(card_html)
    if _cjk:
        note_fallback(f"카드에 한자·일본어가 섞였습니다 — {' '.join(_cjk[:6])}"
                      f"{f' 외 {len(_cjk) - 6}자' if len(_cjk) > 6 else ''}"
                      " (번역표를 안 거친 자리가 있습니다)")

    # `shorter_html`은 하나일 수도, **점점 짧아지는 여러 판**일 수도 있다.
    # 나이트가 둘을 준다(빽빽판 → 건수만). 각 판마다 여백판·조임판을 다 시도한다.
    _shorter = ([] if shorter_html is None
                else [shorter_html] if isinstance(shorter_html, str)
                else list(shorter_html))
    _ladder = [("여백판", card_html), ("조임판", C5.relax(card_html))]
    for i, h in enumerate(_shorter, 1):
        _ladder += [(f"짧은판{i}", h), (f"짧은판{i}(조임)", C5.relax(h))]
    for stage, html in _ladder:
        if html is None:
            continue
        r = _render_once(html, out)
        if r != TOO_TALL:
            return r
        print(f"  ⓘ [v5] {stage}이 높이 상한을 넘어 한 단 내려 다시 그립니다")
    return None


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
        note_fallback(f"카드를 그리지 못해 옛 카드로 나갔습니다: {e.__class__.__name__}")
        return None
    if problems:
        _why = " | ".join(problems[:3])
        print("  ⚠️ [v5] 카드 결함 — 옛 카드로 대신합니다: " + _why)
        note_fallback(f"카드 검사에 걸려 옛 카드로 나갔습니다: {_why}")
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
        note_fallback(f"카드 크기가 계약을 벗어나 옛 카드로 나갔습니다: {e}")
        return None
    jpg = out.with_suffix(".jpg")
    im.save(jpg, "JPEG", quality=SEND_JPEG_QUALITY, optimize=True)
    return w, h, jpg.stat().st_size
