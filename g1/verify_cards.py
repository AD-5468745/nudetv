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

print(f"\n결과: {ok} PASS / {fail} FAIL" + (f" / {skip} SKIP" if skip else ""))
sys.exit(1 if fail else 0)
