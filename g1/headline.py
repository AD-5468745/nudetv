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

from contract import (League, ScoreUnit, SCORE_UNIT_BY_LEAGUE, Status,
                      StreakKind, TEAM_NAMES)


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
        reasons = {(g.meta.cancel_reason or "").strip() for g in off if g.meta}
        reasons.discard("")
        # 사유가 하나로 모이면 그것을 쓴다. 여럿이면 뭉뚱그리지 않는다.
        why = reasons.pop() if len(reasons) == 1 else "취소·연기"
        return Headline(
            rule="R-CANCEL",
            text=f"{len(off)}경기 {why}",
            sub=f"{len(fin)}경기 종료" if fin else "",
            facts={"off": len(off), "final": len(fin), "reason": why})
    return None


# ══════════════════════════════════════════════════════════════
# 모닝 브리핑
# ══════════════════════════════════════════════════════════════

def for_morning(games: list, league: League, *, kst_times: list[str]
                ) -> Optional[Headline]:
    """`kst_times`는 렌더가 이미 만든 한국시각 문자열(예 '18:30'). 여기서 다시 계산하지 않는다 —
    두 곳에서 계산하면 반드시 어긋난다(약점 104)."""
    playable = [g for g in games
                if g.status not in (Status.CANCELED, Status.POSTPONED)]
    if not playable or not kst_times:
        return None
    uniq = sorted(set(kst_times))
    if len(uniq) == 1 and len(playable) >= 2:
        return Headline(
            rule="M-SAME-TIME",
            text=f"오늘 {len(playable)}경기 전부 {uniq[0]} 시작",
            facts={"count": len(playable), "time": uniq[0]})
    return Headline(
        rule="M-FIRST",
        text=f"오늘 {len(playable)}경기",
        sub=f"{uniq[0]} 첫 경기 — {uniq[-1]}까지 이어집니다"
            if len(uniq) > 1 else f"{uniq[0]} 시작",
        facts={"count": len(playable), "first": uniq[0], "last": uniq[-1]})


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

    # ② 연속 — 최장이 하나일 때만
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

    # ③ 최근 10경기 — 이 값이 있는 리그만(NPB·MLB는 소스에 없다)
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
        n = kw.get("count", 0)
        return Headline(rule="M-COUNT", text=f"오늘 {n}경기",
                        facts={"count": n})
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
        return Headline(rule="N-COUNT", text=f"{lg}개 리그 {n}경기 종료",
                        sub=kw.get("sub", ""), facts={"leagues": lg, "final": n})
    raise ValueError(f"모르는 종류: {kind}")


# 이 파일이 만들 수 있는 규칙 전부. **게이트가 이 목록으로 미등록 규칙을 막는다** —
# 새 규칙을 넣으면 여기 한 줄 더해야 하고, 그때 '이게 정말 사실인가'를 한 번 더 생각하게 된다.
ALL_RULES = frozenset({
    "R-STREAK", "R-BLOWOUT", "R-CANCEL", "R-COUNT",
    "M-SAME-TIME", "M-FIRST", "M-COUNT",
    "A-COUNTDOWN",
    "S-GAP", "S-STREAK", "S-LAST10", "S-RANK",
    "L-SWEEP", "L-SPREAD", "L-TOP",
    "AN-H2H", "AN-RANKGAP", "AN-LAST10", "AN-MATCH",
    "N-COUNT",
})
