"""카드 v5 — 처음부터 다시 짠 렌더 (대표님 지시 2026-09-04).

**왜 다시 짰나.** 대표님이 채널을 보고 넷을 지적했다:
  ① 정보가 다 안 들어감  ② 디자인이 촌스러움  ③ 리그마다 따로 논다
  ④ **어떤 컨텐츠인지 구분이 안 됨**  ⑤ **각 컨텐츠의 정확도가 완전히 떨어짐**

④와 ⑤는 같은 뿌리에서 나왔다 — **옛 카드는 전부 '표'였다.** 표는 값을 나열할 뿐
무슨 일이 있었는지 말하지 않는다. 그래서 7종이 다 같아 보이고(④), 숫자만 있고
뜻이 없어 얕게 느껴진다(⑤). 디자인만 바꾸면 ⑤는 안 고쳐진다.

**v5의 규칙 넷:**
 1. **골격은 콘텐츠 종류가 정하고, 종목은 행의 표기만 정한다.**
    결과 카드는 KBO든 MLB든 같은 모양이고, KBO 결과와 KBO 순위표는 완전히 다르다.
    (테마는 리그로 갈리지만 골격은 안 갈린다 — 갈리면 ④가 되살아난다.)
 2. **그날 그 리그 전부가 한 장에.** "나머지는 아래 글에"가 사라진다.
    리그별로 쪼개므로 국내 리그는 5경기 이하다. MLB만 16경기인데, 카드가
    길어지는 것을 허용한다 — 그게 ①에 대한 정직한 답이다.
 3. **헤드라인은 `headline.py`가 규칙으로 만든다.** 이 파일은 그리기만 한다.
 4. **없는 데이터는 칸도 만들지 않는다.** 날씨·선발투수 자리를 비워 두면
    독자에게는 '망가진 표'로 읽힌다(약점 94).

**폰트.** Pretendard → Noto Sans KR → sans-serif 순으로 떨어진다. 서버에 Pretendard가
없으면 Noto로 그려지고, 그것도 없으면 두부가 되는데 **그건 게이트가 막는다**
(`assert_korean_font` — 브라우저 안에서 한글 '가'의 폭을 재서 두부와 비교한다).
"""
from __future__ import annotations

import html as _html
from datetime import datetime
from typing import Optional

from contract import (KST, League, SCORE_UNIT_BY_LEAGUE, ScoreUnit, Status,
                      StreakKind, TEAM_NAMES, card_theme, venue_name)

from headline import Headline

# 카드 폭. 텔레그램은 세로로 긴 사진도 잘 보여준다(비율 20:1까지) —
# 높이는 내용에 맞춰 늘어나게 두고, 폭만 고정한다.
CARD_W = 1080

LEAGUE_LABEL = {
    League.KBO: "KBO", League.KBL: "KBL", League.VLEAGUE_M: "V리그 남자부",
    League.VLEAGUE_W: "V리그 여자부", League.KL1: "K리그1", League.LCK: "LCK",
    League.INTL_LOL: "LoL 국제대회", League.MLB: "MLB", League.NPB: "NPB",
    League.EPL: "프리미어리그", League.LALIGA: "라리가", League.SERIEA: "세리에A",
    League.BUNDESLIGA: "분데스리가", League.LIGUE1: "리그1", League.UCL: "챔피언스리그",
}

# 콘텐츠 종류마다 **고유한 아이콘 + 라벨**. 색이 아니라 이 둘이 종류를 가른다 —
# 색은 테마(리그)가 이미 쓰고 있어서 종류까지 색으로 나누면 둘이 충돌한다.
KIND_META = {
    "morning":  ("모닝 브리핑", "M4 17h16M6.5 17a5.5 5.5 0 0 1 11 0M12 4.5v2"
                              "M5 8l1.4 1.4M19 8l-1.4 1.4M2.5 13h2M19.5 13h2"),
    "start":    ("시작 알림", "M12 5v8l5 3"),
    "result":   ("경기 결과", "M4 12.5l5 5L20 6.5"),
    "standings": ("팀 순위", "M3 20h5v-6H3zM9.5 20h5V4h-5zM16 20h5v-9h-5z"),
    "leaders":  ("부문 순위", "M8.5 13.5L7 22l5-2.6L17 22l-1.5-8.5"),
    "analysis": ("경기 분석", "M12 4v16M5 8h14M7.5 8l-3 6h6zM16.5 8l-3 6h6z"),
    "night":    ("나이트 브리핑", "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"),
}

# ── 테마 ──────────────────────────────────────────────────────
#
# **두 테마가 같은 이름의 색을 갖는다.** 그래야 골격 코드가 테마를 모르고도
# 그릴 수 있다 — 테마마다 따로 짜면 한쪽만 고치는 사고가 난다(약점 45·110).
THEMES = {
    "dark": {
        "bg": "#0C1016", "ink": "#EEF2F6", "dim": "#7C8798", "faint": "#4E5866",
        "line": "#161C25", "rule": "#1B222D", "accent": "#35E0A1",
        "up": "#35E0A1", "down": "#FF6B6B", "wm": "#39424F",
        "chip_bg": "#35E0A1", "chip_ink": "#0C1016", "radius": "6px",
        "rail": True,
    },
    "paper": {
        "bg": "#FCFBF8", "ink": "#101418", "dim": "#6E6A62", "faint": "#9A968D",
        "line": "#EAE7E0", "rule": "#101418", "accent": "#0B7A4B",
        "up": "#0B7A4B", "down": "#B3261E", "wm": "#C2BDB2",
        "chip_bg": "#101418", "chip_ink": "#FCFBF8", "radius": "2px",
        "rail": False,
    },
}


def esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _nm(league: Optional[League], team) -> str:
    code = getattr(team, "team_code", team)
    return TEAM_NAMES.get(league, {}).get(code, code) if league else code


def _kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%H:%M")


