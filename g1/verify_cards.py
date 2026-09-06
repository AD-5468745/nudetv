"""카드 v5 적대적 검증 — 골격 · 캡션 · 실렌더 (v1.12 신설).

세 가지를 본다:
  1. **일곱 종류가 정말 구분되는가** — 대표님 불만 ④
  2. **카드와 함께 나가는 텍스트가 카드를 되풀이하지 않는가** — 대표님이 직접 지적
  3. **실제로 그려서** 접힘·겹침·이탈·두부가 없는가 — 숫자만 보면 못 잡는다

3번은 브라우저가 필요하다. 없는 환경에서는 SKIP으로 넘어가되 **SKIP은 PASS가 아니다** —
결과 줄에 따로 센다.

돌리는 법:  python3 g1/verify_cards.py
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import cards_v5 as C5                                         # noqa: E402
import headline as H                                          # noqa: E402
from contract import (CARD_THEME_DARK, CARD_THEME_PAPER, League,  # noqa: E402
                      LeaderEntry, Score, ScoreUnit, Standing, Status,
                      StreakKind, WLD, card_theme)

ok = fail = skip = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def _raise(fn) -> bool:
    """부르면 예외가 나는가. **게이트는 막는 것을 확인해야 게이트다.**"""
    try:
        fn()
    except Exception:                                             # noqa: BLE001
        return True
    return False


def plain(html: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", html))).strip()


# ── 시험용 ────────────────────────────────────────────────────
class _Ref:
    def __init__(self, c): self.team_code = c


class _Meta:
    def __init__(self, r=None): self.cancel_reason = r


class _G:
    def __init__(self, h, a, hs=None, as_=None, st=Status.FINAL, venue=None,
                 hh=9, reason=None):
        from datetime import datetime, timezone
        self.home, self.away = _Ref(h), _Ref(a)
        self.status = st
        self.score = Score(hs, as_, ScoreUnit.RUNS) if hs is not None else None
        self.venue = venue
        self.meta = _Meta(reason)
        self.start_utc = datetime(2026, 9, 4, hh, 30, tzinfo=timezone.utc)
        self.sports_day = "2026-09-04"
        # 날짜 표기(`kst_day_label`·`format_kickoff`)가 이 둘을 본다.
        # 시험용 대역이라도 **계약이 요구하는 것은 다 갖춰야** 검사가 실물을 대변한다 —
        # 없이 두면 "카드가 안 만들어진다"만 알고 왜인지는 못 본다.
        from contract import KST as _KST
        self.start_kst = self.start_utc.astimezone(_KST)
        self.start_local = self.start_kst        # 국내 리그: 현지 = 한국


def _st(code, rank, w, l, d=0, gb="0", streak=(StreakKind.WIN, 1), last10=None,
        group=None, league=League.KBO):
    return Standing(league=league, season="2026", team_code=code, rank=rank,
                    games=w + l + d, record=WLD(w, l, d),
                    pct=f"{w/(w+l):.3f}", games_behind=gb, last10=last10,
                    streak_kind=streak[0], streak_len=streak[1], group=group)


KBO, MLB = League.KBO, League.MLB
GAMES = [_G("LT", "HH", 3, 2, venue="사직"), _G("HT", "KT", 4, 3, venue="광주"),
         _G("WO", "NC", 1, 6, venue="고척"),
         _G("SK", "OB", None, None, st=Status.CANCELED, reason="우천취소")]
STAND = [_st("KT", 1, 69, 44, 3, "0", (StreakKind.LOSS, 1), WLD(6, 3, 1)),
         _st("SS", 2, 70, 46, 3, "0.5", (StreakKind.LOSS, 2), WLD(7, 2, 1)),
         _st("LG", 3, 68, 51, 1, "4", (StreakKind.WIN, 5), WLD(8, 2, 0))]
LEAD = {c: [LeaderEntry(category=c, stat_key=c, rank=i + 1, player_id=f"{c}{i}",
                        name=f"선수{i}", team_code=t, value=str(90 - i))
            for i, t in enumerate(["SS", "HT", "LG", "LT", "KT"])]
        for c in ("타율", "홈런", "타점", "도루", "안타", "득점")}

# ═════════════════════════════════════════════════════════════
print("\n1. 일곱 종류가 구분되는가 (대표님 불만 ④)")
# ═════════════════════════════════════════════════════════════
check("일곱 종류가 전부 등록돼 있다", len(C5.KIND_META) == 7, str(list(C5.KIND_META)))
_labels = [v[0] for v in C5.KIND_META.values()]
check("종류 이름이 서로 다르다", len(set(_labels)) == 7)
_icons = [v[1] for v in C5.KIND_META.values()]
check("★ 종류마다 아이콘 도형이 다르다 (색만으로는 안 갈린다)",
      len(set(_icons)) == 7, f"{len(set(_icons))}개")

# 같은 종류는 리그가 달라도 같은 골격, 다른 종류는 같은 리그여도 다른 골격
_head = H.fallback("result", final=3, off=1)
_r_kbo = C5.shell(kind="result", league=KBO, date_label="9.4",
                  head=_head, body=C5.body_scoreboard(GAMES, KBO), foot_left="x")
_r_mlb = C5.shell(kind="result", league=MLB, date_label="9.4",
                  head=_head, body=C5.body_scoreboard(GAMES, MLB), foot_left="x")
_s_kbo = C5.shell(kind="standings", league=KBO, date_label="9.4",
                  head=H.fallback("standings"),
                  body=C5.body_standings(STAND, KBO), foot_left="x")
check("같은 종류는 리그가 달라도 같은 라벨·아이콘",
      "경기 결과" in plain(_r_kbo) and "경기 결과" in plain(_r_mlb))
check("★ 같은 리그라도 종류가 다르면 라벨이 다르다",
      "팀 순위" in plain(_s_kbo) and "경기 결과" not in plain(_s_kbo))
check("리그가 다르면 테마가 갈린다 (반반 배분)",
      card_theme(KBO) == CARD_THEME_PAPER and card_theme(MLB) == CARD_THEME_DARK)
check("테마가 실제로 배경색을 바꾼다",
      C5.THEMES["dark"]["bg"] in _r_mlb and C5.THEMES["paper"]["bg"] in _r_kbo)
check("★ 테마가 갈려도 골격은 같다 (구분이 안 되는 문제가 되살아나면 안 된다)",
      plain(_r_kbo).count("vs") == plain(_r_mlb).count("vs"))
try:
    card_theme(League.UCL); _themed = True
except Exception:
    _themed = False
check("표에 있는 리그는 테마가 나온다", _themed)

# 모르는 종류를 넣으면 막는다
try:
    C5.shell(kind="없는종류", league=KBO, date_label="x", head=_head,
             body="", foot_left="x")
    check("모르는 카드 종류를 막는다", False, "통과시킴")
except ValueError:
    check("모르는 카드 종류를 막는다", True)

# ═════════════════════════════════════════════════════════════
print("\n2. ★ 텍스트가 카드를 되풀이하지 않는가 (대표님 지적)")
# ═════════════════════════════════════════════════════════════
# 옛 캡션은 모닝·결과·순위표에서 **카드를 100% 다시 썼다.**
_cases = [
    ("morning", KBO, H.fallback("morning", count=5)),
    ("result", KBO, H.fallback("result", final=3, off=1)),
    ("standings", KBO, H.fallback("standings")),
    ("start", KBO, H.for_start_alert(128, "18:30 한화 vs 롯데")),
    ("night", None, H.fallback("night", leagues=4, final=18)),
    ("analysis", KBO, H.fallback("analysis")),
]
for kind, lg, hd in _cases:
    parts = C5.caption(kind=kind, league=lg, head=hd, date_label="9.4 금")
    body = plain(parts[0])
    check(f"{kind}: 텍스트가 한 통이다", len(parts) == 1, f"{len(parts)}통")
    # **점수와 시각은 같은 모양이다**(`3:2` vs `18:30`) — 정규식으로는 못 가른다.
    # 되풀이인지 아닌지는 **목록인가**로 본다: 대진이 둘 이상이면 목록이다.
    # 시작 알림이 첫 경기 하나를 부제에 쓰는 것은 되풀이가 아니라 설계다.
    check(f"{kind}: 경기 목록을 되풀이하지 않는다",
          body.count("vs") <= 1 and body.count("—") <= 1, body[:90])
    check(f"{kind}: 두 줄 이내다", len(parts[0].split("\n")) <= 2,
          f"{len(parts[0].split(chr(10)))}줄")

check("★ 캡션이 비지 않는다 (비면 푸시 알림에 '사진'만 뜬다)",
      all(len(plain(C5.caption(kind=k, league=l, head=h)[0]).strip()) >= 8
          for k, l, h in _cases))
check("캡션에 리그와 종류가 들어간다 (알림에서 이것만 보인다)",
      all(C5.KIND_META[k][0] in plain(C5.caption(kind=k, league=l, head=h)[0])
          for k, l, h in _cases))

# 부문 순위만 예외 — **카드에 없는 부문**을 싣는다
_shown = ["타율", "홈런", "타점", "도루"]
_extra = C5.leaders_extra(LEAD, KBO, _shown)
check("부문: 카드에 실린 부문은 텍스트에서 뺀다",
      all(c not in " ".join(_extra) for c in _shown), " / ".join(_extra[:2]))
check("부문: 카드에 없는 부문만 남는다",
      set(x.split(" —")[0] for x in _extra) == {"안타", "득점"}, str(_extra))
_lp = C5.caption(kind="leaders", league=KBO, head=H.fallback("leaders", count=4),
                 extra_lines=_extra, extra_title=f"그 밖의 {len(_extra)}개 부문 1위")
check("부문: 카드 밖 내용이 있으면 인용블록을 붙인다",
      "blockquote" in _lp[0], _lp[0][:100])
check("부문: 인용블록이 접힌다 (긴 목록이 화면을 덮지 않게)",
      "expandable" in _lp[0])
check("부문에 카드 밖 내용이 없으면 인용블록도 없다",
      "blockquote" not in C5.caption(kind="leaders", league=KBO,
                                     head=H.fallback("leaders", count=4))[0])

# 텔레그램 상한
_big = C5.caption(kind="leaders", league=KBO, head=H.fallback("leaders", count=4),
                  extra_lines=[f"부문{i} — 선수{i} (LG) {i}" for i in range(200)],
                  extra_title="많은 부문")
check("캡션이 1024자를 넘지 않는다", len(_big[0]) <= C5.CAPTION_MAX, f"{len(_big[0])}자")
check("이어 보내는 텍스트가 4096자를 넘지 않는다",
      all(len(p) <= C5.FOLLOW_MAX for p in _big[1:]),
      str([len(p) for p in _big[1:]]))
_many = C5.caption(kind="leaders", league=KBO, head=H.fallback("leaders", count=4),
                   extra_lines=[f"부문{i} — 선수{i} (LG) {i}" for i in range(200)],
                   extra_title="많은 부문")
check("★ 넘치면 자르지 않고 이어 보낸다 ('전체'라는 약속을 지킨다)",
      len(_many) > 1 and "이어서" in _many[1], f"{len(_many)}통")
check("이어 보낸 것까지 합치면 전부 들어 있다",
      sum(p.count("부문") for p in _many) >= 200, str(sum(p.count("부문") for p in _many)))

# ═════════════════════════════════════════════════════════════
print("\n3. 본문 골격 — 없는 것은 칸도 만들지 않는다")
# ═════════════════════════════════════════════════════════════
_no10 = [_st("TB", 1, 83, 57, gb="0", group="AL 동부", league=MLB),
         _st("NYY", 2, 80, 60, gb="3", group="AL 동부", league=MLB)]
check("★ 최근10이 하나도 없으면 그 열을 통째로 뺀다 (빈 열은 '망가진 표'로 읽힌다)",
      "최근10" not in plain(C5.body_standings(_no10, MLB)))
check("최근10이 있으면 열이 나온다",
      "최근10" in plain(C5.body_standings(STAND, KBO)))
check("취소 경기는 점수 자리에 사유를 쓴다 (숨기지 않는다)",
      "우천취소" in plain(C5.body_scoreboard(GAMES, KBO)))
check("그날 경기가 전부 카드에 들어간다 ('나머지는 아래 글에'가 없다)",
      plain(C5.body_scoreboard(GAMES, KBO)).count(":") >= 3
      and "아래 글" not in plain(C5.body_scoreboard(GAMES, KBO)))
_mixed = C5.body_leaders({"타율": [], "홈런": LEAD["홈런"]}, KBO, ["타율", "홈런"])
check("빈 부문은 칸을 만들지 않는다",
      _mixed.count('"qb"') == 1 and "타율" not in plain(_mixed),
      f'칸 {_mixed.count(chr(34)+"qb"+chr(34))}개')
check("경기장 이름을 원문 그대로 내보내지 않는다",
      "Progressive" not in plain(C5.body_schedule(
          [_G("CLE", "DET", venue="Progressive Field")], MLB)),
      plain(C5.body_schedule([_G("CLE", "DET", venue="Progressive Field")], MLB)))

# ═════════════════════════════════════════════════════════════
print("\n4. 실렌더 — 그려 봐야 아는 것")
# ═════════════════════════════════════════════════════════════
_CARDS = [
    ("result-KBO", _r_kbo), ("result-MLB", _r_mlb), ("standings-KBO", _s_kbo),
    ("standings-MLB", C5.shell(kind="standings", league=MLB, date_label="9.4",
                               head=H.fallback("standings", label="AL 동부 순위"),
                               body=C5.body_standings(_no10, MLB), foot_left="x",
                               group_label="MLB AL 동부")),
    ("morning-KBO", C5.shell(kind="morning", league=KBO, date_label="9.4",
                             head=H.fallback("morning", count=4),
                             body=C5.body_schedule(GAMES, KBO), foot_left="x")),
    ("leaders-KBO", C5.shell(kind="leaders", league=KBO, date_label="9.4",
                             head=H.fallback("leaders", count=4),
                             body=C5.body_leaders(LEAD, KBO, _shown), foot_left="x")),
    ("night", C5.shell(kind="night", league=None, date_label="9.4",
                       head=H.fallback("night", leagues=4, final=18),
                       body=C5.body_index([("KBO", "5경기 종료", "x"),
                                           ("MLB", "9경기 종료", "y")]),
                       foot_left="x")),
]
# **가장 긴 이름으로도 그려 본다.** 지금 팀명이 짧아 우연히 무사한 것과
# 검사가 지켜 주는 것은 다르다(약점 62).
_long = [_st("세인트루이스", 1, 80, 60, gb="0", league=MLB),
         _st("샌프란시스코", 2, 70, 70, gb="10", league=MLB)]
_CARDS.append(("standings-긴이름",
               C5.shell(kind="standings", league=MLB, date_label="9.4",
                        head=H.fallback("standings"),
                        body=C5.body_standings(_long, MLB), foot_left="x")))

try:
    from playwright.async_api import async_playwright

    async def _run():
        found = []
        async with async_playwright() as p:
            b = await p.chromium.launch()
            pg = await b.new_page(viewport={"width": C5.CARD_W, "height": 900})
            for name, html in _CARDS:
                issues = await C5.audit(pg, html)
                found.append((name, issues))
            # ── 변이시험: 게이트가 정말 잡는가 ──
            mut = _s_kbo.replace('class="rk lead-rank"', 'class="rk top"')
            overlap = await C5.audit(pg, mut)
            narrow = _s_kbo.replace("minmax(150px,1fr)", "16px")
            wrapped = await C5.audit(pg, narrow)
            tiny = _s_kbo.replace("font-size:29px", "font-size:11px")
            small = await C5.audit(pg, tiny)
            await b.close()
        return found, overlap, wrapped, small

    _found, _ov, _wr, _sm = asyncio.run(_run())
    for name, issues in _found:
        check(f"실렌더 결함 없음: {name}", not issues, " | ".join(issues[:2]))
    check("★ 변이 — 클래스 이름을 충돌시키면 겹침을 잡는다",
          any("겹침" in x for x in _ov), str(_ov[:2]))
    check("★ 변이 — 칸을 좁히면 접힘을 잡는다",
          any("접힘" in x for x in _wr), str(_wr[:2]))
    check("★ 변이 — 글자를 줄이면 작은 글자를 잡는다",
          any("작은 글자" in x for x in _sm), str(_sm[:2]))
except ImportError:
    skip = len(_CARDS) + 3
    print(f"  SKIP  실렌더 {skip}건 — playwright가 없습니다 (SKIP은 PASS가 아닙니다)")


# ══════════════════════════════════════════════════════════════
# 5. 흐름표 — 야구 이닝 · 농구 쿼터 · 배구 세트 · 축구 타임라인
# ══════════════════════════════════════════════════════════════
#
# **골격이 하나이므로 검사도 하나다.** 종목이 늘어도 검사를 새로 짜지 않는다.
print("\n5. 흐름표 — 골격은 하나, 언어만 종목이 정한다")

_KBO_IN = dict(labels=[str(i) for i in range(1, 10)],
               away_name="한화", home_name="롯데",
               away=[0, 1, 3, 0, 0, 1, 3, 0, 6], home=[0, 0, 0, 0, 3, 0, 1, 0, 0],
               total_labels=["R", "H", "E"],
               away_totals=[14, 19, 0], home_totals=[4, 10, 2], highlight=True)

_h = C5.body_periods(**_KBO_IN)
check("이닝별 점수가 전부 표에 들어간다",
      all(f">{v}<" in _h or f'">{v}</span>' in _h for v in (6, 14, 19)), "")
check("0은 흐리게, 큰 이닝은 강조한다",
      'class="zero">0<' in _h and 'class="big">6<' in _h, "")
check("이긴 쪽 팀명이 굵다", 'fg fr win' in _h and _h.index('fg fr win') < _h.index('fg fr"'),
      "원정(한화)이 이겼으므로 첫 행이 win")

# **칸이 좁아지면 막는다** — "안 들어가면 줄인다"가 아니라 "들어가는지 먼저 본다"
try:
    C5.body_periods(labels=[str(i) for i in range(1, 19)], away_name="A", home_name="B",
                    away=[0] * 18, home=[0] * 18)
    check("★ 구간이 너무 많으면 막는다", False, "18구간이 통과했다")
except ValueError as e:
    check("★ 구간이 너무 많으면 막는다", "칸이 좁습니다" in str(e), str(e)[:60])

try:
    C5.body_periods(labels=["1", "2"], away_name="A", home_name="B", away=[1], home=[1, 2])
    check("★ 라벨과 점수 개수가 다르면 막는다", False, "통과했다")
except ValueError:
    check("★ 라벨과 점수 개수가 다르면 막는다", True, "")

# 축구 — 구간이 없다. 시각이 곧 흐름이다
_t = C5.body_timeline(away_name="맨시티", home_name="크리스털", away_win=True,
                      events=[(17, "away", "홀란", ""), (56, "home", "돈나룸마", "자책"),
                              (84, "away", "홀란", "")])
check("득점 시각이 오름차순으로 정렬된다", _t.index("17′") < _t.index("56′") < _t.index("84′"), "")
check("자책골은 소스가 준 표시를 그대로 단다", "(자책)" in _t, "")
check("득점이 없으면 '득점 없음'을 쓴다 (빈 표를 내지 않는다)",
      "득점 없음" in C5.body_timeline(away_name="A", home_name="B", events=[]), "")
# **원정이 왼쪽** — 야구·농구·배구 표는 원정이 첫 줄이다. 축구만 다르면
# 같은 카드 안에서 헤드라인과 표가 서로 다른 순서를 말한다(약점 95 계열)
check("★ 축구도 원정이 왼쪽 — 다른 종목 표와 순서가 같다",
      _t.index("맨시티") < _t.index("크리스털"), "")

try:
    from playwright.sync_api import sync_playwright        # noqa: F401
    import asyncio as _aio
    from playwright.async_api import async_playwright as _apw

    _FLOW = [
        ("흐름-야구", League.KBO, C5.body_periods(**_KBO_IN)),
        ("흐름-농구", League.KBL, C5.body_periods(
            labels=["1Q", "2Q", "3Q", "4Q"], away_name="부산 KCC", home_name="원주 DB",
            away=[31, 35, 20, 18], home=[18, 28, 21, 17],
            total_labels=["합계"], away_totals=[104], home_totals=[84])),
        ("흐름-배구", League.VLEAGUE_W, C5.body_periods(
            labels=["1세트", "2세트", "3세트", "4세트"],
            away_name="흥국생명", home_name="페퍼저축은행",
            away=[21, 25, 23, 16], home=[25, 20, 25, 25],
            total_labels=["승세트"], away_totals=[1], home_totals=[3])),
        ("흐름-축구", League.EPL, _t),
        # **가장 긴 이름으로도 그려 본다.** 지금 이름이 짧아 우연히 무사한 것과
        # 검사가 지켜 주는 것은 다르다(약점 62). 전 리그 최장 팀명을 실측으로 뽑았다 —
        # '디플러스 기아'(LCK) · '한국도로공사'·'페퍼저축은행'(V리그) ·
        # '세인트루이스'·'샌프란시스코'(MLB).
        ("흐름-최장이름", League.VLEAGUE_W, C5.body_periods(
            labels=["1세트", "2세트", "3세트", "4세트", "5세트"],
            away_name="페퍼저축은행", home_name="한국도로공사",
            away=[25, 20, 25, 23, 15], home=[23, 25, 21, 25, 13],
            total_labels=["승세트"], away_totals=[3], home_totals=[2])),
        ("흐름-최장이름-야구", League.MLB, C5.body_periods(
            labels=[str(i) for i in range(1, 13)],          # 연장 12회까지
            away_name="샌프란시스코", home_name="세인트루이스",
            away=[0, 1, 0, 2, 0, 0, 1, 0, 0, 0, 0, 1],
            home=[1, 0, 0, 0, 2, 0, 0, 1, 0, 0, 0, 0],
            total_labels=["R", "H", "E"],
            away_totals=[5, 11, 1], home_totals=[4, 9, 2], highlight=True)),
    ]

    async def _run_flow():
        found, clipped = [], []
        async with _apw() as pw:
            b = await pw.chromium.launch()
            pg = await b.new_page(viewport={"width": C5.CARD_W, "height": 1400})
            for name, lg, body in _FLOW:
                html = C5.shell(kind="result", league=lg, date_label="9.4 금",
                                head=H.Headline(rule="X", text="가 1 : 0 나", sub="", facts={}),
                                body=body, foot_left="네이버 스포츠")
                found.append((name, await C5.audit(pg, html)))
            # ── 변이시험: 잘림을 잡는가 ──
            # 실제로 '페퍼저축은행'이 '페퍼저축은헹'으로 나갔는데 게이트 넷이 통과했다.
            mut = C5.shell(kind="result", league=League.VLEAGUE_W, date_label="3.1 일",
                           head=H.Headline(rule="X", text="가 1 : 3 나", sub="", facts={}),
                           body=_FLOW[2][2].replace("212px", "150px"),
                           foot_left="변이시험")
            clipped = await C5.audit(pg, mut)
            # ── 변이시험: **검사 목록에 없는 새 골격도 잡는가** ──
            # 게이트가 '검사할 클래스 목록'이던 시절에는 새 골격을 만들 때마다
            # 그 목록에 넣는 것을 잊으면 그 칸이 무방비였다(약점 92). 이제
            # '전체 - 예외'로 뒤집었으니, **한 번도 등록한 적 없는 클래스**를
            # 일부러 접히게 만들어 잡히는지 본다.
            unknown = C5.shell(
                kind="result", league=League.KBO, date_label="9.4 금",
                head=H.Headline(rule="X", text="가 1 : 0 나", sub="", facts={}),
                body='<div class="li" style="grid-template-columns:1fr">'
                     '<span class="never-registered" style="width:60px;display:block">'
                     '한 번도 검사 목록에 넣은 적 없는 새 골격입니다</span></div>',
                foot_left="변이시험")
            newgate = await C5.audit(pg, unknown)
            await b.close()
        return found, clipped, newgate

    _ff, _clip, _new = _aio.run(_run_flow())
    for name, issues in _ff:
        check(f"실렌더 결함 없음: {name}", not issues, " | ".join(issues[:2]))
    check("★ 변이 — 팀명 칸을 좁히면 잘림을 잡는다",
          any("잘림" in x for x in _clip), str(_clip[:2]))
    check("★★ 변이 — 검사 목록에 없는 새 골격도 잡는다 (게이트가 '전체 - 예외'다)",
          any("접힘" in x for x in _new), str(_new[:2]))
except ImportError:
    skip += 5
    print("  SKIP  흐름표 실렌더 5건 — playwright가 없습니다 (SKIP은 PASS가 아닙니다)")

# ═════════════════════════════════════════════════════════════
print("\n7. ★★ 6종 배선이 정말 카드를 만드는가 (조용한 폴백 잡기)")
# ═════════════════════════════════════════════════════════════
#
# **이 절이 왜 필요한가.** tick.py의 `_try_v5`는 어떤 예외든 삼키고 옛 카드로
# 떨어진다 — 그래야 카드 하나가 그 리그 발송을 통째로 멈추지 않는다. 그런데
# 그 안전장치가 곧 **결함을 숨기는 자리**다. 실제로 분석 카드를 배선할 때
# `for_analysis(h2h=...)`에 표 전체가 아니라 한 쌍을 넘겨 TypeError가 났는데,
# 폴백이 그것을 삼켜서 **분석만 조용히 옛 카드로 나갔을 것**이다. 실렌더를
# 눈으로 보다 우연히 잡았다 — 우연에 기대는 것은 검사가 아니다.
#
# 그래서 여기서는 폴백을 거치지 않고 **함수를 직접 부른다.** 예외가 나면 FAIL,
# None을 돌려줘도 FAIL이다. "안 터진다"가 아니라 **"정말 만든다"**를 본다.
from datetime import datetime, timedelta, timezone                # noqa: E402
import render_v5 as R5                                            # noqa: E402
from contract import RecordBook                                   # noqa: E402

_now5 = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
_v5day = "2026-09-06"


def _sd(code, rank, w, l, d, gb, l10=None, sk=StreakKind.NONE, sl=0):
    return Standing(league=League.KBO, season="2026", team_code=code, rank=rank,
                    games=w + l + d, record=WLD(w, l, d),
                    pct=f"{w / (w + l):.3f}", games_behind=gb,
                    last10=l10, streak_kind=sk, streak_len=sl)


_v5st = [_sd("SS", 1, 72, 46, 3, "0", WLD(7, 2, 1), StreakKind.WIN, 2),
         _sd("KT", 2, 69, 46, 3, "1.5", WLD(5, 5, 0), StreakKind.LOSS, 3),
         _sd("LG", 3, 68, 53, 1, "5.5", WLD(7, 3, 0), StreakKind.LOSS, 2),
         _sd("HT", 4, 66, 52, 2, "6", WLD(6, 4, 0), StreakKind.WIN, 3)]
_v5ld = {c: [LeaderEntry(category=c, stat_key=c, rank=i + 1,
                         player_id=f"{c}{i}", name=f"선수{i}",
                         team_code=["SS", "KT", "LG", "HT"][i % 4],
                         value=f"{0.36 - i * 0.01:.3f}")
             for i in range(5)]
        for c in ("타율", "홈런", "타점", "도루")}
_v5rb = RecordBook(league=League.KBO, season="2026", collected_utc=_now5,
                   source_url="https://example.invalid/rec", standings=_v5st,
                   h2h={("SS", "KT"): WLD(8, 5, 0), ("KT", "SS"): WLD(5, 8, 0)},
                   leaders=_v5ld)
_v5games = [_G("SS", "KT", 5, 3), _G("LG", "HT", 2, 4)]
for _g in _v5games:
    _g.league = League.KBO
    # 시험용 `_G`는 계약의 `Game`이 아니라 이 파일의 최소 대역이다 —
    # 결과 카드가 보는 `is_terminal`을 여기서 채운다.
    _g.is_terminal = True
_v5sched = [_G("SS", "KT", st=Status.SCHEDULED), _G("LG", "HT", st=Status.SCHEDULED)]
for _g in _v5sched:
    _g.league = League.KBO
    _g.is_terminal = False
_v5ts = {"SS": {"avg": 0.279, "era": 4.13, "hr": 110},
         "KT": {"avg": 0.271, "era": 4.38, "hr": 90}}

_v5cases = [
    ("모닝", lambda: R5.morning_card(_v5sched, League.KBO, _v5day)),
    ("경기 결과", lambda: R5.result_card(_v5games, League.KBO, _v5day)),
    ("팀 순위", lambda: R5.standings_card(_v5rb, League.KBO, _v5day)),
    ("부문 순위", lambda: R5.leaders_card(_v5rb, League.KBO, _v5day, 0)),
    ("경기 분석", lambda: R5.analysis_card(_v5rb, _v5sched[0], League.KBO, _v5day,
                                        team_stats=_v5ts, history=_v5games)),
]
for _name, _fn in _v5cases:
    try:
        _made = _fn()
    except Exception as _e:                                       # noqa: BLE001
        _made = None
        check(f"★ {_name} 카드가 예외 없이 만들어진다", False,
              f"{_e.__class__.__name__}: {_e}")
    else:
        check(f"★ {_name} 카드가 예외 없이 만들어진다", bool(_made), "None을 돌려줬다")
    if _made:
        _html, _parts = _made
        check(f"  ↳ {_name}: 골격을 지났다", '<div class="card">' in _html)
        check(f"  ↳ {_name}: 캡션이 비지 않는다", bool(_parts) and bool(_parts[0].strip()))

# 나이트는 리그가 둘 이상이어야 만들어진다 — 하나면 그날 결과 카드와 같은 말이 된다.
_nt = list(_v5games)
_npb = _G("SOF", "HAN", 4, 1)
_npb.league = League.NPB
_npb.is_terminal = True
_nt.append(_npb)
try:
    _mn = R5.night_card(_nt, _v5day)
except Exception as _e:                                           # noqa: BLE001
    _mn = None
    check("★ 나이트 카드가 예외 없이 만들어진다", False, f"{_e.__class__.__name__}: {_e}")
else:
    check("★ 나이트 카드가 예외 없이 만들어진다", bool(_mn), "None을 돌려줬다")
check("나이트: 리그가 하나뿐이면 만들지 않는다 (결과 카드와 같은 말이 된다)",
      R5.night_card(_v5games, _v5day) is None)

# **스위치가 켜져 있는가.** 함수를 다 만들어 놓고 스위치를 안 켜면 아무 일도 안 난다 —
# 그것이 이번 작업 전의 상태였다(순위표가 옛 카드로 나가고 있었다).
for _k in ("result", "morning", "standings", "leaders", "analysis", "night"):
    check(f"USE_V5['{_k}'] 가 켜져 있다", R5.USE_V5.get(_k) is True)
check("USE_V5['start'] 는 꺼져 있다 (시작 알림은 이미지 카드가 아니다)",
      R5.USE_V5.get("start") is False)

# ── 날짜·낱말 (MLB 하루 밀림 — 대표님 지적 2026-08-31의 재발 방지) ──
#
# `sports_day`는 **홈 현지** 날짜다. 그대로 찍으면 한국시각 새벽에 열리는 MLB
# 슬레이트가 하루 묵은 카드로 보인다. 그리고 그 카드를 "오늘"이라 부르면
# 한 번 더 틀린다. 두 가지 모두 옛 카드는 이미 고쳐 놓았던 것이라,
# **새 카드가 되살릴 위험이 가장 큰 자리**다.
class _GM(_G):
    """MLB 대역 — 한국 날짜와 현지 날짜가 갈린다."""
    def __init__(self):
        super().__init__("PHI", "ATL", st=Status.SCHEDULED)
        from contract import KST as _K
        self.start_utc = datetime(2026, 9, 6, 17, 5, tzinfo=timezone.utc)
        self.start_kst = self.start_utc.astimezone(_K)          # 9/7 02:05
        self.start_local = self.start_utc - timedelta(hours=4)  # 9/6 13:05 (현지)
        self.sports_day = "2026-09-06"
        self.league = League.MLB
        self.is_terminal = False
        self.venue = None


_gm = _GM()
check("★ 날짜는 한국 기준으로 찍는다 (MLB 현지 9.6 → 한국 9.7)",
      "9.7" in R5._day_label("2026-09-06", [_gm]), R5._day_label("2026-09-06", [_gm]))
check("  ↳ 현지 날짜는 함께 밝힌다 (묶는 기준을 숨기지 않는다)",
      "현지 9.6" in R5._day_label("2026-09-06", [_gm]))
check("경기가 없으면 sports_day를 그대로 쓴다 (순위표·부문 순위)",
      R5._day_label("2026-09-06") == "9.6 일", R5._day_label("2026-09-06"))

from datetime import datetime as _dtm, timezone as _tz                # noqa: E402
_mm = R5.morning_card([_gm], League.MLB, "2026-09-06",
                      now=_dtm(2026, 9, 6, 1, 0, tzinfo=_tz.utc))     # 9/6 10:00 KST
check("★ 새벽 슬레이트를 '오늘'이라 부르지 않는다 (MLB 하루 착각)",
      _mm and "오늘" not in _mm[0].split('class="lead"')[1][:80],
      _mm and _mm[0].split('class="lead"')[1][:60])
check("  ↳ 대신 '내일 새벽'이라 적는다",
      _mm and "내일 새벽" in _mm[0], "")

# ── 밀도 사다리 ────────────────────────────────────────────────
_air = C5.shell(kind="standings", league=League.KBO, date_label="9.6 일",
                head=H.Headline("T", "머리"), body=C5.body_standings(_v5st, League.KBO),
                foot_left="4개 구단")
_tight = C5.relax(_air)
check("★ 여백판이 기본값이다 (대표님이 고른 안)",
      C5._DENSITY_CSS["air"] in _air and C5._DENSITY_CSS["tight"] not in _air)
check("★ relax()가 여백판을 조임판으로 한 단계 내린다",
      _tight and C5._DENSITY_CSS["tight"] in _tight
      and C5._DENSITY_CSS["air"] not in _tight)
check("  ↳ 밀도 말고는 아무것도 안 바뀐다 (골격·정보 그대로)",
      _tight and (_air.replace(C5._DENSITY_CSS["air"], C5._DENSITY_CSS["tight"])
                  == _tight))
check("조임판을 또 내리지는 않는다 (사다리는 한 칸이다)", C5.relax(_tight) is None)
check("모르는 밀도는 거부한다",
      _raise(lambda: C5.shell(kind="standings", league=League.KBO, date_label="x",
                              head=H.Headline("T", "t"), body="", foot_left="",
                              density="없는밀도")))

# ── 빈 열은 만들지 않는다 (약점 94 — NPB 연속 열) ───────────────
_no_st = [_sd("A", 1, 70, 44, 3, "0"), _sd("B", 2, 69, 51, 3, "4")]
_body_no = C5.body_standings(_no_st, League.NPB)
check("★ 연속 값이 하나도 없으면 '연속' 열을 만들지 않는다 (NPB 실측)",
      "연속" not in _body_no, _body_no[:120])
check("  ↳ 값이 하나라도 있으면 열을 남긴다",
      "연속" in C5.body_standings(_v5st, League.NPB))
_no10 = [_sd("A", 1, 70, 44, 3, "0"), _sd("B", 2, 69, 51, 3, "4")]
check("최근10도 마찬가지로 값이 없으면 열을 뺀다",
      "최근10" not in C5.body_standings(_no10, League.KBO))

print(f"\n결과: {ok} PASS / {fail} FAIL" + (f" / {skip} SKIP" if skip else ""))
sys.exit(1 if fail else 0)
