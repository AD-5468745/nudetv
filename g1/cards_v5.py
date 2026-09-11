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

from contract import (fix_team_name,KST, League, SCORE_UNIT_BY_LEAGUE, SOURCE_CREDIT, ScoreUnit,
                      Status, StreakKind, TEAM_NAMES, card_theme, league_accent,
                      venue_name, cancel_reason_text, is_readable_ko)

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
    League.UEL: "유로파리그", League.MLS: "MLS",
}

# 콘텐츠 종류마다 **고유한 아이콘 + 라벨**. 색이 아니라 이 둘이 종류를 가른다 —
# 색은 테마(리그)가 이미 쓰고 있어서 종류까지 색으로 나누면 둘이 충돌한다.
KIND_META = {
    # **"모닝"이 아니라 "경기 예고"다 (2026-09-07).** 07:30 고정에서 첫 경기
    # 30분 전으로 옮기면서 아침 카드가 아니게 됐다 — MLB는 새벽, 유럽은 심야에
    # 나간다. 이름이 시각을 말하면 시각이 바뀔 때마다 이름이 거짓이 된다.
    "morning":  ("경기 예고", "M4 17h16M6.5 17a5.5 5.5 0 0 1 11 0M12 4.5v2"
                              "M5 8l1.4 1.4M19 8l-1.4 1.4M2.5 13h2M19.5 13h2"),
    "start":    ("시작 알림", "M12 5v8l5 3"),
    # 킥오프는 시간표(start)와 **다른 아이콘**을 쓴다 — 하루에 가장 많이 나가는
    # 카드라 시간표와 구분이 안 되면 둘 다 소음이 된다(대표님 불만 ④).
    "kickoff":  ("경기 시작", "M5 3l14 9-14 9z"),
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


# 리그 강조색을 카드 어디까지 넣을 것인가. `shell()` 주석에 세 값의 뜻이 있다.
# **대표님이 시안 3벌을 보고 고른 값** (2026-09-07): "C — 바 + 라벨까지".
# 한 글자만 바꾸면 전 카드가 따른다.
ACCENT_MODE = "full"


def esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _nm(league: Optional[League], team) -> str:
    """카드에 찍을 팀 이름.

    표에 있으면 표를 쓰고, 없으면 코드를 그대로 쓴다(유럽·MLS는 **코드가 곧
    한글 이름**이다). 마지막에 예외 표를 한 번 거친다 — 소스 이름이 다른
    리그와 어긋나거나 카드 폭을 넘는 몇 건만 여기서 바로잡는다(v1.16).
    """
    code = getattr(team, "team_code", team)
    name = TEAM_NAMES.get(league, {}).get(code, code) if league else code
    return fix_team_name(name)


def _kst(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%H:%M")


def _unit_note(league: League) -> str:
    return {ScoreUnit.MAPS: "맵 스코어", ScoreUnit.SETS: "세트 스코어",
            ScoreUnit.GOALS: "득점", ScoreUnit.POINTS: "득점"}.get(
        SCORE_UNIT_BY_LEAGUE.get(league), "득점")


# ══════════════════════════════════════════════════════════════
# 공통 골격 — 모든 카드가 이 함수를 지난다
# ══════════════════════════════════════════════════════════════

# ── 밀도 (2026-09-06 대표님 결정: "여백판") ───────────────────
#
# 대표님이 옛 v4 순위표 카드를 보고 **"내용이 너무 빼곡하게 채워져있다"**고
# 지적하셨다. 시안 넷을 실데이터로 렌더해 고른 것이 **여백판** — 정보를 하나도
# 깎지 않고 행 간격만 벌린 안이다.
#
# **밀도를 상수가 아니라 층으로 둔다.** 여백을 키우면 카드가 세로로 길어지고,
# 경기가 많은 날(MLB 16경기)이나 전 리그 통합(나이트)은 높이 2000px·세로비
# 1.85 상한에 닿을 수 있다. 그때 옛 카드로 통째로 떨어지면 **대표님이 고른
# 디자인이 가장 정보가 많은 날에만 사라진다** — 정확히 반대로 동작하는 셈이다.
#
# 그래서 사다리를 둔다: **여백판 → (넘치면) 조임판 → (그래도 넘치면) 옛 카드.**
# 조임판은 v5 골격 그대로이고 간격만 원래 값으로 돌린다.
DENSITY = ("air", "tight")

_DENSITY_CSS = {
    # 여백판 — 기본값. 대표님이 고른 것.
    "air": """
.body{padding:16px 56px 0}
.lead{font-size:58px}
.li{padding:27px 0}
.ix{padding:28px 0}
.cmp{padding:24px 0}
.fm{padding:26px 0}
.tl{padding:25px 0}
.fr{padding:26px 0}
.qr{padding:16px 0}
""",
    # 조임판 — 여백판이 상한을 넘을 때만. 골격·정보는 같고 간격만 돌린다.
    "tight": """
.body{padding:8px 56px 0}
.lead{font-size:62px}
.li{padding:19px 0}
.ix{padding:22px 0}
.cmp{padding:18px 0}
.fm{padding:19px 0}
.tl{padding:19px 0}
.fr{padding:22px 0}
.qr{padding:12px 0}
""",
}


def credit_line(leagues) -> str:
    """이 카드에 실린 리그들이 요구하는 소스 표기. 없으면 빈 문자열.

    **한 곳에서만 만든다.** 카드 종류가 여덟이고 꼬리말 호출부가 일곱 군데인데
    거기마다 문구를 적으면 그중 하나를 반드시 빠뜨린다 — 그리고 빠뜨린 카드는
    오류도 경고도 없이 **약관을 어긴 채로** 발행된다(약점 135).
    여러 리그가 섞인 카드(나이트 브리핑)도 하나라도 해당하면 붙인다.
    같은 문구가 둘 이상이면 한 번만 쓴다.
    """
    seen: list = []
    for lg in leagues or ():
        c = SOURCE_CREDIT.get(lg)
        if c and c not in seen:
            seen.append(c)
    return " · ".join(seen)


def shell(*, kind: str, league: Optional[League], date_label: str,
          head: Headline, body: str, foot_left: str,
          theme: Optional[str] = None, group_label: str = "",
          density: str = "air", credit_for=None,
          kind_label: str = "") -> str:
    """머리(라벨·헤드라인) — 본문 — 꼬리. **일곱 종류가 전부 이 골격을 쓴다.**

    바뀌는 것은 `kind`(아이콘·라벨)와 `body`(본문 골격)뿐이다.
    `density`는 여백의 층이다 — 위 `_DENSITY_CSS` 주석을 보라.
    """
    if kind not in KIND_META:
        raise ValueError(f"모르는 카드 종류: {kind}")
    if density not in _DENSITY_CSS:
        raise ValueError(f"모르는 밀도: {density}")
    _theme = theme or card_theme(league)
    th = THEMES[_theme]
    # ── 리그 강조색 (v1.15) ───────────────────────────────────
    # 대표님 지시(2026-09-07): *"리그별 색으로 나눈다"*.
    # **얼마나 넣느냐가 곧 설계다.** 다 칠하면 채널이 산만해지고, 안 칠하면
    # 스크롤할 때 챔피언스리그와 유로파가 같은 카드로 보인다.
    # `ACCENT_MODE`가 그 눈금이다 — 시안을 이 값만 바꿔 세 벌 뽑았다.
    #   "off"  현행. 전 리그 브랜드 민트
    #   "rail" 왼쪽 세로 바만 리그색. 종류 라벨은 민트 유지
    #   "full" 세로 바 + 종류 라벨(아이콘·글자)까지 리그색
    _acc = league_accent(league, _theme) if ACCENT_MODE != "off" else None
    # **원본 테마 표를 건드리지 않는다.** 여기서 `th`를 제자리 수정하면 그 색이
    # 다음 카드로 새어 나간다 — 리그 하나의 색이 다른 리그 카드에 찍히는데,
    # 오류도 안 나고 로그에도 안 남는다(약점 45와 같은 얼굴: 공유 상태의 변형).
    _rail_color = _acc or th["accent"]
    if _acc and ACCENT_MODE == "full":
        th = dict(th, accent=_acc)
    # 소스 표기(football-data 약관 제7.1조). `credit_for`를 주지 않으면
    # 이 카드의 리그 하나로 판단한다 — 그래서 호출부는 아무것도 안 해도 붙는다.
    _c = credit_line(credit_for if credit_for is not None else [league])
    _credit = f'<span class="cr">{esc(_c)}</span>' if _c else ""
    label, icon = KIND_META[kind]
    # **종류 이름을 발송 순간에 바꿔 달 수 있다.** 경기 예고가 첫 경기 뒤에
    # 나가는 날에는 '예고'가 아니라 '안내'다(`contract.morning_label`).
    # 아이콘은 그대로 둔다 — 같은 종류의 카드이고, 아이콘까지 바뀌면
    # 시청자에게는 다른 카드로 보인다.
    label = kind_label or label
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
.rail{{position:absolute;left:0;top:52px;bottom:0;width:6px;background:{_rail_color}}}
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
/* 번호·시각은 **곁들이는 값**이다. 팀명·점수보다 한 단 낮춰 두어야
   눈이 결과를 먼저 읽는다. 그래도 판독 하한(20px)은 지킨다. */
/* 리그 머리줄 — 경기 목록 위에 얹는다. 배지는 색인과 같은 것을 쓰되
   위쪽 여백을 크게 둬서 **묶음이 눈으로 갈라지게** 한다. */
.gh{{display:flex;align-items:center;gap:18px;padding:30px 0 10px;
  border-bottom:1px solid {th['line']}}}
.gh:first-child{{padding-top:6px}}
.gh .bg{{font-size:20px;font-weight:800;letter-spacing:.10em;color:{th['chip_ink']};
  background:{th['chip_bg']};padding:9px 16px;border-radius:{th['radius']};
  min-width:130px;text-align:center}}
.gh .cn{{font-size:27px;font-weight:700;white-space:nowrap;color:{th['ink']}}}
/* 빽빽판 — 경기 조각을 흘려 담는다. 조각 사이는 가운뎃점으로 가른다.
   판독 하한(20px)을 지키면서 줄당 두세 경기가 들어가는 크기다. */
.cw{{padding:16px 0 4px;font-size:26px;line-height:1.78;color:{th['dim']};
  font-weight:600}}
/* 조각 사이는 **여백으로만** 가른다. 가운뎃점을 넣어 봤더니 줄이 바뀌는
   자리에서 점이 줄 맨 앞에 떨어졌다 — 줄바꿈 위치는 브라우저가 정하므로
   글자로 된 구분자는 어디에 붙여도 언젠가 줄 끝이나 줄 앞에 남는다.
   점수가 이미 조각을 갈라 주므로 여백이면 충분하다. */
.cw .cg{{white-space:nowrap;margin-right:30px}}
/* 오늘의 경기 — 목록과 **눈에 띄게 갈라야** 한다. 목록의 한 줄처럼 보이면
   골라 놓은 뜻이 사라진다. 위에 굵은 선을 긋고 제목을 얹는다. */
.bt{{margin-top:34px;padding-top:26px;border-top:2px solid {th['rule']};
  font-size:22px;font-weight:800;letter-spacing:.12em;color:{th['accent']}}}
.bs{{display:flex;align-items:baseline;gap:20px;padding:18px 0;
  border-bottom:1px solid {th['line']}}}
.bs:last-child{{border-bottom:none}}
.bm{{font-size:31px;font-weight:600;color:{th['dim']}}}
.bm b{{color:{th['ink']};font-weight:800}}
.bsc{{font-weight:800;color:{th['ink']};margin:0 6px}}
.bn{{margin-left:auto;font-size:24px;font-weight:700;color:{th['accent']};
  white-space:nowrap}}
.cw .cg b{{color:{th['ink']};font-weight:800}}
.cw .cg .dimt{{color:{th['dim']}}}
.cw .cg i{{font-style:normal;color:{th['faint']};font-size:24px}}
.lead-cell{{display:flex;align-items:baseline;gap:14px}}
.no{{color:{th['faint']};font-size:22px;font-weight:700;min-width:34px}}
.tm{{color:{th['dim']};font-size:26px;font-weight:600;letter-spacing:.01em}}
/* 소스 표기는 약관이 요구하는 문장 그대로라 길다(47자). 읽히되 카드의
   주인공이 되지 않아야 하므로 **굵기와 투명도로만** 낮춘다.
   크기는 못 낮춘다 — 처음에 15px로 넣었더니 v5 판독성 게이트(`MIN_FONT_PX`
   20px)가 즉시 잡았다. 게이트가 옳다: 폰에서 못 읽는 표기는 약관이 말하는
   'visible location'도 아니다. 20px는 워터마크와 같은 크기다. */
.cr{{display:block;font-size:20px;font-weight:500;letter-spacing:.01em;
  opacity:.62;margin-top:6px}}
/* ── 본문 부품 (일곱 종류가 나눠 쓴다) ── */
.li{{display:grid;align-items:baseline;gap:14px;padding:19px 0;
  border-bottom:1px solid {th['line']}}}
.li:last-child{{border-bottom:none}}
.t1{{font-size:31px;font-weight:800;letter-spacing:-.03em;color:{th['ink']}}}
.t2{{font-size:31px;font-weight:600;color:{th['ink']}}}
.t2.dim{{color:{th['dim']};font-weight:500}}
.t3{{font-size:23px;color:{th['faint']};font-weight:600}}
/* ── 홈팀 배지 (v1.15e, 2026-09-07 대표님 지시) ────────────────
   목록형 카드는 '원정 vs 홈' 순서로만 홈을 알렸다 — **보는 사람은 그 규칙을
   모른다.** 한 글자로 못박는다. 팀명과 붙어 다니므로 baseline을 맞추고,
   글자는 안쪽 span이 갖는다(고정 상자를 잎 노드로 두면 접힘 게이트가
   `높이/줄높이`를 접힘으로 읽는다 — 약점 153). */
.hb{{display:inline-flex;align-items:center;justify-content:center;
  margin-left:10px;padding:3px 10px;border-radius:{th['radius']};
  border:1.5px solid {th['line']};vertical-align:2px}}
.hb>span{{font-size:20px;font-weight:700;line-height:1;letter-spacing:.04em;
  color:{th['faint']}}}
/* ── 홈 배지 — **팀명 우측 상단 모서리** (2026-09-07 대표님 확정) ──
   *"팀명 우측상단 모서리쯤 컬러감 있는 배지를 만들어서 박아두고 싶어.
   집모양도 괜찮고."*
   색은 `th['accent']` = **그 카드의 리그 색**이라 카드마다 저절로 갈린다. */
.tn{{font-weight:inherit;color:inherit;letter-spacing:inherit}}
.hm{{display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;margin-left:7px;vertical-align:super;
  border-radius:50%;flex:none}}
.hm>svg{{width:16px;height:16px;display:block}}
.hm.dot{{background:{th['accent']};color:{th['bg']}}}
.hm.ring{{border:2px solid {th['accent']};color:{th['accent']}}}
.hm.tag{{border-radius:9px;background:{th['accent']};color:{th['bg']}}}
/* 진 팀·취소된 경기에서는 **색을 빼지 않고 낮춘다.**
   회색으로 바꾸면 대표님이 원한 '컬러감'이 절반만 남는다 — 배지는 홈을
   말하는 것이지 승패를 말하는 것이 아니므로, 위계는 투명도로만 준다. */
.dim .hm{{opacity:.45}}
/* icon — 색 없이 선만 */
.hi{{display:inline-flex;width:22px;height:22px;margin-left:11px;
  vertical-align:-2px;color:{th['faint']};opacity:.85}}
.hi>svg{{width:100%;height:100%}}
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
/* ── 최근 n경기 폼 (v1.15c) ──────────────────────────────────
   **색만으로 읽히면 안 된다.** 승/패/무 글자를 배지 안에 함께 넣는다 —
   옛 v4 카드가 이미 그렇게 고쳐 놓은 것을 그대로 옮긴다(약점 132). */
.anh{{display:flex;align-items:baseline;justify-content:space-between;
  padding:0 0 16px;border-bottom:1px solid {th['rule']};
  font-size:22px;font-weight:800;letter-spacing:.08em;color:{th['dim']}}}
.anh span{{font-size:20px;font-weight:700;letter-spacing:.04em;color:{th['faint']}}}
.fm{{padding:26px 0;border-bottom:1px solid {th['line']}}}
.fm:last-child{{border-bottom:none}}
.fm .tn{{font-size:32px;font-weight:800;color:{th['ink']};letter-spacing:-.02em}}
.fm .dots{{display:flex;gap:10px;margin:14px 0 12px}}
.fm .d{{width:56px;height:56px;border-radius:{th['radius']};display:flex;
  align-items:center;justify-content:center}}
/* **글자는 안쪽 span이 갖는다.** 배지는 고정 높이 56px짜리 '상자'이고 글자가
   아니다. 잎 노드가 상자면 접힘 게이트가 56/24 = 2.3줄로 읽어 멀쩡한 배지를
   접혔다고 잡는다 — 상자와 글자를 나누면 게이트를 느슨하게 하지 않고도
   정확해진다(예외 목록에 넣어 게이트를 무디게 만드는 쪽이 더 나쁘다). */
.fm .d>span{{font-size:24px;font-weight:800;line-height:1}}
.fm .d.w{{background:{th['accent']};color:{th['bg']}}}
.fm .d.l{{background:{th['line']};color:{th['faint']}}}
.fm .d.t{{background:{th['chip_bg']};color:{th['chip_ink']}}}
.fm .lst{{font-size:24px;font-weight:600;color:{th['dim']}}}
/* ── 시즌 상대전적 — 막대는 길이가 곧 수치다 ── */
.hh{{padding:22px 0}}
.hh .r{{display:grid;grid-template-columns:200px 1fr 120px;align-items:center;
  gap:20px;padding:12px 0}}
.hh .nm{{font-size:30px;font-weight:700;color:{th['ink']};letter-spacing:-.02em;
  white-space:nowrap;overflow:hidden}}
.hh .bg{{height:22px;border-radius:11px;background:{th['line']};overflow:hidden}}
.hh .bg>i{{display:block;height:100%;background:{th['accent']};border-radius:11px}}
.hh .bg>i.q{{background:{th['faint']}}}
.hh .vl{{font-size:30px;font-weight:800;color:{th['ink']};text-align:right}}
/* ── 여러 경기 분석 (v1.15f, 2026-09-07 대표님 지시) ──────────────
   *"경기 분석도 모든 팀 알림으로 변경하자. 몇팀씩 묶어서 카드한장안에
   너무 우겨넣지 않고 보기좋도록 나눠서."*
   한 경기를 **네 줄**로 줄인다: 번호·시각 / 대진 / 비교 / 한 줄 평. */
.ag{{padding:30px 0;border-bottom:1px solid {th['line']}}}
.ag:last-child{{border-bottom:none}}
.agh{{display:flex;align-items:baseline;gap:16px;margin-bottom:16px}}
.agh .no{{font-size:22px;font-weight:800;color:{th['faint']}}}
.agh .tm{{font-size:24px;font-weight:700;color:{th['dim']};letter-spacing:-.01em}}
.agm{{display:grid;grid-template-columns:1fr 84px 1fr;align-items:baseline;
  column-gap:12px}}
.agm .nm{{font-size:36px;font-weight:800;letter-spacing:-.03em;color:{th['ink']};
  white-space:nowrap;overflow:hidden}}
.agm .nm.r{{text-align:right}}
.agm .x{{font-size:20px;font-weight:800;color:{th['faint']};text-align:center;
  letter-spacing:.12em}}
.agm .rk{{font-size:23px;font-weight:600;color:{th['dim']};margin-top:6px}}
.agm .rk.r{{text-align:right}}
.agk{{margin-top:18px;font-size:24px;font-weight:600;color:{th['dim']};
  line-height:1.55;word-break:keep-all}}
.agk b{{font-weight:800;color:{th['ink']}}}
.agv{{margin-top:12px;font-size:25px;font-weight:700;color:{th['ink']};
  letter-spacing:-.02em;line-height:1.45;word-break:keep-all}}
/* ── 선발 라인업 (v1.17) — 축구. 포메이션 줄을 그대로 세로로 쌓는다 ── */
.lu{{padding:24px 0;border-bottom:1px solid {th['line']}}}
.lu:last-child{{border-bottom:none}}
.luh{{display:flex;align-items:baseline;gap:14px;margin-bottom:18px}}
.luh .nm{{font-size:34px;font-weight:800;letter-spacing:-.03em;color:{th['ink']}}}
.luh .fm2{{font-size:23px;font-weight:700;letter-spacing:.06em;color:{th['accent']}}}
.lur{{display:flex;flex-wrap:wrap;gap:10px 0;padding:11px 0}}
.lur+.lur{{border-top:1px solid {th['line']}}}
.lup{{font-size:27px;font-weight:600;color:{th['ink']};letter-spacing:-.02em;
  white-space:nowrap}}
/* **이름 사이에 구분점을 찍는다.** 공백만으로는 한 줄이 통째로 한 덩어리로
   읽힌다 — 유럽은 '리산드로 마르티네스'처럼 이름 자체가 두 단어인 선수가
   많아서, 어디서 끊기는지 눈으로 못 찾는다. 여백을 더 벌리는 방법도 있지만
   그러면 한 줄에 네 명이 안 들어가 줄이 접히고 포메이션 모양이 무너진다.
   구분점은 자리를 거의 안 먹으면서 경계를 확실히 만든다. */
.lup+.lup{{margin-left:16px;padding-left:16px;
  border-left:1px solid {th['line']}}}
.lup .gl{{font-size:22px;font-weight:800;color:{th['accent']};margin-left:6px}}
.lupos{{font-size:20px;font-weight:800;letter-spacing:.1em;color:{th['faint']};
  min-width:44px;align-self:center}}
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
/* ── 밀도 ({density}) — 이 블록만 층에 따라 갈린다 ── */{_DENSITY_CSS[density]}
</style></head><body><div class="card">
  <div class="top">{rail}
    <div class="lab"><svg viewBox="0 0 24 24" fill="none" stroke="{th['accent']}"
      stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="{icon}"/></svg>
      {esc(label)}<span class="lg">{esc(lg)}</span><span class="dt">{esc(date_label)}</span></div>
    <div class="lead">{esc(head.text)}</div>{sub}
    <div class="rule"></div>
  </div>
  <div class="body num">{body}</div>
  <div class="foot"><span>{esc(foot_left)}{_credit}</span><span class="wm">NUDE-TV.NET</span></div>
</div></body></html>"""


# ══════════════════════════════════════════════════════════════
# 본문 — 종류마다 골격이 다르다
# ══════════════════════════════════════════════════════════════

HOME_BADGE_TEXT = "홈"

# 홈 표시를 어떤 모양으로 할 것인가 (v1.15g — 대표님: *"촌스럽다. 트렌디하고
# 감각적으로"*). **한 글자만 바꾸면 전 카드가 따른다.**
#
#   "dot"   **팀명 우측 상단 · 리그 강조색 원 + 집** ← 대표님 확정 방향
#   "ring"  같은 자리 · 색은 테두리만 (더 가볍게)
#   "tag"   같은 자리 · 라운드 사각
#   "icon"  팀명 옆 얇은 선 집 아이콘 (색 없음)
#   "at"    원정 @ 홈 — 국제 스포츠 표기
#   "chip"  테두리 + 한글 '홈'  ← 첫 안. 대표님: *"촌스럽다"*
#   "off"   표시 없음
#
# 색은 `th['accent']`를 쓴다 — `shell()`이 이 값을 **그 카드의 리그 색**으로
# 바꿔 두므로(ACCENT_MODE="full"), 배지가 리그마다 저절로 갈린다.
# 새 색표를 만들지 않는다(약점 17: 표가 있어도 안 쓰면 없는 것과 같다).
HOME_BADGE_STYLE = "dot"


def home_badge() -> str:
    """홈팀 뒤에 붙는 표시. **모양을 한 곳에서만 만든다.**

    2026-09-07 대표님 지시: *"각 경기 홈팀에 배지 붙이자."* → 첫 안(테두리 칩)을
    보시고 *"촌스럽다. 트렌디하고 감각적으로 바꿔줘."*

    끄려면 `HOME_BADGE_STYLE = "off"`.
    """
    st = HOME_BADGE_STYLE
    if st == "off" or not HOME_BADGE_TEXT:
        return ""
    if st == "at":
        return ""            # `@`는 배지가 아니라 구분자다 — `vs_mark()`가 쓴다
    # 집 아이콘 — 카드 헤더 라벨과 **같은 stroke 언어**(굵기·둥근 끝)를 쓴다.
    # 새 조형을 하나 더 만들면 카드 안에 언어가 둘이 된다.
    _house = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
              'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
              '<path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M6 9.6V20h12V9.6"/></svg>')
    if st in ("dot", "ring", "tag"):
        return f'<i class="hm {st}">{_house}</i>'
    if st == "icon":
        return f'<i class="hi">{_house}</i>'
    return f'<i class="hb"><span>{esc(HOME_BADGE_TEXT)}</span></i>'


def vs_mark() -> str:
    """원정과 홈 사이에 쓸 구분자. `at` 스타일이면 **`vs` 대신 `@`**.

    국제 스포츠 표기에서 `@` 뒤가 홈이다 — 글자를 더하지 않고 홈을 알린다.
    """
    return "@" if HOME_BADGE_STYLE == "at" else "vs"


def body_schedule(games: list, league: League, *, with_venue: bool = True,
                  times: Optional[list] = None, numbered: bool = False) -> str:
    """모닝·시작 알림이 함께 쓴다 — 시각 + 대진 + 장소.

    `times`는 **부르는 쪽이 이미 만든 시각 표기**이고 정렬된 순서와 같아야 한다.
    여기서 다시 계산하지 않는다 — 두 곳에서 계산하면 반드시 어긋난다(약점 104).
    한국 날짜 둘에 걸치는 슬레이트(유럽 주말·일부 MLB)는 요일이 붙은 표기가
    와야 한다: 요일이 없으면 23:00과 01:00이 같은 날처럼 읽힌다.
    안 주면 시:분만 쓴다.
    """
    out = []
    for i, g in enumerate(sorted(games, key=lambda x: x.start_utc)):
        cancel = g.status in (Status.CANCELED, Status.POSTPONED)
        # **원문 그대로 내보내지 않는다.** `Progressive Field`·`京セラD大阪`가
        # 그대로 나가면 못 읽는다(약점 32). 표에 없는 곳은 원문을 유지한다 —
        # 지어낸 음역보다 낫다.
        venue = (venue_name(g.venue) or "") if (with_venue and g.venue) else ""
        tail = (f'<span class="t3">{esc(venue)}</span>' if venue else "<span></span>")
        cls = "t2 dim" if cancel else "t2"
        mark = " · 취소" if cancel else ""
        # 번호는 **시간순**이다 — 목록이 시작 시각으로 정렬되므로 번호가 곧
        # "그날 몇 번째로 열리는 경기인가"가 된다. 정리판과 같은 규칙이다
        # (대표님: 시작 흐름을 종료 흐름과 같은 모양으로).
        lead = f'<span class="no">{i + 1}</span>' if numbered else ""
        cols = ("70px minmax(150px,auto) 1fr auto" if numbered
                else "minmax(150px,auto) 1fr auto")
        out.append(
            f'<div class="li" style="grid-template-columns:{cols}">{lead}'
            f'<span class="t1">{esc(times[i] if times else _kst(g.start_utc))}</span>'
            f'<span class="{cls}"><b class="tn">{esc(_nm(league, g.away))} '
            f'{vs_mark()} {esc(_nm(league, g.home))}{mark}</b>'
            f'{home_badge()}</span>{tail}</div>')
    return "".join(out)


def body_scoreboard(games: list, league: League, *,
                    with_time: bool = False, numbered: bool = False) -> str:
    """결과 — 이긴 쪽이 굵다. **취소는 점수 자리에 사유를 쓴다**(숨기지 않는다).

    `with_time`·`numbered`는 2026-09-07 대표님 요청이다:
    *"몇번째 경기라고 표기해주면 좋을것같고 경기시간도 같이 알려주면 좋겠다"*.

    **번호는 시간순이다.** 목록이 시작 시각으로 정렬되므로 번호가 곧
    "그날 몇 번째로 열린 경기인가"가 된다 — 우리가 임의로 매긴 순서가 아니다.
    시각은 **한국시각**이다(카드의 다른 모든 시각과 같다).
    """
    out = []
    _n = 0
    for g in sorted(games, key=lambda x: x.start_utc):
        _n += 1
        if g.status in (Status.CANCELED, Status.POSTPONED):
            # **번역표를 반드시 거친다.** 원문을 그대로 쓰면 일본어가 그대로
            # 인쇄된다 — 2026-09-08 채널에 `히로시마 中止 한신`이 나갔다.
            # `cancel_reason_text`가 그것을 막으려고 만들어졌는데 **v5만 안 썼다**
            # (약점 132: 새 경로는 옛 경로가 고쳐 온 것을 되살린다).
            why = cancel_reason_text(
                g.meta.cancel_reason if g.meta else None, g.status, short=True)
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
        lead = ""
        cols = "1fr 300px 1fr"
        if numbered or with_time:
            bits = []
            if numbered:
                bits.append(f'<span class="no">{_n}</span>')
            if with_time:
                bits.append(f'<span class="tm">{esc(_kst(g.start_utc))}</span>')
            lead = f'<span class="lead-cell">{"".join(bits)}</span>'
            # 번호 50 + 시각 90 = 140px를 앞에 떼어 준다. 점수 칸을 줄이지 않는다 —
            # 점수는 이 카드의 주인공이고, 팀명 칸이 대신 좁아지는 편이 낫다.
            cols = ("140px 1fr 260px 1fr" if (numbered and with_time)
                    else ("70px 1fr 280px 1fr" if numbered else "110px 1fr 280px 1fr"))
        out.append(
            f'<div class="li" style="grid-template-columns:{cols}">{lead}'
            f'<span class="{lc}" style="text-align:right">{esc(_nm(league, g.away))}</span>'
            f'{mid}<span class="{rc}">'
            f'<b class="tn">{"@ " if HOME_BADGE_STYLE == "at" else ""}'
            f'{esc(_nm(league, g.home))}</b>{home_badge()}</span></div>')
    return "".join(out)


def body_standings(rows: list, league: League) -> str:
    """순위표 — **값이 하나도 없는 열은 통째로 뺀다**(약점 94).

    빈 열이 나란히 있으면 정직해 보이는 게 아니라 **고장난 것처럼 보인다.**
    최근10에는 이 방어가 있었는데 **연속에는 없었다** — NPB는 소스가 연속을
    주지 않아 12행 전부 빈 채로 머리글만 서 있었다(2026-09-06 실렌더에서 잡음).
    같은 결함을 한 열에만 막으면 다른 열에서 반드시 다시 난다.
    """
    has10 = any(s.last10 and s.last10.total for s in rows)
    has_st = any(s.streak_len and s.streak_kind in (StreakKind.WIN, StreakKind.LOSS)
                 for s in rows)
    # **팀명 칸에 최소 폭을 보장한다.** 처음에는 `1fr`로 뒀는데, 고정폭 열들이
    # 968px(본문 폭)을 거의 다 먹어 팀명에 16px만 남았다 — '삼성'이 세로로
    # 두 줄로 쪼개진 채 게이트를 통과했다. `minmax()`로 바닥을 깐다.
    cols = ("56px minmax(150px,1fr) 190px 110px 100px"
            + (" 140px" if has10 else "") + (" 110px" if has_st else ""))
    out = [f'<div class="li" style="grid-template-columns:{cols}">'
           f'<span class="t3">#</span><span class="t3">팀</span>'
           f'<span class="t3" style="text-align:right">승-패-무</span>'
           f'<span class="t3" style="text-align:right">승률</span>'
           f'<span class="t3" style="text-align:right">승차</span>'
           + ('<span class="t3" style="text-align:right">최근10</span>' if has10 else "")
           + ('<span class="t3" style="text-align:right">연속</span>' if has_st else "")
           + '</div>']
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
            f'{l10}'
            + (f'<span style="text-align:right;font-size:26px">{st}</span>'
               if has_st else "")
            + '</div>')
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


def body_matchup(*, away_name: str, home_name: str, kst: str,
                 venue: str = "", note: str = "", local: str = "") -> str:
    """한 경기짜리 카드의 본문 — **킥오프 알림** (v1.14).

    경기가 하나뿐이니 표가 아니라 **그 경기 자체**를 크게 보여준다.
    리그 시간표 카드(여러 경기)와 눈에 띄게 달라야 한다 — 하루에 가장 많이
    나가는 카드라, 시간표와 헷갈리면 그 순간 두 카드가 다 소음이 된다.

    `note`는 카드가 **주장하지 않는 것만** 담는다(예: "선발 정보 없음"이 아니라
    빈 문자열). 없는 것을 쓰지 않는다 — 그 자리에 넣을 사실이 없으면 안 넣는다.
    """
    when = esc(kst) + (f' <em>· 현지 {esc(local)}</em>' if local else "")
    tail = "".join(
        f'<div class="bar"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>'
        for k, v in (("경기장", venue), ("", note)) if v)
    return (f'<div class="duo"><div><div class="n">{esc(away_name)}</div>'
            f'<div class="p">원정</div></div><div class="x">VS</div>'
            f'<div><div class="n r">{esc(home_name)}</div>'
            f'<div class="p r">홈</div></div></div>'
            f'<div class="bar"><span class="k">시작</span>'
            f'<span class="v">{when}</span></div>' + tail)


def body_gameinfo(*, kst: str, local: str = "", venue: str = "",
                  extra: Optional[list] = None) -> str:
    """한 경기 결과 카드에 **흐름표를 못 만들 때** 쓰는 본문 (v1.14).

    **점수를 다시 쓰지 않는다.** 머리말이 이미 "밀워키 8 : 12 신시내티"라고
    말했는데 본문이 같은 줄을 또 그리면, 카드 한 장이 한 가지 사실만 두 번
    말하는 셈이다(실렌더에서 435px짜리 그런 카드가 나왔다).

    대신 **머리말이 말하지 않은 것**을 적는다 — 언제, 어디서. 그것뿐이라도
    "몇 시에 어디서 있었던 경기인가"는 점수만큼 자주 궁금한 사실이다.
    """
    when = esc(kst) + (f' <em>· 현지 {esc(local)}</em>' if local else "")
    rows = [("시작", when)] + [(k, esc(v)) for k, v in (extra or []) if v]
    if venue:
        rows.append(("경기장", esc(venue)))
    return "".join(
        f'<div class="bar"><span class="k">{esc(k)}</span>'
        f'<span class="v">{v}</span></div>' for k, v in rows)


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


def body_form(rows: list[tuple], *, title: str = "최근 5경기") -> str:
    """분석 ③ — 최근 n경기 폼. `rows`는 (팀명, [결과…], 직전 한 줄) 이다.

    결과는 `"W"|"L"|"D"`. **색만으로 읽히면 안 되므로 글자를 함께 넣는다** —
    옛 v4 카드가 이미 그렇게 고쳐 놓았던 것을 그대로 가져온다(약점 132:
    새 경로는 옛 경로가 고쳐 온 것을 먼저 옮겨 적고 시작한다).

    **비어 있으면 빈 문자열을 돌려준다** — 부르는 쪽이 블록을 안 붙이면 된다.
    """
    if not rows:
        return ""
    # **'무'의 클래스는 `t`다 — `d`로 두면 배지 컨테이너 클래스와 겹친다.**
    # `class="d d"`가 되면 셀렉터 `.d.d`가 `.d`와 같아져 **모든 배지**에
    # 매치되고, 뒤에 오는 규칙이 승·패 색까지 덮어쓴다. 실렌더로 잡았다
    # (약점 58의 CSS 판 — 이름이 겹치면 조용히 덮어쓴다).
    _W = {"W": ("w", "승"), "L": ("l", "패"), "D": ("t", "무")}
    out = [f'<div class="anh">{esc(title)}<span>왼쪽이 오래된 경기</span></div>']
    for name, results, last in rows:
        dots = "".join(
            f'<div class="d {_W[r][0]}"><span>{_W[r][1]}</span></div>'
            for r in results if r in _W)
        out.append(f'<div class="fm"><div class="tn">{esc(name)}</div>'
                   f'<div class="dots">{dots}</div>'
                   f'<div class="lst">{esc(last)}</div></div>')
    return "".join(out)


def body_h2h(away_name: str, home_name: str, away_win: int, home_win: int,
             draw: int = 0, *, title: str = "시즌 상대전적") -> str:
    """분석 ④ — 시즌 상대전적. 막대 길이가 곧 수치다.

    **무승부는 막대에서 뺀다.** 세 값을 한 막대에 쌓으면 '누가 앞서나'가
    흐려진다 — 무는 제목 옆 총 경기 수에만 담는다.
    """
    total = away_win + home_win + draw
    if total <= 0:
        return ""
    top = max(away_win, home_win) or 1
    out = [f'<div class="anh">{esc(title)}'
           f'<span>{total}경기{f" · {draw}무" if draw else ""}</span></div>',
           '<div class="hh">']
    for name, win in ((away_name, away_win), (home_name, home_win)):
        pct = int(round(win * 100 / top))
        cls = "" if win == max(away_win, home_win) and away_win != home_win else " q"
        out.append(f'<div class="r"><div class="nm">{esc(name)}</div>'
                   f'<div class="bg"><i class="{cls.strip()}" '
                   f'style="width:{pct}%"></i></div>'
                   f'<div class="vl">{win}승</div></div>')
    out.append("</div>")
    return "".join(out)


def body_analysis_multi(rows: list) -> str:
    """분석 — **여러 경기를 한 장에** (v1.15f · 대표님 지시).

    `rows`는 경기마다 dict:
      `no`(그날 몇 번째) `time` `away` `home` `away_sub` `home_sub`
      `keys`(비교 한 줄 · 없으면 생략) `verdict`(한 줄 평 · 없으면 생략)

    **한 경기를 네 줄로 줄인다.** 한 경기 상세 카드(비교표 6줄 + 폼 + 상대전적)를
    그대로 여러 벌 쌓으면 카드가 1만 픽셀이 된다 — 대표님이 말한
    *"우겨넣지 않고 보기좋도록"*은 **경기 수를 줄이는 것이 아니라 경기당 줄을
    줄이는 것**으로 푼다. 몇 경기씩 나눌지는 부르는 쪽(렌더)이 정한다.
    """
    out = []
    for r in rows:
        head = []
        if r.get("no"):
            head.append(f'<span class="no">{esc(str(r["no"]))}</span>')
        if r.get("time"):
            head.append(f'<span class="tm">{esc(r["time"])}</span>')
        out.append(
            f'<div class="ag">'
            + (f'<div class="agh">{"".join(head)}</div>' if head else "")
            + f'<div class="agm">'
              f'<span class="nm">{esc(r["away"])}</span>'
              f'<span class="x">VS</span>'
              f'<span class="nm r"><b class="tn">{esc(r["home"])}</b>'
              f'{home_badge()}</span>'
              f'<span class="rk">{esc(r.get("away_sub", ""))}</span>'
              f'<span></span>'
              f'<span class="rk r">{esc(r.get("home_sub", ""))}</span>'
              f'</div>'
            + (f'<div class="agk">{r["keys"]}</div>' if r.get("keys") else "")
            + (f'<div class="agv">{esc(r["verdict"])}</div>'
               if r.get("verdict") else "")
            + '</div>')
    return "".join(out)


POS_LABELS = ("GK", "DF", "MF", "FW")


def body_lineup(teams: list) -> str:
    """선발 라인업 — 축구 (v1.17).

    `teams`는 팀마다 dict:
      `name` `formation`(예 "4-2-3-1") `rows`([[(이름, 골분[]), …], …])
    `rows`는 **골키퍼가 첫 줄**이어야 한다 — 소스는 피치 배치 순서로 주므로
    원정팀은 뒤집혀 온다. 방향 판정은 부르는 쪽이 한다(포메이션과 맞춰서).

    포지션 라벨은 **줄 수가 4일 때만** 붙인다. 3줄·5줄 포메이션에 GK·DF·MF·FW를
    억지로 맞추면 카드가 없는 사실을 말한다 — 그때는 라벨 없이 줄만 그린다.
    """
    out = []
    for t in teams:
        rows = t.get("rows") or []
        head = (f'<div class="luh"><span class="nm">{esc(t["name"])}</span>'
                + (f'<span class="fm2">{esc(t["formation"])}</span>'
                   if t.get("formation") else "") + '</div>')
        # GK + 3줄 = 4줄일 때만 DF·MF·FW가 참말이 된다
        labeled = len(rows) == 4
        body = []
        for i, line in enumerate(rows):
            names = []
            for nm, goals in line:
                g = ("".join(f"<span class='gl'>{esc(m)}′</span>" for m in goals)
                     if goals else "")
                names.append(f'<span class="lup">{esc(nm)}{g}</span>')
            lab = (f'<span class="lupos">{POS_LABELS[i]}</span>'
                   if labeled and i < len(POS_LABELS) else "")
            body.append(f'<div class="lur">{lab}{"".join(names)}</div>')
        out.append(f'<div class="lu">{head}{"".join(body)}</div>')
    return "".join(out)


def body_index(rows: list[tuple]) -> str:
    """나이트 — 리그별 한 줄. `rows`는 (리그라벨, 건수문구) 이다.

    **가장 짧은 단이다.** 전 경기를 실으면 카드가 높이 상한을 넘는 날
    (리그 서넛 · 30~40경기)에만 여기까지 내려온다.

    ⚠️ **'대표 경기'를 더 이상 고르지 않는다** (2026-09-07).
    예전에는 리그마다 '가장 점수차가 큰 경기' 하나를 골라 옆에 적었다.
    대표님 지적: *"대표경기라는 기준이 사람들마다 다를텐데"* — 맞다.
    점수차가 크다는 것은 **일방적이었다**는 뜻이지 볼 만했다는 뜻이 아니다.
    1점차 접전을 더 치는 사람이 많다. 우리가 정한 기준을 카드가 사실인 양
    내세우고 있었던 것이고, 그건 지어내기(FACT_LOCK)에 한 발 걸친 일이다.
    **고를 수 없으면 고르지 않는다** — 전부 싣거나, 숫자만 적는다.
    """
    return "".join(
        f'<div class="ix"><span class="bg">{esc(lg)}</span>'
        f'<span class="cn">{cnt}</span></div>' for lg, cnt in rows)


def compact_result(g, league: League) -> str:
    """한 경기를 **한 조각**으로. `LG 5:3 두산` · `SK 우천취소 OB`.

    빽빽한 판에서 쓴다 — 줄마다 한 경기를 두면 30경기가 카드에 안 담긴다.
    이긴 쪽을 굵게 하는 규칙은 목록판과 같다(같은 사실을 다르게 보이지 않는다).
    """
    aw, hm = _nm(league, g.away), _nm(league, g.home)
    if g.status in (Status.CANCELED, Status.POSTPONED):
        why = cancel_reason_text(
            g.meta.cancel_reason if g.meta else None, g.status, short=True)
        return (f'<span class="cg"><span class="dimt">{esc(aw)}</span> '
                f'<i>{esc(why)}</i> <span class="dimt">{esc(hm)}</span></span>')
    if not g.score:
        return (f'<span class="cg"><span class="dimt">{esc(aw)}</span> '
                f'<i>진행 중</i> <span class="dimt">{esc(hm)}</span></span>')
    a_, h_ = g.score.away, g.score.home
    aw_s = f'<b>{esc(aw)}</b>' if a_ > h_ else f'<span class="dimt">{esc(aw)}</span>'
    hm_s = f'<b>{esc(hm)}</b>' if h_ > a_ else f'<span class="dimt">{esc(hm)}</span>'
    return f'<span class="cg">{aw_s} {a_}:{h_} {hm_s}</span>'


def body_night_compact(groups: list[tuple]) -> str:
    """나이트 **빽빽판** — 리그별 머리줄 + 그 리그 경기를 한 덩어리로 흘린다.

    `groups`는 (리그라벨, 건수문구, [경기조각HTML]) 이다.

    **왜 이 단이 필요한가.** 대표님이 고른 것은 "대표 경기가 아니라 전 경기"다.
    그런데 리그 넷이 겹친 날은 30~40경기라, 줄마다 한 경기를 두면 3,800px가
    되어 어떤 밀도로도 2000px 상한에 안 담긴다(실측 2026-09-05).
    그때 리그별 건수만 남기면 **대표님이 요청한 '전 경기'가 사라진다** —
    가장 경기가 많은 날에만 요청이 무효가 되는 셈이다.
    그래서 줄을 버리지 않고 **줄바꿈을 버린다**: 경기를 가운뎃점으로 이어 흘리면
    같은 30경기가 열 줄 남짓에 들어간다.
    """
    out = []
    for lg, cnt, chunks in groups:
        out.append(f'<div class="gh"><span class="bg">{esc(lg)}</span>'
                   f'<span class="cn">{cnt}</span></div>'
                   f'<div class="cw">{" ".join(chunks)}</div>')
    return "".join(out)


def body_best(picks: list, league: League, *, title: str = "오늘의 경기") -> str:
    """오늘 눈여겨볼 경기 한둘 + **왜 골랐는지** (v1.15).

    `picks`는 `[(경기, 코멘트)]` — 코멘트는 `headline.best_games`가 만든다.

    **코멘트가 없으면 이 칸을 통째로 만들지 않는다.** 근거 없이 경기만 올리면
    그게 대표님이 지적한 자의적 '대표 경기'로 되돌아간다.
    """
    if not picks:
        return ""
    rows = []
    for g, note in picks:
        if not note:
            continue
        aw, hm = _nm(league, g.away), _nm(league, g.home)
        a_, h_ = (g.score.away, g.score.home) if g.score else ("", "")
        aw_s = f"<b>{esc(aw)}</b>" if g.score and a_ > h_ else esc(aw)
        hm_s = f"<b>{esc(hm)}</b>" if g.score and h_ > a_ else esc(hm)
        rows.append(f'<div class="bs"><span class="bm">{aw_s} '
                    f'<span class="bsc">{a_}:{h_}</span> {hm_s}</span>'
                    f'<span class="bn">{esc(note)}</span></div>')
    if not rows:
        return ""
    return (f'<div class="bt">{esc(title)}</div>' + "".join(rows))


def body_night_grouped(groups: list[tuple]) -> str:
    """나이트 — **리그별로 묶고 그 안에 그날 경기를 전부** 싣는다 (2026-09-07).

    `groups`는 (리그라벨, 건수문구, 경기목록HTML) 이다.
    대표님이 고른 형태다: *"대표경기가 아니라 전 경기 넣어주고, 리그별 색인"*.

    리그 머리줄이 있어야 어느 리그의 경기인지 알 수 있다 — 경기만 죽 이으면
    KBO와 MLB가 한 덩어리로 보인다.
    """
    out = []
    for lg, cnt, rows_html in groups:
        out.append(f'<div class="gh"><span class="bg">{esc(lg)}</span>'
                   f'<span class="cn">{cnt}</span></div>{rows_html}')
    return "".join(out)


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


def relax(html: str) -> Optional[str]:
    """여백판 카드를 **조임판으로 한 단계만** 내린다. 여백판이 아니면 None.

    바꾸는 것은 밀도 블록 하나뿐이다 — 골격도 정보도 그대로다. 그래서
    "높이 때문에 카드를 못 냈다"는 일이 안 생긴다(위 DENSITY 주석).
    """
    air = _DENSITY_CSS["air"]
    if air not in html:
        return None
    return html.replace(air, _DENSITY_CSS["tight"], 1)


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
  // ⚠️ **`.agk`·`.agv`는 v1.16에서 빠뜨렸다 (2026-09-08에 대표님이 잡으심).**
  // 단일 경기 총평(`.vd p`)은 처음부터 예외였는데, 여러 경기 분석 골격을
  // 만들면서 같은 성격의 문장 둘을 넣지 않았다 — 이 주석이 바로 위에서
  // 경고한 그 실수다(약점 92·153과 같은 얼굴).
  //
  // **증상이 조용했던 것이 이 사고의 핵심이다.** 게이트에 걸린 카드는 오류를
  // 내지 않고 **옛 v4 카드로 조용히 떨어진다**. 그래서 검증 1,450건이 다
  // 통과하는데도 채널에는 옛 디자인이 나갔고, 대표님 눈에만 보였다.
  // 게다가 문장 길이에 따라 걸리는 카드와 안 걸리는 카드가 갈려
  // (실측: NPB 묶음0 통과 · 묶음1 탈락) "가끔 옛날 것이 섞여 나온다"로 보였다.
  const WRAP_OK = '.sub,.vd p,.agk,.agv';   // 문장은 여러 줄이 정상이다
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

KIND_EMOJI = {"morning": "📋", "start": "⏰", "kickoff": "🔔", "result": "✅",
              "standings": "📊",
              "leaders": "🏅", "analysis": "⚖️", "night": "🌙"}


def caption(*, kind: str, league: Optional[League], head: Headline,
            date_label: str = "", extra_lines: Optional[list[str]] = None,
            extra_title: str = "", note: str = "") -> list[str]:
    """`[0]`은 사진에 붙는 캡션, `[1:]`은 이어 보내는 텍스트.

    `extra_lines`는 **카드에 없는 것만** 넣는다. 카드에 있는 것을 여기 또 쓰면
    같은 내용이 한 화면에 두 번 나온다 — 그게 고치려던 문제다.

    `note`는 머리줄 끝에 붙는 짧은 단서다(v1.31). **접히지 않는 자리**에 둔다 —
    기록 기준시각처럼 **신뢰의 근거가 되는 것**은 펼쳐야 보이면 뜻이 없다.

    ⚠️ **여기에 출처 이름을 넣지 않는다.** 이 프로젝트는 "꼬리말은 출처를
    주장하지 않는다"로 이미 결론을 냈고(약점 107: 'LCK 공식 결과'라 적었는데
    실제로는 팬 위키였다), 약관상 표기 의무가 있는 소스는 `credit_line`이
    카드 꼬리말에 따로 붙인다. 그 둘을 섞으면 한쪽이 반드시 낡는다.
    """
    emoji = KIND_EMOJI.get(kind, "")
    lg = LEAGUE_LABEL.get(league, "전 리그") if league else "전 리그"
    label = KIND_META[kind][0]
    lead = head.text.replace("\n", " ").strip()
    parts = [f"{emoji} <b>{esc(lg)} {esc(label)}</b>"]
    if date_label:
        parts.append(f" · {esc(date_label)}")
    if note:
        parts.append(f" · <i>{esc(note)}</i>")
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