def _unit_note(league: League) -> str:
    return {ScoreUnit.MAPS: "맵 스코어", ScoreUnit.SETS: "세트 스코어",
            ScoreUnit.GOALS: "득점", ScoreUnit.POINTS: "득점"}.get(
        SCORE_UNIT_BY_LEAGUE.get(league), "득점")


# ══════════════════════════════════════════════════════════════
# 공통 골격 — 모든 카드가 이 함수를 지난다
# ══════════════════════════════════════════════════════════════

def shell(*, kind: str, league: Optional[League], date_label: str,
          head: Headline, body: str, foot_left: str,
          theme: Optional[str] = None, group_label: str = "") -> str:
    """머리(라벨·헤드라인) — 본문 — 꼬리. **일곱 종류가 전부 이 골격을 쓴다.**

    바뀌는 것은 `kind`(아이콘·라벨)와 `body`(본문 골격)뿐이다.
    """
    if kind not in KIND_META:
        raise ValueError(f"모르는 카드 종류: {kind}")
    th = THEMES[theme or card_theme(league)]
    label, icon = KIND_META[kind]
    lg = group_label or (LEAGUE_LABEL.get(league, "전 리그") if league else "전 리그")
    sub = (f'<div class="sub">{esc(head.sub)}</div>' if head.sub else "")
    rail = '<div class="rail"></div>' if th["rail"] else ""
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{CARD_W}px;background:{th['bg']};
  font-family:Pretendard,'Noto Sans KR','Apple SD Gothic Neo',sans-serif;
  color:{th['ink']};-webkit-font-smoothing:antialiased}}
.card{{width:{CARD_W}px;border-radius:{th['radius']};overflow:hidden;position:relative}}
.num{{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}}
.top{{padding:52px 56px 0;position:relative}}
.rail{{position:absolute;left:0;top:52px;bottom:0;width:6px;background:{th['accent']}}}
.lab{{display:flex;align-items:center;gap:16px;font-size:22px;font-weight:800;
  letter-spacing:.16em;color:{th['accent']}}}
.lab svg{{width:30px;height:30px;flex:none}}
.lab .lg{{color:{th['faint']};letter-spacing:.10em}}
.lab .dt{{margin-left:auto;color:{th['faint']};letter-spacing:.06em;font-weight:700}}
.lead{{font-size:62px;font-weight:800;letter-spacing:-.035em;line-height:1.14;
  margin-top:26px;color:{th['ink']}}}
.sub{{font-size:27px;color:{th['dim']};margin-top:16px;font-weight:500;
  letter-spacing:-.01em;line-height:1.4}}
.rule{{height:{'2px' if not th['rail'] else '1px'};background:{th['rule']};margin-top:36px}}
.body{{padding:8px 56px 0}}
.foot{{padding:30px 56px 44px;display:flex;justify-content:space-between;
  align-items:center;font-size:21px;color:{th['faint']};font-weight:600;
  letter-spacing:.03em}}
.wm{{color:{th['wm']};font-weight:800;letter-spacing:.14em;font-size:20px}}
/* ── 본문 부품 (일곱 종류가 나눠 쓴다) ── */
.li{{display:grid;align-items:baseline;gap:14px;padding:19px 0;
  border-bottom:1px solid {th['line']}}}
.li:last-child{{border-bottom:none}}
.t1{{font-size:31px;font-weight:800;letter-spacing:-.03em;color:{th['ink']}}}
.t2{{font-size:31px;font-weight:600;color:{th['ink']}}}
.t2.dim{{color:{th['dim']};font-weight:500}}
.t3{{font-size:23px;color:{th['faint']};font-weight:600}}
.sc{{font-size:44px;font-weight:800;letter-spacing:-.03em;text-align:center;
  color:{th['ink']}}}
.sc i{{font-style:normal;color:{th['faint']};padding:0 12px;font-weight:500}}
.off{{font-size:21px;font-weight:800;letter-spacing:.10em;color:{th['dim']};
  text-align:center;display:block}}
.cd{{display:flex;align-items:baseline;gap:16px;margin-top:24px}}
.cd b{{font-size:96px;font-weight:800;letter-spacing:-.05em;line-height:.95;
  color:{th['accent']}}}
.cd span{{font-size:33px;font-weight:700;color:{th['dim']}}}
.up{{color:{th['up']};font-weight:800}}
.dn{{color:{th['down']};font-weight:800}}
.rk{{font-size:24px;font-weight:800;color:{th['faint']}}}
.rk.lead-rank{{color:{th['accent']}}}
.quad{{display:grid;grid-template-columns:1fr 1fr;gap:6px 52px}}
.qb{{padding-bottom:20px}}
.qb h4{{font-size:21px;font-weight:800;letter-spacing:.14em;color:{th['accent']};
  padding-bottom:14px;border-bottom:1px solid {th['rule']};margin-bottom:6px}}
.qr{{display:flex;align-items:baseline;gap:14px;padding:12px 0;font-size:27px}}
.qr i{{font-style:normal;font-size:20px;font-weight:800;color:{th['faint']};width:20px}}
.qr b{{font-weight:600;flex:1;color:{th['ink']}}}
.qr small{{color:{th['faint']};font-size:20px;font-weight:600}}
.qr span{{font-weight:800;color:{th['ink']}}}
.qr.first b{{font-weight:800}}
.qr.first span{{color:{th['accent']}}}
.duo{{display:grid;grid-template-columns:1fr auto 1fr;padding:14px 0 32px;
  border-bottom:1px solid {th['rule']};margin-bottom:12px;align-items:end}}
.duo .n{{font-size:48px;font-weight:800;letter-spacing:-.03em;color:{th['ink']}}}
.duo .n.r{{text-align:right}}
.duo .p{{font-size:21px;color:{th['faint']};font-weight:700;letter-spacing:.08em;
  margin-top:10px}}
