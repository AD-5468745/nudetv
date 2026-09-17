# -*- coding: utf-8 -*-
"""손님 질문에 답한다 (v1.39 신설).

대표님 지시(2026-09-17): 토론방에서 손님이 물으면 봇이 답한다.
**AI는 다음에 붙인다 — 지금은 뼈대만 두고 꺼 놓는다.**

────────────────────────────────────────────────────────────────────
**답하는 순서.**

    질문
     ├─ ① 우리 자료에서 찾는다 ────▶ 찾으면 그대로 답한다
     │     (순위 · 오늘 경기 · 다음 경기 · 최근 결과)
     ├─ ② AI에게 넘긴다 ───────────▶ 아직 꺼져 있다 (`AI_ENABLED = False`)
     └─ ③ 둘 다 못 하면 ──────────▶ **모른다고 답한다**

①이 먼저인 이유는 셋이다.
  · **틀릴 수가 없다.** 숫자를 우리 표에서 꺼내 읽을 뿐이라 지어낼 여지가 없다.
    이 프로젝트의 '사실 잠금' 원칙이 그대로 지켜진다.
  · 공짜다. AI 무료 한도를 안 쓴다.
  · AI가 막혀도 계속 답한다.

**모르면 모른다고 답한다.** 이 파일에서 가장 중요한 줄이다 — 억지로 답을
만들어 내느니 침묵이 낫고, 침묵보다 "모릅니다"가 낫다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

from contract import (KST, League, Status, TEAM_NAMES, fix_team_name,
                      pct_label)

# ── AI 자리 (v1.39 — 뼈대만) ────────────────────────────────────
#
# **켜지 않는다.** 대표님 지시: *"ai 는 다음에 적용시키자, 뼈대만 만들어두고"*.
# 켤 때 할 일은 셋뿐이다:
#   ① `AI_ENABLED = True`
#   ② `ask_ai`를 실제 호출로 채운다 (키는 비밀값에서 읽는다 — 코드에 안 적는다)
#   ③ `verify_answers.py`의 'AI 답에도 숫자 대조가 걸린다' 검사를 켠다
#
# 켤 때 **반드시 지킬 것**(지금 미리 적어 둔다 — 나중에 잊는다):
#   · 우리 자료를 함께 넘기고 **그 밖은 모른다고 답하게** 못 박는다
#   · 답에 나온 숫자가 우리 자료에 없으면 **그 답을 버린다**
#   · 손님 글이 외부로 나가므로 채널 소개에 그 사실을 밝힌다
AI_ENABLED = False


def ask_ai(question: str, facts: str) -> Optional[str]:
    """AI에게 묻는다. **아직 꺼져 있다** — 언제나 None.

    `facts`는 우리가 뽑아 준 자료다. 켤 때도 이 인자는 그대로 쓴다 —
    자료 없이 묻는 길을 아예 만들지 않기 위해서다.
    """
    if not AI_ENABLED:
        return None
    raise NotImplementedError("AI 경로는 아직 켜지 않았습니다")


# ── 묻는 말 알아보기 ────────────────────────────────────────────
#
# **팀 이름 사전은 이미 있다**(`contract.TEAM_NAMES`, 15개 리그 전 구단).
# 여기서 새로 만들지 않는다 — 두 곳에 두면 한쪽이 낡는다.

_RANK = re.compile(r"몇\s*위|순위|랭킹|등수")
_TODAY = re.compile(r"오늘|투데이|지금")
_NEXT = re.compile(r"다음\s*경기|언제\s*(해|하나|하냐|함|경기)|다음에")
_RESULT = re.compile(r"결과|몇\s*대\s*몇|스코어|이겼|졌")
_SCHEDULE = re.compile(r"경기|일정|편성|스케줄")
_HELP = re.compile(r"^\s*(도움말|help|명령|뭐\s*할\s*수)\s*$")

# 리그 이름 → League. 사람이 쓰는 말을 모은다.
_LEAGUE_WORDS = {
    "kbo": League.KBO, "케이비오": League.KBO, "프로야구": League.KBO,
    "야구": League.KBO,
    "mlb": League.MLB, "메이저": League.MLB, "메이저리그": League.MLB,
    "npb": League.NPB, "일본야구": League.NPB,
    "k리그": League.KL1, "케이리그": League.KL1, "kleague": League.KL1,
    "kbl": League.KBL, "농구": League.KBL,
    "epl": League.EPL, "프리미어": League.EPL, "프리미어리그": League.EPL,
    "라리가": League.LALIGA, "laliga": League.LALIGA,
    "세리에": League.SERIEA, "세리에a": League.SERIEA,
    "분데스": League.BUNDESLIGA, "분데스리가": League.BUNDESLIGA,
    "리그1": League.LIGUE1, "리그앙": League.LIGUE1,
    "챔스": League.UCL, "챔피언스": League.UCL, "ucl": League.UCL,
    "유로파": League.UEL, "uel": League.UEL,
    "mls": League.MLS,
}


def _norm(s: str) -> str:
    return re.sub(r"[\s·,\.]", "", str(s or "")).lower()


# 사람이 실제로 쓰는 표기 → 우리 표의 이름.
#
# **지어낸 것이 아니라 소리 나는 대로 쓰는 말이다.** `엘지`·`기아`·`케이티`로
# 묻는 손님이 많은데 우리 표에는 `LG`·`KIA`·`KT`만 있어서 한 글자도 못 찾았다.
# 표를 늘리지 않고 여기서만 옮긴다 — 카드에 찍히는 이름은 그대로 둔다.
_ALIAS = {
    "엘지": "LG", "기아": "KIA", "케이티": "KT", "엔씨": "NC",
    "에스에스지": "SSG", "쓱": "SSG", "롯데자이언츠": "롯데",
    "두산베어스": "두산", "삼성라이온즈": "삼성", "한화이글스": "한화",
    "키움히어로즈": "키움", "엘지트윈스": "LG", "기아타이거즈": "KIA",
    "다저스": "LA다저스", "양키스": "뉴욕양키스", "메츠": "뉴욕메츠",
    "에인절스": "LA에인절스", "토트넘": "토트넘", "손흥민": "토트넘",
}


def find_team(text: str, *, prefer: Optional[set] = None) -> Optional[tuple]:
    """질문에서 팀을 찾는다. `(리그, 팀코드, 표시이름)` 또는 None.

    **가장 긴 이름부터 본다** — `LG`가 `LG 트윈스`보다 먼저 걸리면 안 된다.
    여럿이 걸리면 **답하지 않는다**: 어느 팀을 묻는지 모르는 채로 한쪽을
    고르면 틀린 팀의 순위를 말하게 된다.
    """
    t = _norm(text)
    for a, b in _ALIAS.items():
        if a in t:
            t = t.replace(a, _norm(b))
    hits = []
    for lg, table in TEAM_NAMES.items():
        for code, name in table.items():
            n = _norm(fix_team_name(name))
            if len(n) >= 2 and n in t:
                hits.append((len(n), lg, code, fix_team_name(name)))
    if not hits:
        return None
    hits.sort(key=lambda h: -h[0])
    best_len = hits[0][0]
    top = [h for h in hits if h[0] == best_len]
    if len(top) > 1:
        # **같은 이름이 여러 리그에 있다** — `삼성`·`LG`·`KT`는 야구에도 농구에도
        # 있다. 아무 쪽이나 고르면 **틀린 리그의 순위**를 말하게 된다.
        # 지금 경기가 있는 리그(`prefer`)를 먼저 본다. 그래도 안 갈리면
        # **답하지 않는다** — 어느 팀인지 모르는 채로 답하느니 되묻는 게 낫다.
        if prefer:
            narrowed = [h for h in top if h[1] in prefer]
            if len(narrowed) == 1:
                top = narrowed
        if len(top) > 1:
            return None
    h = top[0]
    return h[1], h[2], h[3]


def find_league(text: str) -> Optional[League]:
    t = _norm(text)
    best = None
    for word, lg in _LEAGUE_WORDS.items():
        if word in t and (best is None or len(word) > len(best[0])):
            best = (word, lg)
    return best[1] if best else None


# ── 답 만들기 ──────────────────────────────────────────────────

def _fmt_game(g, league: League, name_of) -> str:
    k = g.start_utc.astimezone(KST)
    venue = f" ({g.venue})" if getattr(g, "venue", None) else ""
    return (f"{k:%H:%M} {name_of(league, g.away)} vs "
            f"{name_of(league, g.home)}{venue}")


def answer(question: str, *, games_by_league: dict, records: dict,
           now: datetime, name_of) -> Optional[str]:
    """질문 하나에 대한 답. **모르면 None** (부르는 쪽이 '모릅니다'를 보낸다).

    `games_by_league`: {리그: [경기...]}  `records`: {리그값: RecordBook}
    `name_of(league, team)`은 카드가 찍는 그 이름을 돌려주는 함수다(§7-45) —
    채널과 답이 같은 이름을 써야 한다.
    """
    q = (question or "").strip()
    if not q or len(q) > 200:
        return None
    if _HELP.search(q):
        return ("이렇게 물어보시면 됩니다.\n"
                "· <b>LG 몇위?</b> — 팀 순위\n"
                "· <b>오늘 KBO 경기</b> — 그날 편성\n"
                "· <b>삼성 다음 경기</b> — 다음 일정\n"
                "· <b>두산 결과</b> — 최근 경기 결과")

    # 지금 경기가 있는 리그를 알려 주면 같은 이름(삼성·LG·KT)이 갈린다.
    _hint = find_league(q)
    _prefer = {_hint} if _hint else {lg for lg, gs in (games_by_league or {}).items() if gs}
    team = find_team(q, prefer=_prefer)
    league = (team[0] if team else None) or _hint
    today = now.astimezone(KST).strftime("%Y-%m-%d")

    # ① 순위 — "LG 몇위?"
    if team and _RANK.search(q):
        rb = records.get(team[0].value)
        if rb is None:
            return None
        st = rb.team(team[1])
        if st is None:
            return None
        bits = [f"<b>{team[2]}</b>는 {st.rank}위입니다"]
        rec = f"{st.record.win}승 {st.record.loss}패"
        if st.record.draw:
            rec += f" {st.record.draw}무"
        bits.append(f"{rec} · {pct_label(team[0])} {st.pct}")
        if st.last10 and st.last10.total:
            bits.append(f"최근 10경기 {st.last10.win}승 {st.last10.loss}패")
        return " · ".join(bits) + "."

    # ② 오늘 경기 — "오늘 KBO 경기?"
    if league and (_TODAY.search(q) or _SCHEDULE.search(q)) and not _NEXT.search(q):
        gs = [g for g in (games_by_league.get(league) or [])
              if g.sports_day == today]
        if not gs:
            return f"오늘 {_label(league)} 경기는 없습니다."
        gs = sorted(gs, key=lambda g: g.start_utc)[:10]
        lines = "\n".join("· " + _fmt_game(g, league, name_of) for g in gs)
        return f"오늘 <b>{_label(league)}</b> {len(gs)}경기입니다.\n{lines}"

    # ③ 다음 경기 — "삼성 다음 경기 언제?"
    if team and _NEXT.search(q):
        gs = [g for g in (games_by_league.get(team[0]) or [])
              if g.status is Status.SCHEDULED and g.start_utc > now
              and team[1] in (g.away.team_code, g.home.team_code)]
        if not gs:
            return f"{team[2]}의 다음 경기 일정을 아직 모릅니다."
        g = min(gs, key=lambda x: x.start_utc)
        k = g.start_utc.astimezone(KST)
        # **시각을 한 문장에 두 번 쓰지 않는다** — `_fmt_game`이 이미 시각을 넣는다.
        venue = f" ({g.venue})" if getattr(g, "venue", None) else ""
        return (f"<b>{team[2]}</b> 다음 경기는 {k:%m월 %d일 %H:%M}, "
                f"{name_of(team[0], g.away)} vs {name_of(team[0], g.home)}"
                f"{venue}입니다.")

    # ④ 최근 결과 — "두산 결과?"
    if team and _RESULT.search(q):
        gs = [g for g in (games_by_league.get(team[0]) or [])
              if g.status is Status.FINAL and g.score
              and team[1] in (g.away.team_code, g.home.team_code)]
        if not gs:
            return f"{team[2]}의 최근 결과를 아직 모릅니다."
        g = max(gs, key=lambda x: x.start_utc)
        k = g.start_utc.astimezone(KST)
        return (f"{k:%m월 %d일} {name_of(team[0], g.away)} "
                f"{g.score.away} : {g.score.home} {name_of(team[0], g.home)}")

    return None


def _label(league: League) -> str:
    import cards_v5 as C5
    return C5.LEAGUE_LABEL.get(league, league.value)


UNKNOWN = ("그건 제가 아직 못 찾습니다. "
           "팀 이름과 함께 <b>순위 · 오늘 경기 · 다음 경기 · 결과</b> 중 하나를 "
           "물어봐 주세요.")


def reply_for(question: str, **kw) -> str:
    """언제나 문자열을 돌려준다 — 침묵하지 않는다.

    ①로 못 답하면 AI(꺼져 있음)를 거쳐 **모른다는 답**으로 끝난다.
    """
    a = answer(question, **kw)
    if a:
        return a
    if AI_ENABLED:
        got = ask_ai(question, facts="")
        if got:
            return got
    return UNKNOWN
