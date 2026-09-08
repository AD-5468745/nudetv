"""카드 헤드라인 — **지어내지 않고 규칙으로만 뽑는다** (v1.12 신설).

**왜 만들었나 (대표님 지적 2026-09-04).**
"경기시작시간알림, 경기결과알림, 경기분석·평가 등등 각 컨텐츠들의 정확도가 완전히 떨어짐."

그 정체는 디자인이 아니라 **카드가 하는 말이 사실과 어긋나는 것**이었다:
  · 시작 알림이 "곧 시작"이라 했는데 실제로는 2시간 30분 뒤였다.
  · 경기 분석의 총평이 "비교 7개 항목 중 롯데가 4개에서 앞섭니다" — 분석이 아니라 산수다.
  · 결과 카드는 점수만 나열해 그날 무슨 일이 있었는지 말하지 않았다.

그렇다고 "오늘의 명승부" 같은 문장을 만들면 **FACT_LOCK을 어긴다** — 수집한 값에
없는 사실이 생긴다. 이 프로젝트는 그런 문장으로 이미 여러 번 다쳤다
(약점 60 '최고·최장을 max로 안 뽑아 거짓', 107 '문장이 주장하는 사실을 아무도 검사 안 함').

**그래서 헤드라인은 사전에 고정한 정량 이벤트 중 우선순위 최상위 하나만 쓴다.**
조건에 맞는 이벤트가 없으면 **헤드라인을 만들지 않고** 검증 가능한 상태 라벨로 내려간다.
모든 헤드라인은 자기가 쓴 값(`facts`)과 규칙 번호(`rule`)를 들고 다닌다 —
게이트가 "이 문장이 정말 이 값에서 나왔나"를 뒤에서 확인할 수 있어야 하기 때문이다.

**임계값은 내가 정하지 않았다.** 실측 분위수에서 왔다(아래 `BLOWOUT_MARGIN` 주석).
표본이 없는 리그는 임계를 **지어내지 않고 그 규칙을 끈다.**
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from contract import (League, REGULAR_PERIODS, ScoreUnit, SCORE_UNIT_BY_LEAGUE,
                      Status, StreakKind, TEAM_NAMES, cancel_reason_text, josa)


@dataclass(frozen=True)
class Headline:
    """카드 머리에 올라가는 한 마디.

    · `rule`  — 어느 규칙이 만들었나. 카드에는 안 찍히고 검증·로그에만 쓴다.
    · `text`  — 큰 글씨.
    · `sub`   — 작은 글씨(없을 수 있다).
    · `facts` — 이 문장이 쓴 값들. **게이트가 이걸로 문장을 되짚는다.**
    """
    rule: str
    text: str
    sub: str = ""
    facts: dict = field(default_factory=dict)


# ── 임계값 — 전부 실측에서 왔다 ───────────────────────────────
#
# **'대승'을 몇 점부터라 부를 것인가.** 내 감이 아니라 그 리그의 실제 분포에서
# 상위 10%가 되는 지점을 쓴다(2026-09-04 측정, `state/games` 종료 경기).
#   KBO n=100 중앙3 → 90%는 9점 · NPB n=162 중앙3 → 6점
#   MLB n=33 중앙4 → 7점 · K리그1 n=35 중앙1 → 3점
#   LCK n=48 — 맵 스코어라 최대가 3이다. 2-0/3-0 완봉만 '완승'이라 부른다.
# **KBL·V리그는 표본 0건(비시즌)이라 임계를 정할 수 없다 — 규칙을 끈다.**
# 개막 후 같은 방법으로 측정해서 넣는다. 지어낸 숫자를 넣으면 첫날부터 거짓말이 된다.
BLOWOUT_MARGIN: dict[League, Optional[int]] = {
    League.KBO: 9,
    League.NPB: 6,
    League.MLB: 7,
    League.KL1: 3,
    League.LCK: 2,          # 맵 스코어: 2-0 또는 3-0
    League.INTL_LOL: 2,
    League.KBL: None,       # 표본 없음 — 규칙 꺼짐
    League.VLEAGUE_M: None,
    League.VLEAGUE_W: None,
}

# 연속 몇 승부터 '길다'인가. KBO 실측(2026-09-04)에서 10팀 중 5 이상은 2팀뿐이었다.
STREAK_NOTABLE = 5
# 1·2위가 이만큼 붙으면 그것이 그날 순위표의 사건이다.
TIGHT_RACE_GB = 1.0
# 1위가 이만큼 벌어지면 그것 자체가 그날의 순위 이야기다("N경기 차 선두").
# 4.0은 지어낸 값이 아니라 **TIGHT(1.0)와 겹치지 않는 가장 낮은 자리**로 잡았다 —
# 그 사이(1.0~4.0)는 어느 쪽 서사도 아니어서 ④연속·⑤최근10으로 내려보낸다.
RUNAWAY_GB = 4.0
# 최근 10경기가 이만큼 치우치면 사건이다.
LAST10_HOT = 9
LAST10_COLD = 1
# 상대전적을 '편중'이라 부를 최소 표본과 승률.
H2H_MIN_GAMES = 5
H2H_LOPSIDED = 0.70
# 순위가 이만큼 벌어지면 그것 자체가 볼거리다.
RANK_GAP_NOTABLE = 5


def _code(team) -> str:
    """`Game.home`은 팀 코드 문자열이 아니라 `TeamRef`다 — 둘을 섞으면 표에서 못 찾는다.

    실제로 처음 짤 때 이걸 놓쳐 연속 기록 규칙이 조용히 한 번도 안 걸렸다
    (`{TeamRef} & {str}` 교집합이 늘 비어 있었다). 한 곳에서 벗겨 낸다.
    """
    return getattr(team, "team_code", team)


def _nm(league: League, team) -> str:
    code = _code(team)
    return TEAM_NAMES.get(league, {}).get(code, code)


def _unit_word(league: League) -> str:
    """'득점'인가 '맵 스코어'인가. 리그마다 다른 것을 뭉개면 카드가 거짓말을 한다."""
    return {ScoreUnit.MAPS: "맵 스코어", ScoreUnit.SETS: "세트 스코어"}.get(
        SCORE_UNIT_BY_LEAGUE.get(league), "득점")


# ══════════════════════════════════════════════════════════════
# 경기 결과
# ══════════════════════════════════════════════════════════════

def for_result(games: list, league: League, *, standings: list | None = None
               ) -> Optional[Headline]:
    """우선순위: 연속 기록 → 큰 점수차 → 취소. 없으면 None(상태 라벨로 강등)."""
    fin = [g for g in games if g.status is Status.FINAL and g.score]
    off = [g for g in games if g.status in (Status.CANCELED, Status.POSTPONED)]

    # ① 연속 기록 — **오늘 경기한 팀 중**, 리그 최장일 때만.
    #    오늘 안 뛴 팀의 연승을 오늘 결과 카드 머리에 올리면 어긋난다.
    if standings and fin:
        played = {_code(g.home) for g in fin} | {_code(g.away) for g in fin}
        pool = [s for s in standings
                if s.team_code in played and s.streak_len >= STREAK_NOTABLE
                and s.streak_kind in (StreakKind.WIN, StreakKind.LOSS)]
        if pool:
            best = max(s.streak_len for s in pool)
            longest = [s for s in pool if s.streak_len == best]
            league_max = max((s.streak_len for s in standings), default=0)
            # **동률이면 '최장'이라 단정하지 않는다** (약점 60).
            if len(longest) == 1 and best >= league_max:
                s = longest[0]
                word = "연승" if s.streak_kind is StreakKind.WIN else "연패"
                return Headline(
                    rule="R-STREAK",
                    text=f"{_nm(league, s.team_code)} {s.streak_len}{word}",
                    sub="리그 최장",
                    facts={"team": s.team_code, "streak": s.streak_len,
                           "kind": s.streak_kind.value, "league_max": league_max})

    # ② 큰 점수차 — 임계가 있는 리그만. 동률이면 '최다'라 못 한다.
    margin = BLOWOUT_MARGIN.get(league)
    if margin is not None and fin:
        diffs = [(abs(g.score.home - g.score.away), g) for g in fin]
        top = max(d for d, _ in diffs)
        tied = [g for d, g in diffs if d == top]
        if top >= margin and len(tied) == 1:
            g = tied[0]
            win, lose = ((_code(g.home), _code(g.away)) if g.score.home > g.score.away
                         else (_code(g.away), _code(g.home)))
            hi, lo = max(g.score.home, g.score.away), min(g.score.home, g.score.away)
            return Headline(
                rule="R-BLOWOUT",
                text=f"{_nm(league, win)} {hi}-{lo} {_nm(league, lose)}",
                sub=f"오늘 최다 {_unit_word(league)}차",
                facts={"margin": top, "threshold": margin,
                       "winner": win, "loser": lose,
                       # **문장에 찍는 값은 전부 여기 남긴다.** 점수를 빼놓았다가
                       # 게이트에 걸렸다 — 카드에 보이는 수의 출처를 못 대면
                       # 그건 '지어낸 문장'과 구별되지 않는다.
                       "winner_score": hi, "loser_score": lo})

    # ③ 취소·연기 — 사실이고, 구독자가 가장 먼저 궁금해하는 것이다.
    if off:
        # ★ **사유는 계약의 번역 함수를 거친다** (2026-09-07 실렌더로 잡음).
        # 전에는 소스 원문을 그대로 썼다 — NPB 카드에 **"1경기 中止"**가 나갔다.
        # 한국어 채널에 일본어 원문이 제목으로 올라간 셈이다(약점 32의 재발).
        # `cancel_reason_text()`가 이미 그것을 막고 있었는데 **여기만 안 썼다**
        # (약점 45·110: 같은 병을 앓는 곳을 전부 훑지 않았다).
        reasons = {cancel_reason_text(g.meta.cancel_reason, g.status)
                   for g in off if g.meta}
        reasons.discard("")
        # 사유가 하나로 모이면 그것을 쓴다. 여럿이면 뭉뚱그리지 않는다.
        why = reasons.pop() if len(reasons) == 1 else "취소·연기"
        # **전 경기가 취소면 그렇다고 말한다.**
        # "2경기 우천취소"만 보면 다른 경기가 남았는지 알 수 없다. 구독자에게
        # 중요한 것은 사유가 아니라 **오늘 볼 경기가 없다**는 사실이다.
        # (v1.11h에서 전 경기 취소를 알리기로 한 이유가 그것이었다.)
        allout = not fin
        return Headline(
            rule="R-CANCEL",
            text=(f"전 경기 {why}" if allout else f"{len(off)}경기 {why}"),
            sub=(f"{len(off)}경기 모두" if allout
                 else (f"{len(fin)}경기 종료" if fin else "")),
            facts={"off": len(off), "final": len(fin), "reason": why})
    return None


def for_single_result(game, league: League) -> Optional[Headline]:
    """한 경기짜리 결과 카드의 머리말 (v1.12).

    **묶음 카드와 규칙이 다르다.** 다섯 경기를 한 장에 담을 때는 "5경기 종료"가
    맞지만, 한 경기만 담은 카드에서 그 말은 아무것도 알려주지 않는다.
    한 경기에는 그 경기의 이야기가 있다 — 흐름표가 그것을 보여주므로,
    머리말은 **표에서 눈에 띄는 한 가지**를 말한다.

    문장에 찍는 모든 수는 `facts`에 담는다. 출처를 못 대는 수는 지어낸 것이다.
    """
    if not game.score or game.status is not Status.FINAL:
        return None
    a, h = game.score.away, game.score.home
    an = TEAM_NAMES.get(league, {}).get(_code(game.away), _code(game.away))
    hn = TEAM_NAMES.get(league, {}).get(_code(game.home), _code(game.home))
    text = f"{an} {a} : {h} {hn}"
    facts: dict = {"away": a, "home": h}
    rows = list(getattr(game.meta, "line_score", ()) or ())

    # ① 한 구간에 몰아친 점수 — 표에서 가장 먼저 눈에 띄는 칸이다
    if rows:
        best_i = best_v = None
        for i, (hv, av) in enumerate(rows):
            for v in (hv, av):
                if v is not None and (best_v is None or v > best_v):
                    best_i, best_v = i + 1, v
        if best_v is not None and best_v >= BIG_PERIOD_RUNS:
            facts["period"] = best_i
            facts["runs"] = best_v
            unit = SCORE_UNIT_BY_LEAGUE.get(league)
            word = "세트" if unit is ScoreUnit.SETS else (
                "쿼터" if unit is ScoreUnit.POINTS else "회")
            return Headline(rule="G-BIGPERIOD", text=text,
                            sub=f"{best_i}{word}에만 {best_v}점", facts=facts)

    # ② 한 점 차 — 표 없이도 말할 수 있는 사실
    if abs(a - h) == 1:
        return Headline(rule="G-CLOSE", text=text, sub="한 점 차", facts=facts)

    # ③ 대승 — 리그별 실측 상위 10% 임계값을 그대로 쓴다
    margin = BLOWOUT_MARGIN.get(league)
    if margin and abs(a - h) >= margin:
        facts["margin"] = abs(a - h)
        return Headline(rule="G-BLOWOUT1", text=text,
                        sub=f"{abs(a - h)}점 차", facts=facts)

    # ④ 아무 규칙도 안 걸리면 **점수만** 말한다. 없는 이야기를 짓지 않는다.
    return Headline(rule="G-SCORE", text=text, sub="", facts=facts)


# 한 구간에 이만큼 나면 그 자체가 이야기다. 카드에서 강조되는 칸과 같은 값을 쓴다 —
# 두 곳이 다르면 "강조된 칸"과 "머리말이 말하는 칸"이 어긋난다.
BIG_PERIOD_RUNS = 4


# ══════════════════════════════════════════════════════════════
# 모닝 브리핑
# ══════════════════════════════════════════════════════════════

def for_morning(games: list, league: League, *, kst_times: list[str],
                day_word: str = "오늘") -> Optional[Headline]:
    """`kst_times`는 렌더가 이미 만든 한국시각 문자열(예 '18:30'). 여기서 다시 계산하지 않는다 —
    두 곳에서 계산하면 반드시 어긋난다(약점 104).

    `day_word`도 같은 이유로 **받아 쓴다**. MLB 슬레이트는 한국시각 새벽에 열리고
    유럽 주말은 한국 날짜 둘에 걸치므로 '오늘'이 항상 참이 아니다 —
    그 판정은 경기 목록과 보내는 시각을 함께 보는 `pipeline.day_word_span`이 한다.
    """
    playable = [g for g in games
                if g.status not in (Status.CANCELED, Status.POSTPONED)]
    if not playable or not kst_times:
        return None
    uniq = sorted(set(kst_times))
    if len(uniq) == 1 and len(playable) >= 2:
        return Headline(
            rule="M-SAME-TIME",
            text=f"{day_word} {len(playable)}경기 전부 {uniq[0]} 시작",
            facts={"count": len(playable), "time": uniq[0], "day_word": day_word})
    return Headline(
        rule="M-FIRST",
        text=f"{day_word} {len(playable)}경기",
        sub=f"{uniq[0]} 첫 경기 — {uniq[-1]}까지 이어집니다"
            if len(uniq) > 1 else f"{uniq[0]} 시작",
        facts={"count": len(playable), "first": uniq[0], "last": uniq[-1],
               "day_word": day_word})


# ══════════════════════════════════════════════════════════════
# 시작 알림 — 여기가 대표님이 콕 집은 자리다
# ══════════════════════════════════════════════════════════════

def for_start_alert(minutes_left: int, first_label: str) -> Headline:
    """**'곧'이라고 쓰지 않는다.** 남은 시간을 숫자로 박는다.

    전에는 "오늘 KBO 5경기 곧 시작"이라 써놓고 실제로는 2시간 30분 뒤였다.
    앞창이 2.5시간이라 '곧'을 지킬 방법이 없다 — 그러면 **약속의 정밀도를
    지킬 수 있는 수준까지 낮춘다**(약점 108). 남은 시간은 렌더하는 순간 계산하므로
    언제 나가든 참이다.
    """
    m = max(0, int(minutes_left))
    h, mm = divmod(m, 60)
    if m >= 60:
        left = f"{h}시간 {mm}분" if mm else f"{h}시간"
    else:
        left = f"{m}분"
    # 문장은 128분을 '2시간 8분'으로 쪼개 보여준다. **쪼갠 값도 facts에 남긴다** —
    # 같은 값의 다른 표현이라도, 카드에 보이는 수는 전부 출처를 댈 수 있어야 한다.
    return Headline(rule="A-COUNTDOWN", text=f"{left} 뒤 첫 경기",
                    sub=first_label,
                    facts={"minutes": m, "hours": h, "rest_minutes": mm})


# ── 오늘의 경기 (v1.15 — 대표님: "베스트 경기를 뽑아 간단히 코멘트") ──
#
# ⚠️ **오늘 아침에 '대표 경기'를 없앴다가 다시 만드는 것이 아니다.**
# 없앤 것은 *"가장 점수차가 큰 경기"* 하나를 **아무 설명 없이** 올리던 것이다.
# 대표님 지적이 정확했다 — *"대표경기라는 기준이 사람들마다 다를텐데."*
# 기준이 안 보이니 자의적이었다.
#
# 여기서 달라지는 것은 **왜 골랐는지를 카드가 말한다**는 점이다.
# "9회 역전"·"연장 11회"·"1점 차"는 **데이터에서 읽히는 사실**이지 우리 감상이
# 아니다. 근거를 함께 적으면 고른 이유가 검증 가능해진다 — 그러면 그것은
# 자의적 선택이 아니라 규칙이다(이 파일 전체가 그 원리로 서 있다).
#
# **지어낸 형용사는 하나도 쓰지 않는다.** "명승부"·"짜릿한"은 없다.
# 규칙에 걸리는 것이 없으면 **아무것도 고르지 않는다.**
BEST_MAX = 2                      # 대표님: "한두경기정도"
BIG_TOTAL_RUNS = 15               # 야구·농구가 아닌 '합계' 종목의 대량 득점 임계
BIG_TOTAL_GOALS = 6               # 축구


def _cum_lead_flip(seq_home, seq_away) -> "int | None":
    """구간별 득점으로 **역전이 일어난 구간**을 찾는다. 없으면 None.

    한 번이라도 뒤졌던 팀이 최종 승리했으면 역전이다.
    돌려주는 값은 **마지막으로 앞뒤가 뒤바뀐 구간 번호**(1부터).
    """
    h = a = 0
    lead = 0                      # +1 홈 우세 · -1 원정 우세 · 0 동점
    flip_at = None
    for i, (hv, av) in enumerate(zip(seq_home, seq_away), start=1):
        h += hv or 0
        a += av or 0
        cur = 1 if h > a else (-1 if a > h else 0)
        if cur and lead and cur != lead:
            flip_at = i
        if cur:
            lead = cur
    return flip_at


def _period_word(league) -> str:
    """구간을 뭐라 부르나. 종목이 다르면 이름도 다르다."""
    unit = SCORE_UNIT_BY_LEAGUE.get(league)
    if unit is ScoreUnit.RUNS:
        return "회"
    if unit is ScoreUnit.POINTS:
        return "쿼터"
    if unit is ScoreUnit.SETS:
        return "세트"
    return ""


def best_games(games: list, league) -> list:
    """오늘 눈여겨볼 경기 최대 `BEST_MAX`개. `[(경기, 코멘트)]`.

    규칙은 순서대로 본다 — 위에 있을수록 강한 근거다:
      ① **역전**   경기 중 뒤졌던 팀이 이겼다 (구간 점수·득점 시각으로 확인)
      ② **연장**   정규 구간을 넘겼다
      ③ **1점 차** 끝까지 한 점 싸움이었다
      ④ **대량 득점** 두 팀 합계가 임계를 넘었다
    한 경기가 여러 규칙에 걸리면 코멘트를 **모아서** 적는다.
    걸리는 것이 없으면 **빈 목록** — 억지로 채우지 않는다.
    """
    done = [g for g in games if g.status is Status.FINAL and g.score]
    if not done:
        return []
    pw = _period_word(league)
    unit = SCORE_UNIT_BY_LEAGUE.get(league)
    scored: list = []
    for g in done:
        bits: list[str] = []
        rank = 0
        meta = getattr(g, "meta", None)
        rows = list(getattr(meta, "line_score", ()) or ()) if meta else []
        goals = list(getattr(meta, "goals", ()) or ()) if meta else []

        # ① 역전
        flip = None
        if rows:
            flip = _cum_lead_flip([r[0] for r in rows], [r[1] for r in rows])
            if flip and pw:
                bits.append(f"{flip}{pw} 역전")
                rank = max(rank, 4)
        elif goals:
            seq_h = [1 if x.side == "home" else 0 for x in sorted(goals, key=lambda x: x.minute)]
            seq_a = [1 if x.side == "away" else 0 for x in sorted(goals, key=lambda x: x.minute)]
            flip = _cum_lead_flip(seq_h, seq_a)
            if flip:
                mins = sorted(x.minute for x in goals)
                bits.append(f"{mins[flip - 1]}분 역전")
                rank = max(rank, 4)

        # ② 연장 — 구간 수가 정규를 넘었다
        reg = REGULAR_PERIODS.get(unit)
        if rows and reg and len(rows) > reg and pw:
            bits.append(f"연장 {len(rows)}{pw}")
            rank = max(rank, 3)

        # ③ 1점 차 (세트·맵 종목은 '한 세트 차'가 흔해 세지 않는다)
        diff = abs(g.score.home - g.score.away)
        if diff == 1 and unit in (ScoreUnit.RUNS, ScoreUnit.POINTS, ScoreUnit.GOALS):
            bits.append("1점 차" if unit is not ScoreUnit.GOALS else "한 골 차")
            rank = max(rank, 2)

        # ④ 대량 득점
        total = g.score.home + g.score.away
        cap = BIG_TOTAL_GOALS if unit is ScoreUnit.GOALS else BIG_TOTAL_RUNS
        if unit in (ScoreUnit.RUNS, ScoreUnit.POINTS, ScoreUnit.GOALS) and total >= cap:
            bits.append(f"두 팀 합계 {total}{'골' if unit is ScoreUnit.GOALS else '점'}")
            rank = max(rank, 1)

        if bits:
            # 같은 순위끼리는 **점수차가 작은 쪽**을 앞에 둔다 — 접전이 먼저다.
            scored.append((rank, -diff, total, g, " · ".join(bits)))
    scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
    return [(g, c) for _r, _d, _t, g, c in scored[:BEST_MAX]]


def korean_player_sub(lines) -> str:
    """한국 선수 출전 한 줄. 없으면 빈 문자열 (v1.15).

    **왜 필요한가.** MLS는 리그 전체가 아니라 한국 선수가 뛰는 경기만 실린다
    (대표님 지시). 그런데 카드가 그 말을 안 하면 시청자는 왜 이 한 경기만
    올라왔는지 모른다 — **고른 이유가 카드에 없으면 고른 것이 아니다.**

    **소스가 준 것만 말한다.** 출전 시간·평점은 소스에 없으므로 쓰지 않는다.
    라인업을 못 본 경기는 `lines`가 비어 있고, 그러면 아무 말도 안 한다 —
    대표님 결정: *"그냥 보낸다 (출전 언급 없이)"*.
    """
    out: list[str] = []
    for pl in lines or ():
        sc = getattr(pl, "soccer", None)
        if not sc:
            # 축구 칸이 없는 줄(야구 코리안리거)이다. **축구 말로 옮기지 않는다** —
            # 옮기면 이정후가 '교체 명단'이 된다. 모르는 것은 말하지 않는다.
            continue
        bits = ["선발" if sc.get("start") else "교체 명단"]
        g = int(sc.get("goals") or 0)
        if g:
            bits.append(f"{g}골")
        og = int(sc.get("own_goals") or 0)
        if og:
            bits.append(f"자책골 {og}" if og > 1 else "자책골")
        if sc.get("card"):
            bits.append(str(sc["card"]))
        out.append(f"{pl.name_ko} {' · '.join(bits)}")
    return " / ".join(out)


def for_kickoff(minutes_left: int) -> Headline:
    """경기별 킥오프 알림 (v1.14). **'곧'이라고 쓰지 않는다** — 108의 규칙.

    창이 [T-10분, T-1분]이라 남은 시간은 항상 1~10분이다. 그 숫자를 그대로 쓴다.
    대진은 본문(`body_matchup`)이 크게 말하므로 **여기서 되풀이하지 않는다.**

    이 문장은 **보내는 순간에 다시 계산된다**(`REJUDGE_AT_SEND`) — 페이서가
    몇 분 미루면 "10분 뒤"가 그 즉시 거짓이 되기 때문이다.
    """
    m = max(1, int(minutes_left))
    return Headline(rule="K-COUNTDOWN", text=f"{m}분 뒤 시작",
                    facts={"minutes": m})


def for_lineup(away_name: str, home_name: str, minutes_left: int) -> Headline:
    """선발 라인업 카드 머리말 (v1.17).

    **남은 시간을 분으로 못 박지 않는다.** 킥오프 카드와 달리 이 카드의 창은
    넓다(라인업 발표는 킥오프 1시간 전 언저리이고 우리는 본 즉시 보낸다).
    "37분 뒤"처럼 적으면 페이서가 미루는 동안 그대로 거짓이 되고, 이 카드는
    킥오프 카드처럼 창이 좁지도 않아 어긋날 여지가 더 크다.

    그래서 **시간은 구간으로만 말한다.** 한 시간이 넘게 남았으면 아예 말하지
    않는다 — 정확히 알 수 없는 것을 숫자로 적는 것보다 안 적는 편이 정직하다.
    """
    m = max(0, int(minutes_left))
    if m >= 60:
        tail = ""
    elif m >= 30:
        tail = " · 1시간 이내 시작"
    else:
        tail = " · 곧 시작"
    return Headline(rule="L-STARTING", text=f"선발 라인업 발표{tail}",
                    facts={"minutes": m, "away": away_name, "home": home_name})


# ══════════════════════════════════════════════════════════════
# 팀 순위표
# ══════════════════════════════════════════════════════════════

def for_standings(standings: list, league: League, *, group: str | None = None
                  ) -> Optional[Headline]:
    """`group`이 있으면 그 단위(MLB 지구·NPB 리그) 안에서만 본다.
    단위를 섞으면 1위가 여럿이라 '선두'라는 말이 거짓이 된다."""
    rows = [s for s in standings if group is None or s.group == group]
    if len(rows) < 2:
        return None
    rows = sorted(rows, key=lambda s: s.rank)

    # ① 선두 다툼
    try:
        gap = float(rows[1].games_behind) - float(rows[0].games_behind)
    except (TypeError, ValueError):
        gap = None
    if gap is not None and 0 < gap <= TIGHT_RACE_GB:
        g = f"{gap:g}"
        return Headline(
            rule="S-GAP", text=f"1·2위 {g}경기 차",
            sub=f"{_nm(league, rows[0].team_code)} · {_nm(league, rows[1].team_code)}",
            facts={"gap": gap, "first": rows[0].team_code,
                   "second": rows[1].team_code,
                   "ranks": [rows[0].rank, rows[1].rank]})

    # ② 상위권 순위 다툼 — 1·2위가 벌어져 있어도 **어딘가는 붙어 있다**
    #
    # 2026-09-06 대표님 결정: *"순위 경쟁을 우선"*.
    # 그전에는 ①이 1.0경기 차를 못 넘으면 곧장 연속·최근10으로 떨어졌고,
    # 부진 기록이 호조 기록보다 통계적으로 더 극단적이라 **꼴찌 팀의 부진이
    # 순위표 카드의 얼굴이 되는 일이 반복**됐다("롯데 최근 10경기 1승").
    # 순위표는 순위를 말하는 카드다.
    #
    # 상위 절반만 본다. "9·10위 0.5경기 차"는 사실이지만 순위 서사가 아니다.
    # **가을야구 경계라고 주장하지 않는다** — 리그마다 다르고 우리는 모른다.
    half = max(2, len(rows) // 2)
    best_pair = None
    for i in range(len(rows[:half]) - 1):
        try:
            d = float(rows[i + 1].games_behind) - float(rows[i].games_behind)
        except (TypeError, ValueError):
            continue
        if 0 <= d <= TIGHT_RACE_GB and (best_pair is None or d < best_pair[0]):
            best_pair = (d, rows[i], rows[i + 1])
    if best_pair is not None:
        d, a, b = best_pair
        # ★ **'공동'은 순위가 같을 때만 쓴다 (2026-09-07 실렌더로 잡음).**
        # 전에는 승차 차이 `d == 0`이면 '공동'이라 적었다. 그런데 승차가 같아도
        # 순위는 다를 수 있다 — 실측 NPB 9/8: 세이부 .574 2위 · 닛폰햄 .573 3위,
        # 둘 다 승차 6.0. 카드가 "2·3위 공동"이라 적었는데 **'공동'은 같은 순위를
        # 뜻하는 낱말**이라 그 자리에서 거짓이 된다.
        # 승차가 같고 순위가 다르면 옆 문형과 똑같이 '0경기 차'로 적는다 —
        # 새 낱말을 만들지 않는다.
        if a.rank == b.rank:
            txt = f"공동 {a.rank}위"
        else:
            txt = f"{a.rank}·{b.rank}위 {d:g}경기 차"
        return Headline(
            rule="S-RACE", text=txt,
            sub=f"{_nm(league, a.team_code)} · {_nm(league, b.team_code)}",
            facts={"gap": d, "ranks": [a.rank, b.rank],
                   "teams": [a.team_code, b.team_code]})

    # ③ 선두 독주 — 위가 전부 벌어졌다면 그것 자체가 그날의 순위 이야기다.
    if gap is not None and gap >= RUNAWAY_GB:
        return Headline(
            rule="S-LEAD",
            text=f"{_nm(league, rows[0].team_code)} {gap:g}경기 차 선두",
            sub=f"2위 {_nm(league, rows[1].team_code)}",
            facts={"gap": gap, "first": rows[0].team_code,
                   "second": rows[1].team_code})

    # ④ 연속 — 최장이 하나일 때만
    hot = [s for s in rows if s.streak_len >= STREAK_NOTABLE
           and s.streak_kind in (StreakKind.WIN, StreakKind.LOSS)]
    if hot:
        best = max(s.streak_len for s in hot)
        top = [s for s in hot if s.streak_len == best]
        if len(top) == 1:
            s = top[0]
            word = "연승" if s.streak_kind is StreakKind.WIN else "연패"
            return Headline(
                rule="S-STREAK",
                text=f"{_nm(league, s.team_code)} {s.streak_len}{word}",
                sub="리그 최장", facts={"team": s.team_code, "streak": s.streak_len})

    # ⑤ 최근 10경기 — 이 값이 있는 리그만(NPB·MLB는 소스에 없다)
    ext = [s for s in rows if s.last10 and s.last10.total >= 10]
    for s in ext:
        if s.last10.win >= LAST10_HOT or s.last10.win <= LAST10_COLD:
            n = s.last10.win
            return Headline(
                rule="S-LAST10",
                text=f"{_nm(league, s.team_code)} 최근 10경기 {n}승",
                sub=f"현재 {s.rank}위",
                facts={"team": s.team_code, "last10_win": n, "rank": s.rank,
                       "window": s.last10.total})
    return None


# ══════════════════════════════════════════════════════════════
# 부문 순위
# ══════════════════════════════════════════════════════════════

def for_leaders(leaders: dict, league: League, categories: list[str]
                ) -> Optional[Headline]:
    """카드에 실리는 부문(`categories`)만 본다. 카드에 없는 부문으로 헤드라인을
    만들면 독자가 근거를 못 찾는다."""
    firsts = []
    for cat in categories:
        top = [e for e in (leaders.get(cat) or []) if e.rank == 1]
        if len(top) == 1:                    # 공동 1위는 '1위'라 단정하지 않는다
            firsts.append((cat, top[0]))
    if len(firsts) < 2:
        return None

    # ① 한 선수가 여러 부문 1위
    by_player: dict = {}
    for cat, e in firsts:
        by_player.setdefault(e.player_id, []).append((cat, e))
    for pid, items in by_player.items():
        if len(items) >= 2:
            e = items[0][1]
            cats = " · ".join(c for c, _ in items)
            return Headline(
                rule="L-SWEEP",
                text=f"{e.name} {len(items)}개 부문 1위",
                sub=f"{cats} — {_nm(league, e.team_code)}",
                facts={"player_id": pid, "count": len(items),
                       "categories": [c for c, _ in items]})

    # ② 1위가 전부 다른 팀
    teams = [e.team_code for _, e in firsts]
    if len(set(teams)) == len(teams):
        return Headline(
            rule="L-SPREAD",
            text=f"{len(firsts)}개 부문 1위 전원 다른 팀",
            sub=" · ".join(_nm(league, t) for t in teams),
            facts={"count": len(firsts), "teams": teams, "rank": 1})
    return None


# ══════════════════════════════════════════════════════════════
# 경기 분석
# ══════════════════════════════════════════════════════════════

def for_analysis(game, league: League, *, standings: list | None = None,
                 h2h=None) -> Optional[Headline]:
    """**승패를 예측하지 않는다.** 선발투수가 없어 예측의 근거가 없다.
    검증 가능한 사실 하나를 올린다."""
    home, away = _code(game.home), _code(game.away)

    # ① 시즌 상대전적이 한쪽으로 기울었다 — 가장 눈에 띄는 사실
    if h2h:
        wld = h2h.get((home, away))
        if wld:
            tot = wld.win + wld.loss
            if tot >= H2H_MIN_GAMES:
                hi, lo = max(wld.win, wld.loss), min(wld.win, wld.loss)
                lead = home if wld.win > wld.loss else away
                if hi / tot >= H2H_LOPSIDED:
                    return Headline(
                        rule="AN-H2H",
                        text=f"시즌 상대전적 {_nm(league, lead)} {hi}-{lo}",
                        sub=f"{tot}경기 맞대결",
                        facts={"lead": lead, "win": hi, "loss": lo, "games": tot})

    rank = {s.team_code: s for s in (standings or [])}
    sh, sa = rank.get(home), rank.get(away)

    # ② 순위 차
    if sh and sa:
        gap = abs(sh.rank - sa.rank)
        if gap >= RANK_GAP_NOTABLE:
            hi = sh if sh.rank < sa.rank else sa
            lo = sa if sh.rank < sa.rank else sh
            return Headline(
                rule="AN-RANKGAP",
                text=f"{hi.rank}위 {_nm(league, hi.team_code)} vs "
                     f"{lo.rank}위 {_nm(league, lo.team_code)}",
                sub=f"{gap}계단 차",
                facts={"gap": gap, "higher": hi.team_code, "lower": lo.team_code})

    # ③ 최근 10경기 (있는 리그만)
    for s, other in ((sh, sa), (sa, sh)):
        if s and s.last10 and s.last10.total >= 10:
            if s.last10.win >= LAST10_HOT or s.last10.win <= LAST10_COLD:
                return Headline(
                    rule="AN-LAST10",
                    text=f"{_nm(league, s.team_code)} 최근 10경기 {s.last10.win}승",
                    sub=f"현재 {s.rank}위",
                    facts={"team": s.team_code, "last10_win": s.last10.win,
                           "window": s.last10.total, "rank": s.rank})
    return None


# ══════════════════════════════════════════════════════════════
# 강등 — 헤드라인을 못 만들 때
# ══════════════════════════════════════════════════════════════

def fallback(kind: str, **kw) -> Headline:
    """**조건에 맞는 이벤트가 없으면 만들지 않는다.** 검증 가능한 상태만 적는다.

    이것이 이 파일의 절반이다. "오늘의 명승부" 같은 문장을 짜내는 대신
    "4경기 종료"라고 쓰는 것 — 재미없어 보여도 **거짓이 아니다.**
    """
    if kind == "result":
        n, off = kw.get("final", 0), kw.get("off", 0)
        return Headline(rule="R-COUNT", text=f"{n}경기 종료",
                        sub=f"{off}경기 취소·연기" if off else "",
                        facts={"final": n, "off": off})
    if kind == "morning":
        n, w = kw.get("count", 0), kw.get("day_word") or "오늘"
        return Headline(rule="M-COUNT", text=f"{w} {n}경기",
                        facts={"count": n, "day_word": w})
    if kind == "standings":
        return Headline(rule="S-RANK", text=kw.get("label") or "현재 순위",
                        facts={"group": kw.get("group")})
    if kind == "leaders":
        n = kw.get("count", 0)
        return Headline(rule="L-TOP", text=f"{kw.get('set_name', '부문')} {n}개 부문",
                        facts={"count": n})
    if kind == "analysis":
        return Headline(rule="AN-MATCH", text=kw.get("label") or "오늘의 맞대결",
                        facts={})
    if kind == "night":
        lg, n = kw.get("leagues", 0), kw.get("final", 0)
        # **리그가 하나면 "1개 리그"라고 세지 않는다** (2026-09-07 대표님:
        # *"1개리그 라는 단어가 어색해"*). 하나뿐인 것을 "1개"라고 세는 것은
        # 정보가 아니라 소음이다.
        #
        # 리그 이름은 **머리말(`group_label`)이 맡는다.** 제목에 넣어 봤더니
        # 제목·본문 배지·꼬리말에 같은 이름이 세 번 나왔다 —
        # 대표님이 두 형태를 보고 머리말 쪽을 골랐다. 결과·순위표 카드와
        # 같은 구조라 채널 전체가 한 결로 읽힌다.
        # 그래서 제목은 숫자만 말하고, `facts`에는 리그를 남긴다(게이트가 되짚는다).
        label = (kw.get("league_label") or "").strip()
        if lg == 1 and label:
            return Headline(rule="N-ONE", text=f"{n}경기 종료",
                            sub=kw.get("sub", ""),
                            facts={"leagues": 1, "final": n, "league": label})
        return Headline(rule="N-COUNT", text=f"{lg}개 리그 {n}경기 종료",
                        sub=kw.get("sub", ""), facts={"leagues": lg, "final": n})
    raise ValueError(f"모르는 종류: {kind}")


# ══════════════════════════════════════════════════════════════
# 관전 포인트 — "어느 쪽이 좋아 보이나" (v1.12 신설)
# ══════════════════════════════════════════════════════════════
#
# 대표님 요청: *"어느 팀이 더 좋아보인다 프로토 고객들을 위한 분석 예상글도
# 간단히 재미있게 적어주는게 어때?"*
#
# **하는 것과 안 하는 것을 분명히 가른다.**
#
#   한다  — 우리가 가진 숫자를 읽어 준다. "여섯 중 다섯을 삼성이 가져간다",
#           "가장 벌어진 곳은 평균자책 4.13 대 4.78". 전부 데이터에 있는 수다.
#   안 한다 — 승률·확률·추천. "삼성 승리 확률 62%"를 쓰려면 모델이 있어야 하는데
#           우리에겐 없다. 없는 것을 쓰면 그 순간 카드가 지어낸 말을 하는 것이고,
#           틀렸을 때 브랜드에 남는 상처가 재미보다 크다.
#
# **한쪽 근거만 대면 예측이 되고, 양쪽을 다 대면 정보가 된다.**
# 그래서 뒤진 쪽이 앞선 항목이 하나라도 있으면 **반드시 함께 적는다** —
# 이것이 이 규칙의 핵심이고, 아래 게이트가 그것을 강제한다.

# **예상은 하되 단정은 안 한다.**
# 대표님 정정: "승부예측이라기 보다는 어디가 이길것같다 예상".
# 그래서 방향은 분명히 가리킨다 — "삼성 우세". 다만 아래 낱말은 계속 막는다.
#   확률·배당 — 모델이 없으면 지어낸 수다
#   추천·픽·베팅 — 돈을 걸라는 말이 되고, 그건 우리가 할 말이 아니다
#   필승·무조건·장담·확실 — 결과를 보장하는 말. 예상은 보장이 아니다
BANNED_WORDS = ("확률", "배당", "추천", "배팅", "베팅", "픽",
                "적중", "필승", "무조건", "장담", "확실")

# 우세의 정도를 말로 옮긴다. **몇 대 몇인지에 따라 세기가 달라진다** —
# 4대 2와 6대 0을 똑같이 "우세"라 하면 그 말이 아무 뜻도 없어진다.
def _edge_word(lead: int, trail: int) -> str:
    """**비율이 아니라 격차로 잰다.** 4대 2를 비율로 재면 0.67이라 '근소'로
    떨어지는데, 사람 눈에 4대 2는 근소가 아니다. 몇 개 더 가져갔는지가 곧 세기다."""
    d = lead - trail
    if d >= 4:
        return "크게 우세"
    if d >= 2:
        return "우세"
    return "근소 우세"


@dataclass(frozen=True)
class Metric:
    """비교 항목 하나. `higher_better`가 방향을 정한다 — 평균자책은 낮아야 좋다."""
    label: str
    away_text: str          # 카드에 찍히는 표기 ("4.13")
    home_text: str
    away_val: float         # 비교용 수
    home_val: float
    higher_better: bool = True
    # **이 값이 언제 기준인가 (v1.12b, 약점 123).**
    #
    # 네이버 `preview`는 경기 **전날** 만들어져 굳고(`generateDate`),
    # `statistics`는 **지금**을 준다. 실측 2026-09-05:
    #     preview     삼성 70승 46패 · 2위   (9/4 기준)
    #     statistics  삼성 71승 46패 · 1위   (지금)
    # 둘 다 맞다 — 시점이 다를 뿐이다. 그런데 한 카드가 둘을 섞으면
    # **카드가 스스로 모순된다.** 구독자는 어느 쪽이 맞는지 알 길이 없고,
    # 그 순간 카드의 모든 숫자가 못 믿을 것이 된다.
    #
    # 그래서 시점을 값에 붙여 들고 다닌다. 빈 문자열은 '안 밝힘'이고,
    # 섞이면 `for_preview`가 카드를 **만들지 않는다**(아래 게이트).
    as_of: str = ""
    # **'가장 벌어진 곳'으로 뽑힐 수 있는가.** 순위는 안 된다 — 순위는 다른
    # 항목들의 *결과*라서, "가장 벌어진 곳은 순위"는 동어반복이 된다
    # ("1위와 3위인 이유는 1위와 3위이기 때문"). 실측에서 실제로 그렇게 나왔다.
    can_headline: bool = True

    def winner(self) -> str:
        if self.away_val == self.home_val:
            return ""
        a_better = (self.away_val > self.home_val) if self.higher_better else (
            self.away_val < self.home_val)
        return "away" if a_better else "home"

    def gap(self) -> float:
        base = max(abs(self.away_val), abs(self.home_val)) or 1.0
        return abs(self.away_val - self.home_val) / base


@dataclass(frozen=True)
class Verdict:
    rule: str
    lines: tuple
    facts: dict = field(default_factory=dict)
    pick: str = ""          # 카드 배지에 찍히는 한 줄 — "삼성 우세" · "팽팽"


PREVIEW_MAX_LINES = 6
"""분석글 최대 줄 수. 넘치면 카드가 세로로 무너진다 — **길이도 사실의 일부다.**"""


def for_preview(*, away_name: str, home_name: str, metrics: list,
                h2h_text: str = "", h2h_full: bool = True,
                form: tuple | None = None,
                streak: tuple | None = None, ranks: tuple | None = None,
                h2h_recent: str = "") -> Optional[Verdict]:
    """관전 포인트를 만든다. 항목이 셋보다 적으면 만들지 않는다.

    **숫자가 갈리면 갈렸다고 쓴다.** 억지로 한쪽을 고르지 않는다 —
    그것이 곧 예측이 되기 때문이다.
    """
    # ── 시점 게이트 (약점 123) ──────────────────────────────
    # **한 카드는 한 시점만 말한다.** 항목마다 기준일이 다르면 카드를 만들지
    # 않는다 — 틀린 카드를 내느니 안 내는 쪽이 언제나 낫다.
    # 이 게이트는 분석 카드를 배선하기 **전에** 세운다. 배선하면서 세우면
    # 그때 잊고, 잊으면 조용히 틀린 카드가 나간다.
    stamps = {m.as_of for m in metrics if getattr(m, "as_of", "")}
    if len(stamps) > 1:
        return None
    graded = [m for m in metrics if m.winner()]
    if len(metrics) < 3 or not graded:
        return None
    a_win = [m for m in graded if m.winner() == "away"]
    h_win = [m for m in graded if m.winner() == "home"]
    lead_name, lead, trail_name, trail = (
        (away_name, a_win, home_name, h_win) if len(a_win) >= len(h_win)
        else (home_name, h_win, away_name, a_win))

    # **분모는 '비교한 항목'이 아니라 '승부가 갈린 항목'이다.**
    # 동점을 분모에 넣으면 "6개 중 3개"가 "나머지 3개는 상대 것"으로 읽힌다 —
    # 실제로는 2개와 동점 1개인데. 검증이 이것을 잡았다.
    n = len(graded)
    facts: dict = {"total": n, "lead": len(lead), "trail": len(trail),
                   "compared": len(metrics)}
    # 시점을 밝혔으면 카드 꼬리말에 그대로 찍는다 — "언제 기준인가"를
    # 구독자가 알 수 있어야 숫자를 믿을 수 있다.
    if stamps:
        facts["as_of"] = next(iter(stamps))
    lines: list = []

    if len(lead) == len(trail):
        # **억지로 한쪽을 고르지 않는다.** 반반이면 반반이라고 말하는 것이
        # 예상이고, 그때 한쪽을 찍는 것은 예상이 아니라 찍기다.
        lines.append(f"기록이 반씩 갈린다 — {n}개 항목 중 "
                     f"{len(lead)}대 {len(trail)}.")
        return _verdict(rule="V-SPLIT", lines=lines, facts=facts, lead=lead,
                        trail=trail, trail_name=trail_name, h2h_text=h2h_text,
                        pick="팽팽", away_name=away_name, home_name=home_name,
                        form=form, streak=streak, ranks=ranks,
                        h2h_recent=h2h_recent, h2h_full=h2h_full)
    word = _edge_word(len(lead), len(trail))
    facts["edge"] = word
    lines.append(f"기록은 {lead_name} 쪽이다. "
                 f"{n}개 항목 중 {len(lead)}개를 가져간다.")
    return _verdict(rule="V-EDGE", lines=lines, facts=facts, lead=lead,
                    trail=trail, trail_name=trail_name, h2h_text=h2h_text,
                    pick=f"{lead_name} {word}", away_name=away_name,
                    home_name=home_name, form=form, streak=streak,
                    ranks=ranks, h2h_recent=h2h_recent, h2h_full=h2h_full)


def _verdict(*, rule, lines, facts, lead, trail, trail_name, h2h_text, pick,
             away_name="", home_name="", form=None, streak=None, ranks=None,
             h2h_recent="", h2h_full=True):
    """첫 줄 뒤는 규칙이 갈리지 않는다 — 격차·반대 근거·맞대결은 늘 같은 순서다."""

    pool = [m for m in lead if m.can_headline] or lead
    top = max(pool, key=lambda m: m.gap())
    facts["top_label"] = top.label
    facts["top_away"] = top.away_text
    facts["top_home"] = top.home_text
    lines.append(f"가장 벌어진 곳은 {top.label} — "
                 f"{top.away_text} 대 {top.home_text}.")

    # **반대 근거.** 뒤진 쪽이 앞선 항목이 있으면 반드시 적는다 —
    # 한쪽 말만 하면 그것은 정보가 아니라 예측이다.
    if trail:
        tpool = [m for m in trail if m.can_headline] or trail
        t = max(tpool, key=lambda m: m.gap())
        facts["counter_label"] = t.label
        facts["counter_away"] = t.away_text
        facts["counter_home"] = t.home_text
        lines.append(f"다만 {t.label}{josa(t.label, '은', '는')} "
                     f"{trail_name}{josa(trail_name, '이', '가')} 낫다 — "
                     f"{t.away_text} 대 {t.home_text}.")
    # ── 근거를 두껍게 (v1.16 — 대표님: *"분석글을 상세하게"*) ────────
    #
    # **확률을 지어내지 않는다.** 우리에겐 모델이 없다. 대신 **근거의 각도**를
    # 늘린다 — 팀 지표(위)에 더해 최근 흐름·연속 기록·맞대결·순위 격차를
    # 각각 한 줄로 적는다. 전부 수집한 값에서 나오고, 문장에 찍힌 수는
    # 빠짐없이 `facts`에 남는다(게이트가 되짚는다).
    #
    # **없으면 그 줄을 안 쓴다.** 리그마다 소스가 주는 것이 달라서, 있는 척하면
    # 그 리그에서만 카드가 거짓말을 한다.
    if form and all(form):
        (aw, al, ad), (hw, hl, hd) = form
        _a = f"{aw}승 {al}패" + (f" {ad}무" if ad else "")
        _h = f"{hw}승 {hl}패" + (f" {hd}무" if hd else "")
        facts["form_away"], facts["form_home"] = _a, _h
        facts["form_n"] = max(aw + al + ad, hw + hl + hd)
        lines.append(f"최근 {facts['form_n']}경기는 "
                     f"{away_name} {_a}, {home_name} {_h}.")
    if streak:
        for _nm_, _kind, _len in streak:
            if not _kind or _len < STREAK_NOTABLE:
                continue
            _w = "연승" if _kind is StreakKind.WIN else "연패"
            facts.setdefault("streaks", []).append([_nm_, _kind.value, _len])
            lines.append(f"{_nm_}{josa(_nm_, '은', '는')} {_len}{_w} 중이다.")
    if h2h_text:
        facts["h2h"] = h2h_text
        # ★ **'올해'는 시즌 전체를 셌을 때만 쓴다.** 우리가 스냅샷에서 센 것은
        # 가진 범위일 뿐이다(실측: MLB 스냅샷은 일주일치). 범위를 모르면
        # 범위를 말하지 않는 편이 정확하다 — 약점 108.
        facts["h2h_full"] = bool(h2h_full)
        _tail = f" 최근 흐름은 {h2h_recent}." if h2h_recent else ""
        if h2h_recent:
            facts["h2h_recent"] = h2h_recent
        _when = "올해" if h2h_full else "최근"
        lines.append(f"{_when} 맞대결은 {h2h_text}.{_tail}")
    if ranks and all(ranks) and abs(ranks[0] - ranks[1]) >= RANK_GAP_NOTABLE:
        facts["rank_away"], facts["rank_home"] = ranks
        facts["rank_gap"] = abs(ranks[0] - ranks[1])
        lines.append(f"순위는 {ranks[0]}위와 {ranks[1]}위 — "
                     f"{facts['rank_gap']}계단 차다.")
    # **길이도 사실의 일부다.** 넘치면 카드가 무너지므로 앞에서 자른다 —
    # 줄 순서가 곧 중요도 순서라 뒤가 잘리는 것이 옳다.
    if len(lines) > PREVIEW_MAX_LINES:
        facts["lines_dropped"] = len(lines) - PREVIEW_MAX_LINES
        lines = lines[:PREVIEW_MAX_LINES]
    return Verdict(rule=rule, lines=tuple(lines), facts=dict(facts), pick=pick)


# 이 파일이 만들 수 있는 규칙 전부. **게이트가 이 목록으로 미등록 규칙을 막는다** —
# 새 규칙을 넣으면 여기 한 줄 더해야 하고, 그때 '이게 정말 사실인가'를 한 번 더 생각하게 된다.
ALL_RULES = frozenset({
    "R-STREAK", "R-BLOWOUT", "R-CANCEL", "R-COUNT",
    "M-SAME-TIME", "M-FIRST", "M-COUNT",
    "A-COUNTDOWN", "K-COUNTDOWN", "L-STARTING", "N-ONE",
    "S-GAP", "S-RACE", "S-LEAD", "S-STREAK", "S-LAST10", "S-RANK",
    "L-SWEEP", "L-SPREAD", "L-TOP",
    "AN-H2H", "AN-RANKGAP", "AN-LAST10", "AN-MATCH",
    "N-COUNT",
    "V-EDGE", "V-SPLIT",
    "G-BIGPERIOD", "G-CLOSE", "G-BLOWOUT1", "G-SCORE",
})