.duo .p.r{{text-align:right}}
.duo .x{{font-size:21px;font-weight:800;color:{th['faint']};letter-spacing:.14em;
  padding:0 28px 12px}}
.cmp{{display:grid;grid-template-columns:1fr 320px 1fr;align-items:center;
  padding:18px 0;border-bottom:1px solid {th['line']}}}
.cmp:last-of-type{{border-bottom:none}}
/* **값은 자기 팀 이름 아래에 선다.** 전에는 좌우 값이 둘 다 가운데로 몰려서
   팀명(바깥 끝)과 값(가운데) 사이가 끊겼다 — 어느 값이 누구 것인지
   눈으로 이어붙여야 했다. 게이트 다섯이 전부 통과한 채로 그랬다. */
.cmp .v{{font-size:30px;font-weight:600;color:{th['dim']};text-align:right}}
.cmp .v.r{{text-align:left}}
.cmp .v.on{{color:{th['accent']};font-weight:800}}
.cmp .k{{text-align:center;font-size:20px;font-weight:700;letter-spacing:.09em;
  color:{th['faint']}}}
.bar{{margin-top:28px;border:1px solid {th['rule']};border-radius:4px;
  padding:26px 32px;display:flex;justify-content:space-between;align-items:center}}
.bar .k{{font-size:20px;font-weight:800;letter-spacing:.14em;color:{th['faint']}}}
.bar .v{{font-size:31px;font-weight:800;color:{th['ink']}}}
.ix{{display:flex;align-items:center;gap:22px;padding:22px 0;
  border-bottom:1px solid {th['line']}}}
.ix:last-child{{border-bottom:none}}
.ix .bg{{font-size:20px;font-weight:800;letter-spacing:.10em;color:{th['chip_ink']};
  background:{th['chip_bg']};padding:9px 16px;border-radius:{th['radius']};
  min-width:130px;text-align:center}}
.ix .cn{{font-size:27px;font-weight:700;white-space:nowrap;color:{th['ink']}}}
.ix .cn em{{font-style:normal;color:{th['faint']};font-weight:600;font-size:24px}}
.ix .pk{{margin-left:auto;font-size:24px;color:{th['dim']};font-weight:600;
  text-align:right}}
.ix .pk b{{color:{th['ink']};font-weight:800}}
/* ── 흐름표 — 야구 이닝 · 농구 쿼터 · 배구 세트가 같은 골격을 쓴다 ── */
.fw{{margin-top:6px}}
.fg{{display:grid;align-items:center}}
.fh{{padding:0 0 14px;border-bottom:1px solid {th['rule']}}}
.fh>span{{font-size:20px;font-weight:800;letter-spacing:.06em;color:{th['faint']};
  text-align:center}}
.fh>span.nm{{text-align:left;letter-spacing:.14em}}
.fr{{padding:22px 0;border-bottom:1px solid {th['line']}}}
.fr:last-child{{border-bottom:none}}
.fr>span{{font-size:34px;font-weight:600;text-align:center;color:{th['ink']}}}
.fr>span.nm{{font-size:32px;font-weight:600;text-align:left;color:{th['dim']};
  letter-spacing:-.02em;white-space:nowrap;overflow:hidden}}
.fr.win>span.nm{{font-weight:800;color:{th['ink']}}}
.fr>span.tot{{font-weight:800;color:{th['ink']}}}
.fr>span.big{{font-size:38px;font-weight:800;color:{th['accent']}}}
.fr>span.zero{{color:{th['faint']};font-weight:500}}
/* ── 타임라인 — 축구는 구간이 없다. 시각이 곧 흐름이다 ── */
.tw{{margin-top:6px}}
.th2{{display:grid;grid-template-columns:1fr 120px 1fr;padding:0 0 16px;
  border-bottom:1px solid {th['rule']}}}
.th2>span{{font-size:24px;font-weight:800;letter-spacing:-.02em;color:{th['dim']};
  white-space:nowrap;overflow:hidden}}
.th2>span.win{{color:{th['ink']}}}
.th2>span.r{{text-align:right}}
.th2>span.c{{text-align:center;font-size:20px;color:{th['faint']};letter-spacing:.10em}}
.tl{{display:grid;grid-template-columns:1fr 120px 1fr;align-items:baseline;
  padding:19px 0;border-bottom:1px solid {th['line']}}}
.tl:last-child{{border-bottom:none}}
.tl>span{{font-size:30px;font-weight:700;color:{th['ink']};white-space:nowrap;
  overflow:hidden}}
.tl>span.r{{text-align:right}}
.tl>span.m{{text-align:center;font-size:24px;font-weight:800;color:{th['accent']};
  letter-spacing:-.01em}}
.tl>span em{{font-style:normal;font-size:22px;font-weight:600;color:{th['faint']}}}
/* ── 관전 포인트 — 숫자를 읽어 주는 자리 (예측하는 자리가 아니다) ── */
.vd{{margin-top:30px;border-left:4px solid {th['accent']};padding:2px 0 2px 26px}}
.vd p{{font-size:28px;line-height:1.52;color:{th['ink']};font-weight:600;
  letter-spacing:-.02em}}
.vd p+p{{margin-top:12px;color:{th['dim']};font-weight:500}}
.vd .pick{{display:flex;align-items:baseline;gap:16px;margin-bottom:18px}}
.vd .pick i{{font-style:normal;font-size:20px;font-weight:800;letter-spacing:.14em;
  color:{th['chip_ink']};background:{th['chip_bg']};padding:8px 14px;
  border-radius:{th['radius']}}}
.vd .pick b{{font-size:36px;font-weight:800;letter-spacing:-.03em;color:{th['ink']}}}
.vd .tag{{margin-top:20px;font-size:21px;font-weight:700;letter-spacing:.08em;
  color:{th['faint']}}}
