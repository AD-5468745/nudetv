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
                      assert_result_deadline, kst_day_label, result_deadline)
from contract import assert_card_typography, team_name

# 워터마크(.wm/.wm3)는 읽으라고 넣은 글자가 아니므로 타이포 게이트에서 제외한다.
_TYPO_JS = """
() => {
  const res=[];
  const walk=(el)=>{
    if(el.closest('.wm, .wm3')) return;
    for(const n of el.childNodes){
      if(n.nodeType===3 && n.textContent.trim()){
        const r=el.getBoundingClientRect();
        if(r.width>0 && r.height>0)
          res.push({t:n.textContent.trim().slice(0,16),
                    fs:parseFloat(getComputedStyle(el).fontSize),
                    cls:(el.className||'').toString().slice(0,24),
                    // 줄 수 — 2 이상이면 그 요소가 두 줄로 접혔다는 뜻이다.
                    // 팀명이 접히면 행 높이가 들쭉날쭉해져 카드가 무너진다.
                    lines: el.getClientRects().length});
      } else if(n.nodeType===1) walk(n);
    }
  };
  walk(document.querySelector('#card'));
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
ONE_LINE_CLASSES = ("n1", "n2", "s1", "s2", "mt")


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
        # 너무 늦은 것은 여기서 거른다. 남겨두면 매일 빈 항목이 쌓인다.
        if (now - _m_utc).total_seconds() > GRACE_SECONDS[ContentType.MORNING]:
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
    by_sched: dict[str, list[Game]] = defaultdict(list)
    for g in games:
        if g.status is Status.SCHEDULED:
            by_sched[day_schedule_scope(g)].append(g)
    for scope, gs in by_sched.items():
        at = min(x.start_utc for x in gs) - timedelta(minutes=START_ALERT_LEAD_MINUTES)
        if not (lo <= at <= hi):
            continue
        items.append(QueueItem(
            idem_key=idem_key(channel, ContentType.START_ALERT, scope,
                              start_rev=max(x.start_rev for x in gs)),
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
        if (now - deadline).total_seconds() > GRACE_SECONDS[ContentType.LEAGUE_RESULT]:
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
        settled = league_day_settled(games, day)
        at = now if settled else deadline
        items.append(QueueItem(
            idem_key=idem_key(channel, ContentType.LEAGUE_RESULT,
                              f"{league.value}:{day}"),
            content_type=ContentType.LEAGUE_RESULT, scope=f"{league.value}:{day}",
            scheduled_utc=at, league=league, sports_day=day,
            render_at_utc=at - timedelta(minutes=15)))

    items.sort(key=lambda i: i.scheduled_utc)
    return items


def league_day_settled(games: list[Game], sports_day: str) -> bool:
    """결과 카드 트리거 — '마지막 경기 종료'가 아니라 '미종결 0건'."""
    todays = [g for g in games if g.sports_day == sports_day]
    return bool(todays) and all(g.is_terminal for g in todays)


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


LEAGUE_LABEL = {
    League.KBO: "KBO", League.KBL: "KBL", League.VLEAGUE_M: "V리그 남",
    League.VLEAGUE_W: "V리그 여", League.KL1: "K리그1", League.LCK: "LCK",
    League.INTL_LOL: "LoL 국제", League.MLB: "MLB", League.NPB: "NPB",
    League.EPL: "EPL", League.LALIGA: "라리가", League.SERIEA: "세리에A",
    League.BUNDESLIGA: "분데스리가", League.LIGUE1: "리그1", League.UCL: "UCL",
}


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
                   top_n: int = CARD_ROWS_MAX) -> str:
    """오늘 편성 브리핑.

    v1.10 수정: 헤드라인이 SCHEDULED 개수를 세고 있었다. 그래서 취소가 섞이면
    '오늘 KBO 1경기 / 5경기 편성'처럼 자기모순인 카드가 나왔다(실제 채널에 나갔다).
    열리는 경기 = 취소·연기가 아닌 경기다.
    """
    off = {Status.CANCELED, Status.POSTPONED}
    playable = [g for g in games if g.status not in off]
    dropped = [g for g in games if g.status in off]

    shown = sorted(games, key=lambda x: x.start_utc)[:top_n]
    rows = []
    for g in shown:
        kst, loc = format_kickoff(g)
        gone = g.status in off
        reason = (g.meta.cancel_reason or "취소") if gone else ""
        rows.append(
            f'<div class="row{" off" if gone else ""}"><div class="mt">'
            f'{esc(team_name(g.away))}<span class="sep">vs</span>'
            f'{esc(team_name(g.home))}</div>'
            f'<div class="meta">{esc(venue_name(g.venue))}'
            + (f' <span class="hook">{esc(reason)}</span>' if gone else "") +
            f'</div><div class="tm"><div class="k">{esc(kst)}</div>'
            + (f'<div class="l">현지 {esc(loc)}</div>' if loc else "") + "</div></div>")

    _lg = games[0].league if games else League.KBO
    h1 = f'오늘 {esc(LEAGUE_LABEL.get(_lg, _lg.value))} <em>{len(playable)}경기</em>'
    more = len(games) - len(shown)
    bits = []
    if dropped:
        bits.append(f"{len(games)}경기 편성 · {len(dropped)}경기 취소")
    if more:
        bits.append(f"아래에 나머지 {more}경기")
    # 안내 문구는 **실제로 하는 일**만 적는다. 예전에는 아직 없는 기능
    # ("예측 투표는 경기 3시간 전")을 안내하고 있었다 — 카드가 거짓말을 하면
    # 카드 전체를 못 믿게 된다.
    sub = " · ".join(bits) if bits else start_alert_lead_text()
    lg = games[0].league if games else League.KBO
    lgname = LEAGUE_LABEL.get(lg, lg.value)
    _dtk, _dtl = kst_day_label(games, day)
    body = (_hdr(*LEAGUE_COLORS[lg], lgname, "모닝 브리핑",
                 _dtk, h1, sub, league=lg, dt_local=_dtl) +
            f'<div class="body">{"".join(rows)}</div>'
            f'<div class="foot"><div class="tk">{esc(start_alert_lead_text())}</div>'
            '<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, lg)


def _name_cls(name: str) -> str:
    """긴 팀명을 한 줄에 담기 위한 크기 클래스.

    국내 표기를 쓰면 '세인트루이스'(6자)·'샌프란시스코'(7자)가 나온다.
    그대로 두면 결과 카드에서 두 줄로 접혀 행 높이가 무너진다
    (2026-08-30 육안 점검에서 발견). 이름을 줄이는 대신 크기를 낮춘다 —
    시청자가 쓰는 이름을 바꾸는 것보다 글자를 조금 작게 하는 편이 낫다.
    """
    n = len(name)
    return " n6" if n == 6 else (" n7" if n >= 7 else "")


def render_result(games: list[Game], day: str,
                  top_n: int = CARD_ROWS_MAX) -> str:
    shown = sorted(games, key=lambda x: x.start_utc)[:top_n]
    rows = []
    for g in shown:
        a, h = esc(team_name(g.away)), esc(team_name(g.home))
        if g.status is Status.CANCELED:
            rows.append(f'<div class="res cx"><div class="n1{_name_cls(team_name(g.away))}">{a}</div>'
                        f'<div class="s1">—</div>'
                        f'<div class="s2">—</div>'
                        f'<div class="n2{_name_cls(team_name(g.home))}">{h}</div>'
                        f'<div class="st">{esc(g.meta.cancel_reason or "취소")}</div></div>')
        elif g.score:
            draw = g.is_draw()
            cls = "dr" if draw else ("w1" if g.score.away > g.score.home else "w2")
            st = "무승부" if draw else "종료"
            rows.append(f'<div class="res {cls}">'
                        f'<div class="n1{_name_cls(team_name(g.away))}">{a}</div>'
                        f'<div class="s1">{g.score.away}</div><div class="s2">{g.score.home}</div>'
                        f'<div class="n2{_name_cls(team_name(g.home))}">{h}</div>'
                        f'<div class="st">{st}</div></div>')
    fin = [g for g in games if g.status is Status.FINAL]
    lg = games[0].league if games else League.KBO
    lgname = LEAGUE_LABEL.get(lg, lg.value)
    # 우천취소가 없는 종목에 '취소 경기 안내'를 쓰지 않는다.
    tk = ("취소 경기는 편성 확정 시 안내" if lg in OUTDOOR_LEAGUES
          else f"{lgname} 공식 결과")
    _dtk, _dtl = kst_day_label(games, day)
    body = (_hdr(*LEAGUE_COLORS[lg], lgname, "경기 결과",
                 _dtk,
                 f'{esc(lgname)} <em>{len(fin)}경기</em> 종료',
                 f"아래에 나머지 {len(games) - len(shown)}경기" if len(games) > len(shown) else "",
                 league=lg, dt_local=_dtl) +
            f'<div class="body">{"".join(rows)}</div>'
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


def render_start_alert(gs: list[Game], now: datetime | None = None) -> str:
    """오늘 그 리그의 **경기 시간표 전체**를 한 메시지로 (v1.11c).

    전에는 같은 시각 경기만 묶어 시각마다 따로 보냈고, 실측 하루 26건이 나왔다.
    이제 하루 한 번이므로 이 한 통에 그날 정보가 다 들어가야 한다 —
    시각별로 묶어 보여주고, 첫 경기까지 남은 시간을 계산해 붙인다.
    """
    now = now or datetime.now(timezone.utc)
    lg = gs[0].league
    ordered = sorted(gs, key=lambda x: x.start_utc)
    first = ordered[0].start_utc

    # 같은 시각 경기는 한 줄 아래 모은다. 15경기가 15줄이면 읽히지 않는다.
    by_time: dict[str, list[Game]] = defaultdict(list)
    for g in ordered:
        by_time[g.start_kst.strftime("%H:%M")].append(g)

    lines: list[str] = []
    for t, group in by_time.items():
        lines.append(f"◆ {esc(t)}")
        for g in group:
            row = f"  {esc(team_name(g.away))} vs {esc(team_name(g.home))}"
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

    # 현지 시간 병기가 필요한 리그(해외)는 첫 경기 기준으로 한 줄 덧붙인다
    _, loc = format_kickoff(ordered[0])
    first_kst = ordered[0].start_kst
    tail = (f"\n첫 경기 {first_kst:%H:%M} 시작 ({esc(when)})"
            + (f" · 현지 {esc(loc)}" if loc else ""))

    # **'오늘'은 발송 시점 한국 날짜 기준으로 말한다 (v1.11f).**
    # MLB 알림은 한국시각 밤 10시에 나가는데 첫 경기는 다음날 새벽 1시다.
    # 그걸 "오늘 MLB 14경기"라고 부르면 거짓말이고, 아침에 읽는 사람에게는
    # 이미 다 끝난 경기 목록이 '오늘 경기'로 보인다(대표님 지적 2026-08-31).
    days = (first_kst.date() - now.astimezone(KST).date()).days
    if days <= 0:
        head_when = "오늘"
    elif days == 1:
        head_when = "내일 새벽" if first_kst.hour < 6 else "내일"
    else:
        head_when = f"{first_kst.month}월 {first_kst.day}일"

    emoji = LEAGUE_EMOJI.get(lg, "🏟")
    label = LEAGUE_LABEL.get(lg, lg.value)
    head = f"{emoji} <b>{head_when} {esc(label)} {len(gs)}경기</b>\n"
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
EXTRA_CSS = ""   # v3.0에서 카드 CSS로 흡수됨


def _dt(day: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return f"{d.month}.{d.day} {_WD[d.weekday()]}"


def _wld(w: WLD) -> str:
    """무승부가 있는 리그만 세 칸으로 쓴다.

    MLB는 무승부가 없어 '83-51-0'이 되고, 그 한 글자 때문에 순위표 열이 줄바꿈으로 깨졌다.
    """
    return f"{w.win}-{w.loss}-{w.draw}" if w.draw else f"{w.win}-{w.loss}"


def _streak(s: Standing) -> str:
    if s.streak_kind is StreakKind.NONE or not s.streak_len:
        return "—"
    return f"{s.streak_len}{ {'W': '승', 'L': '패', 'D': '무'}[s.streak_kind.value] }"


def record_headline(rb: RecordBook) -> tuple[str, str]:
    """골드 패널에 들어갈 '기록 한 줄'. 결정론적 템플릿만 쓴다.

    후보를 우선순위대로 훑어 첫 번째로 성립하는 것을 쓴다.
    성립하는 게 없으면 선두-2위 승차라는 항상 성립하는 문장으로 떨어진다.
    """
    order = sorted(rb.standings, key=lambda x: x.rank)
    top, second = order[0], order[1]

    # 1) 5연승 이상 / 5연패 이상
    hot = [s for s in order if s.streak_kind is StreakKind.WIN and s.streak_len >= 5]
    if hot:
        s = max(hot, key=lambda x: x.streak_len)
        return ("연승", f"<b>{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</b> {s.streak_len}연승 "
                        f"— 리그 최장 (현재 {s.rank}위)")
    cold = [s for s in order if s.streak_kind is StreakKind.LOSS and s.streak_len >= 5]
    if cold:
        s = max(cold, key=lambda x: x.streak_len)
        return ("연패", f"<b>{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</b> {s.streak_len}연패 "
                        f"— 리그 최장 (현재 {s.rank}위)")

    # 2) 최근 10경기 8승 이상 / 8패 이상
    for s in order:
        if s.last10 and s.last10.win >= 8:
            return ("최근 10경기",
                    f"<b>{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</b> 최근 10경기 "
                    f"{s.last10.win}승{s.last10.loss}패 — 리그 최고 페이스")
    for s in order:
        if s.last10 and s.last10.loss >= 8:
            return ("최근 10경기",
                    f"<b>{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</b> 최근 10경기 "
                    f"{s.last10.win}승{s.last10.loss}패")

    # 3) 항상 성립 — 선두-2위 승차
    return ("선두 경쟁",
            f"<b>{esc(TEAM_NAMES[rb.league].get(top.team_code, top.team_code))}</b>가 2위 "
            f"{esc(TEAM_NAMES[rb.league].get(second.team_code, second.team_code))}에 {second.games_behind}경기 앞선 선두")


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
    has_draw = any(x.record.draw for x in order)
    wl = "승-패-무" if has_draw else "승-패"
    head = (f'<tr><th class="pad">순위</th><th>팀</th><th>{wl}</th><th>승률</th>'
            '<th>승차</th><th>최근10</th><th class="padr">연속</th></tr>')
    rows = []
    for s_ in order:
        cls = ' class="hl"' if highlight and s_.team_code == highlight else ""
        l10 = _wld(s_.last10) if s_.last10 else "—"
        gb = "—" if s_.rank == 1 else esc(s_.games_behind)
        rows.append(
            f'<tr{cls}><td class="pad">{s_.rank}</td>'
            f'<td>{esc(TEAM_NAMES[s_.league].get(s_.team_code, s_.team_code))}</td>'
            f'<td>{_wld(s_.record)}</td>'
            f'<td>{esc(s_.pct)}</td><td>{gb}</td><td>{l10}</td>'
            f'<td class="padr">{_streak(s_)}</td></tr>')

    label, line = record_headline(rb)
    lgname = LEAGUE_LABEL.get(rb.league, rb.league.value)
    sub = f"전체 {total}팀 중 상위 {len(order)}팀" if len(order) < total else ""
    body = (_hdr(*LEAGUE_COLORS[rb.league], lgname, "팀 순위", _dt(day),
                 f'1·2위 <em>{esc(gap)}경기</em> 차', sub, league=rb.league) +
            f'<div class="body"><table class="stb">{head}{"".join(rows)}</table></div>'
            f'<div class="rec"><div class="l">{esc(label)}</div>'
            f'<div class="t">{line}</div></div>'
            f'<div class="foot"><div class="tk">최근10 = {wl} · {esc(lgname)} 공식기록</div>'
            f'<div class="lg">NUDE-TV.NET</div></div>')
    return _card(body, rb.league)


# 리더보드 세트 — 요일로 돌린다. 부문명은 Top5 페이지의 표기를 그대로 쓴다.
LEADER_SETS: list[tuple[str, list[str]]] = [
    ("타격 부문", ["타율", "홈런", "타점", "도루"]),
    ("투수 부문", ["평균자책점", "승리", "탈삼진", "세이브"]),
    ("출루·장타", ["출루율", "장타율", "OPS", "안타"]),
    ("제구·이닝", ["WHIP", "QS", "이닝", "피안타율"]),
]


def render_leaders(rb: RecordBook, day: str, set_idx: int = 0, top_n: int = 5) -> str:
    """부문별 리더보드 카드. 4부문 × TOP N.

    리그마다 있는 부문이 다르다. 없는 부문을 요구하면 카드가 아예 안 나가므로,
    그 리그가 실제로 가진 부문 중에서 세트를 채운다. 순서는 세트 정의를 따른다.
    """
    assert_recordbook(rb, require_h2h=bool(rb.h2h))
    title, wanted = LEADER_SETS[set_idx % len(LEADER_SETS)]
    cats = [c for c in wanted if c in rb.leaders]
    if len(cats) < 4:
        # 남는 자리는 그 리그가 가진 다른 부문으로 채운다
        extra = [c for c in rb.leaders if c not in cats]
        cats = (cats + extra)[:4]
    if not cats:
        raise GateError(f"리더보드: {rb.league.value}에 쓸 부문이 없다")

    boxes = []
    for c in cats:
        rows = []
        for e in rb.leaders[c][:top_n]:
            r1 = " r1" if e.rank == 1 else ""
            # 이름+팀이 길면 팀은 약칭으로. MLB는 선수 이름이 영문이라 한글 팀명까지
            # 붙이면 자리가 없어 '애스…'처럼 잘린다 — 잘린 팀명은 정보가 아니다.
            tn = TEAM_NAMES[rb.league].get(e.team_code, e.team_code)
            if len(e.name) + len(tn) > 9:
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
                 f"{esc(lgname)} 공식 부문 순위 · 규정 미달 선수 포함", league=rb.league) +
            f'<div class="body"><div class="lb">{"".join(boxes)}</div></div>'
            f'<div class="foot"><div class="tk">{esc(lgname)} 공식기록 · {esc(_dt(day))} 기준</div>'
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

    played = wld.win + wld.loss
    aw = round(wld.win / played * 100) if played else 50
    kst, _ = format_kickoff(game)

    def side(s: Standing, right: bool) -> str:
        # 한 줄에 몰아넣으면 폭이 모자라 줄바꿈이 깨진다. 두 줄로 나눈다.
        cls = "tr rt" if right else "tr"
        l10 = f"최근10 {_wld(s.last10)}" if s.last10 else ""
        return (f'<div{" class=rt" if right else ""}>'
                f'<div class="tn">{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))}</div>'
                f'<div class="{cls}">{s.rank}위 · {_wld(s.record)}</div>'
                f'<div class="{cls} l2">{l10}</div></div>')

    # 막대 색은 '우세팀'이 골드. 원정/홈이 아니라 전적이 색을 정한다.
    ca, ch = ("w", "l") if wld.win > wld.loss else ("l", "w") if wld.win < wld.loss else ("l", "l")

    if wld.win == wld.loss:
        edge = f"시즌 상대전적 <b>{wld.win}승 {wld.loss}패</b> 팽팽"
    else:
        lead_t, lw, ll = ((a, wld.win, wld.loss) if wld.win > wld.loss
                          else (h, wld.loss, wld.win))
        edge = (f"시즌 상대전적 <b>{esc(TEAM_NAMES[rb.league].get(lead_t, lead_t))} {lw}승 {ll}패</b> 우세")
    # "두산 7승 4패 우세" 바로 밑에 "두산 2패"만 있으면 무슨 2패인지 모른다.
    parts = [f"{esc(TEAM_NAMES[s.league].get(s.team_code, s.team_code))} {s.streak_len}연{ {'W':'승','L':'패','D':'무'}[s.streak_kind.value] }"
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
            f'<div class="bar"><div class="fill" style="width:{aw}%"></div></div>'
            f'<span class="pct">{wld.win}승</span></div>'
            f'<div class="brow {ch}"><span class="bn">{esc(TEAM_NAMES[rb.league].get(h, h))}</span>'
            f'<div class="bar"><div class="fill" style="width:{100-aw}%"></div></div>'
            f'<span class="pct">{wld.loss}승</span></div></div>'
            f'<div class="rec"><div class="l">맞대결</div><div class="t">{edge}'
            + (f'<br>{streaks}' if streaks else "") +
            f'</div></div>'
            f'<div class="foot"><div class="tk">시즌 상대전적 = 승-패-무 · {esc(lgname)} 공식기록</div>'
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


def _clip(head: str, lines: list[str], tail: str = "") -> str:
    """캡션 하나로 만든다. 넘치면 줄인다 — 하지만 버리지는 않는다.

    잘린 나머지는 `_clip_parts()`가 후속 텍스트 메시지로 이어 보낸다.
    이 함수만 쓰면 초과분이 사라지므로, 발송 경로는 반드시 `_clip_parts()`를 쓴다.
    """
    return _clip_parts(head, lines, tail)[0]


def _clip_parts(head: str, lines: list[str], tail: str = "") -> list[str]:
    """캡션 + (필요하면) 후속 텍스트 메시지들.

    대표님 지시: **"카드에 다 안 들어가는 내용은 상위 옵션을 넣고, 전체 내용은 텍스트로."**
    그래서 초과분을 '외 N건'으로 버리지 않는다 — 버리면 '전체'가 아니다.

    · [0] 사진에 붙는 캡션 (텔레그램 상한 1024자)
    · [1:] 이어 보내는 텍스트 메시지 (상한 4096자, 필요한 만큼)

    실측상 가장 긴 케이스(MLB 순위 30팀)가 707자라 평소엔 [0] 하나로 끝난다.
    이 분할은 리그가 늘거나 더블헤더가 겹쳐 넘칠 때를 위한 안전망이다.
    """
    def build(ls: list[str], more: int) -> str:
        body = quote(ls, expandable=True)
        note = f"\n<i>나머지 {more}건은 이어지는 메시지에</i>" if more else ""
        return head + body + note + (("\n" + tail) if tail else "")

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
    """이어지는 텍스트 메시지. 캡션과 같은 접고펼치기 인용블록을 쓴다."""
    return "<b>(이어서)</b>\n" + quote(lines, expandable=True)


def caption_morning(games: list[Game], day: str, *, as_parts: bool = False):
    """오늘 편성 전체. 카드에 상위 N만 실렸어도 여기엔 전부 있다.

    as_parts=True면 [캡션, 이어지는 텍스트...] 목록을 준다(발송 경로가 쓴다).
    """
    off = {Status.CANCELED, Status.POSTPONED}
    lines = []
    for g in sorted(games, key=lambda x: x.start_utc):
        kst, _ = format_kickoff(g)
        mark = f" · {esc(g.meta.cancel_reason or '취소')}" if g.status in off else ""
        lines.append(f"{esc(kst)}  {esc(team_name(g.away))} vs {esc(team_name(g.home))}{mark}")
    lg = games[0].league if games else League.KBO
    head = f"📋 <b>{esc(LEAGUE_LABEL.get(lg, lg.value))} 오늘 전체 편성 {len(games)}경기</b>\n"
    tail = start_alert_lead_text()
    return _clip_parts(head, lines, tail) if as_parts else _clip(head, lines, tail)


def caption_result(games: list[Game], day: str, *, as_parts: bool = False):
    """그날 전 경기 결과. as_parts=True면 파트 목록."""
    lines = []
    for g in sorted(games, key=lambda x: x.start_utc):
        a, h = esc(team_name(g.away)), esc(team_name(g.home))
        if g.status is Status.CANCELED:
            lines.append(f"{a} — {h} · {esc(g.meta.cancel_reason or '취소')}")
        elif g.score:
            lines.append(f"{a} {g.score.away} : {g.score.home} {h}")
    lg = games[0].league if games else League.KBO
    fin = [g for g in games if g.status is Status.FINAL]
    head = f"📋 <b>{esc(LEAGUE_LABEL.get(lg, lg.value))} 전 경기 결과 {len(fin)}경기</b>\n"
    return _clip_parts(head, lines) if as_parts else _clip(head, lines)


def caption_standings(rb: RecordBook, *, as_parts: bool = False):
    """순위표 전체. MLB는 30팀이라 카드엔 10팀만 실린다. as_parts=True면 파트 목록."""
    names = TEAM_NAMES.get(rb.league, {})
    lines = [f"{s.rank:>2}. {esc(names.get(s.team_code, s.team_code))} "
             f"{_wld(s.record)} · {esc(s.pct)}"
             for s in sorted(rb.standings, key=lambda x: x.rank)]
    head = (f"📋 <b>{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} "
            f"전체 순위 {len(rb.standings)}팀</b>\n")
    return _clip_parts(head, lines) if as_parts else _clip(head, lines)


def caption_leaders(rb: RecordBook, set_idx: int = 0, top_n: int = 5,
                    *, as_parts: bool = False):
    """부문 순위 전체 텍스트.

    카드는 4부문 × TOP5만 싣는다. KBO는 부문이 29개라 카드 한 장으로는 어림도 없다 —
    대표님 지시대로 **카드엔 상위만, 전체는 텍스트로** 붙인다.
    카드에 실린 그 세트의 전체 순위를 그대로 옮기고, 카드에 못 실린 나머지 부문은
    1위만 한 줄씩 적어 '무엇이 더 있는지'를 남긴다.
    """
    title, wanted = LEADER_SETS[set_idx % len(LEADER_SETS)]
    cats = [c for c in wanted if c in rb.leaders]
    if len(cats) < 4:
        cats = (cats + [c for c in rb.leaders if c not in cats])[:4]
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
    return _clip_parts(head, lines, tail) if as_parts else _clip(head, lines, tail)


def caption_matchup(rb: RecordBook, game: Game, *, as_parts: bool = False):
    """맞대결 분석 전체 텍스트. 카드엔 요약 막대만 실린다."""
    a, h = game.away.team_code, game.home.team_code
    sa, sh = rb.team(a), rb.team(h)
    if not sa or not sh:
        raise GateError(f"맞대결 캡션: 순위표에 없는 팀 {a}/{h}")
    names = TEAM_NAMES.get(rb.league, {})
    na, nh = esc(names.get(a, a)), esc(names.get(h, h))
    wld = rb.between(a, h)

    lines = []
    if wld is not None:
        lines.append(f"시즌 상대전적 {na} {wld.win}승 {wld.loss}패"
                     + (f" {wld.draw}무" if wld.draw else ""))
    for s, nm in ((sa, na), (sh, nh)):
        bits = [f"{s.rank}위", _wld(s.record), f"승률 {esc(s.pct)}"]
        if s.last10:
            bits.append(f"최근10 {_wld(s.last10)}")
        if s.streak_kind is not StreakKind.NONE and s.streak_len:
            bits.append(_streak(s))
        lines.append(f"{nm} — " + " · ".join(esc(b) for b in bits))
        lines.append(f"  홈 {_wld(s.home)} · 방문 {_wld(s.away)}")

    head = (f"📋 <b>{esc(LEAGUE_LABEL.get(rb.league, rb.league.value))} "
            f"{na} vs {nh} 맞대결</b>\n")
    tail = "상대전적 = 승-패-무"
    return _clip_parts(head, lines, tail) if as_parts else _clip(head, lines, tail)
