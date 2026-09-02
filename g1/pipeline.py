"""G1 세로 관통 — 수집 → 큐 → 렌더 → 드라이런.

파이프라인은 리그를 모른다. 어댑터가 계약(Game)으로 넘겨준 것만 다룬다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from contract import (CARD_MAX_ASPECT, CARD_MAX_HEIGHT_PX, CARD_WIDTH_PX, KST,
                      ContentType, Game, GateError, League, LEAGUE_COLORS,
                      TELEGRAM_TEXT_MAX,
                      QueueItem, SEND_JPEG_QUALITY, SEND_JPEG_SUBSAMPLING,
                      GRACE_SECONDS,
                      Status, assert_card_geometry, esc, format_kickoff,
                      START_ALERT_LEAD_MINUTES,
                      idem_key, plan_send_parts, quote, stale_grace_for,
                      QUOTE_EXPANDABLE_THRESHOLD_LINES,
                      day_schedule_scope, start_alert_bucket,
                      start_alert_lead_text, venue_name,
                      assert_result_deadline, kst_day_label, result_deadline,
                      SCORE_UNIT_BY_LEAGUE, ScoreUnit,
                      shift_out_of_quiet_hours,
                      # v1.11i — 문안 사실성 헬퍼. 조사·사유 표기·모닝 이름·큐 잔류는
                      # 계약이 한 번만 정한다. 렌더마다 다시 지으면 반드시 갈라진다.
                      KBL_SEASON_CATEGORY_ALLOW, needs_local_time,
                      cancel_reason_text, josa, keep_in_queue, morning_label)
from contract import assert_card_typography, team_name

# 워터마크(.wm/.wm3)는 읽으라고 넣은 글자가 아니므로 타이포 게이트에서 제외한다.
#
# ── 줄 수·잘림·카드 밖 이탈을 **실제로** 재는 법 (v1.11j) ────────────────
# 이 세 값이 전부 틀려 있었고, 그래서 접힘·잘림 게이트가 한 번도 작동한 적이 없다.
#
# ① `lines: el.getClientRects().length` — **블록 요소는 몇 줄이든 rect가 1개다.**
#    실측(2026-08-28 KBO 결과 카드): 두 줄로 접힌 `.st`("그라운드사/정 취소",
#    높이 92px = 46px×2)가 `getClientRects().length === 1`을 돌려줬다.
#    `.n1/.n2/.st/.mt`가 전부 블록이라 접힘 검사가 통과만 하고 있었다.
#    → **텍스트 노드에 Range를 씌워** 줄상자(line box)를 직접 센다. 한 줄에 여러
#      런(한글+영문)으로 쪼개진 rect가 나올 수 있으므로 **rect의 top 값 종류 수**를
#      센다. 요소 높이÷line-height는 쓰지 않았다 — 패딩·플렉스가 있는 칸에서
#      오판한다(실측: `.pill` 높이 66 ÷ 행간 36 = 2줄로 잘못 나옴, `.wmc`도 2줄).
#
# ② `clipped: scrollWidth > clientWidth` — 넘침이 `visible`인 칸에서도 참이 된다.
#    실측: `.s1`은 `::after`(가운데 콜론)가 `left:100%`로 밖에 놓여 있어
#    결과 카드 **전 행에서 항상 참**이었다. 지금은 NO_CLIP_CLASSES에 없어 무해하지만
#    넣는 순간 전 카드가 막힌다. 글자는 `overflow:hidden|clip`일 때만 잘려 보이므로
#    그 조건을 함께 본다.
#
# ③ 줄도 안 접히고 잘리지도 않은 채 **행이 통째로 카드 밖으로 밀려나는** 사고는
#    아예 재는 값이 없었다. 실측(팀명을 '세인트루이스카디널스'/'샌프란시스코
#    자이언츠'로 바꿔 렌더): 상태 칸 `.st`가 카드 오른쪽 끝을 66px 넘어가
#    화면에서 사라졌는데 게이트 3종이 전부 통과했다. `over`(카드 밖으로 나간 px)와
#    `box`(그려진 상자 크기)를 함께 재서 `assert_within_card()`가 판정한다.
_TYPO_JS = """
() => {
  const res=[];
  const card=document.querySelector('#card');
  const cb=card.getBoundingClientRect();
  const walk=(el)=>{
    if(el.closest('.wm, .wm3')) return;
    for(const n of el.childNodes){
      if(n.nodeType===3 && n.textContent.trim()){
        const r=el.getBoundingClientRect();
        const cs=getComputedStyle(el);
        // 줄상자를 직접 센다 — 텍스트 노드에 Range를 씌우면 줄마다 rect가 나온다.
        const rg=document.createRange(); rg.selectNodeContents(n);
        const rects=[...rg.getClientRects()].filter(x=>x.width>0||x.height>0);
        const tops=new Set(rects.map(x=>Math.round(x.top)));
        const tr=rg.getBoundingClientRect();
        const ov=cs.overflowX;
        res.push({t:n.textContent.trim().slice(0,16),
                  fs:parseFloat(cs.fontSize),
                  cls:(el.className||'').toString().slice(0,24),
                  // 줄 수 — 2 이상이면 그 글자가 두 줄로 접혔다는 뜻이다.
                  // 팀명이 접히면 행 높이가 들쭉날쭉해져 카드가 무너진다.
                  lines: Math.max(1, tops.size),
                  // 말줄임(…)으로 잘린 칸. 넘침을 감추는 칸에서만 성립한다.
                  clipped: (ov==='hidden'||ov==='clip')
                           && el.scrollWidth > el.clientWidth + 1,
                  // 카드 밖으로 나간 px(좌·우 중 큰 쪽). 0보다 크면 안 보인다.
                  over: Math.round(Math.max(tr.right-cb.right, cb.left-tr.left)),
                  // 그려진 상자. 0이면 글자가 있는데 자리가 없어 사라진 것이다.
                  box: Math.round(Math.min(r.width, r.height))});
      } else if(n.nodeType===1) walk(n);
    }
  };
  walk(card);
  return res;
}
"""

# 한글이 **실제 글자로** 그려지는지 브라우저 안에서 직접 확인한다.
#
# 왜 필요한가: 카드는 서버의 시스템 폰트로 그려진다. 내 개발 컴퓨터에는 한글 폰트가
# 깔려 있지만 **GitHub 서버(ubuntu-latest)에는 없다.** 폰트가 없으면 크로미움은
# 조용히 두부(□□□)로 그린다 — 오류도 경고도 없다. 숫자 검증은 전부 통과하고,
# 두부 카드가 채널로 나간 뒤에야 알게 된다.
# (전에 '전 리그 카드가 KBO 색'을 숫자검증 215건이 다 통과시키고 눈으로 보고서야
#  잡은 적이 있다. 같은 계열의 사고를 이번엔 기계가 잡게 한다.)
#
# 재는 법: 한글 '가'와 **어떤 폰트에도 없는 글자**의 폭을 비교한다.
# 폰트가 없으면 둘 다 같은 두부 글리프라 폭이 같아진다. 다르면 진짜 글자다.
_FONT_JS = r"""
() => {
  const cv = document.createElement('canvas').getContext('2d');
  const fam = getComputedStyle(document.querySelector('#card')).fontFamily;
  cv.font = '64px ' + fam;
  const ko = cv.measureText('가').width;
  const tofu = cv.measureText('�').width / 2;   // 없는 글자 = 두부
  const zero = cv.measureText('0').width;
  return {family: fam, ko: ko, tofu: tofu, zero: zero,
          ready: document.fonts ? document.fonts.status : 'n/a'};
}
"""


class FontMissing(GateError):
    """한글 폰트가 없다 — 카드가 두부로 나간다."""


def assert_korean_font(m: dict) -> None:
    """한글이 두부로 그려지고 있으면 발송 전에 멈춘다."""
    if m.get("ko", 0) <= 0:
        raise FontMissing(f"한글 폭을 재지 못했습니다 (font-family: {m.get('family')})")
    # 두부 글리프와 폭이 같으면(0.5px 이내) 한글이 안 그려지고 있는 것이다.
    if abs(m["ko"] - m.get("tofu", -99)) < 0.5:
        raise FontMissing(
            "한글 폰트가 없어 카드가 두부(□)로 그려집니다. "
            f"font-family={m.get('family')} · 한글폭 {m['ko']:.1f} = 두부폭 {m['tofu']:.1f}. "
            "서버에 한글 폰트를 설치하세요 (우분투: apt-get install -y fonts-noto-cjk).")


class NameWrapped(GateError):
    """팀명이 두 줄로 접혔다 — 카드 행이 무너진다."""


# 팀명·점수처럼 한 줄이어야 하는 칸. 여기가 접히면 행 높이가 들쭉날쭉해진다.
# **`st`(결과 카드 상태 칸)를 넣는다 (v1.11j).** 폭이 152px로 고정인데 검사 대상이
# 아니어서, "그라운드사정 취소"가 낱말 중간에서 "그라운드사/정 취소"로 쪼개진 채
# 2026-08-16·08-28 KBO 카드가 그대로 나갔다(실측 높이 92px = 46px 두 줄).
ONE_LINE_CLASSES = ("n1", "n2", "s1", "s2", "mt", "st")

# **잘리면 안 되는 칸.** 이 칸들은 CSS가 줄바꿈 대신 말줄임(…)으로 처리하므로
# 줄 수 검사에 아무것도 안 걸린다. 실측: MLB 모닝 8행 중 5행이 구장명 잘림
# ("에인…", "다저 …", "그레이트…"). 잘린 이름은 정보가 아니다.
NO_CLIP_CLASSES = ("meta", "p")


def assert_no_wrapped_names(samples: list[dict]) -> None:
    """한 줄이어야 하는 칸이 접혔으면 막는다.

    **왜 게이트가 필요한가.** 팀명을 국내 표기로 바꾸자마자
    '세인트루이스'(6자)와 '샌프란시스코'(7자)가 결과 카드에서 두 줄로 접혔다.
    글자 수 상한(8자)은 통과했다 — 상한은 글자 수를 세지, 실제로 그려진 폭을
    보지 않기 때문이다. 카드를 눈으로 보고서야 알았다.
    이제 브라우저가 실제로 그린 줄 수를 재서 확인한다.
    """
    bad = []
    for s in samples:
        cls = str(s.get("cls") or "")
        if not any(c in cls.split() for c in ONE_LINE_CLASSES):
            continue
        if int(s.get("lines") or 1) > 1:
            bad.append(f"{s.get('t')!r}({cls}, {s.get('lines')}줄)")
    if bad:
        raise NameWrapped(
            "한 줄이어야 하는 칸이 접혔습니다 — 행 높이가 무너집니다: "
            + " · ".join(bad[:4])
            + ". 표시명을 줄이거나 카드 CSS에서 크기를 낮추세요.")


class TextClipped(GateError):
    """이름이 말줄임으로 잘렸다 — 잘린 이름은 정보가 아니다."""


def assert_not_clipped(samples: list[dict]) -> None:
    """구장명·선수명이 …으로 잘렸으면 막는다."""
    bad = []
    for s in samples:
        cls = str(s.get("cls") or "")
        if not any(c in cls.split() for c in NO_CLIP_CLASSES):
            continue
        if s.get("clipped"):
            bad.append(f"{s.get('t')!r}({cls})")
    if bad:
        raise TextClipped(
            "이름이 잘려 나갑니다(…): " + " · ".join(bad[:5])
            + ". 짧은 표기를 쓰거나 카드에서 그 칸을 빼세요.")


class TextOutsideCard(GateError):
    """글자가 카드 밖으로 밀려났다 — 화면에는 아무것도 없다."""


# 워터마크(장식)는 카드 경계에 걸쳐도 된다. 읽으라고 넣은 글자가 아니다.
_DECOR_CLASSES = ("wm", "wm3", "wmc")


def assert_within_card(samples: list[dict]) -> None:
    """행이 카드 밖으로 밀려나 글자가 사라졌으면 막는다 (v1.11j).

    **접힘·잘림 검사만으로는 못 잡는 사고가 있다.** 팀명이 길어지면 격자
    (`.res`는 `1fr 92px 92px 1fr 152px`)가 통째로 오른쪽으로 밀린다. 이때
    팀명은 한 줄 그대로고(nowrap) 말줄임도 아니라(`overflow:visible`) 기존 두
    검사에 아무것도 안 걸리는데, 카드는 `overflow:hidden`이라 밀려난 칸이
    **그냥 안 보인다.**

    실측(2026-09-01 KBO 결과 카드, 팀명을 '세인트루이스카디널스'·'샌프란시스코
    자이언츠'로 바꿔 렌더): 첫 행의 상태 칸 `.st`("종료")가 카드 오른쪽 끝을
    66px 넘어가 사라졌다. 그런데 폰트·접힘·잘림 게이트가 전부 통과했다 —
    지금 팀명이 최장 7자라 우연히 무사했을 뿐이지 검사가 지켜 준 게 아니다.
    """
    bad = []
    for s in samples:
        cls = str(s.get("cls") or "")
        if any(c in cls.split() for c in _DECOR_CLASSES):
            continue
        over = int(s.get("over") or 0)
        if over > 1:
            bad.append(f"{s.get('t')!r}({cls}, {over}px 이탈)")
        elif int(s.get("box", 1) or 0) <= 0:
            # 글자가 있는데 상자가 0 — 자리를 못 얻어 통째로 사라진 칸이다.
            bad.append(f"{s.get('t')!r}({cls}, 상자 0)")
    if bad:
        raise TextOutsideCard(
            "글자가 카드 밖으로 밀려났습니다 — 화면에서 사라집니다: "
            + " · ".join(bad[:5])
            + ". 표시명을 줄이거나 그 행의 칸 폭을 조정하세요.")


OUT = pathlib.Path(__file__).resolve().parent / "dryrun"
# 카드 디자인. **절대 경로를 쓰면 배포 환경에서 카드를 못 그린다** —
# 개발 폴더 경로가 박혀 있었고, 그대로 올렸으면 첫 발송에서 파일 없음으로 죽었을 것이다.
# 저장소 안에서의 상대 위치로 잡는다: g1/pipeline.py → ../cards/v4.html
CSS = pathlib.Path(__file__).resolve().parents[1] / "cards" / "v4.html"


# ── 1. 큐 생성 (롤링 지평선 +6h ~ +30h) ─────────────────────

def build_queue(games: list[Game], now: datetime, channel: str,
                floor_hours: int = 6, horizon_hours: int = 30) -> list[QueueItem]:
    """한 리그의 큐를 만든다. 틱의 자가치유·수집 잡의 add는 floor_hours=0으로 부른다.

    **멱등키에 리그를 반드시 넣는다 (v1.11c).**
    KBO 하나만 있던 시절엔 `채널|morning|2026-08-29`로 충분했다. 그런데 리그가 9개가 되자
    아홉 리그의 모닝 브리핑이 **전부 같은 키**가 되어, 하나가 나가면 나머지 여덟은
    '이미 보냄'으로 버려졌다. 결과 카드도 똑같았다.
    켰다면 KBO만 나가고 MLB·NPB·K리그·LCK는 영영 안 나갔을 것이다.
    시계를 만들어 리그별로 큐를 돌려보고서야 드러났다 — 검증 215건이 못 잡은 이유는
    큐를 한 리그로만 시험했기 때문이다.
    """
    lo, hi = now + timedelta(hours=floor_hours), now + timedelta(hours=horizon_hours)
    items: list[QueueItem] = []
    if not games:
        return items
    league = games[0].league
    if any(g.league is not league for g in games):
        raise GateError("build_queue는 한 리그씩 부른다 — 섞으면 멱등키가 엉킨다")

    # 모닝 브리핑 — 대상 구간은 sports_day가 아니라 절대 시각 [07:30, 익일 07:30)
    #
    # **지나간 07:30도 유예 안이면 큐에 남긴다 (v1.11d).**
    # 전에는 `while morning < now: morning += 1일`로 **항상 미래 것만** 잡았다.
    # 그래서 시계가 07:30~08:30 창에 안 들어오면 그날 모닝은 큐에서 통째로
    # 사라졌고, 유예를 아무리 늘려도 소용이 없었다 — 유예는 큐에 있는 항목에만
    # 적용되기 때문이다. 실측 시계 간격이 100분이라 이 일이 매일 벌어졌다.
    # (결과 카드는 같은 이유로 이미 '과거 마감도 큐에 포함'으로 고쳐져 있다.)
    _base = now.astimezone(KST).replace(hour=7, minute=30, second=0, microsecond=0)
    for _m in (_base, _base + timedelta(days=1)):
        _m_utc = _m.astimezone(timezone.utc)
        if _m_utc > hi:
            continue
        # **버림 판정은 여기서 하지 않는다 (v1.11i).**
        # 큐 생성부가 유예로 먼저 잘라내면 tick의 is_late()는 영원히 참이 되지 않는다 —
        # 실제로 38,283건 중 한 번도 참이 아니었고, 그래서 사라진 모닝 24%가
        # 로그에도 알림에도 남지 않았다. 큐는 남기고, 버림과 기록은 tick 한 곳에서 한다.
        if not keep_in_queue(_m_utc, now, ContentType.MORNING):
            continue
        day = _m.strftime("%Y-%m-%d")
        # 그날 경기가 없으면 모닝 브리핑도 없다. 큐에 넣어두고 렌더에서 버리면
        # 매일 빈 항목이 쌓여 진짜 항목이 안 보인다(V리그는 시즌이 7개월 뒤다).
        if not any(g.sports_day == day for g in games):
            continue
        scope = f"{league.value}:{day}"
        items.append(QueueItem(
            idem_key=idem_key(channel, ContentType.MORNING, scope),
            content_type=ContentType.MORNING, scope=scope,
            scheduled_utc=_m_utc, league=league, sports_day=day,
            render_at_utc=_m_utc - timedelta(minutes=15)))

    # 시작 알림 — **그 리그의 하루 시간표를 한 번에** (v1.11c에서 바뀜).
    #
    # 전에는 같은 시각(±5분) 경기를 묶어 시각마다 따로 보냈다. 리그가 아홉이 되자
    # 실측 **하루 26건**, 그중 17건이 MLB 새벽 1~3시대였다. 채널 도배다.
    # 이제 리그마다 하루 한 건, 그 리그 첫 경기의 리드타임(기본 2시간) 전에 보낸다.
    # **예약 시각은 그날 첫 경기에 고정한다 (v1.11h).**
    #
    # 전에는 `SCHEDULED`인 경기만 모아 그중 첫 경기 기준으로 잡았다.
    # 경기가 시작되면 그 경기가 집합에서 빠지므로 **예약 시각이 틱마다 뒤로 밀린다.**
    # 멱등키는 그대로라 알림 한 건이 하루 종일 미끄러졌다 — 실측(MLB 08-29):
    # 첫 경기 02:05인데 09:00에도 처리 대상이었고, 그때 알림은 이미 14경기 중
    # 3경기만 남은 시간표였다. 리드 -420분(시작 7시간 뒤)까지 나갈 수 있었다.
    #
    # 예약 시각이 움직이면 유예·지각 판정 자체가 무의미해진다. 상태와 무관하게
    # **그날 첫 경기**로 고정하고, 늦은 것은 is_late()가 버린다.
    by_day: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        by_day[day_schedule_scope(g)].append(g)
    for scope, gs_all in by_day.items():
        gs = [g for g in gs_all if g.status is Status.SCHEDULED]
        if not gs:
            continue                      # 전부 시작했거나 취소됐다 — 알릴 것이 없다
        at = shift_out_of_quiet_hours(
            min(x.start_utc for x in gs_all)
            - timedelta(minutes=START_ALERT_LEAD_MINUTES))
        # 지나간 예약도 큐에 남긴다. 유예(리드-5분)는 큐에 있는 항목에만 걸리므로,
        # 여기서 잘라내면 유예가 한 번도 쓰이지 않는다 — 실측 창이 235분이 아니라
        # 125분이었던 이유다. 너무 늦은 것은 is_late()가 버린다.
        if at > hi:
            continue
        if not keep_in_queue(at, now, ContentType.START_ALERT):
            continue
        items.append(QueueItem(
            # **멱등키 성분은 시간에 따라 줄어드는 집합에서 뽑지 않는다 (v1.11i).**
            # 전에는 `max(start_rev for gs)` — gs는 SCHEDULED만 남긴 집합이라
            # 경기가 하나 시작하기만 해도 최댓값이 s1→s0으로 **작아졌다.**
            # 키가 달라지니 같은 날 시작 알림이 두 번 나갔다(KBO 2026-08-29 실증).
            # 예약 시각(at)은 이미 gs_all에서 뽑고 있다 — 근거 집합을 맞춘다.
            idem_key=idem_key(channel, ContentType.START_ALERT, scope,
                              start_rev=max(x.start_rev for x in gs_all)),
            content_type=ContentType.START_ALERT, scope=scope, scheduled_utc=at,
            league=gs[0].league, sports_day=gs[0].sports_day))

    # 리그 결과 카드 — sports_day의 미종결 0건일 때. 큐에는 하드 데드라인으로 예약
    by_day: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        by_day[g.sports_day].append(g)
    for day, gs in by_day.items():
        # **마감은 '마지막 경기가 끝나는 시각'에서 잡는다 (v1.11f).**
        #
        # 전에는 `첫 경기의 UTC 자정 + 26시간`이라는 상한이 함께 걸려 있었다.
        # UTC 자정은 리그 현지 시간대를 모르는 값이라, MLB 야간 슬레이트에서는
        # **마지막 경기가 시작하는 시각**이 마감이 됐다(실측: 마지막 경기 10:40 시작,
        # 마감 11:00 KST). 그때 시계가 돌았다면 1회 진행 중인 경기를 빼고
        # '전 경기 결과'를 내보냈을 것이다 — 사실 오류다.
        #
        # 소스가 결과를 늦게 채우는 리그(NPB)를 위한 여유는 그대로 살린다.
        # 둘 중 **늦은 쪽**을 쓴다 — 마감은 '이때까지는 낸다'는 상한이지
        # 보내야 할 시각이 아니고, 실제 발송은 아래 `settled`가 앞당긴다.
        base = result_deadline(gs)
        grace = stale_grace_for(league)
        deadline = max(base, max(x.start_utc for x in gs) + timedelta(seconds=grace))
        assert_result_deadline(gs, deadline)
        # 과거 마감도 큐에 넣는다 — 소스가 늦게 채우면 마감이 지난 뒤에야 카드가 만들어진다.
        # 너무 늦은 것은 is_late()가 버리고, 이미 보낸 것은 멱등키가 막는다.
        # (전에는 `lo <= deadline` 이라 마감이 1분만 지나도 그날 결과가 통째로 사라졌다)
        if deadline > hi:
            continue
        if not keep_in_queue(deadline, now, ContentType.LEAGUE_RESULT):
            continue

        # **그날 경기가 전부 끝났으면 마감을 기다리지 않는다 (v1.11d, 대표님 지시:
        # "같은 리그 마지막 경기가 종료되고 1시간 이내에 발송").**
        #
        # 마감(deadline)은 "이때까지는 소스가 결과를 채웠을 것"이라는 상한이지
        # 보내야 할 시각이 아니다. 경기가 일찍 끝난 날에도 그 시각까지 기다리면
        # 결과가 몇 시간씩 늦는다. 전부 종결된 것을 확인했다면 더 기다릴 이유가 없다.
        #
        # 마감은 **안전망으로 남긴다** — 우천 연기 등으로 마지막 경기가 영영
        # 종결되지 않으면, 마감 시각에 그때까지의 결과로라도 내보낸다.
        # (그러지 않으면 그날 결과가 통째로 사라진다.)
        settled = league_day_settled(games, day, now)
        at = now if settled else deadline
        items.append(QueueItem(
            idem_key=idem_key(channel, ContentType.LEAGUE_RESULT,
                              f"{league.value}:{day}"),
            content_type=ContentType.LEAGUE_RESULT, scope=f"{league.value}:{day}",
            scheduled_utc=at, league=league, sports_day=day,
            render_at_utc=at - timedelta(minutes=15)))

    items.sort(key=lambda i: i.scheduled_utc)
    return items


def league_day_settled(games: list[Game], sports_day: str,
                       now: datetime | None = None) -> bool:
    """결과 카드 트리거 — '미종결 0건' **그리고** 그날이 실제로 지나갔을 것.

    **'종결'과 '지나갔다'는 다른 말이다 (v1.11i).**
    CANCELED도 is_terminal이라, 전 경기가 미리 취소된 날은 경기가 열리기 훨씬 전에
    `all(is_terminal)`이 참이 된다. 그래서 "내일 KBO 결과" 카드가 전날 밤에 나갔다
    (KBO 2026-08-28 실증: 예정보다 최소 28.5시간, 최대 30시간 이른 발송).
    취소는 '결과'가 아니라 '그날 무슨 일이 있었나'인데, 그날이 아직 오지도 않았다.

    그래서 '그날이 실제로 지나갔다'는 근거를 따로 요구한다. 근거는 둘 중 하나다.
      · 한 경기라도 **실제로 치러져 끝났다**(FINAL) — 그날은 분명히 왔다.
        (나머지가 취소라면 더 일어날 일이 없으므로 바로 내보내는 것이 맞다.)
      · 아직 아무도 뛰지 않았다면, 최소한 **첫 경기 시작 시각은 지나야** 한다.
    여기서 늦춰도 발행이 사라지지 않는다 — 마감(deadline)은 어차피 이보다 뒤이고,
    이 판정은 '마감을 안 기다리고 앞당길지'만 정하기 때문이다.
    """
    todays = [g for g in games if g.sports_day == sports_day]
    if not todays or not all(g.is_terminal for g in todays):
        return False
    if any(g.status is Status.FINAL for g in todays):
        return True
    now = now or datetime.now(timezone.utc)
    return now >= min(g.start_utc for g in todays)


# ── 2. 렌더 ────────────────────────────────────────────────

def _css() -> str:
    return re.search(r"<style>(.*?)</style>", CSS.read_text(encoding="utf-8"), re.S).group(1)


# 워터마크 3층: 대각 타일 · 중앙 로고(대표님 지시) · 노이즈(CSS ::before)
WM = ('<div class="wm">' + "<span>NUDE-TV.NET</span>" * 24 + '</div>'
      '<div class="wmc">누드TV</div>')


# 종목별 배지 아이콘. 전에는 야구공 하나를 전 리그에 썼다 —
# 농구·배구·축구·e스포츠 카드에 야구공이 박혀 나갔다(2026-08-28 육안 점검에서 발견).
_ICON_BALL = ('<circle cx="12" cy="12" r="9"/><path d="M5.5 5.5c2.2 1.8 3.5 4 3.5 6.5s-1.3 4.7-3.5 6.5'
              'M18.5 5.5c-2.2 1.8-3.5 4-3.5 6.5s1.3 4.7 3.5 6.5"/>')          # 야구
_ICON_HOOP = ('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3v18'
              'M5.6 5.6c3.5 3.5 3.5 9.3 0 12.8M18.4 5.6c-3.5 3.5-3.5 9.3 0 12.8"/>')  # 농구
_ICON_VOLLEY = ('<circle cx="12" cy="12" r="9"/><path d="M12 3a15 15 0 0 0 0 18'
                'M3.5 8.5a15 15 0 0 1 15.6 8.6M20.5 8.5A15 15 0 0 0 4.9 17.1"/>')     # 배구
_ICON_FOOT = ('<circle cx="12" cy="12" r="9"/><path d="m12 7.4 4.3 3.1-1.6 5h-5.4l-1.6-5z'
              'M12 3v4.4M4.1 9.7l4-.2M19.9 9.7l-4-.2M7.3 19.6l2-4.1M16.7 19.6l-2-4.1"/>')  # 축구
_ICON_GAME = ('<rect x="2.5" y="7" width="19" height="10" rx="4"/><path d="M7 10v4M5 12h4'
              'M15.5 11.2h.01M18 13.4h.01"/>')                                          # e스포츠

_LEAGUE_ICON = {
    League.KBO: _ICON_BALL, League.MLB: _ICON_BALL, League.NPB: _ICON_BALL,
    League.KBL: _ICON_HOOP,
    League.VLEAGUE_M: _ICON_VOLLEY, League.VLEAGUE_W: _ICON_VOLLEY,
    League.KL1: _ICON_FOOT, League.EPL: _ICON_FOOT, League.LALIGA: _ICON_FOOT,
    League.SERIEA: _ICON_FOOT, League.BUNDESLIGA: _ICON_FOOT,
    League.LIGUE1: _ICON_FOOT, League.UCL: _ICON_FOOT,
    League.LCK: _ICON_GAME, League.INTL_LOL: _ICON_GAME,
}

# 야외 종목만 우천취소가 있다. 실내·e스포츠 카드에 '취소 경기' 문구를 쓰면
# 있지도 않은 사고를 안내하는 셈이다.
OUTDOOR_LEAGUES = frozenset({
    League.KBO, League.MLB, League.NPB, League.KL1, League.EPL, League.LALIGA,
    League.SERIEA, League.BUNDESLIGA, League.LIGUE1, League.UCL,
})


def _hdr(lg: str, ink: str, pill: str, kind: str, dt: str, h1: str, sub: str = "",
         league: League = League.KBO, dt_local: str = "") -> str:
    """dt는 **한국 날짜**, dt_local은 현지 날짜 병기(다를 때만).

    MLB 현지 8/30 슬레이트는 한국시각 8/31 새벽~오전에 열린다. 헤더에 현지 날짜만
    찍으면 8월 31일에 도착한 카드가 "8.30"이라고 말해 하루 묵은 것처럼 보인다.
    """
    icon = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
            + _LEAGUE_ICON.get(league, _ICON_BALL) + '</svg>')
    dt_html = (f'<span class="dt">{esc(dt)}</span>' if not dt_local else
               f'<span class="dtw"><span class="dt">{esc(dt)}</span>'
               f'<span class="dtl">{esc(dt_local)}</span></span>')
    s = (f'<div class="strip"></div><div class="hdr"><div class="hdr-top">'
         f'<span class="pill">{icon}{esc(pill)}</span>'
         f'<span class="kind">{esc(kind)}</span>{dt_html}</div>'
         f'<h1>{h1}</h1>')
    if sub:
        s += f'<div class="sub">{esc(sub)}</div>'
    return s + "</div>"


_WD = ["월", "화", "수", "목", "금", "토", "일"]

# 카드에 찍을 리그 이름. 코드(VLEAGUE_M)를 그대로 보여줄 수는 없다.
# 카드 한 장에 담을 경기 수. 넘치는 만큼은 캡션의 접고펼치기 인용블록으로 간다.
# (MLB는 하루 15경기라 세로 한 줄로 3000px을 넘는다)
CARD_ROWS_MAX = 8


# **줄임말을 더 줄이면 리그 이름이 아니게 된다 (v1.11i).**
# "V리그 남"·"LoL 국제"는 자리를 아끼려고 낱말을 중간에서 끊은 것인데,
# 정식 명칭은 '남자부'·'국제대회'다. 카드 폭은 실렌더로 확인했고 남는다.
LEAGUE_LABEL = {
    League.KBO: "KBO", League.KBL: "KBL", League.VLEAGUE_M: "V리그 남자부",
    League.VLEAGUE_W: "V리그 여자부", League.KL1: "K리그1", League.LCK: "LCK",
    League.INTL_LOL: "LoL 국제대회", League.MLB: "MLB", League.NPB: "NPB",
    League.EPL: "EPL", League.LALIGA: "라리가", League.SERIEA: "세리에A",
    League.BUNDESLIGA: "분데스리가", League.LIGUE1: "리그1", League.UCL: "UCL",
}


# ── 포스트시즌 표기 (v1.11i) ─────────────────────────────────
# 어댑터가 `season_category`를 채워 스냅샷에 넣는데 렌더가 한 번도 읽지 않았다.
# 그래서 플레이오프 경기가 정규시즌과 똑같은 카드로 나갔다 — 시청자에게는
# 그 경기의 무게가 완전히 다른데도.
#
# **정규시즌은 표시하지 않는다.** 매일 "정규시즌"이 붙으면 아무 정보도 아니고,
# 정작 포스트시즌일 때 눈에 띄지 않는다. 모르는 값도 표시하지 않는다 —
# 소스 원문(영문 stage 코드·대회명)을 배지에 그대로 흘리지 않기 위해서다.
_POSTSEASON_TAG: dict[str, str] = {
    "PO": "플레이오프", "CP": "챔피언결정전",          # KBL
    "스플릿A": "파이널A", "스플릿B": "파이널B",         # K리그
    "승강PO": "승강 플레이오프",
    "PLAYOFFS": "플레이오프", "ROUND_OF_16": "16강",   # football-data stage
    "QUARTER_FINALS": "8강", "SEMI_FINALS": "4강", "FINAL": "결승",
    "THIRD_PLACE": "3-4위전",
}


# **영문 대회 단계도 알아본다 (v1.11j).**
# e스포츠·국제대회의 `season_category`는 영문 대회명이라("LCK 2026 Season
# Playoffs", "MSI 2026") 위 표에 하나도 안 맞고 `season_tag()`가 늘 빈 문자열을 냈다.
# 결과: LCK 플레이오프 10경기와 MSI 20경기가 **정규시즌과 똑같은 카드**로 나갔고,
# MSI 결승 "한화생명 3:2 BLG"조차 "LoL 국제대회 1경기 종료"로만 나갔다(실측 E1·E5).
# 낱말 단위로 훑되 **긴 것부터** 본다("Semifinal"이 "Final"에 먼저 걸리면 안 된다).
_EN_STAGE_TAG: tuple[tuple[str, str], ...] = (
    ("GRAND FINAL", "결승"), ("THIRD PLACE", "3-4위전"),
    ("QUARTERFINAL", "8강"), ("QUARTER FINAL", "8강"), ("QUARTER-FINAL", "8강"),
    ("SEMIFINAL", "4강"), ("SEMI FINAL", "4강"), ("SEMI-FINAL", "4강"),
    ("PLAY-IN", "플레이인"), ("PLAY IN", "플레이인"), ("PLAYIN", "플레이인"),
    ("PLAYOFF", "플레이오프"),
    ("BRACKET", "본선"),          # MSI 본선(플레이인 통과 팀들의 토너먼트)
    ("FINAL", "결승"),
)


def _en_stage(raw: str) -> str:
    """영문 문자열에서 대회 단계를 뽑는다. 모르면 빈 문자열(지어내지 않는다)."""
    u = raw.upper()
    for key, ko in _EN_STAGE_TAG:
        if key in u:
            return ko
    return ""


# 단계가 `season_category`가 아니라 `source_key`에 들어 있는 리그.
# 실측: INTL_LOL은 전 경기가 `season_category="MSI 2026"` 하나뿐이고, 단계는
# `source_key`("…_Play-In Day 1_1", "…_Bracket Round 4_1", "…_Finals_1")에 있다.
# 이 예외는 **e스포츠에만** 연다 — 다른 리그의 source_key는 숫자 ID라
# 낱말을 훑으면 엉뚱한 것이 걸린다(MLB "823539", NPB "scores-2026-0801-g-db-15").
_STAGE_IN_SOURCE_KEY = frozenset({League.LCK, League.INTL_LOL})


def _stage_of(g: Game) -> str:
    """이 경기 한 건의 구간 이름(한국어). 모르면 빈 문자열."""
    raw = (g.meta.season_category or "").strip()
    if raw in _POSTSEASON_TAG:
        return _POSTSEASON_TAG[raw]
    # KBL 코드표는 계약이 갖는다. 정규시즌(R)은 여기서 걸러진다.
    ko = KBL_SEASON_CATEGORY_ALLOW.get(raw, "")
    if ko and ko != "정규시즌":
        return ko
    # 한글 원문에 포스트시즌 낱말이 들어 있으면 그대로 쓴다(소스가 한국어인 리그).
    if any(w in raw for w in ("플레이오프", "챔피언", "결승", "준결승", "와일드카드")):
        return raw
    en = _en_stage(raw)
    if en:
        return en
    if g.league in _STAGE_IN_SOURCE_KEY:
        return _en_stage(g.source_key or "")
    return ""


def season_tag(games: list[Game]) -> str:
    """카드 배지에 덧붙일 포스트시즌 표기. 없으면 빈 문자열.

    카드 한 장의 경기가 모두 같은 구간일 때만 붙인다 — 섞인 날 한쪽 이름을
    붙이면 나머지 경기를 잘못 부른다.
    """
    tags = {_stage_of(g) for g in games}
    if len(tags) != 1:
        return ""
    return tags.pop()


def _pill(lg: League, games: list[Game] | None = None) -> str:
    """배지 문구 = 리그명 (+ 포스트시즌이면 구간)."""
    name = LEAGUE_LABEL.get(lg, lg.value)
    tag = season_tag(games) if games else ""
    return f"{name} · {tag}" if tag else name


def _reason(g: Game) -> str:
    """취소·연기 사유 한 줄. **카드와 캡션이 같은 함수를 쓴다 (v1.11i).**

    전에는 카드가 `cancel_reason or '취소'`, 캡션이 또 따로 같은 식을 써서
    상태가 POSTPONED인 경기를 카드는 "연기", 캡션은 "취소"라고 불렀다 —
    사진과 캡션은 한 메시지라 한 화면에 나란히 보인다.
    일본어 원문("中止")이 한국어 채널에 그대로 인쇄되던 것도 같은 자리다.
    """
    return cancel_reason_text(g.meta.cancel_reason, g.status)


def _reason_short(g: Game) -> str:
    """좁은 칸(결과 카드 상태 칸 `.st`, 152px)에 쓸 짧은 사유 (v1.11j).

    긴 표기를 그대로 넣었더니 `.st`가 낱말 중간에서 쪼개졌다 —
    실측(2026-08-28 KBO 결과 카드): "그라운드사정 취소"가
    "그라운드사 / 정 취소" 두 줄(높이 92px = 46px×2)로 나갔다.
    짧은 표기는 계약이 정한다(`CANCEL_REASON_SHORT_MAX = 5`). 긴 표기는
    자리가 넉넉한 캡션과 모닝 카드가 그대로 받는다 — 정보를 버리지 않는다.
    """
    return cancel_reason_text(g.meta.cancel_reason, g.status, short=True)


def assert_league_render_maps() -> None:
    """리그를 추가할 때 렌더러 매핑을 빠뜨리지 않았는지 확인한다.

    2026-08-28에 실제로 터진 것: LEAGUE_COLORS에 15개 리그 색을 정의해두고
    `_card(body)`가 리그를 안 넘겨서 **전 리그 카드가 KBO 색으로 나갔다.**
    배지 아이콘도 야구공 하나가 농구·배구·축구·e스포츠에 전부 박혔다.
    표가 있어도 쓰지 않으면 없는 것과 같으므로, 표와 사용을 같이 검사한다.
    """
    missing = {
        "LEAGUE_LABEL": [l.value for l in League if l not in LEAGUE_LABEL],
        "LEAGUE_COLORS": [l.value for l in League if l not in LEAGUE_COLORS],
        "_LEAGUE_ICON": [l.value for l in League if l not in _LEAGUE_ICON],
    }
    bad = {k: v for k, v in missing.items() if v}
    if bad:
        raise GateError(f"렌더러 매핑 누락: {bad} — 새 리그가 KBO 기본값으로 렌더됩니다.")


def _card(body: str, league: League = League.KBO) -> str:
    lg, ink = LEAGUE_COLORS[league]
    return f'<div class="card" id="card" style="--lg:{lg};--lg-ink:{ink}">{WM}{body}</div>'


def render_morning(games: list[Game], day: str,
                   top_n: int = CARD_ROWS_MAX, now: datetime | None = None) -> str:
    """오늘 편성 브리핑.

    v1.10 수정: 헤드라인이 SCHEDULED 개수를 세고 있었다. 그래서 취소가 섞이면
    '오늘 KBO 1경기 / 5경기 편성'처럼 자기모순인 카드가 나왔다(실제 채널에 나갔다).
    열리는 경기 = 취소·연기가 아닌 경기다.
    """
    # **취소와 연기는 다른 말이다 (v1.11h).** 둘을 한 낱말로 부르면
    # 연기 경기를 "취소"라고 발표하게 된다.
    off = {Status.CANCELED, Status.POSTPONED}
    playable = [g for g in games if g.status not in off]
    canceled = [g for g in games if g.status is Status.CANCELED]
    postponed = [g for g in games if g.status is Status.POSTPONED]
    dropped = canceled + postponed

    shown = sorted(games, key=lambda x: x.start_utc)[:top_n]
    wd = spans_two_kst_days(games)           # 목록이 한국 날짜 둘에 걸치면 전 행에 요일
    _wdarg = True if wd else None
    # 경기장은 전 행이 들어갈 때만 넣는다. 자리 판정은 **그려질 폭**으로 한다 —
    # 요일이 붙으면 시각 칸이 넓어져 가운데 칸이 그만큼 좁아지므로 같은 값을 넘긴다.
    venue_ok = _venues_fit(shown, _wdarg)
    rows = []
    for g in shown:
        kst, loc = format_kickoff(g, with_weekday=_wdarg)
        gone = g.status in off
        reason = _reason(g) if gone else ""
        place = _card_venue(g, _wdarg) if venue_ok else ""
        # 경기장과 사유는 서로 다른 사실이다. 붙여 쓰면 "사직 폭염취소"가
        # 한 낱말처럼 읽힌다 — 구분자를 넣는다.
        meta = esc(place)
        if gone:
            meta += (" · " if place else "") + f'<span class="hook">{esc(reason)}</span>'
        rows.append(
            f'<div class="row{" off" if gone else ""}"><div class="mt">'
            f'{esc(team_name(g.away))}<span class="sep">vs</span>'
            f'{esc(team_name(g.home))}{esc(_dh(g))}</div>'
            f'<div class="meta">{meta}'
            f'</div><div class="tm"><div class="k">{esc(kst)}</div>'
            + (f'<div class="l">현지 {esc(loc)}</div>' if loc else "") + "</div></div>")

    _lg = games[0].league if games else League.KBO
    _when = day_word_span(games, now)
    _name = esc(LEAGUE_LABEL.get(_lg, _lg.value))
    more = len(games) - len(shown)
    bits = []
    if not playable and dropped:
        # **전 경기가 취소된 날 "0경기"라고 세지 않는다 (v1.11i).**
        # 헤드라인이 "오늘 KBO 0경기", 부제가 "5경기 편성 · 5경기 취소"라
        # 같은 말을 두 번 하면서 정작 '왜'는 헤드라인에 없었다.
        h1 = f'{_when} {_name} <em>경기 없음</em>'
        for label, n in _reason_counts(dropped):
            bits.append(f"{label} {n}경기")
    else:
        # **한 카드 안에서 '편성'이 두 수를 가리키면 안 된다 (v1.11j).**
        # 실측(D2a, KBO 2026-08-30): 헤드라인 "오늘 KBO 편성 2경기" /
        # 부제 "5경기 편성 · 3경기 취소" — 같은 낱말이 2와 5를 동시에 가리켰다.
        # 낱말을 가른다: 헤드라인은 **열리는 수**('열림'), 부제는 **총계**('총 N경기').
        h1 = f'{_when} {_name} <em>{len(playable)}경기</em> 열림'
        if dropped:
            _d = []
            if canceled:
                _d.append(f"{len(canceled)}경기 취소")
            if postponed:
                _d.append(f"{len(postponed)}경기 연기")
            bits.append(f"총 {len(games)}경기 중 " + " · ".join(_d))
    if more:
        # 서술어 없는 명사구("아래에 나머지 3경기")는 안내가 아니다.
        bits.append(f"나머지 {more}경기는 아래 글에")
    # 안내 문구는 **실제로 하는 일**만 적는다. 예전에는 아직 없는 기능
    # ("예측 투표는 경기 3시간 전")을 안내하고 있었다 — 카드가 거짓말을 하면
    # 카드 전체를 못 믿게 된다.
    #
    # 기본값으로 푸터와 같은 문장을 쓰지 않는다 — 같은 카드에 같은 말이 두 번
    # 찍혀 있었다(헤드라인 아래와 푸터). 쓸 말이 없으면 비운다.
    sub = " · ".join(bits)
    lg = games[0].league if games else League.KBO
    _dtk, _dtl = kst_day_label(games, day)
    # **배지 이름을 발송 순간에 정한다 (v1.11i).** 유예가 6시간이라 최악의 날엔
    # 낮에 나가는데, 그때까지 "모닝 브리핑"이라 부르면 카드가 스스로 거짓말을 한다.
    # **열리는 경기가 0이면 오지 않을 알림을 약속하지 않는다 (v1.11j).**
    # 실측(D1a, KBO 2026-08-28 전 경기 취소): 헤드라인 "오늘 KBO 경기 없음"인데
    # 푸터·캡션 꼬리말은 "경기 시작 2시간 전 알림"이었다. 시작할 경기가 없으니
    # 그 알림은 만들어지지도 않는다 — 카드가 지키지 못할 약속을 한 것이다.
    _foot = start_alert_lead_text() if playable else ""
    body = (_hdr(*LEAGUE_COLORS[lg], _pill(lg, games),
                 morning_label(now or datetime.now(timezone.utc)),
                 _dtk, h1, sub, league=lg, dt_local=_dtl) +
            f'<div class="body">{"".join(rows)}</div>'
            f'<div class="foot"><div class="tk">{esc(_foot)}</div>'
            '<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, lg)


# 카드 한 행의 가운데 칸(.meta)은 팀명 다음에 남는 자리라 아주 좁다.
# 팀명이 긴 리그(MLB: '뉴욕양키스 vs LA에인절스')에서는 경기장이 들어갈 자리가 없어
# "에인…", "그레이트…"로 잘려 나갔다 — 잘린 이름은 정보가 아니다.
# **자리에 안 들어가면 카드에서 빼고 캡션에 남긴다.** 지어내서 줄이지 않는다.
# 값은 짐작이 아니라 **실렌더로 맞췄다.** 게이트(assert_not_clipped)가 판정한다.
# **자리는 리그마다 다르다 (v1.11i).** 해외 리그 행은 오른쪽 시각 칸에
# "현지 토 13:05" 한 줄이 더 붙어 가운데 칸이 그만큼 좁아진다.
# 실측(모닝 카드 .meta 실폭): 병기가 있는 MLB는 116~235px, 없는 NPB는 245~324px.
# 한 값으로 묶으면 국내·NPB 카드가 들어갈 수 있는 경기장까지 통째로 잃는다
# (NPB 8/1: 여섯 행 전부 잘림 없이 들어가는데 예산 때문에 여섯 개 다 빠졌다).
# 값은 짐작이 아니라 전 리그·전 날짜 실렌더로 **경계를 재서** 잡았다
# (203행을 다 그려 .meta가 …으로 잘리는 조합을 찾고, 잘림이 하나도 없는 최대 예산).
CARD_VENUE_BUDGET_LOCAL = (8, 5)    # 현지 병기가 있는 리그: (팀명 합, 경기장) 글자 수
CARD_VENUE_BUDGET_PLAIN = (8, 8)    # 병기가 없는 리그(국내·NPB)
# 옛 이름은 남겨 둔다 — 바깥에서 참조하는 검증이 있다.
CARD_VENUE_NAME_BUDGET, CARD_VENUE_MAX_LEN = CARD_VENUE_BUDGET_LOCAL

# ── 실폭으로 재는 경기장 예산 (v1.11j) ──────────────────────────────
# **글자 수 예산은 폭을 재지 못한다.** 8자·5자 상한은 "부천 종합 운동장"(9자)과
# "그레이트 아메리칸 볼파크"(13자)를 자리와 무관하게 통째로 떨어뜨렸다.
# 실측 표시율: KL1 20일 중 1일, MLB 7일 중 0일(팀명이 두 글자인 K리그 행은
# 자리가 남는데도 비어 있었다). 반대로 한글 한 글자는 영문 한 글자의 1.5배 폭이라
# 같은 글자 수라도 실제 폭은 두 배 가까이 차이 난다.
# 이제 **그려질 폭을 계산해서** 판정한다.
#
# 문자 폭 표(em) — 크로미움 `canvas.measureText` 실측(Noto Sans CJK KR).
# **글자 종류로 뭉뚱그리면 안 된다.** 영문 대문자를 평균 0.645로 잡았더니
# 'DeNA'를 121px 대신 109px로, 'KIA'를 76px 대신 85px로 봤다 — 한 행에서
# 12px이 어긋나면 경기장이 잘리거나 들어갈 것이 빠진다. 글자마다 실측값을 쓴다.
# (굵기 700/800/900 × 33/44/45px에서 재고 **가장 넓은 값**을 취했다. 편차 0.057em)
# 한글·가나·한자는 전각이라 굵기·크기와 무관하게 0.92em으로 일정했다.
_CH_EM_CJK = 0.92
_CH_EM: dict[str, float] = {
    " ": .229, "!": .397, '"': .631, "#": .609, "$": .609, "%": .986, "&": .773,
    "'": .351, "(": .400, ")": .400, "*": .529, "+": .609, ",": .351, "-": .383,
    ".": .351, "/": .387, ":": .351, ";": .351, "<": .609, "=": .609, ">": .609,
    "?": .536, "@": 1.041, "[": .400, "\\": .387, "]": .400, "^": .609, "_": .572,
    "`": .637, "{": .400, "|": .311, "}": .400, "~": .609,
    "0": .609, "1": .609, "2": .609, "3": .609, "4": .609,
    "5": .609, "6": .609, "7": .609, "8": .609, "9": .609,
    "A": .660, "B": .695, "C": .667, "D": .729, "E": .630, "F": .604, "G": .733,
    "H": .774, "I": .350, "J": .586, "K": .708, "L": .598, "M": .877, "N": .764,
    "O": .786, "P": .687, "Q": .786, "R": .708, "S": .639, "T": .640, "U": .763,
    "V": .643, "W": .935, "X": .657, "Y": .608, "Z": .619,
    "a": .606, "b": .658, "c": .537, "d": .658, "e": .596, "f": .398, "g": .616,
    "h": .658, "i": .321, "j": .323, "k": .634, "l": .332, "m": .985, "n": .658,
    "o": .637, "p": .658, "q": .658, "r": .464, "s": .510, "t": .445, "u": .654,
    "v": .607, "w": .897, "x": .598, "y": .604, "z": .532,
}


def _text_px(s: str, fs: float, tracking: float = -0.005) -> float:
    """이 글자열이 `fs`px로 그려질 때의 폭(px) 추정.

    `tracking`은 `letter-spacing`(em). 카드는 `.card`에 -0.005em,
    `.row .mt`·`.row .tm .k`에 -0.02em을 준다. 빼먹으면 한 행에서 8~9px이 어긋나
    (실측: `.mt` 355px를 363px로 봤다) 들어갈 경기장을 떨어뜨린다.
    """
    w = sum(_CH_EM.get(ch, _CH_EM_CJK) for ch in s)
    return (w + tracking * len(s)) * fs


# 모닝 행의 치수. 전부 `cards/v4.html`에 적힌 값이다 — 여기서 지어낸 수가 아니다.
# (`.body`는 카드 폭에서 좌우 `--pad`를 뺀 만큼, `.row`는 padding 34 · gap 20,
#  `.mt`는 padding-right 24 · `.sep` 31px에 좌우 margin 11, `.tm`은 padding-left 20)
_ROW_MT_FS, _ROW_SEP_FS, _ROW_META_FS = 44, 31, 33
_ROW_K_FS, _ROW_L_FS = 45, 28
_CARD_SIDE_PAD = 96
_ROW_PAD, _ROW_GAP = 34, 20
_MT_PAD_R, _SEP_MARGIN, _TM_PAD_L = 24, 11, 20
# 추정과 실렌더의 오차분. 글자별 실측표 + 자간까지 넣은 뒤, 데이터에 있는
# 팀명·구장명·시각 문자열 전수(약 400개)에 대해 **과소추정이 0px**이었다
# (과대추정만 최대 3.5px — 안전한 쪽이다). 그래서 여유는 1px이면 충분하다.
# 최종 판정은 잘림 게이트(assert_not_clipped)가 한다 — 이 값은 그 앞의 여유다.
# 실측 표시일: KBO 52/52 · KL1 20/20 · NPB 49/56 · V리그 54/126 · MLB 0/7
# (MLB는 팀명이 길고 현지 시각 병기까지 붙어 정말로 자리가 없다 — 캡션이 받는다.)
_VENUE_SAFETY_PX = 1
_TRACK_TIGHT = -0.02        # .row .mt · .row .tm .k
_TRACK_CARD = -0.005        # .card 전역(.meta · .tm .l이 물려받는다)


def _mt_px(g: Game) -> float:
    """이 행의 팀명 칸(.mt) 폭(px). 이름 두 개 + 'vs' + 좌우 여백 + 오른쪽 패딩."""
    return (_text_px(team_name(g.away), _ROW_MT_FS, _TRACK_TIGHT)
            + _text_px(team_name(g.home) + _dh(g), _ROW_MT_FS, _TRACK_TIGHT)
            + _text_px("vs", _ROW_SEP_FS, _TRACK_TIGHT) + _SEP_MARGIN * 2 + _MT_PAD_R)


def _tm_px(g: Game, with_weekday: bool | None = None) -> float:
    """이 행의 시각 칸(.tm) 폭(px). 현지 병기가 있으면 그쪽이 더 넓다."""
    kst, loc = format_kickoff(g, with_weekday=with_weekday)
    return _TM_PAD_L + max(_text_px(kst, _ROW_K_FS, _TRACK_TIGHT),
                           _text_px(f"현지 {loc}", _ROW_L_FS, _TRACK_CARD) if loc else 0.0)


def _row_columns(rows: list[Game], with_weekday: bool | None = None) -> tuple[float, float, float]:
    """(팀명 칸, 시각 칸, 남는 가운데 칸) 폭 — 넘긴 행들 중 가장 넓은 것 기준.

    `.row`의 격자는 `auto minmax(0,1fr) auto`라 가운데 칸(.meta)은
    **남는 자리**다. 그래서 자리는 행마다 다르고, 판정도 행마다 해야 한다.
    (칸 폭을 카드 한 장에 고정해 보았더니 가장 긴 이름 행에 전 행이 맞춰져
     NPB 경기장 표시일이 28일→19일로 오히려 줄었다. 정렬은 `.meta`를
     오른쪽 정렬해 해결했다 — 오른쪽 끝은 시각 칸이 맞춰 준다.)
    """
    mt = max((_mt_px(g) for g in rows), default=0.0)
    tm = max((_tm_px(g, with_weekday) for g in rows), default=0.0)
    body_w = CARD_WIDTH_PX - _CARD_SIDE_PAD * 2
    return mt, tm, body_w - _ROW_PAD * 2 - _ROW_GAP * 2 - mt - tm


def _venue_need_px(g: Game) -> float:
    """이 행의 가운데 칸에 들어가야 할 폭(경기장 + 취소 사유)."""
    v = venue_name(g.venue)
    if not v:
        return 0.0
    # 취소·연기 행은 같은 칸에 사유도 함께 들어간다("잠실 · 우천취소").
    # 그 폭을 빼놓으면 그 행만 잘린다 — 잘린 이름은 정보가 아니다.
    extra = (_text_px(" · " + _reason(g), _ROW_META_FS, _TRACK_CARD)
             if g.status in (Status.CANCELED, Status.POSTPONED) else 0.0)
    return _text_px(v, _ROW_META_FS, _TRACK_CARD) + extra


def _venues_fit(shown: list[Game], with_weekday: bool | None = None) -> bool:
    """경기장을 **전 행에** 넣을 수 있는가.

    **판정은 행이 아니라 카드 단위로 한다 (v1.11i).**
    예산을 행마다 따로 보니 MLB 모닝 카드 8행 중 1행에만 경기장이 들어갔다.
    한 장 안에서 어떤 줄엔 있고 어떤 줄엔 없으면, 읽는 사람은 그 차이를
    '정보가 없는 경기'로 오해한다. 전부 못 넣으면 전부 빼고 캡션에 남긴다 —
    캡션에는 자리가 넉넉해서 하나도 잃지 않는다.
    """
    have = [g for g in shown if venue_name(g.venue)]
    return bool(have) and all(_card_venue(g, with_weekday) for g in have)


def _card_venue(g: Game, with_weekday: bool | None = None) -> str:
    """카드 행에 넣을 경기장. **그려질 폭이 자리에 안 들어가면** 빈 문자열.

    자리는 그 행의 실제 칸이다 — 팀명 칸은 `auto`라 행마다 다르다.
    (칸을 카드 단위로 고정해 보았더니 가장 긴 이름 행에 전 행이 맞춰져
     NPB 표시일이 28일→19일로 오히려 줄었다. 정렬은 `.meta`를 오른쪽 정렬해
     해결하고, 자리 계산은 행 단위로 둔다.)
    """
    v = venue_name(g.venue)
    if not v:
        return ""
    _, _, meta = _row_columns([g], with_weekday)
    return v if _venue_need_px(g) + _VENUE_SAFETY_PX <= meta else ""


def _reason_counts(games: list[Game]) -> list[tuple[str, int]]:
    """(사유, 건수) 목록. 많은 순. 전 경기 취소일 부제에 쓴다."""
    c: dict[str, int] = {}
    for g in games:
        r = _reason(g)
        c[r] = c.get(r, 0) + 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def spans_two_kst_days(games: list[Game]) -> bool:
    """이 목록이 한국 날짜 둘 이상에 걸치는가.

    `format_kickoff`는 KST와 현지 날짜가 다를 때만 요일을 붙인다. 그래서 유럽
    주말 슬레이트에서는 **같은 목록 안에서 어떤 줄만 요일이 있었다** —
    요일이 붙었다 말았다 하면 요일 자체가 신호가 아니라 잡음이 된다.
    한국 날짜가 둘 이상이면 전 행에 요일을 붙여 어느 날 경기인지 줄마다 밝힌다.
    """
    return len({g.start_kst.date() for g in games}) > 1


def _dh(g: Game) -> str:
    """더블헤더 표시. 같은 대진이 하루 두 번 나오면 오보로 보인다.

    실측(MLB 2026-08-29): "보스턴 6:0 뉴욕양키스"와 "보스턴 2:9 뉴욕양키스"가
    같은 캡션에 나란히 찍혔다. 어댑터는 `gameNumber`를 뽑아 두는데
    스냅샷이 버리고 렌더도 안 썼다(둘 다 v1.11h에서 고침).

    **1차전에도 붙인다 (v1.11i 육안검수).** 전에는 2차전에만 붙였는데,
    그러면 위쪽 줄이 1차전이라는 근거가 카드 어디에도 없다 — 독자가
    "목록이 시간순일 것"이라고 추론해야 알 수 있고, 정렬 규칙은 카드에 안 적혀 있다.
    한쪽만 표시하는 것은 표시가 아니라 수수께끼다.
    """
    n = g.meta.doubleheader_seq
    return f" ({n}차전)" if n and n >= 1 else ""


def _day_word_at(first, now_kst) -> str:
    """한 시각(KST)에 대한 '오늘/내일 아침/…' 낱말. day_word·day_word_span이 함께 쓴다."""
    days = (first.date() - now_kst.date()).days
    if days < 0:
        # 지난 날짜다. '오늘'이라 하면 하루 묵은 카드가 오늘 것으로 보인다.
        return f"{first.month}.{first.day} {_WD[first.weekday()]}"
    if days == 0:
        return "오늘"
    if days == 1:
        if first.hour < 6:
            return "내일 새벽"
        return "내일 아침" if first.hour < 12 else "내일"
    return f"{first.month}.{first.day} {_WD[first.weekday()]}"


def day_word(games: list[Game], now: datetime | None = None) -> str:
    """'오늘 / 내일 아침 / 내일 새벽 / 9.3 수' — **보내는 순간** 기준으로 고른다.

    **날짜 표기를 한 가지로 통일한다 (v1.11i).** 헤더는 `8.30 일`, 헤드라인은
    `9월 3일`, 병기는 `현지 8.29`라 한 카드에 날짜 표기가 세 가지였다.
    기준은 헤더와 같은 `M.D 요일`이다.

    **'오늘'을 문자열로 박아두면 리그마다 다른 방식으로 거짓말한다 (v1.11f).**
      · 시작 알림: 한국시각 밤 10시에 나가는데 첫 경기는 다음날 새벽 1시였다.
      · 모닝 브리핑: 07:30에 나가는데 MLB 슬레이트는 **다음날 아침** 07:40 시작이다
        (미국 현지 날짜로 묶기 때문). "오늘 MLB 15경기"는 하루를 착각하게 만든다.
    둘 다 같은 병이라 판정을 한 군데로 모은다. 카드·캡션·알림이 전부 이걸 쓴다.
    """
    now = now or datetime.now(timezone.utc)
    if not games:
        return "오늘"
    return _day_word_at(min(g.start_utc for g in games).astimezone(KST),
                        now.astimezone(KST))


def day_word_span(games: list[Game], now: datetime | None = None) -> str:
    """한국 날짜 둘에 걸친 슬레이트를 한 낱말로 부르지 않는다 (v1.11i).

    유럽 주말 슬레이트는 한국시각 토요일 밤과 일요일 새벽에 걸쳐 열린다.
    그 목록 전체를 "오늘"이라 부르면 절반이 틀린 말이 된다. 걸치면 `오늘~내일`처럼
    양 끝을 함께 적는다 — 목록의 각 줄에는 `spans_two_kst_days()`가 요일을 붙인다.

    **같은 날 안에서는 첫 경기 하나로 정하지 않는다 (v1.11j).**
    전에는 한국 날짜가 같으면 `첫 경기`의 낱말을 그대로 썼다. 그래서
    실측(D4d, MLB 2026-09-04): 16경기 중 15경기가 07:10 이후인데 03:10짜리
    한 경기 때문에 "내일 새벽 16경기"가 됐다. 08-31·09-01은 실제로 같은 모양인데
    "내일 아침"이라, 같은 슬레이트가 날마다 다른 이름으로 불렸다.
    이제 **다수 경기가 속한 시간대**로 고르고, 뚜렷한 다수가 없으면 범위로 말한다.
    (한국 날짜가 둘에 걸치는 날은 다수결로 뭉개지 않는다 — 날짜는 사실이고
     새벽/아침은 묘사다. `오늘~내일`은 그대로 유지된다.)
    """
    now = now or datetime.now(timezone.utc)
    if not games:
        return "오늘"
    nk = now.astimezone(KST)
    starts = sorted(g.start_utc.astimezone(KST) for g in games)
    first, last = starts[0], starts[-1]
    a = _day_word_at(first, nk)
    b = _day_word_at(last, nk)
    if first.date() != last.date():
        # 날짜가 갈리면 양 끝을 함께 적는다 (기존 동작).
        return a if a == b else f"{a}~{b}"
    if a == b:
        return a
    words = [_day_word_at(s, nk) for s in starts]
    top = max(set(words), key=words.count)
    if words.count(top) * 3 >= len(words) * 2:      # 2/3 이상이면 그 낱말로 부른다
        return top
    # 뚜렷한 다수가 없으면 범위로. 같은 날이므로 앞머리('내일')는 한 번만 적는다.
    for pre in ("내일 ", "오늘 "):
        if a.startswith(pre) and b.startswith(pre):
            return f"{a}~{b[len(pre):]}"
    return f"{a}~{b}"


def _name_cls(name: str) -> str:
    """긴 팀명을 한 줄에 담기 위한 크기 클래스.

    국내 표기를 쓰면 '세인트루이스'(6자)·'샌프란시스코'(7자)가 나온다.
    그대로 두면 결과 카드에서 두 줄로 접혀 행 높이가 무너진다
    (2026-08-30 육안 점검에서 발견). 이름을 줄이는 대신 크기를 낮춘다 —
    시청자가 쓰는 이름을 바꾸는 것보다 글자를 조금 작게 하는 편이 낫다.
    """
    n = len(name)
    return " n6" if n == 6 else (" n7" if n >= 7 else "")


# 결과 카드에 실을 수 있는 상태 — **결과가 확정된 것만**.
# LIVE(진행 중)·SCHEDULED는 결과가 아니다. 이 구분이 없어서 2026-09-01에
# 진행 중인 KBO 5경기가 "0:0 무승부 종료"로 카드에 실려 채널에 나갔다.
SETTLED_FOR_RESULT = frozenset({Status.FINAL, Status.CANCELED})


def render_result(games: list[Game], day: str,
                  top_n: int = CARD_ROWS_MAX) -> str:
    """그날 결과 카드.

    **결과가 확정된 경기만 싣는다.** 진행 중 경기를 빼는 것이 아니라,
    애초에 결과가 아닌 것을 결과라고 부르지 않는 것이다.
    보통은 `league_day_settled()`가 전부 끝난 뒤에만 이 카드를 만들지만,
    우천 연기 등으로 영영 종결되지 않는 경기가 있으면 마감 시각에 그때까지의
    결과로 내보낸다 — 그때 빠진 경기 수를 카드에 **명시한다**.
    """
    # **점수 없는 FINAL은 '종료'로 세지 않는다.** 소스가 Final인데 점수를 빼면
    # 그 경기는 행에도 없고 미확정에도 없어 카드에서 통째로 사라졌다 —
    # 헤드라인만 "N경기 종료"로 남아 숫자와 본문이 어긋났다.
    fin = [g for g in games if g.status is Status.FINAL and g.score]
    canceled = [g for g in games if g.status is Status.CANCELED]
    # **'실을 수 있는 결과'와 '상태가 종결됨'은 다르다 (v1.11j).**
    # `done`을 상태(FINAL·CANCELED)로만 잡으니 점수 없는 FINAL이 `done`에 들어가
    # 자리(top_n)와 "나머지 N경기" 계산은 먹으면서 정작 행은 그리지 못했다.
    # 실측(S2, KBO 2026-09-01 5경기 중 1건의 점수를 지움): 카드는 "4경기 종료"에
    # 본문 4행뿐이라 그 경기를 어디에서도 언급하지 않았고, 같은 메시지의 캡션은
    # 5줄(그중 "롯데 vs 삼성 · 결과 미확정")이라 사진과 글이 그날 경기 수를
    # 다르게 말했다. 점수 없는 FINAL은 **결과가 아직 없는 경기**로 센다.
    def _has_result(g: Game) -> bool:
        return (g.status is Status.FINAL and bool(g.score)) or g.status is Status.CANCELED

    done = [g for g in games if _has_result(g)]
    pending = [g for g in games if not _has_result(g)]
    # **결과가 하나도 없으면 결과 카드를 만들지 않는다 (v1.11i).**
    # 취소 1건만 있고 나머지가 진행 중이면 "MLB 전 경기 결과 0경기" 아래
    # 16줄이 전부 "결과 미확정"인 카드가 만들어졌다 — 그 카드는 아무 사실도
    # 전하지 않으면서 '결과가 나왔다'고 말한다. 만들지 않는 것이 옳다.
    if not fin and not canceled:
        raise GateError(
            "결과 카드: 종결 0건 · 취소 0건 — 아직 전할 결과가 없습니다 "
            f"({games[0].league.value if games else '?'} {day}, 미확정 {len(pending)}건)")
    shown = sorted(done, key=lambda x: x.start_utc)[:top_n]
    rows = []
    # 상태 칸에 적을 것이 한 행도 없으면 칸 자체를 없앤다 — 152px이 통째로
    # 빈 여백으로 남아 점수 덩어리가 카드 왼쪽으로 쏠렸다(전 행 '종료'를 뺀 뒤).
    need_st = False
    for g in shown:
        a, h = esc(team_name(g.away)), esc(team_name(g.home))
        # **더블헤더 차전은 결과 카드 본문에도 붙인다 (v1.11i).**
        # `_dh()`가 캡션에서만 쓰여, 같은 대진이 카드에 두 줄로 나란히 찍혔다
        # (v1.11h가 고쳤다고 한 사고가 카드 본문에는 그대로 남아 있었다).
        # **다만 팀명 칸에 붙이면 안 된다** — 이름 칸은 nowrap 1fr이라 글자가
        # 늘어난 만큼 격자가 밀려 오른쪽 상태 칸이 카드 밖으로 나갔다(실측 59px).
        # 행에 대한 주석은 상태 칸(.st)의 일이다.
        dh = _dh(g).strip(" ()")
        note = f'<span class="dhq">{esc(dh)}</span>' if dh else ""
        if dh:
            need_st = True
        if g.status is Status.CANCELED:
            need_st = True
            # 상태 칸은 152px 고정이다 — 긴 표기는 낱말 중간에서 쪼개진다.
            # 짧은 표기를 쓰고(`_reason_short`), 캡션이 긴 표기를 받는다.
            rows.append(f'<div class="res cx"><div class="n1{_name_cls(team_name(g.away))}">{a}</div>'
                        f'<div class="s1">—</div>'
                        f'<div class="s2">—</div>'
                        f'<div class="n2{_name_cls(team_name(g.home))}">{h}</div>'
                        f'<div class="st">{note}{esc(_reason_short(g))}</div></div>')
        elif g.status is Status.FINAL and g.score:
            draw = g.is_draw()
            cls = "dr" if draw else ("w1" if g.score.away > g.score.home else "w2")
            # **평범한 '종료'는 적지 않는다 (v1.11j).** 헤드라인이 이미
            # "N경기 종료"라 전 행에 같은 낱말이 반복되면 정보가 0인데,
            # 정작 눈에 띄어야 할 "무승부"·"우천취소"의 대비만 깎였다.
            # 상태 칸은 **예외를 적는 자리**로 둔다.
            st = "무승부" if draw else ""
            if st:
                need_st = True
            rows.append(f'<div class="res {cls}">'
                        f'<div class="n1{_name_cls(team_name(g.away))}">{a}</div>'
                        f'<div class="s1">{g.score.away}</div><div class="s2">{g.score.home}</div>'
                        f'<div class="n2{_name_cls(team_name(g.home))}">{h}</div>'
                        f'<div class="st">{note}{st}</div></div>')
    lg = games[0].league if games else League.KBO
    lgname = LEAGUE_LABEL.get(lg, lg.value)
    # **실제로 하지 않는 일을 안내하지 않는다 (v1.11h).**
    # "취소 경기는 편성 확정 시 안내"가 야외 리그 결과 카드에 무조건 박혀 있었다.
    # 재편성을 안내하는 콘텐츠도 큐도 발송 경로도 없다 —
    # "예측 투표는 경기 3시간 전"과 같은 계열의 거짓말이다.
    # 대신 점수의 **단위**를 밝힌다: LCK 0:3이 맵 스코어라는 표시가 없었다.
    tk = f"{lgname} 공식 결과"
    _unit = SCORE_UNIT_BY_LEAGUE.get(lg)
    _unit_ko = {ScoreUnit.MAPS: "맵 스코어", ScoreUnit.SETS: "세트 스코어"}.get(_unit)
    if _unit_ko:
        tk += f" · {_unit_ko}"
    _dtk, _dtl = kst_day_label(games, day)
    _bits = []
    if len(done) > len(shown):
        _bits.append(f"나머지 {len(done) - len(shown)}경기는 아래 글에")
    # **'결과 미확정'은 상태 이름이 아니다 (v1.11i).**
    # SETTLED_FOR_RESULT가 FINAL·CANCELED뿐이라 연기·서스펜디드가 전부
    # "결과 미확정"으로 뭉뚱그려졌다. 연기는 결과를 기다리는 상태가 아니라
    # 그날 열리지 않은 것이고, 서스펜디드는 속개가 예정된 것이다.
    _post = [g for g in pending if g.status is Status.POSTPONED]
    _susp = [g for g in pending if g.status is Status.SUSPENDED]
    # 점수 없는 FINAL도 여기로 온다 — '종료'라고 세면 카드에 없는 경기를 센 것이 된다.
    _unk = [g for g in pending
            if g.status not in (Status.POSTPONED, Status.SUSPENDED)]
    # **헤드라인이 이미 말한 것을 부제가 되풀이하지 않는다 (v1.11j).**
    # 실측(S4): 헤드라인 "취소 1경기 · 나머지 진행 중" / 부제 "1경기 취소 ·
    # 4경기는 아직 결과 미확정" — 어순만 뒤집은 반복이었다.
    # 취소만 확정된 날은 취소 수를 헤드라인이 갖고, 부제는 **남은 경기의 내역**만 센다.
    _cancel_in_h1 = not fin and bool(canceled)
    if canceled and not _cancel_in_h1:
        # 모닝 카드는 취소를 밝히는데 결과 카드는 안 밝혔다 —
        # "3경기 종료"인데 본문이 5행이면 읽는 사람이 센 수와 안 맞는다.
        _bits.append(f"{len(canceled)}경기 취소")
    if _post:
        _bits.append(f"{len(_post)}경기 연기")
    if _susp:
        _bits.append(f"{len(_susp)}경기 서스펜디드(속개 예정)")
    if _unk:
        # 빠진 경기를 숨기지 않는다. 숨기면 '전 경기 결과'가 거짓이 된다.
        _bits.append(f"{len(_unk)}경기는 아직 결과 미확정")
    if _cancel_in_h1:
        # 전 경기가 취소된 날인지, 취소만 확정되고 나머지는 진행 중인지는
        # 완전히 다른 사실이다. 진행 중이 남았는데 '전 경기 취소'라 하면 거짓이다.
        _h1 = (f'{esc(lgname)} <em>전 경기 취소</em>' if not pending
               else f'{esc(lgname)} <em>취소 {len(canceled)}경기</em> 확정')
    else:
        _h1 = f'{esc(lgname)} <em>{len(fin)}경기</em> 종료'
    # **결과가 하나도 없는 카드는 배지·푸터가 결과를 있다고 말하지 않는다 (v1.11j).**
    # 실측(S4): 헤드라인이 "취소 1경기 · 나머지 진행 중"인데 배지는 "경기 결과",
    # 푸터는 "KBO 공식 결과"였다 — 카드 안에 결과가 한 줄도 없는데도.
    _kind = "경기 결과"
    if not fin:
        _kind = "취소 안내"
        tk = f"{lgname} 공식 발표"
    body = (_hdr(*LEAGUE_COLORS[lg], _pill(lg, games), _kind,
                 _dtk, _h1,
                 " · ".join(_bits),
                 league=lg, dt_local=_dtl) +
            f'<div class="body{"" if need_st else " nost"}">{"".join(rows)}</div>'
            f'<div class="foot"><div class="tk">{esc(tk)}</div>'
            '<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, lg)



def render_png(card_html: str, out: pathlib.Path) -> tuple[int, int, int]:
    from playwright.sync_api import sync_playwright
    from PIL import Image
    html = ("<!DOCTYPE html><html><head><meta charset='UTF-8'><style>"
            + _css() + EXTRA_CSS + "</style></head><body>" + card_html + "</body></html>")
    out = out.resolve()
    # 출력 폴더는 여기서 만든다. 호출자마다 따로 챙기게 하면 반드시 하나는 빠뜨린다 —
    # 실제로 배포본을 다른 폴더에서 돌렸더니 `dryrun/`이 없어 카드 렌더가 전부 죽었다.
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html")
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1200, "height": 900})
        pg.goto(f"file://{tmp}")
        pg.wait_for_timeout(900)
        samples = pg.evaluate(_TYPO_JS)
        font = pg.evaluate(_FONT_JS)
        pg.query_selector("#card").screenshot(path=str(out))
        b.close()
    # 폰트부터 본다. 두부 카드는 다른 어떤 검사를 통과해도 내보낼 수 없다.
    assert_korean_font(font)
    assert_no_wrapped_names(samples)
    assert_not_clipped(samples)
    assert_within_card(samples)
    assert_card_typography([(x["t"], x["fs"], x["cls"]) for x in samples])
    im = Image.open(out).convert("RGB")
    w, h = im.size
    assert_card_geometry(w, h)                       # 게이트: 폭·높이·세로비
    jpg = out.with_suffix(".jpg")
    im.save(jpg, "JPEG", quality=SEND_JPEG_QUALITY,
            subsampling=SEND_JPEG_SUBSAMPLING, optimize=True)
    return w, h, jpg.stat().st_size


# ── 3. 텍스트 알림 (인용블록) ────────────────────────────────

# 종목별 이모지. 전에는 야구 이모지 하나가 전 리그 알림에 박혀 나갔다
# (카드 배지에 야구공이 박혀 있던 것과 같은 계열의 결함).
LEAGUE_EMOJI = {
    League.KBO: "⚾", League.MLB: "⚾", League.NPB: "⚾",
    League.KBL: "🏀", League.VLEAGUE_M: "🏐", League.VLEAGUE_W: "🏐",
    League.KL1: "⚽", League.EPL: "⚽", League.LALIGA: "⚽", League.SERIEA: "⚽",
    League.BUNDESLIGA: "⚽", League.LIGUE1: "⚽", League.UCL: "⚽",
    League.LCK: "🎮", League.INTL_LOL: "🎮",
}


# '곧'이라 부를 수 있는 한계. 앞창(LOOKAHEAD 2.5시간)보다 조금 넉넉하게 잡는다 —
# 정상 발송은 전부 이 안쪽이고, 이걸 넘는 것은 심야 회피로 밀려난 메시지뿐이다.
START_ALERT_SOON_MAX_MINUTES = 180


def render_start_alert(gs: list[Game], now: datetime | None = None,
                       all_games: list[Game] | None = None) -> str:
    """오늘 그 리그의 **경기 시간표 전체**를 한 메시지로 (v1.11c).

    전에는 같은 시각 경기만 묶어 시각마다 따로 보냈고, 실측 하루 26건이 나왔다.
    이제 하루 한 번이므로 이 한 통에 그날 정보가 다 들어가야 한다 —
    시각별로 묶어 보여주고, 첫 경기까지 남은 시간을 계산해 붙인다.

    `gs`는 **아직 시작 안 한 경기**만 들어온다(발송 경로가 SCHEDULED만 넘긴다).
    그래서 이 수를 "오늘 N경기"라고 부르면 이미 시작한 경기가 빠진 수가
    그날 편성 수로 둔갑한다(실측: 편성 5경기인데 "오늘 KBO 3경기").
    문형을 '곧 시작하는 수'로 바꾸고, 그날 전체를 알 수 있으면(`all_games`)
    '5경기 중 3경기'로 밝힌다.
    """
    now = now or datetime.now(timezone.utc)
    lg = gs[0].league
    ordered = sorted(gs, key=lambda x: x.start_utc)
    first = ordered[0].start_utc
    # 목록이 한국 날짜 둘에 걸치면 **전 줄에** 요일을 붙인다 — 어떤 줄만 요일이
    # 있으면 요일이 신호가 아니라 잡음이 된다(유럽 주말 슬레이트).
    _wd = True if spans_two_kst_days(ordered) else None

    # 같은 시각 경기는 한 줄 아래 모은다. 15경기가 15줄이면 읽히지 않는다.
    # **그룹 키에 요일을 포함한다 (v1.11h).** `%H:%M`만 쓰면 한국 날짜가
    # 다른 두 경기가 한 줄로 합쳐지고, 카드는 "일 01:30"이라 쓰는데
    # 알림만 "01:30"이라 같은 사실을 다르게 말한다(유럽 주말 슬레이트).
    by_time: dict[str, list[Game]] = defaultdict(list)
    for g in ordered:
        _k, _ = format_kickoff(g, with_weekday=_wd)
        by_time[_k].append(g)

    lines: list[str] = []
    for t, group in by_time.items():
        lines.append(f"◆ {esc(t)}")
        for g in group:
            row = (f"  {esc(team_name(g.away))} vs "
                   f"{esc(team_name(g.home))}{esc(_dh(g))}")
            if g.venue:
                row += f" · {esc(venue_name(g.venue))}"
            lines.append(row)

    mins = int((first - now).total_seconds() // 60)
    if mins >= 60:
        when = (f"{mins // 60}시간 {mins % 60}분 뒤" if mins % 60
                else f"{mins // 60}시간 뒤")
    elif mins >= 1:
        when = f"{mins}분 뒤"
    else:
        when = "잠시 뒤"

    # 현지 시간 병기가 필요한 리그(해외)는 첫 경기 기준으로 한 줄 덧붙인다.
    # **주 표기(KST)에도 요일을 붙인다 (v1.11i).** 전에는 꼬리말만 `%H:%M`을
    # 직접 찍어 "첫 경기 03:10 시작 · 현지 금 14:10"이 됐다 —
    # 요일이 붙은 현지 시각만 보이고 정작 우리 시각은 어느 날인지 알 수 없었다.
    kst_first, loc = format_kickoff(ordered[0], with_weekday=_wd)
    tail = (f"\n첫 경기 {esc(kst_first)} 시작 ({esc(when)})"
            + (f" · 현지 {esc(loc)}" if loc else ""))

    head_when = day_word_span(ordered, now)

    emoji = LEAGUE_EMOJI.get(lg, "🏟")
    label = LEAGUE_LABEL.get(lg, lg.value)
    _total = len(all_games) if all_games else 0
    # **'곧'과 '9시간 뒤'가 한 메시지 안에서 부딪치면 안 된다 (v1.11j).**
    # 심야 회피(quiet hours)로 22:00에 밀려 나가는 MLB 알림이 실측(I1)에서
    # "⚾ 내일 아침 MLB 12경기 곧 시작 … 첫 경기 화 07:05 시작 (9시간 5분 뒤)"였다.
    # 앞창이 2.5시간이라 정상 발송은 늘 3시간 안쪽이다 — 그보다 멀면 이 메시지는
    # '임박 알림'이 아니라 '시간표'다. 문형을 그때 바꾼다.
    _verb = "곧 시작" if mins <= START_ALERT_SOON_MAX_MINUTES else "시간표"
    _count = (f"{_total}경기 중 {len(gs)}경기 {_verb}" if _total > len(gs)
              else f"{len(gs)}경기 {_verb}")
    head = f"{emoji} <b>{head_when} {esc(label)} {_count}</b>\n"
    # 경기가 많으면(MLB 15경기) 접고펼치기로 채널 스크롤을 아낀다
    return head + quote(lines, expandable=len(lines) > QUOTE_EXPANDABLE_THRESHOLD_LINES) + tail


# ── 4. 드라이런 발송 ─────────────────────────────────────────

def dryrun_send(item: QueueItem, payload: dict) -> dict:
    """채널 발송 대신 산출물을 남긴다. 실발송과 동일한 게이트를 통과시킨다."""
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"idem_key": item.idem_key, "content_type": item.content_type.value,
           "scheduled_kst": item.scheduled_utc.astimezone(KST).isoformat(),
           "scope": item.scope, **payload}
    (OUT / "ledger.jsonl").open("a", encoding="utf-8").write(
        json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ── 5. 기록·순위 카드 (v1.10) ────────────────────────────────
#
# 여기 들어가는 모든 문장은 RecordBook의 필드를 그대로 치환한 것이다.
# 형용사·추측·LLM 작문 금지 — 사실 잠금 원칙.

from contract import (TEAM_NAMES, WLD, LeaderEntry, RecordBook, Standing,  # noqa: E402
                      StreakKind, assert_recordbook)

# 순위표를 9열로 넓히기 위한 변형. 최소 폰트 28px 규칙은 지킨다.
#
# v1.11i에서 두 가지가 되살아났다. 둘 다 **카드 CSS 파일이 아니라 여기**에 둔다 —
# 렌더가 만드는 마크업과 짝이 맞아야 하는 규칙이라 같은 파일에 있어야 안 어긋난다.
#
#  ① 점수 구분자 — 카드는 `지바롯데 5 3 니혼햄`처럼 구분자 없이 두 숫자를 붙여
#     내보내 어느 쪽이 몇 점인지 한눈에 안 잡혔다(같은 사실을 캡션은 `1 : 15`로
#     썼다). 취소 행(`— —`)에는 붙이지 않는다.
#  ② 상대전적 막대의 무승부 몫 — 막대를 두 칸(승/무)으로 나누려면 가로로 쌓여야 한다.
EXTRA_CSS = """
.res:not(.cx) .s1{text-align:right;position:relative;padding-right:20px;white-space:nowrap;}
.res:not(.cx) .s1::after{content:":";position:absolute;top:0;left:100%;
  transform:translateX(-50%);color:var(--dim);font-weight:800;}
.res:not(.cx) .s2{text-align:left;padding-left:20px;white-space:nowrap;}
.res .st .dhq{display:block;font-size:28px;font-weight:800;color:var(--dim);
  line-height:1.15;}
.brow .bar{display:flex;}
.brow .fill{flex:0 0 auto;border-radius:18px 0 0 18px;}
.brow .fdraw{flex:0 0 auto;height:100%;background:#c8d2e0;}
"""


def _dt(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.month}.{d.day} {_WD[d.weekday()]}"


def _wld(w: WLD, three: bool | None = None) -> str:
    """전적 표기. `three`가 주어지면 **표 전체에서 형식을 통일한다.**

    전에는 행마다 `w.draw`를 보고 정해서, 한 열에 '6-3-1'과 '8-2'가 섞였다.
    푸터 범례는 표 하나에 하나뿐이라 "8-2"가 8승2패인지 8승2무인지 알 수 없었다.
    형식은 **열 단위로** 정해야 한다.

    MLB처럼 무승부가 없는 리그에 '83-51-0'을 쓰면 그 한 글자 때문에
    순위표 열이 줄바꿈으로 깨진다 — 그래서 리그 단위로 끄고 켠다.
    """
    if three is None:
        three = bool(w.draw)
    return f"{w.win}-{w.loss}-{w.draw}" if three else f"{w.win}-{w.loss}"


def _streak(s: Standing) -> str:
    """연속 기록 표기. **'6승'이 아니라 '6연승'이다 (v1.11i).**

    전에는 `f"{len}{승/패}"`라 6연승이 "6승"으로 찍혔다. 같은 캡션에 시즌 승수
    ("81-59")가 나란히 있어 어느 쪽이 연속인지 구분되지 않았고, 같은 값을
    카드 골드 패널은 "6연승"이라 써서 한 시스템이 한 사실을 두 표기로 말했다.
    """
    if s.streak_kind is StreakKind.NONE or not s.streak_len:
        return "—"
    return f"{s.streak_len}연{ {'W': '승', 'L': '패', 'D': '무'}[s.streak_kind.value] }"


def _tn(s: Standing) -> str:
    """순위표 한 행의 팀 표시명."""
    return TEAM_NAMES[s.league].get(s.team_code, s.team_code)


def _longest(cands: list[Standing]) -> tuple[Standing, bool]:
    """(최댓값 하나, 동률이 있는가). `max()`는 동률 중 첫 번째를 조용히 고른다."""
    best = max(c.streak_len for c in cands)
    tied = [c for c in cands if c.streak_len == best]
    return tied[0], len(tied) > 1


def record_headline(rb: RecordBook) -> tuple[str, str]:
    """골드 패널에 들어갈 '기록 한 줄'. 결정론적 템플릿만 쓴다.

    후보를 우선순위대로 훑어 첫 번째로 성립하는 것을 쓴다.
    성립하는 게 없으면 선두-2위 승차라는 항상 성립하는 문장으로 떨어진다.

    **단정("리그 최장"·"리그 최고 페이스")은 유일할 때만 한다 (v1.11i).**
    `max()`도 `for ... : return`도 동률을 조용히 하나로 줄여버려, 같은 표에
    똑같은 9연승 팀이 또 있는데도 한 팀만 "리그 최장"이라 불렀다.
    """
    order = sorted(rb.standings, key=lambda x: x.rank)
    top, second = order[0], order[1]

    # 1) 5연승 이상 / 5연패 이상
    for kind, word, label in ((StreakKind.WIN, "연승", "연승"),
                              (StreakKind.LOSS, "연패", "연패")):
        cands = [s for s in order if s.streak_kind is kind and s.streak_len >= 5]
        if not cands:
            continue
        s, tied = _longest(cands)
        # 동률이면 '리그 최장'이라 단정하지 않는다 — 공동임을 밝힌다.
        note = "— 리그 공동 최장" if tied else "— 리그 최장"
        return (label, f"<b>{esc(_tn(s))}</b> {s.streak_len}{word} "
                       f"{note} (현재 {s.rank}위)")

    # 2) 최근 10경기 8승 이상 / 8패 이상
    #    **순위 순서로 처음 만난 팀이 아니라 최댓값을 뽑는다.** 전에는 같은 표에
    #    9승1패가 있어도 순위가 앞선 8승2패 팀을 "리그 최고 페이스"라 불렀다.
    hot = [s for s in order if s.last10 and s.last10.win >= 8]
    if hot:
        best = max(s.last10.win for s in hot)
        tied = [s for s in hot if s.last10.win == best]
        s = tied[0]
        note = " — 리그 공동 최고 페이스" if len(tied) > 1 else " — 리그 최고 페이스"
        return ("최근 10경기",
                f"<b>{esc(_tn(s))}</b> 최근 10경기 "
                f"{s.last10.win}승 {s.last10.loss}패{note}")
    cold = [s for s in order if s.last10 and s.last10.loss >= 8]
    if cold:
        s = max(cold, key=lambda x: x.last10.loss)
        return ("최근 10경기",
                f"<b>{esc(_tn(s))}</b> 최근 10경기 "
                f"{s.last10.win}승 {s.last10.loss}패")

    # 3) 항상 성립 — 선두-2위 승차
    #    **승차 0.0은 '앞선 선두'가 아니라 공동 선두다 (v1.11i).**
    #    또 팀명 뒤에 조사를 박아두면 받침 있는 이름이 전부 비문이 된다
    #    ("보스턴가 2위 …", "전북가", "인천가"). 조사는 josa()가 고른다.
    n1, n2 = _tn(top), _tn(second)
    if _gb_zero(second.games_behind):
        # 순위 순서로 승차 0이 이어지는 데까지가 공동 선두다.
        co = [top]
        for s in order[1:]:
            if not _gb_zero(s.games_behind):
                break
            co.append(s)
        names = "·".join(esc(_tn(s)) for s in co)
        return ("선두 경쟁", f"<b>{names}</b> 공동 선두 (승차 없음)")
    return ("선두 경쟁",
            f"선두 <b>{esc(n1)}</b> · 2위 {esc(n2)}{josa(n2, '과', '와')} "
            f"{esc(second.games_behind)}경기 차")


def _gb_zero(gb: str) -> bool:
    """승차가 0인가. 소스는 '0.0'·'-'·''처럼 여러 표기로 준다."""
    s = str(gb).strip()
    if s in ("", "-", "—"):
        return True
    try:
        return float(s) == 0.0
    except ValueError:
        return False


def render_standings(rb: RecordBook, day: str, highlight: str | None = None,
                     top_n: int | None = None) -> str:
    """일간 순위표.

    v3.0: 9열 → 7열. 승·패·무를 한 열로 묶어 여백을 벌었다.
    정보는 그대로이고 읽기는 편해진다.
    """
    assert_recordbook(rb, require_h2h=bool(rb.h2h))
    order = sorted(rb.standings, key=lambda x: x.rank)
    gap = order[1].games_behind if len(order) > 1 else "0"
    total = len(order)
    # 30팀(MLB)은 한 장에 안 들어간다. 자르되 몇 중 몇인지 밝힌다.
    if top_n:
        order = order[:top_n]

    # 무승부가 없는 리그(MLB)에 '승-패-무'라고 쓰면 틀린 머리글이다
    # **형식은 열 단위로 정한다.** 표 안 어느 행이든 무승부가 있으면
    # 그 열은 전부 세 칸으로 쓴다 — 행마다 다르면 범례가 거짓이 된다.
    has_draw = any(x.record.draw for x in order)
    l10_draw = any(x.last10.draw for x in order if x.last10)
    wl = "승-패-무" if has_draw else "승-패"
    head = (f'<tr><th class="pad">순위</th><th>팀</th><th>{wl}</th><th>승률</th>'
            '<th>승차</th><th>최근10</th><th class="padr">연속</th></tr>')
    rows = []
    for s_ in order:
        cls = ' class="hl"' if highlight and s_.team_code == highlight else ""
        l10 = _wld(s_.last10, l10_draw) if s_.last10 else "—"
        gb = "—" if s_.rank == 1 else esc(s_.games_behind)
        rows.append(
            f'<tr{cls}><td class="pad">{s_.rank}</td>'
            f'<td>{esc(TEAM_NAMES[s_.league].get(s_.team_code, s_.team_code))}</td>'
            f'<td>{_wld(s_.record, has_draw)}</td>'
            f'<td>{esc(s_.pct)}</td><td>{gb}</td><td>{l10}</td>'
            f'<td class="padr">{_streak(s_)}</td></tr>')

    label, line = record_headline(rb)
    lgname = LEAGUE_LABEL.get(rb.league, rb.league.value)
    sub = f"전체 {total}팀 중 상위 {len(order)}팀" if len(order) < total else ""
    # 승차 0.0을 "0.0경기 차"라 쓰면 차이가 있는 것처럼 읽힌다. 공동 선두다.
    h1 = ('1·2위 <em>승차 없음</em>' if _gb_zero(gap)
          else f'1·2위 <em>{esc(gap)}경기</em> 차')
    body = (_hdr(*LEAGUE_COLORS[rb.league], lgname, "팀 순위", _dt(day),
                 h1, sub, league=rb.league) +
            f'<div class="body"><table class="stb">{head}{"".join(rows)}</table></div>'
            f'<div class="rec"><div class="l">{esc(label)}</div>'
            f'<div class="t">{line}</div></div>'
            f'<div class="foot"><div class="tk">최근10 = {"승-패-무" if l10_draw else "승-패"} · {esc(lgname)} 공식 기록</div>'
            f'<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, rb.league)


# 리더보드 세트 — 요일로 돌린다. 부문명은 Top5 페이지의 표기를 그대로 쓴다.
# **세트 이름에 '부문'을 붙였다 말았다 하지 않는다 (v1.11i).**
# 넷 중 둘만 "…부문"이라 같은 자리에 들어가는 이름이 두 계열로 갈렸다.
LEADER_SETS: list[tuple[str, list[str]]] = [
    ("타격 부문", ["타율", "홈런", "타점", "도루"]),
    ("투수 부문", ["평균자책점", "승리", "탈삼진", "세이브"]),
    ("출루·장타 부문", ["출루율", "장타율", "OPS", "안타"]),
    ("제구·이닝 부문", ["WHIP", "QS", "이닝", "피안타율"]),
]

# 야구가 아닌 리그의 세트 이름. **제목은 부문명을 이어 붙인 것이 아니라 이름이어야 한다.**
# "득점 · 리바운드 · 어시스트 · 3점"은 제목 자리에 들어가면 제목으로 안 읽히고
# 카드 폭을 넘긴다(h1은 한 줄짜리 자리다). 부문이 무엇인지는 바로 아래 4개
# 상자의 머리글이 이미 말하고 있으므로, 제목은 묶음의 이름만 말하면 된다.
_LEADER_SET_NAMES: dict[League, list[str]] = {
    League.KBL: ["공격 부문", "수비 부문"],
    League.VLEAGUE_M: ["공격 부문", "수비 부문"],
    League.VLEAGUE_W: ["공격 부문", "수비 부문"],
    League.KL1: ["공격 부문", "수비 부문"],
    League.LCK: ["개인 지표", "교전 지표"],
    League.INTL_LOL: ["개인 지표", "교전 지표"],
}
LEADER_SET_FALLBACK = "주요 부문"


def leader_set(rb: "RecordBook", set_idx: int) -> tuple[str, list[str]]:
    """(카드 제목, 실을 부문 4개).

    **제목은 실제로 실린 부문에서 만든다 (v1.11h).**
    LEADER_SETS는 야구 전용이다. 다른 리그는 요구 부문이 하나도 안 맞아
    "그 리그가 가진 부문으로 채우는" 분기로 떨어지는데, **제목만 야구 그대로**였다.
    그래서 KBL 4세트가 전부 같은 내용(득점·리바운드·어시스트·3점)에
    "타격 부문 / 투수 부문 / 출루·장타 / 제구·이닝" 제목만 바꿔 달고 나갔다.
    요일 로테이션이 같은 카드를 네 번 내면서 제목으로 거짓말을 하는 셈이다.
    """
    sets = leader_sets_for(rb)
    if not sets:
        return LEADER_SET_FALLBACK, []
    return sets[set_idx % len(sets)]


def leader_sets_for(rb: "RecordBook") -> list[tuple[str, list[str]]]:
    """이 리그에서 **실제로 만들 수 있는** (제목, 부문 4개) 목록.

    **이름과 내용을 함께 정한다 (v1.11i).** 전에는 세트 번호로 부문 목록을
    잘라 쓰면서 제목은 따로 골랐다. 그래서 두 가지가 어긋났다:

      · 부문 수가 4의 배수가 아니면 목록이 되감겨(`pool[(start+i) % len]`)
        `수비 부문` 칸에 득점·리바운드 같은 공격 부문이 섞였다 — 제목이 거짓이 된다.
      · KBO처럼 부문이 정확히 8개면 세트 0과 세트 2의 **내용이 완전히 같은데**
        제목만 `타격 부문` / `주요 부문`으로 갈렸다. 요일 로테이션이 같은 카드를
        두 번 내보내는 셈이다.

    그래서 "만들 수 있는 만큼만 만든다". 세트 수를 4로 고정하지 않고,
    한 번 쓴 부문은 다시 쓰지 않으며, 4개를 못 채우는 나머지는 세트로 만들지 않는다.
    """
    out: list[tuple[str, list[str]]] = []
    used: set[str] = set()

    # 1) 이름 붙은 세트(야구) 중 요구 부문이 네 개 다 있는 것만.
    for title, wanted in LEADER_SETS:
        cats = [c for c in wanted if c in rb.leaders and c not in used]
        if len(cats) >= 4:
            out.append((title, cats[:4]))
            used.update(cats[:4])

    # 2) 남은 부문을 그 리그의 세트 이름으로 묶는다. 이름이 있는 만큼만 만든다 —
    #    이름 없는 묶음에 공용 이름을 붙여 늘리면 1)과 같은 내용이 다시 나올 수 있다.
    rest = [c for c in rb.leaders if c not in used]
    names = _LEADER_SET_NAMES.get(rb.league) or []
    for i, name in enumerate(names):
        chunk = rest[i * 4:i * 4 + 4]
        if len(chunk) < 4:
            break
        out.append((name, chunk))
        used.update(chunk)

    if out:
        return out

    # 3) 세트를 하나도 못 만들었다 — 부문이 4개 미만이거나 이름표가 없는 리그다.
    #    가진 것을 한 묶음으로 내되 제목은 아무것도 주장하지 않는 공용 이름을 쓴다.
    pool = [c for c in rb.leaders]
    return [(LEADER_SET_FALLBACK, pool[:4])] if pool else []


def render_leaders(rb: RecordBook, day: str, set_idx: int = 0, top_n: int = 5) -> str:
    """부문별 리더보드 카드. 4부문 × TOP N.

    리그마다 있는 부문이 다르다. 없는 부문을 요구하면 카드가 아예 안 나가므로,
    그 리그가 실제로 가진 부문 중에서 세트를 채운다. 순서는 세트 정의를 따른다.
    """
    assert_recordbook(rb, require_h2h=bool(rb.h2h))
    title, cats = leader_set(rb, set_idx)
    if not cats:
        raise GateError(f"리더보드: {rb.league.value}에 쓸 부문이 없다")

    boxes = []
    for c in cats:
        rows = []
        for e in rb.leaders[c][:top_n]:
            r1 = " r1" if e.rank == 1 else ""
            # **한 표 안에서 팀 표기를 한 가지로 통일한다 (v1.11i).**
            # 전에는 이름 길이에 따라 한글 팀명·팀 코드·표기 없음 세 가지가
            # 한 카드에 섞였다(실측 20행 중 세 종류). 읽는 사람은 그 차이를
            # 뜻으로 읽는다 — 실제로는 자리 계산 결과일 뿐인데도.
            # 자리가 확실한 팀 코드로 전 행을 맞추고, 꼬리말에 약칭임을 밝힌다.
            tn = e.team_code
            if len(e.name) > 11:
                tn = ""              # 이름만으로 자리가 찬다. 잘린 팀명보다 없는 편이 낫다
            rows.append(
                f'<div class="lbr{r1}"><div class="r">{e.rank}</div>'
                f'<div class="p">{esc(e.name)}' + (f'<small>{esc(tn)}</small>' if tn else '') + '</div>'
                f'<div class="v">{esc(e.value)}</div></div>')
        boxes.append(f'<div class="lbx"><div class="lbt">{esc(c)}</div>{"".join(rows)}</div>')

    lgname = LEAGUE_LABEL.get(rb.league, rb.league.value)
    body = (_hdr(*LEAGUE_COLORS[rb.league], lgname, "부문 순위", _dt(day),
                 f'{esc(title)} <em>TOP {top_n}</em>',
                 # **소스가 보장하지 않는 성질을 카드가 단정하지 않는다 (v1.11h).**
                 # "규정 미달 선수 포함"이 박혀 있었는데, 부문 순위는 KBO 공식
                 # Top5 페이지에서 가져오고 그 페이지는 규정 충족자 기준이다.
                 # 코드 어디에도 규정 미달 포함 여부를 확인하는 로직이 없다.
                 f"{esc(lgname)} 공식 부문 순위", league=rb.league) +
            f'<div class="body"><div class="lb">{"".join(boxes)}</div></div>'
            f'<div class="foot"><div class="tk">팀은 약칭 표기 · {esc(lgname)} 공식 기록 · '
            f'{esc(_dt(day))} 기준</div>'
            f'<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, rb.league)


def render_matchup(rb: RecordBook, game: Game, day: str) -> str:
    """경기 전 맞대결 분석 카드. 두 팀 순위 + 시즌 상대전적."""
    assert_recordbook(rb, require_h2h=bool(rb.h2h))
    a, h = game.away.team_code, game.home.team_code
    sa, sh = rb.team(a), rb.team(h)
    if not sa or not sh:
        raise GateError(f"맞대결: 순위표에 없는 팀 {a}/{h}")
    wld = rb.between(a, h)
    if wld is None:
        raise GateError(f"맞대결: 상대전적 없음 {a} vs {h}")

    played = wld.win + wld.loss + wld.draw
    # **무승부를 분모에서 빼면 막대가 전적을 과장한다 (v1.11h).**
    # 7-4-1을 64:36으로 그렸는데, 실제로는 12경기 중 7승이다.
    # 같은 카드의 가운데에는 '7-4-1'이 찍혀 있어 서로 어긋났다.
    #
    # **홈 막대를 `100-원정`으로 채우면 무승부 몫이 홈에 얹힌다 (v1.11i).**
    # 7-4-1에서 홈은 4승(33%)인데 막대는 42%를 그렸다 — 있지도 않은 1승이
    # 그림에만 있었다. 양쪽 다 자기 승수로 계산하고, 남는 몫은 무승부 색으로 둔다.
    aw = round(wld.win / played * 100) if played else 50
    hw = round(wld.loss / played * 100) if played else 50
    dw = max(0, 100 - aw - hw)
    # 두 팀의 전적 표기는 **한 카드 안에서 같은 형식**이어야 한다. 행마다
    # `w.draw`를 보고 정하면 한쪽만 '78-63-3'이 되어 열이 갈린다.
    _three = bool(sa.record.draw or sh.record.draw)
    _three10 = bool((sa.last10 and sa.last10.draw) or (sh.last10 and sh.last10.draw))
    kst, _ = format_kickoff(game)

    def side(s: Standing, right: bool) -> str:
        # 한 줄에 몰아넣으면 폭이 모자라 줄바꿈이 깨진다. 두 줄로 나눈다.
        cls = "tr rt" if right else "tr"
        l10 = f"최근10 {_wld(s.last10, _three10)}" if s.last10 else ""
        return (f'<div{" class=rt" if right else ""}>'
                f'<div class="tn">{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</div>'
                f'<div class="{cls}">{s.rank}위 · {_wld(s.record, _three)}</div>'
                f'<div class="{cls} l2">{l10}</div></div>')

    # 막대 색은 '우세팀'이 골드. 원정/홈이 아니라 전적이 색을 정한다.
    ca, ch = ("w", "l") if wld.win > wld.loss else ("l", "w") if wld.win < wld.loss else ("l", "l")

    # **무승부를 빼고 말하지 않는다.** 카드 가운데는 '7-4-1'인데 골드 패널은
    # '7승 4패 우세'라 한 카드 안에서 숫자가 어긋났다(캡션은 1무를 적고 있었다).
    _d = f" {wld.draw}무" if wld.draw else ""
    if wld.win == wld.loss:
        edge = f"시즌 상대전적 <b>{wld.win}승 {wld.loss}패{_d}</b> 팽팽"
    else:
        lead_t, lw, ll = ((a, wld.win, wld.loss) if wld.win > wld.loss
                          else (h, wld.loss, wld.win))
        edge = (f"시즌 상대전적 <b>{esc(TEAM_NAMES[rb.league].get(lead_t, lead_t))} "
                f"{lw}승 {ll}패{_d}</b> 우세")
    # "두산 7승 4패 우세" 바로 밑에 "두산 2패"만 있으면 무슨 2패인지 모른다.
    # 연속 기록 표기는 _streak() 한 곳에서만 만든다 — 여기서 따로 조립했더니
    # 순위표 '연속' 열과 표기가 갈렸다.
    parts = [f"{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))} {_streak(s)}"
             for s in (sa, sh) if s.streak_kind is not StreakKind.NONE and s.streak_len]
    streaks = "현재 " + " · ".join(parts) if parts else ""

    lgname = LEAGUE_LABEL.get(rb.league, rb.league.value)
    body = (_hdr(*LEAGUE_COLORS[rb.league], lgname, "맞대결 분석", _dt(day),
                 f'{esc(TEAM_NAMES[rb.league].get(a, a))} <em>vs</em> {esc(TEAM_NAMES[rb.league].get(h, h))}',
                 f'{esc(kst)} · {esc(venue_name(game.venue))}') +
            f'<div class="body"><div class="vs">{side(sa, False)}'
            f'<div class="mid"><span class="ml">시즌</span>'
            f'<span class="mv">{wld.win}-{wld.loss}-{wld.draw}</span></div>'
            f'{side(sh, True)}</div>'
            f'<div class="brow {ca}"><span class="bn">{esc(TEAM_NAMES[rb.league].get(a, a))}</span>'
            f'<div class="bar"><div class="fill" style="width:{aw}%"></div>'
            + (f'<div class="fdraw" style="width:{dw}%"></div>' if dw else "") +
            f'</div>'
            f'<span class="pct">{wld.win}승</span></div>'
            f'<div class="brow {ch}"><span class="bn">{esc(TEAM_NAMES[rb.league].get(h, h))}</span>'
            f'<div class="bar"><div class="fill" style="width:{hw}%"></div>'
            + (f'<div class="fdraw" style="width:{dw}%"></div>' if dw else "") +
            f'</div>'
            f'<span class="pct">{wld.loss}승</span></div></div>'
            f'<div class="rec"><div class="l">맞대결</div><div class="t">{edge}'
            + (f'<br>{streaks}' if streaks else "") +
            f'</div></div>'
            # 꼬리말은 한 줄에 들어가야 한다 — 넘치면 로고와 겹친다(실렌더로 확인).
            f'<div class="foot"><div class="tk">상대전적 = 승-패-무'
            + (" · 회색 = 무" if dw else "") +
            f' · {esc(lgname)} 공식 기록</div>'
            f'<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, rb.league)


# ── 6. 캡션 (v1.11) ─────────────────────────────────────────
#
# 카드에 다 안 들어가는 내용은 캡션이 받는다 — 대표님 설계.
#   · 카드는 상위 N개만 (한눈에 보이는 것)
#   · 전체는 접고펼치기 인용블록 (필요한 사람만 편다)
#   · 이미지와 캡션이 한 메시지로 나간다 (sendPhoto의 caption)
#
# 텔레그램 캡션 상한은 1024자다. 넘치면 카드에 실린 만큼을 빼서 줄인다 —
# 잘라내서 사실을 지우는 대신, 이미 보이는 것을 반복하지 않는 쪽을 택한다.

CAPTION_MAX = 1024


def _clip(head: str, lines: list[str], tail: str = "", unit: str = "경기") -> str:
    """캡션 하나로 만든다. 넘치면 줄인다 — 하지만 버리지는 않는다.

    잘린 나머지는 `_clip_parts()`가 후속 텍스트 메시지로 이어 보낸다.
    이 함수만 쓰면 초과분이 사라지므로, 발송 경로는 반드시 `_clip_parts()`를 쓴다.
    """
    return _clip_parts(head, lines, tail, unit)[0]


def _clip_parts(head: str, lines: list[str], tail: str = "",
                unit: str = "경기") -> list[str]:
    """캡션 + (필요하면) 후속 텍스트 메시지들.

    대표님 지시: **"카드에 다 안 들어가는 내용은 상위 옵션을 넣고, 전체 내용은 텍스트로."**
    그래서 초과분을 '외 N건'으로 버리지 않는다 — 버리면 '전체'가 아니다.

    · [0] 사진에 붙는 캡션 (텔레그램 상한 1024자)
    · [1:] 이어 보내는 텍스트 메시지 (상한 4096자, 필요한 만큼)

    실측상 가장 긴 케이스(MLB 순위 30팀)가 707자라 평소엔 [0] 하나로 끝난다.
    이 분할은 리그가 늘거나 더블헤더가 겹쳐 넘칠 때를 위한 안전망이다.
    """
    def build(ls: list[str], more: int) -> str:
        # **접고펼치기 기준을 한 가지로 통일한다 (v1.11j).**
        # 시작 알림만 "6줄 초과"를 쓰고 캡션은 줄 수와 무관하게 늘 접혀 있었다.
        # 그래서 같은 5경기가 모닝 캡션에서는 접히고 시작 알림에서는 안 접혔다 —
        # 읽는 사람에게는 접힘 자체가 '내용이 더 있다'는 신호인데 그 신호가 거짓이었다.
        body = quote(ls, expandable=len(ls) > QUOTE_EXPANDABLE_THRESHOLD_LINES)
        # 서술어가 없는 명사구는 안내가 아니다. 단위도 내용에 맞춘다 —
        # 경기 목록에 "3건"이라 쓰면 무엇이 셋인지 읽는 사람이 되짚어야 한다.
        # 단위가 바뀌면 조사도 바뀐다("3경기는" / "19팀은"). 박아두면 반드시 틀린다.
        note = (f"\n<i>나머지 {more}{unit}{josa(unit, '은', '는')} "
                f"다음 메시지에 이어집니다</i>" if more else "")
        return head + body + note + (("\n" + tail) if tail else "")

    # **0건이면 인용블록을 만들지 않는다 (v1.11i).**
    # `quote([])`는 GateError로 죽는다. 경기 0건은 오류가 아니라 사실이고,
    # 그 사실을 전하지 못해 캡션 전체가 사라지면 카드도 함께 못 나간다.
    if not lines:
        return [head.rstrip() + (("\n" + tail) if tail else "")]

    out = build(lines, 0)
    if len(out) <= CAPTION_MAX:
        return [out]

    # 캡션에 들어갈 만큼만 남기고, 나머지는 뒤로 넘긴다.
    n = len(lines)
    while n > 1:
        cand = build(lines[:n], len(lines) - n)
        if len(cand) <= CAPTION_MAX:
            break
        n -= 1
    parts = [build(lines[:n], len(lines) - n)]

    rest = lines[n:]
    while rest:
        k = len(rest)
        while k > 1:
            cand = _cont(rest[:k])
            if len(cand) <= TELEGRAM_TEXT_MAX:
                break
            k -= 1
        parts.append(_cont(rest[:k]))
        rest = rest[k:]
    return parts


def _cont(lines: list[str]) -> str:
    """이어지는 텍스트 메시지. 캡션과 같은 접고펼치기 기준을 쓴다."""
    return ("<b>(이어서)</b>\n"
            + quote(lines, expandable=len(lines) > QUOTE_EXPANDABLE_THRESHOLD_LINES))


def caption_morning(games: list[Game], day: str, *, as_parts: bool = False,
                    now: datetime | None = None):
    """오늘 편성 전체. 카드에 상위 N만 실렸어도 여기엔 전부 있다.

    as_parts=True면 [캡션, 이어지는 텍스트...] 목록을 준다(발송 경로가 쓴다).
    """
    off = {Status.CANCELED, Status.POSTPONED}
    wd = spans_two_kst_days(games)      # 카드와 같은 규칙 — 걸치면 전 줄에 요일
    lines = []
    for g in sorted(games, key=lambda x: x.start_utc):
        kst, _ = format_kickoff(g, with_weekday=True if wd else None)
        # 사진과 캡션은 한 메시지로 함께 나간다. 카드가 "연기"라 쓴 경기를
        # 캡션이 "취소"라 부르면 한 화면 안에서 서로 모순된다 — 같은 함수를 쓴다.
        mark = f" · {esc(_reason(g))}" if g.status in off else ""
        # 카드에서 자리가 없어 뺀 경기장을 여기서 살린다 — 캡션은 자리가 넉넉하다.
        place = venue_name(g.venue)
        vv = f" · {esc(place)}" if place else ""
        lines.append(f"{esc(kst)}  {esc(team_name(g.away))} vs "
                     f"{esc(team_name(g.home))}{esc(_dh(g))}{vv}{mark}")
    lg = games[0].league if games else League.KBO
    # **카드와 같은 수를 말한다 (v1.11h).** 카드 헤드라인은 열리는 경기 수,
    # 캡션은 전체 수를 쓰고 있었다. 사진과 캡션은 한 메시지로 함께 나가므로
    # "오늘 KBO 0경기 / 전체 편성 5경기"가 한 화면에 보였다(실측 불일치 16일).
    _play = [g for g in games if g.status not in off]
    _cx = [g for g in games if g.status is Status.CANCELED]
    _po = [g for g in games if g.status is Status.POSTPONED]
    # 취소와 연기를 "(+2경기 취소·연기)" 한 덩어리로 묶으면 어느 쪽이 몇인지 없다.
    _off = " · ".join(x for x in (f"{len(_cx)}경기 취소" if _cx else "",
                                  f"{len(_po)}경기 연기" if _po else "") if x)
    # **카드 배지와 캡션 머리말이 같은 이름을 쓴다 (v1.11i).**
    # 카드는 "모닝 브리핑", 캡션은 "편성"이라 한 메시지가 스스로를 두 이름으로 불렀다.
    # 어순·낱말도 카드 헤드라인("오늘 KBO 편성 3경기")에 맞춘다.
    _pre = (f"📋 <b>{esc(morning_label(now or datetime.now(timezone.utc)))} · "
            f"{day_word_span(games, now)} {esc(LEAGUE_LABEL.get(lg, lg.value))} ")
    if not _play and (_cx or _po):
        # 카드가 "경기 없음"이라 쓰는 날 캡션만 "편성 0경기"라 하면 또 갈린다.
        _why = " · ".join(f"{lab} {n}경기"
                          for lab, n in _reason_counts(_cx + _po))
        head = _pre + f"경기 없음 (총 {len(games)}경기 · {_why})</b>\n"
    else:
        # **총계를 밝힌다 (v1.11j).** 카드 부제는 "총 5경기 중 3경기 취소"라 쓰는데
        # 캡션은 "(3경기 취소)"뿐이라 그날 몇 경기가 잡혀 있었는지 캡션만 읽어서는
        # 알 수 없었다. 카드와 같은 낱말('총 N경기')로 같은 사실을 적는다.
        _paren = (f" (총 {len(games)}경기 · {_off})" if _off else "")
        head = _pre + f"편성 {len(_play)}경기" + _paren + "</b>\n"
    # 열리는 경기가 0이면 시작 알림도 없다 — 카드 푸터와 같은 규칙(v1.11j).
    tail = start_alert_lead_text() if _play else ""
    return _clip_parts(head, lines, tail) if as_parts else _clip(head, lines, tail)


def caption_result(games: list[Game], day: str, *, as_parts: bool = False):
    """그날 전 경기 결과. as_parts=True면 파트 목록."""
    # **한 목록, 시작 시각순 (v1.11i).** 카드 본문은 시간순인데 캡션만 미확정
    # 경기를 뒤로 몰아, 같은 메시지의 사진과 글이 다른 순서로 같은 날을 말했다.
    # 시간순은 '언제 무슨 일이 있었나'를 읽는 유일한 순서다.
    lines = []
    for g in sorted(games, key=lambda x: x.start_utc):
        a, h = esc(team_name(g.away)), esc(team_name(g.home))
        dh = esc(_dh(g))
        if g.status is Status.CANCELED:
            # 대진 구분자는 카드·캡션·알림 전부 `vs`로 통일한다 —
            # 취소 행만 `—`라 같은 목록 안에서 두 기호가 섞였다.
            lines.append(f"{a} vs {h}{dh} · {esc(_reason(g))}")
        elif g.status is Status.FINAL and g.score:
            # 점수 표기도 카드와 같은 `1:15` 한 가지로 (카드는 구분자가 없었고
            # 캡션은 `1 : 15`였다). 무승부는 카드에만 있고 캡션엔 없었다.
            draw = " (무승부)" if g.is_draw() else ""
            lines.append(f"{a} {g.score.away}:{g.score.home} {h}{dh}{draw}")
        elif g.status is Status.POSTPONED:
            lines.append(f"{a} vs {h}{dh} · {esc(_reason(g))}")
        elif g.status is Status.SUSPENDED:
            lines.append(f"{a} vs {h}{dh} · 서스펜디드(속개 예정)")
        else:
            # 결과가 안 나온 경기는 **점수 없이** 이름만 남긴다. 빼버리면 그날
            # 편성이 줄어든 것처럼 보이고, 점수를 적으면 진행 중 점수가 결과가 된다.
            lines.append(f"{a} vs {h}{dh} · 결과 미확정")
    lg = games[0].league if games else League.KBO
    fin = [g for g in games if g.status is Status.FINAL and g.score]
    cx = [g for g in games if g.status is Status.CANCELED]
    po = [g for g in games if g.status is Status.POSTPONED]
    sp = [g for g in games if g.status is Status.SUSPENDED]
    # **점수 없는 FINAL을 여기서도 센다 (v1.11j).** 목록에는 "결과 미확정"으로
    # 이미 한 줄이 있는데 머리말의 어느 수에도 안 들어가, 카드(4경기)와 캡션(5줄)이
    # 그날 경기 수를 다르게 말했다. 카드 부제와 같은 규칙으로 센다.
    unk = [g for g in games
           if not ((g.status is Status.FINAL and g.score)
                   or g.status is Status.CANCELED)
           and g.status not in (Status.POSTPONED, Status.SUSPENDED)]
    _name = esc(LEAGUE_LABEL.get(lg, lg.value))
    # 카드 부제와 같은 낱말·같은 수를 쓴다. 표현이 갈리면 한 메시지 안에서
    # 사진과 글이 같은 날을 다르게 말한다.
    _rest = " · ".join(f"{_w} {len(_n)}경기"
                       for _n, _w in ((po, "연기"), (sp, "서스펜디드"),
                                      (unk, "미확정")) if _n)
    if not fin and cx and not (po or sp or unk):
        head = f"📋 <b>{_name} 전 경기 취소 {len(cx)}경기</b>\n"
    elif not fin and cx:
        # "결과 0경기"는 말이 안 된다 — 아직 결과가 없고 취소만 확정된 날이다.
        # 카드 헤드라인과 같은 문형을 쓴다(카드도 '나머지 진행 중'을 부제로 옮겼다).
        head = f"📋 <b>{_name} 취소 {len(cx)}경기 확정 ({_rest})</b>\n"
    else:
        # **'전 경기 결과'라 부를 수 있는 날에만 그렇게 부른다 (v1.11i).**
        # 미확정이 남아 있는데 머리말이 그대로라 캡션이 스스로를 반박했다.
        # **괄호 안 단위를 섞지 않는다 (v1.11j).** "전 경기 결과 2경기 (편성 5 · 취소 3)"은
        # 앞은 '경기', 뒤는 맨숫자라 같은 괄호에서 두 단위가 섞였다.
        _bits = [f"편성 {len(games)}경기"]
        for _n, _w in ((cx, "취소"), (po, "연기"), (sp, "서스펜디드"),
                       (unk, "미확정")):
            if _n:
                _bits.append(f"{_w} {len(_n)}경기")
        # 취소는 '그날 있었던 일'이 확정된 것이므로 '전 경기'를 깨지 않는다.
        # 깨는 것은 아직 결과를 모르는 경기(연기·서스펜디드·진행 중)다.
        _whole = not (po or sp or unk)
        head = (f"📋 <b>{_name} {'전 경기 결과' if _whole else '경기 결과'} {len(fin)}경기"
                + (f" ({' · '.join(_bits)})" if len(_bits) > 1 else "") + "</b>\n")
    return _clip_parts(head, lines) if as_parts else _clip(head, lines)


def caption_standings(rb: RecordBook, *, as_parts: bool = False):
    """순위표 전체. MLB는 30팀이라 카드엔 10팀만 실린다. as_parts=True면 파트 목록."""
    names = TEAM_NAMES.get(rb.league, {})
    order = sorted(rb.standings, key=lambda x: x.rank)
    # 전적 표기는 **열 단위로 통일한다** — 캡션도 카드와 같은 규칙이다.
    # 행마다 정하면 한 목록에 '80-55'와 '78-63-3'이 섞여, 두 칸짜리가
    # 무승부 0인지 무승부가 없는 리그인지 알 수 없어진다.
    three = any(s.record.draw for s in order)
    # 텔레그램은 가변폭 글꼴이라 `{rank:>2}`로 자리를 맞출 수 없다.
    # 정렬은 안 되고 한 자리 순위 앞에 공백만 생겨 목록이 들쭉날쭉해 보였다.
    lines = [f"{s.rank}. {esc(names.get(s.team_code, s.team_code))} "
             f"{_wld(s.record, three)} · {esc(s.pct)}"
             for s in order]
    head = (f"📋 <b>{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} "
            f"전체 순위 {len(rb.standings)}팀</b>\n")
    # 값에 라벨이 없으면 '0.579'가 승률인지 승차인지 알 수 없다.
    tail = f"순위. 팀 {'승-패-무' if three else '승-패'} · 승률"
    return (_clip_parts(head, lines, tail, unit="팀") if as_parts
            else _clip(head, lines, tail, unit="팀"))


def caption_leaders(rb: RecordBook, set_idx: int = 0, top_n: int = 5,
                    *, as_parts: bool = False):
    """부문 순위 전체 텍스트.

    카드는 4부문 × TOP5만 싣는다. KBO는 부문이 29개라 카드 한 장으로는 어림도 없다 —
    대표님 지시대로 **카드엔 상위만, 전체는 텍스트로** 붙인다.
    카드에 실린 그 세트의 전체 순위를 그대로 옮기고, 카드에 못 실린 나머지 부문은
    1위만 한 줄씩 적어 '무엇이 더 있는지'를 남긴다.
    """
    # 카드와 **같은 함수**로 고른다. 따로 고르면 사진과 캡션이 다른 부문을 말한다.
    title, cats = leader_set(rb, set_idx)
    names = TEAM_NAMES.get(rb.league, {})

    lines: list[str] = []
    for c in cats:
        lines.append(f"◆ {esc(c)}")
        for e in rb.leaders[c][:top_n]:
            tm = esc(names.get(e.team_code, e.team_code))
            lines.append(f"  {e.rank}. {esc(e.name)} ({tm}) {esc(e.value)}")

    rest = [c for c in rb.leaders if c not in cats]
    if rest:
        lines.append(f"◆ 그 밖의 부문 1위 ({len(rest)}개)")
        for c in rest:
            top = rb.leaders[c][0]
            tm = esc(names.get(top.team_code, top.team_code))
            lines.append(f"  {esc(c)} — {esc(top.name)} ({tm}) {esc(top.value)}")

    head = (f"📋 <b>{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} "
            f"{esc(title)} 전체</b>\n")
    tail = f"{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} 공식 부문 순위"
    return (_clip_parts(head, lines, tail, unit="줄") if as_parts
            else _clip(head, lines, tail, unit="줄"))


def caption_matchup(rb: RecordBook, game: Game, *, as_parts: bool = False):
    """맞대결 분석 전체 텍스트. 카드엔 요약 막대만 실린다."""
    a, h = game.away.team_code, game.home.team_code
    sa, sh = rb.team(a), rb.team(h)
    if not sa or not sh:
        raise GateError(f"맞대결 캡션: 순위표에 없는 팀 {a}/{h}")
    names = TEAM_NAMES.get(rb.league, {})
    na, nh = esc(names.get(a, a)), esc(names.get(h, h))
    wld = rb.between(a, h)

    # **한 캡션 안에서 전적 표기를 한 형식으로 (v1.11i).**
    # 첫 줄은 서술형("7승 4패 1무"), 아래 줄은 하이픈형("81-59-4")이라
    # 같은 종류의 값이 두 문법으로 적혀 있었다. 열 단위로 정하고 하이픈으로 맞춘다
    # (꼬리말 범례가 하이픈 표기를 설명하므로 그쪽이 기준이다).
    # 홈/방문 전적은 없을 수 있다(소스가 안 주는 리그) — None을 그냥 읽으면 죽는다.
    _splits = [x for x in (sa.home, sa.away, sh.home, sh.away) if x]
    three = bool((wld and wld.draw) or sa.record.draw or sh.record.draw
                 or any(x.draw for x in _splits))
    three10 = bool((sa.last10 and sa.last10.draw) or (sh.last10 and sh.last10.draw))

    lines = []
    if wld is not None:
        lines.append(f"시즌 상대전적 {na} {_wld(wld, three)}")
    for s, nm in ((sa, na), (sh, nh)):
        bits = [f"{s.rank}위", _wld(s.record, three), f"승률 {esc(s.pct)}"]
        if s.last10:
            bits.append(f"최근10 {_wld(s.last10, three10)}")
        if s.streak_kind is not StreakKind.NONE and s.streak_len:
            bits.append(_streak(s))
        lines.append(f"{nm} — " + " · ".join(esc(b) for b in bits))
        # 홈/방문 전적을 주지 않는 리그가 있다. 없는 값을 억지로 찍지 않는다.
        if s.home and s.away:
            lines.append(f"  홈 {_wld(s.home, three)} · 방문 {_wld(s.away, three)}")

    head = (f"📋 <b>{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} "
            f"{na} vs {nh} 맞대결</b>\n")
    tail = f"전적 = {'승-패-무' if three else '승-패'}"
    return (_clip_parts(head, lines, tail, unit="줄") if as_parts
            else _clip(head, lines, tail, unit="줄"))