</style></head><body><div class="card">
  <div class="top">{rail}
    <div class="lab"><svg viewBox="0 0 24 24" fill="none" stroke="{th['accent']}"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="{icon}"/></svg>
      {esc(label)}<span class="lg">{esc(lg)}</span><span class="dt">{esc(date_label)}</span></div>
    <div class="lead">{esc(head.text)}</div>{sub}
    <div class="rule"></div>
  </div>
  <div class="body num">{body}</div>
  <div class="foot"><span>{esc(foot_left)}</span><span class="wm">NUDE-TV.NET</span></div>
</div></body></html>"""


# ══════════════════════════════════════════════════════════════
# 본문 — 종류마다 골격이 다르다
# ══════════════════════════════════════════════════════════════

def body_schedule(games: list, league: League, *, with_venue: bool = True) -> str:
    """모닝·시작 알림이 함께 쓴다 — 시각 + 대진 + 장소."""
    out = []
    for g in sorted(games, key=lambda x: x.start_utc):
        cancel = g.status in (Status.CANCELED, Status.POSTPONED)
        # **원문 그대로 내보내지 않는다.** `Progressive Field`·`京セラD大阪`가
        # 그대로 나가면 못 읽는다(약점 32). 표에 없는 곳은 원문을 유지한다 —
        # 지어낸 음역보다 낫다.
        venue = (venue_name(g.venue) or "") if (with_venue and g.venue) else ""
        tail = (f'<span class="t3">{esc(venue)}</span>' if venue else "<span></span>")
        cls = "t2 dim" if cancel else "t2"
        mark = " · 취소" if cancel else ""
        out.append(
            f'<div class="li" style="grid-template-columns:150px 1fr auto">'
            f'<span class="t1">{esc(_kst(g.start_utc))}</span>'
            f'<span class="{cls}">{esc(_nm(league, g.away))} vs '
            f'{esc(_nm(league, g.home))}{mark}</span>{tail}</div>')
    return "".join(out)


def body_scoreboard(games: list, league: League) -> str:
    """결과 — 이긴 쪽이 굵다. **취소는 점수 자리에 사유를 쓴다**(숨기지 않는다)."""
    out = []
    for g in sorted(games, key=lambda x: x.start_utc):
        if g.status in (Status.CANCELED, Status.POSTPONED):
            why = (g.meta.cancel_reason if g.meta else "") or "취소"
            mid = f'<span class="off">{esc(why)}</span>'
            lc = rc = "t2 dim"
        elif g.score:
            hs, as_ = g.score.home, g.score.away
            mid = f'<span class="sc">{as_} <i>:</i> {hs}</span>'
            lc = "t2" if as_ > hs else "t2 dim"
            rc = "t2" if hs > as_ else "t2 dim"
        else:
            mid = '<span class="off">진행 중</span>'
            lc = rc = "t2"
        out.append(
            f'<div class="li" style="grid-template-columns:1fr 300px 1fr">'
            f'<span class="{lc}" style="text-align:right">{esc(_nm(league, g.away))}</span>'
            f'{mid}<span class="{rc}">{esc(_nm(league, g.home))}</span></div>')
    return "".join(out)


def body_standings(rows: list, league: League) -> str:
    """순위표 — **최근10 열은 값이 하나도 없으면 통째로 뺀다**(약점 94)."""
    has10 = any(s.last10 and s.last10.total for s in rows)
    # **팀명 칸에 최소 폭을 보장한다.** 처음에는 `1fr`로 뒀는데, 고정폭 열들이
    # 968px(본문 폭)을 거의 다 먹어 팀명에 16px만 남았다 — '삼성'이 세로로
    # 두 줄로 쪼개진 채 게이트를 통과했다. `minmax()`로 바닥을 깐다.
    cols = ("56px minmax(150px,1fr) 190px 110px 100px"
            + (" 140px" if has10 else "") + " 110px")
    out = [f'<div class="li" style="grid-template-columns:{cols}">'
           f'<span class="t3">#</span><span class="t3">팀</span>'
           f'<span class="t3" style="text-align:right">승-패-무</span>'
           f'<span class="t3" style="text-align:right">승률</span>'
           f'<span class="t3" style="text-align:right">승차</span>'
           + ('<span class="t3" style="text-align:right">최근10</span>' if has10 else "")
           + '<span class="t3" style="text-align:right">연속</span></div>']
    for s in sorted(rows, key=lambda x: x.rank):
        gb = "—" if s.games_behind in ("0", "0.0", "") else s.games_behind
        st = ""
        if s.streak_kind in (StreakKind.WIN, StreakKind.LOSS) and s.streak_len:
            word = "승" if s.streak_kind is StreakKind.WIN else "패"
            cls = "up" if s.streak_kind is StreakKind.WIN else "dn"
            st = f'<span class="{cls}">{s.streak_len}{word}</span>'
        l10 = ""
        if has10:
            v = (f"{s.last10.win}-{s.last10.loss}"
                 + (f"-{s.last10.draw}" if s.last10 and s.last10.draw else "")
                 ) if s.last10 and s.last10.total else "—"
            l10 = f'<span class="t2" style="text-align:right;font-size:26px">{esc(v)}</span>'
        rec = f"{s.record.win}-{s.record.loss}" + (f"-{s.record.draw}"
                                                  if s.record.draw else "")
        out.append(
            f'<div class="li" style="grid-template-columns:{cols}">'
            f'<span class="rk{" lead-rank" if s.rank <= 2 else ""}">{s.rank}</span>'
            f'<span class="t1" style="font-size:29px">{esc(_nm(league, s.team_code))}</span>'
            f'<span class="t2" style="text-align:right;font-size:27px">{esc(rec)}</span>'
            f'<span class="t2" style="text-align:right;font-size:27px">{esc(s.pct)}</span>'
            f'<span class="t2" style="text-align:right;font-size:27px">{esc(gb)}</span>'
            f'{l10}<span style="text-align:right;font-size:26px">{st}</span></div>')
    return "".join(out)


def body_leaders(leaders: dict, league: League, categories: list[str]) -> str:
    """부문 — 2×2. **비어 있는 부문은 칸을 만들지 않는다.**"""
    out = []
    for cat in categories:
        entries = leaders.get(cat) or []
        if not entries:
            continue
        rows = "".join(
            f'<div class="qr{" first" if e.rank == 1 else ""}">'
            f'<i>{e.rank}</i><b>{esc(e.name)}</b>'
            f'<small>{esc(_nm(league, e.team_code))}</small>'
            f'<span>{esc(e.value)}</span></div>' for e in entries)
        out.append(f'<div class="qb"><h4>{esc(cat)}</h4>{rows}</div>')
    return f'<div class="quad">{"".join(out)}</div>' if out else ""


def body_compare(rows: list[tuple], away_name: str, home_name: str,
                 away_sub: str, home_sub: str, footer: tuple | None = None) -> str:
    """분석 — 좌우 대비. `rows`는 (왼값, 이름, 오른값, 어느쪽이_앞서나) 이다.
    `앞서나`는 'l' | 'r' | '' — **비기면 아무 쪽도 강조하지 않는다.**"""
    body = [f'<div class="duo"><div><div class="n">{esc(away_name)}</div>'
            f'<div class="p">{esc(away_sub)}</div></div><div class="x">VS</div>'
            f'<div><div class="n r">{esc(home_name)}</div>'
            f'<div class="p r">{esc(home_sub)}</div></div></div>']
    for left, key, right, better in rows:
        lc = "v r on" if better == "l" else "v r"
        rc = "v on" if better == "r" else "v"
        body.append(f'<div class="cmp"><div class="{lc}">{esc(left)}</div>'
                    f'<div class="k">{esc(key)}</div>'
                    f'<div class="{rc}">{esc(right)}</div></div>')
    if footer:
        body.append(f'<div class="bar"><span class="k">{esc(footer[0])}</span>'
                    f'<span class="v">{esc(footer[1])}</span></div>')
    return "".join(body)


def body_index(rows: list[tuple]) -> str:
    """나이트 — 리그별 한 줄. `rows`는 (리그라벨, 건수문구, 대표결과) 이다.

    **결과를 다시 쓰지 않는다.** 상세는 리그별 결과 카드에 있다 — 여기서 되풀이하면
    같은 내용이 하루에 두 번 나간다(옛 나이트 브리핑이 정확히 그랬다).
    """
    return "".join(
        f'<div class="ix"><span class="bg">{esc(lg)}</span>'
        f'<span class="cn">{cnt}</span>'
        f'<span class="pk">{pick}</span></div>' for lg, cnt, pick in rows)


# ══════════════════════════════════════════════════════════════
# 게이트 — 카드를 실제로 그려서 잰다
# ══════════════════════════════════════════════════════════════
#
# **재는 방법이 틀리면 통과는 증거가 아니다 (약점 62).**
# 그때 `getClientRects().length`로 줄을 셌는데, 대상이 grid item이라 블록으로
# 바뀌어 **몇 줄이든 rect가 1개**였다. 이 파일을 처음 짤 때 나도 같은 함정에
# 그대로 다시 빠졌다 — '삼성'이 세로 두 줄로 쪼개진 채 검사를 통과했다.
#
# 그래서 **높이 ÷ 줄높이**로 잰다. 블록이든 인라인이든 접히면 높이가 늘어난다.

# ══════════════════════════════════════════════════════════════
# 흐름표 — **골격은 하나, 언어만 종목이 정한다**
# ══════════════════════════════════════════════════════════════
#
# 조사에서 나온 것: 종목마다 데이터는 다른데 **하는 일은 같다** —
# "경기가 어떻게 흘러갔나"를 한 줄로 보여주는 것.
#
#   야구  이닝별 `0 1 3 0 0 1 3 0 6`     네이버 record.scoreBoard.inn
#   농구  쿼터별 `18 28 21 17`           네이버 record.homeQ1Score~Q4Score
#   배구  세트별 `25 20 25 25`           네이버 base game.currentScoreBySet
#
# 그래서 셋이 **같은 함수**를 쓴다. 리그가 늘어도 카드가 따로 놀지 않는다.
# (축구만 구간이 없다 — 시각이 곧 흐름이라 `body_timeline`을 쓴다)

NAME_COL_PX = 212             # 팀명 칸. '페퍼저축은행'(6자)이 178px에서 잘렸다
NAME_FS_MAX = 32              # 팀명 기본 크기
NAME_FS_MIN = 24              # 이보다 줄이면 폰에서 안 읽힌다 — 그 전에 게이트가 막는다
CELL_MIN_PX = 46              # 이보다 좁으면 두 자리 수가 접힌다
BIG_CELL_MIN = 4              # 이 값 이상인 칸을 강조한다(야구 한 이닝 4점)


def _name_cell(name: str) -> str:
    """팀명 칸 — **글자 수가 아니라 폭으로 정한다**(약점 63).

    한글은 글자당 폰트 크기만큼, 영문·숫자·공백은 그 절반 남짓을 쓴다.
    넘치면 줄이고, 최소 크기로도 안 되면 **게이트가 잡도록 그대로 둔다** —
    여기서 몰래 자르면 '페퍼저축은헹' 사고가 그대로 되풀이된다.
    """
    units = sum(1.0 if ord(c) > 0x1100 else 0.55 for c in name)
    fs = NAME_FS_MAX
    while fs > NAME_FS_MIN and units * fs > NAME_COL_PX - 8:
        fs -= 1
    return f'<span class="nm" style="font-size:{fs}px">{esc(name)}</span>'


def _cells(vals, *, highlight: bool) -> str:
    out = []
    for v in vals:
        if v is None:
            out.append('<span class="zero">·</span>')
            continue
        cls = "big" if (highlight and isinstance(v, int) and v >= BIG_CELL_MIN) else (
            "zero" if v == 0 else "")
        out.append(f'<span class="{cls}">{esc(v)}</span>')
    return "".join(out)


def body_periods(*, labels: list, away_name: str, home_name: str,
                 away: list, home: list,
                 total_labels: Optional[list] = None,
                 away_totals: Optional[list] = None,
                 home_totals: Optional[list] = None,
                 highlight: bool = False) -> str:
    """구간별 점수표 — 야구(이닝)·농구(쿼터)·배구(세트)가 함께 쓴다.

    `labels`가 구간 이름, `total_labels`가 오른쪽 합계 칸(야구 R·H·E 등).
    **칸이 좁아지면 두 자리 수가 접힌다.** 그래서 구간 수 상한을 계산으로 막는다 —
    "표에 안 들어가면 표를 줄인다"가 아니라 **들어가는지 먼저 검사한다**(약점 62·92).
    """
    total_labels = total_labels or []
    away_totals = away_totals or []
    home_totals = home_totals or []
    if not (len(labels) == len(away) == len(home)):
        raise ValueError("구간 라벨과 점수 개수가 다릅니다")
    if not (len(total_labels) == len(away_totals) == len(home_totals)):
        raise ValueError("합계 라벨과 값 개수가 다릅니다")

    n = len(labels) + len(total_labels)
    avail = CARD_W - 56 * 2 - NAME_COL_PX
    if n and avail / n < CELL_MIN_PX:
        raise ValueError(
            f"칸이 좁습니다: {n}칸에 {avail}px (칸당 {avail / n:.0f}px < {CELL_MIN_PX}px). "
            f"구간을 줄이거나 팀명 칸을 좁혀야 합니다.")
    cols = f"{NAME_COL_PX}px repeat({n}, 1fr)" if n else f"{NAME_COL_PX}px"

    def row(name, vals, totals, win):
        tot = "".join('<span class="tot">%s</span>' % esc(v) for v in totals)
        cls = "fg fr win" if win else "fg fr"
        return (f'<div class="{cls}" style="grid-template-columns:{cols}">'
                f'{_name_cell(name)}'
                f'{_cells(vals, highlight=highlight)}{tot}</div>')

    a_sum = away_totals[0] if away_totals else sum(v for v in away if v)
    h_sum = home_totals[0] if home_totals else sum(v for v in home if v)
    cells = "".join('<span>%s</span>' % esc(x) for x in list(labels) + list(total_labels))
    head = (f'<div class="fg fh" style="grid-template-columns:{cols}">'
            f'<span class="nm"></span>{cells}</div>')
    return ('<div class="fw">' + head
            + row(away_name, away, away_totals, a_sum > h_sum)
            + row(home_name, home, home_totals, h_sum > a_sum) + '</div>')


def body_timeline(*, away_name: str, home_name: str, events: list,
                  away_win: bool = False, home_win: bool = False) -> str:
    """득점 타임라인 — 축구. `events`는 (분, 'home'|'away', 이름, 꼬리표) 순서쌍.

    **꼬리표는 소스가 주는 것만 쓴다.** 자책골은 `ownGoal` 플래그가 있어서 쓴다 —
    없는 것을 추측해 붙이지 않는다.
    """
    rows = []
    for minute, side, name, note in sorted(events, key=lambda e: e[0]):
        tag = f' <em>({esc(note)})</em>' if note else ""
        cell = f'{esc(name)}{tag}'
        if side == "away":
            rows.append(f'<div class="tl"><span class="r">{cell}</span>'
                        f'<span class="m">{esc(minute)}′</span>'
                        f'<span></span></div>')
        else:
            rows.append(f'<div class="tl"><span></span>'
                        f'<span class="m">{esc(minute)}′</span>'
                        f'<span>{cell}</span></div>')
    acls = "r win" if away_win else "r"
    hcls = "win" if home_win else ""
    head = (f'<div class="th2"><span class="{acls}">{esc(away_name)}</span>'
            f'<span class="c">득점</span>'
            f'<span class="{hcls}">{esc(home_name)}</span></div>')
    if not rows:
        rows = ['<div class="tl"><span></span><span class="m">—</span>'
                '<span>득점 없음</span></div>']
    return '<div class="tw">' + head + "".join(rows) + '</div>'


def body_verdict(v, *, note: str = "팀 기록으로 본 예상입니다 · 결과를 보장하지 않습니다") -> str:
    """관전 포인트 — `headline.for_preview()`가 만든 문장을 그대로 싣는다.

    **여기서 문장을 짓지 않는다.** 카드가 말을 만들기 시작하면 그 말이 어디서
    왔는지 아무도 추적할 수 없다. 규칙 엔진이 만들고, 게이트가 그 문장의 모든
    숫자가 `facts`에 있는지 검사하고, 카드는 받아 적기만 한다.

    꼬리표(`note`)는 **지운다고 예뻐지지 않는다** — 프로토를 보는 독자에게
    이것이 예측이 아니라는 것을 밝히는 자리다.
    """
    if not v or not v.lines:
        return ""
    pick = (f'<div class="pick"><i>예상</i><b>{esc(v.pick)}</b></div>'
            if getattr(v, "pick", "") else "")
    ps = "".join(f"<p>{esc(x)}</p>" for x in v.lines)
    tag = f'<div class="tag">{esc(note)}</div>' if note else ""
    return f'<div class="vd">{pick}{ps}{tag}</div>'


WRAP_TOLERANCE = 1.5          # 이 줄 수를 넘으면 접힌 것으로 본다
MIN_FONT_PX = 20              # 이보다 작으면 폰에서 못 읽는다

_MEASURE_JS = """() => {
  const out = [];
  const card = document.querySelector('.card');
  // **카드가 없으면 조용히 빈 결과를 돌려준다.** 여기서 터지면 드라이런 렌더
  // 시험이 통째로 죽고, 그건 카드 한 장이 깨지는 것보다 나쁘다(v1.11h).
  if (!card) return ['카드 골격(.card)이 없습니다'];
  const cr = card.getBoundingClientRect();
  // ① 한 줄이어야 하는 칸이 접혔나 — 높이 ÷ 줄높이로 잰다
  //
  // **목록이 아니라 '전체 - 예외'로 잰다.** 전에는 검사할 클래스를 하나하나
  // 적어 두었는데, 새 골격을 만들 때마다 그 목록에 넣는 것을 잊으면 그 칸은
  // 무방비가 된다. 실제로 흐름표를 만들 때 그랬다(약점 92의 재발).
  // 이제는 잎 노드 전부를 보고, **접혀도 되는 것만** 예외로 적는다 —
  // 예외는 눈에 띄고, 빠뜨리면 오검출이 나서 바로 알게 된다.
  const WRAP_OK = '.sub,.vd p';        // 문장은 여러 줄이 정상이다
  document.querySelectorAll('.card *').forEach(el => {
    if (el.children.length || !el.textContent.trim()) return;   // 잎 노드만
    if (el.closest(WRAP_OK)) return;
    const cs = getComputedStyle(el);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.2;
    // **안쪽 여백을 빼고 잰다.** 배지처럼 padding이 있는 칸은 글자가 한 줄이어도
    // 바깥 높이가 1.7배가 된다 — 그대로 재면 멀쩡한 칸을 접혔다고 잡는다.
    const h = el.getBoundingClientRect().height
              - (parseFloat(cs.paddingTop) || 0) - (parseFloat(cs.paddingBottom) || 0);
    if (lh > 0 && h / lh > %(tol)s)
      out.push('접힘(' + (h/lh).toFixed(1) + '줄): ' + el.textContent.trim().slice(0, 24));
  });
  // ② 같은 행의 칸끼리 포개졌나
  //    **이탈 검사만으로는 못 잡는다** — 카드 안에서 겹치는 것은 밖으로 나가지
  //    않기 때문이다. 실제로 순위 배지가 카드 머리와 같은 클래스 이름(`.top`)을
  //    써서 padding을 상속받아 폭이 두 배가 되고 팀명 위로 42px 포개졌는데,
  //    접힘·이탈·폰트 검사 셋 다 통과했다. 이름 충돌은 조용히 겹친다.
  //
  // **여기도 목록을 버린다.** 가로로 나란히 놓이는 것은 `display:grid`나 `flex`가
  // 만든다 — 그러니 그것을 직접 찾으면 새 골격이 저절로 검사 대상이 된다.
  document.querySelectorAll('.card *').forEach(row => {
    const disp = getComputedStyle(row).display;
    if (disp !== 'grid' && disp !== 'flex') return;
    if (getComputedStyle(row).gridTemplateColumns === 'none' && disp === 'grid') return;
    const kids = [...row.children].map(el => el.getBoundingClientRect())
                   .filter(r => r.width > 0);
    if (kids.length < 2) return;
    // **여러 줄로 접히는 grid만 건너뛴다.**
    //
    // 처음엔 '자식들의 top이 어긋나면 건너뛴다'로 짰는데, 그러자 원래 잡던 사고를
    // 못 잡게 됐다 — 그 사고가 바로 **배지가 커지면서 줄이 어긋난** 경우였기 때문이다.
    // 게이트를 고치다 게이트를 죽인 셈이고, 변이시험이 그것을 잡았다.
    //
    // 옳은 기준은 위치가 아니라 **골격**이다. grid의 열 수보다 자식이 많으면
    // 그 골격은 원래 여러 줄이다(2열 부문 상자 같은 것). 그때만 건너뛰고,
    // 한 줄짜리 골격은 **DOM 순서 그대로** 좌우를 견준다.
    const gtc = getComputedStyle(row).gridTemplateColumns;
    if (disp === 'grid' && gtc && gtc !== 'none'
        && kids.length > gtc.trim().split(/\s+/).length) return;
    for (let i = 1; i < kids.length; i++)
      if (kids[i].left < kids[i-1].right - 1) {
        out.push('겹침(' + Math.round(kids[i-1].right - kids[i].left) + 'px): '
                 + row.textContent.trim().slice(0, 26));
        break;
      }
  });
  // ③ 칸 안에서 잘렸나 — **overflow:hidden은 조용히 글자를 먹는다**
  //    실측: 팀명 칸 178px에 '페퍼저축은행'(6자)이 '페퍼저축은헹'으로 잘려 나갔고
  //    접힘·겹침·이탈·폰트 검사 넷이 전부 통과했다. 높이도 정상, 카드 안에도 있고,
  //    폰트도 크다 — 잘린 것만 아무도 안 봤다(약점 62·92의 새 얼굴).
  document.querySelectorAll('.card *').forEach(el => {
    if (el.children.length === 0 && el.textContent.trim()
        && el.scrollWidth > el.clientWidth + 1)
      out.push('잘림(' + (el.scrollWidth - el.clientWidth) + 'px): '
               + el.textContent.trim().slice(0, 20));
  });
  // ④ 카드 밖으로 밀려났나
  document.querySelectorAll('.card *').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && (r.right > cr.right + 1 || r.left < cr.left - 1))
      out.push('이탈: ' + el.textContent.trim().slice(0, 20));
  });
  // ⑤ 읽을 수 없이 작은 글자
  document.querySelectorAll('.card *').forEach(el => {
    if (el.children.length === 0 && el.textContent.trim()) {
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < %(min)s)
        out.push('작은 글자 ' + fs + 'px: ' + el.textContent.trim().slice(0, 18));
    }
  });
  // ⑥ 한글이 두부로 그려지나 — 폰트가 없으면 오류도 경고도 없이 □□□가 된다
  const c = document.createElement('canvas'), x = c.getContext('2d');
  x.font = '40px Pretendard, "Noto Sans KR", sans-serif';
  const ko = x.measureText('가').width;
  x.font = '40px monospace';
  const tofu = x.measureText('\\uFFFD').width;
  if (ko <= 0 || Math.abs(ko - tofu) < 0.5) out.push('두부 의심: 한글 폭 ' + ko);
  return [...new Set(out)];
}""" % {"tol": WRAP_TOLERANCE, "min": MIN_FONT_PX}


async def audit(page, html: str) -> list[str]:
    """카드 하나를 그려서 결함 목록을 돌려준다. 빈 목록이면 통과."""
    await page.set_content(html)
    await page.wait_for_timeout(180)
    return await page.evaluate(_MEASURE_JS)


# ══════════════════════════════════════════════════════════════
# 카드와 함께 나가는 텍스트
# ══════════════════════════════════════════════════════════════
#
# **옛 캡션은 카드를 통째로 다시 썼다.** 대표님이 그걸 보고 지적하셨다 —
# "굳이 텍스트를 넣지 않아도 되는 카드들도 있는 것 같아."
# 실측하니 더 심했다: 모닝·결과·순위표는 **100% 중복**이었고, 순위표 텍스트는
# 카드보다 정보가 **적었다**(최근10·연속이 빠져 있었다).
#
# 원래 지시는 "카드에 다 안 들어가는 내용은 상위만 카드에, 전체는 텍스트로"였다.
# 그 전제 — *카드에 다 안 들어간다* — 가 v5에서 사라졌다. 리그별로 쪼개서
# 그날 전부를 한 장에 담기 때문이다.
#
# **그래서 규칙은 하나다: 카드에 다 들어갔으면 본문 텍스트를 안 붙인다.**
#
# 다만 **머리줄 한 줄은 반드시 남긴다.** 텔레그램 푸시 알림에는 카드가 안 보이고
# 캡션 글자만 뜬다 — 캡션이 비면 구독자 폰에 "사진"이라고만 온다.
# 그 한 줄은 카드와 같은 말을 하지만 중복이 아니다. **매체가 다르다** —
# 알림은 글자만 보이고, 카드는 열어야 보인다.
CAPTION_MAX = 1024
FOLLOW_MAX = 4096

KIND_EMOJI = {"morning": "📋", "start": "⏰", "result": "✅", "standings": "📊",
              "leaders": "🏅", "analysis": "⚖️", "night": "🌙"}


def caption(*, kind: str, league: Optional[League], head: Headline,
            date_label: str = "", extra_lines: Optional[list[str]] = None,
            extra_title: str = "") -> list[str]:
    """`[0]`은 사진에 붙는 캡션, `[1:]`은 이어 보내는 텍스트.

    `extra_lines`는 **카드에 없는 것만** 넣는다. 카드에 있는 것을 여기 또 쓰면
    같은 내용이 한 화면에 두 번 나온다 — 그게 고치려던 문제다.
    """
    emoji = KIND_EMOJI.get(kind, "")
    lg = LEAGUE_LABEL.get(league, "전 리그") if league else "전 리그"
    label = KIND_META[kind][0]
    lead = head.text.replace("\n", " ").strip()
    parts = [f"{emoji} <b>{esc(lg)} {esc(label)}</b>"]
    if date_label:
        parts.append(f" · {esc(date_label)}")
    head_line = "".join(parts) + f"\n{esc(lead)}"
    if head.sub:
        head_line += f" — {esc(head.sub)}"

    if not extra_lines:
        return [head_line[:CAPTION_MAX]]

    # 카드에 없는 것이 있을 때만 인용블록을 붙인다(부문 순위의 '그 밖의 부문' 등).
    title = f"\n\n<b>{esc(extra_title)}</b>" if extra_title else ""
    quoted = ("<blockquote expandable>"
              + "\n".join(esc(x) for x in extra_lines) + "</blockquote>")
    whole = head_line + title + "\n" + quoted
    if len(whole) <= CAPTION_MAX:
        return [whole]

    # 넘치면 뒤로 넘긴다. **자르지 않는다** — 자르면 '전체'라는 약속이 거짓이 된다.
    keep = len(extra_lines)
    while keep > 1:
        cand = (head_line + title + "\n<blockquote expandable>"
                + "\n".join(esc(x) for x in extra_lines[:keep]) + "</blockquote>"
                + f"\n<i>나머지 {len(extra_lines) - keep}줄은 다음 메시지에 이어집니다</i>")
        if len(cand) <= CAPTION_MAX:
            break
        keep -= 1
    out = [(head_line + title + "\n<blockquote expandable>"
            + "\n".join(esc(x) for x in extra_lines[:keep]) + "</blockquote>"
            + f"\n<i>나머지 {len(extra_lines) - keep}줄은 다음 메시지에 이어집니다</i>")]
    rest = extra_lines[keep:]
    while rest:
        k = len(rest)
        while k > 1:
            cand = ("<b>(이어서)</b>\n<blockquote expandable>"
                    + "\n".join(esc(x) for x in rest[:k]) + "</blockquote>")
            if len(cand) <= FOLLOW_MAX:
                break
            k -= 1
        out.append("<b>(이어서)</b>\n<blockquote expandable>"
                   + "\n".join(esc(x) for x in rest[:k]) + "</blockquote>")
        rest = rest[k:]
    return out


def leaders_extra(leaders: dict, league: League, shown: list[str]) -> list[str]:
    """부문 순위 카드에 **안 실린** 부문의 1위만. 카드에 있는 부문은 뺀다."""
    out = []
    for cat, entries in leaders.items():
        if cat in shown or not entries:
            continue
        top = [e for e in entries if e.rank == 1]
        if len(top) != 1:          # 공동 1위는 '1위'라 단정하지 않는다
            continue
        e = top[0]
        out.append(f"{cat} — {e.name} ({_nm(league, e.team_code)}) {e.value}")
    return out
