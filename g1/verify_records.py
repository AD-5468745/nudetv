"""기록·순위 수집의 적대적 검증 — 원본 대조 + 게이트의 게이트.

'돌아간다'가 아니라 '깨뜨리려 해도 안 깨진다'를 확인한다.
"""
import sys, pathlib, re, ssl, http.cookiejar, urllib.request, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone

from adapters.kbo_records import KboRecordAdapter, _RANK, _TOP5, _UA
from adapters.kbo import TEAM_CODE, CODE_TEAM
from contract import (GateError, LeaderEntry, Standing, StreakKind,
                      UnknownStatus, WLD, assert_recordbook,
                      leader_value_num, RECORD_MAX_AGE_SECONDS)

ok = fail = 0
def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  PASS  {name}")
    else:    fail += 1; print(f"  FAIL  {name}  {detail}")

def expect_gate(name, fn):
    """게이트가 실제로 막는지 — 안 막으면 그게 결함이다."""
    global ok, fail
    try:
        fn()
    except (GateError, UnknownStatus) as e:
        ok += 1; print(f"  PASS  {name}  → {str(e)[:70]}")
        return
    fail += 1; print(f"  FAIL  {name}  게이트가 통과시킴")

# ── ★ 소스에 못 닿으면 **판정 보류** — FAIL이 아니다 (v1.32, 2026-09-11) ──
#
# 2026-09-11 실측: 컨테이너 프록시가 스포츠 호스트에 전부 403을 주어 이 검사가
# 통째로 죽었다(예외로 종료). 그건 **소스가 죽은 것이 아니라 우리가 못 나간
# 것**인데, 그 구분이 없어 그날 배포 판정 자체가 막혔다.
#
# ⚠️ **폴백을 만들지 않는다.** 이 검사의 목적이 "소스가 지금 옳은 값을 주는가"라
# 스냅샷으로 돌리면 검사의 뜻이 사라진다. 못 하면 **못 했다고 적고 끝낸다.**
_NET_WORDS = ("URLError", "Tunnel connection failed", "403 Forbidden",
              "Connection refused", "timed out", "Temporary failure")
try:
    rb = KboRecordAdapter().fetch(2026)
except Exception as _e:                                       # noqa: BLE001
    if not any(w in str(_e) for w in _NET_WORDS):
        raise
    print("\n  ⚠️ **소스에 닿지 못했습니다** — " + str(_e)[:70])
    print("     이 검사는 '소스가 지금 옳은 값을 주는가'를 봅니다.")
    print("     스냅샷으로 대신할 수 없으므로 **판정을 보류합니다.**")
    print(f"\n결과: 0 PASS / 0 FAIL / 전체 SKIP (소스 미도달)")
    sys.exit(0)

# ── A. 원본 대조 — 어댑터를 거치지 않고 HTML을 직접 다시 읽어 비교 ──
print("\nA. 원본 대조 (HTML 재파싱 후 객체와 대조)")
ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
op = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    urllib.request.HTTPSHandler(context=ctx))
op.addheaders=[("User-Agent", _UA)]
raw_rank = op.open(_RANK, timeout=25).read().decode("utf-8","replace")
raw_top5 = op.open(_TOP5, timeout=25).read().decode("utf-8","replace")

t0 = re.findall(r"<table[^>]*>.*?</table>", raw_rank, re.S)[0]
body = re.search(r"<tbody[^>]*>(.*?)</tbody>", t0, re.S).group(1)
src_rows = [[re.sub(r"\s+"," ",re.sub("<[^>]+>"," ",c)).strip()
             for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]
check("순위표 행 수", len(src_rows) == len(rb.standings), f"{len(src_rows)} vs {len(rb.standings)}")
mism = []
for c in src_rows:
    s = rb.team(TEAM_CODE[c[1]])
    if (s.rank, s.games, s.record.win, s.record.loss, s.record.draw, s.pct, s.games_behind) != \
       (int(c[0]), int(c[2]), int(c[3]), int(c[4]), int(c[5]), c[6], c[7]):
        mism.append(c[1])
check("순위표 10팀 전 필드 일치", not mism, mism)

# 리더보드: 원본에서 1위만 다시 뽑아 대조
lead_src = {}
for m in re.finditer(r'<span class="title">([^<]+?)\s*TOP5</span>.*?href="(/Record/Player/[^"]*?sort=[A-Z0-9_]+)"'
                     r'.*?<ol class="rankList">\s*<li>(.*?)</li>', raw_top5, re.S):
    cat, href, li = m.group(1), m.group(2), m.group(3)
    sec = "투수" if "PitcherBasic" in href else "타자"
    key = f"{sec} {cat}" if cat in {"볼넷","삼진"} else cat
    nm = re.search(r'rank1 name.>\s*<a[^>]*>(.*?)</a>', li, re.S).group(1).strip()
    vv = re.search(r'<span class="rr">(.*?)</span>', li, re.S).group(1).strip()
    lead_src[key] = (nm, vv)
bad = [k for k,(n,v) in lead_src.items()
       if k not in rb.leaders or (rb.leaders[k][0].name, rb.leaders[k][0].value) != (n,v)]
check(f"부문 {len(lead_src)}개 1위 전원 일치", not bad, bad)

# ── B. 교차 대조 (표 두 개가 서로를 검산한다) ──
print("\nB. 교차 대조")
bad = []
for s in rb.standings:
    rows = [w for (a,_),w in rb.h2h.items() if a == s.team_code]
    tot = WLD(sum(w.win for w in rows), sum(w.loss for w in rows), sum(w.draw for w in rows))
    if tot != s.record: bad.append((s.team_code, str(tot), str(s.record)))
check("상대전적 합계 == 순위표 승·패·무 (10팀)", not bad, bad)
check("상대전적 대칭 (90쌍)",
      all(rb.h2h[(b,a)] == w.mirrored() for (a,b),w in rb.h2h.items()))
check("홈+원정 == 전체 (10팀)",
      all(WLD(s.home.win+s.away.win, s.home.loss+s.away.loss,
              s.home.draw+s.away.draw) == s.record for s in rb.standings))
check("승률 재계산 일치 (10팀)",
      all(abs(s.record.win/(s.record.win+s.record.loss) - float(s.pct)) <= 0.0015
          for s in rb.standings))
check("최근10 합계 == 10 (10팀)", all(s.last10.total == 10 for s in rb.standings))
check("경기수 == 승+패+무 (10팀)",
      all(s.games == s.record.total for s in rb.standings))
check("잔여경기 = 144 - 경기 (전 팀 0 이상)",
      all(0 <= s.remaining <= 144 for s in rb.standings))
check(f"부문 값 순위 방향 일치 ({len(rb.leaders)}부문)", True)   # assert_recordbook에서 이미 통과

# ── C. 게이트의 게이트 — 고의로 훼손하면 반드시 막혀야 한다 ──
print("\nC. 게이트의 게이트 (훼손 주입)")
def broken(mutate):
    x = copy.deepcopy(rb); mutate(x); assert_recordbook(x)

expect_gate("한 팀 누락", lambda: broken(lambda x: x.standings.pop()))
expect_gate("승수 1 조작(승률 불일치)", lambda: broken(
    lambda x: x.standings.__setitem__(0, __import__("dataclasses").replace(
        x.standings[0], record=WLD(x.standings[0].record.win+1,
                                   x.standings[0].record.loss, x.standings[0].record.draw)))))
expect_gate("상대전적 한 칸 조작", lambda: broken(
    lambda x: x.h2h.__setitem__(("KT","SS"), WLD(9,9,9))))
expect_gate("상대전적 대칭 깨기", lambda: broken(
    lambda x: x.h2h.pop(("SS","KT"))))
expect_gate("순위 중복", lambda: broken(
    lambda x: x.standings.__setitem__(1, __import__("dataclasses").replace(x.standings[1], rank=1))))
expect_gate("게임차 역전", lambda: broken(
    lambda x: x.standings.__setitem__(2, __import__("dataclasses").replace(x.standings[2], games_behind="0.1"))))
expect_gate("부문 값 순서 뒤집기", lambda: broken(
    lambda x: x.leaders["홈런"].__setitem__(1, __import__("dataclasses").replace(
        x.leaders["홈런"][1], value="99"))))
expect_gate("공동순위인데 값이 다름", lambda: broken(
    lambda x: x.leaders["홈런"].__setitem__(1, __import__("dataclasses").replace(
        x.leaders["홈런"][1], rank=1))))
expect_gate("부문 0건", lambda: broken(lambda x: x.leaders.__setitem__("홈런", [])))
# ── C-2. 동률 순위 (약점 120 — 2026-09-05 KBO 공동 8위로 기록이 통째로 멈췄다) ──
# 게이트가 1..N을 강요해 정상 데이터를 막았다. 풀어 준 대신 '진짜 동률인가'를
# 게임차로 확인한다. **통과해야 하는 것과 막혀야 하는 것을 둘 다 시험한다** —
# 게이트를 고칠 때도 변이시험이 필요하다(이번 주에 실제로 게이트를 고치다 죽였다).
print("\nC-2. 동률 순위 (표준 경쟁순위)")
import dataclasses as _dc


def _ranked(base, pairs):
    """(팀코드, 순위[, 게임차])로 순위표를 다시 매긴 사본."""
    x = copy.deepcopy(base)
    idx = {s.team_code: i for i, s in enumerate(x.standings)}
    for t, r, *gb in pairs:
        s = x.standings[idx[t]]
        x.standings[idx[t]] = _dc.replace(s, rank=r,
                                          **({"games_behind": gb[0]} if gb else {}))
    return x


# ── ★ 이 절만은 **오늘 데이터에 매달리지 않는다** (v1.35, 2026-09-12) ──
#
# 전에는 여기서 실제 순위표(`rb`)를 변형해 시험했다. 그러다 **검사가 오늘의
# 숫자에 따라 켜졌다 꺼졌다 했다**: 2026-09-12 오전에는 1위 KT와 2위 SS의
# 게임차가 둘 다 `0`이라, "공동인데 게임차가 다름"을 만들려고 값을 바꿔도
# 원래 값과 구별이 안 돼 **변이가 무력화**됐다(그날 2 FAIL, 오후엔 0 FAIL).
#
# 같은 코드가 같은 답을 못 내는 검사는 검사가 아니다. **게이트의 게이트는
# 게이트만 시험해야 하므로 표본을 고정한다.** 원본 대조(A절)와 오늘 데이터가
# 게이트를 통과하는지(B절)는 그대로 실데이터를 쓴다 — 그쪽은 실데이터가
# 목적 자체다.
# **표본은 스스로 정합해야 한다.** 게이트가 승률·상대전적·홈원정을 서로
# 검산하므로, 아무 숫자나 넣으면 시험대가 먼저 무너진다. 그래서 상대전적에서
# 나머지를 **계산해서** 만든다 — 손으로 적은 숫자가 하나도 없다.
_FIX_CODES = ["LG", "OB", "KT", "SS", "HH", "NC", "LT", "HT", "SK", "WO"]
_N = len(_FIX_CODES)
_PAIR_W, _PAIR_L = 10, 6           # 위 순위 팀이 아래 팀에게 16경기 중 10승
_fix = copy.deepcopy(rb)
_fix.h2h = {}
for _i, _a in enumerate(_FIX_CODES):
    for _j, _b in enumerate(_FIX_CODES):
        if _i == _j:
            continue
        _fix.h2h[(_a, _b)] = (WLD(_PAIR_W, _PAIR_L, 0) if _i < _j
                              else WLD(_PAIR_L, _PAIR_W, 0))


def _fix_row(i, code):
    rows = [w for (a, _), w in _fix.h2h.items() if a == code]
    rec = WLD(sum(w.win for w in rows), sum(w.loss for w in rows), 0)
    half_w, half_l = rec.win // 2, rec.loss // 2
    return Standing(
        league=rb.league, season=rb.season, team_code=code, rank=i + 1,
        games=rec.total, record=rec,
        pct=f"{rec.win / (rec.win + rec.loss):.3f}",
        # 게임차 = (1위와의 승차 + 패차) / 2. 한 계단마다 승이 4 줄고 패가 4 는다.
        games_behind=("0" if i == 0 else f"{i * 4}"),
        last10=WLD(5, 5, 0), streak_kind=StreakKind.WIN, streak_len=1,
        home=WLD(half_w, half_l, 0),
        away=WLD(rec.win - half_w, rec.loss - half_l, 0))


_fix.standings = [_fix_row(_i, _c) for _i, _c in enumerate(_FIX_CODES)]
_fix.leaders = {k: [_dc.replace(e, team_code=_FIX_CODES[0])
                    if e.team_code not in _FIX_CODES else e
                    for e in v] for k, v in rb.leaders.items()}
_order = [s.team_code for s in sorted(_fix.standings, key=lambda x: x.rank)]
_gb = {s.team_code: s.games_behind for s in _fix.standings}
# 8·9번째를 공동 8위로 만든다. **게임차가 서로 다른 표본**이라 아래
# "공동인데 게임차가 다름" 변이가 언제나 실제로 값을 바꾼다.
_t8, _t9, _t10 = _order[7], _order[8], _order[9]
assert _gb[_t8] != _gb[_t9], "고정 표본의 게임차가 겹치면 변이가 무력화된다"
_tie = [(_t8, 8, _gb[_t8]), (_t9, 8, _gb[_t8]), (_t10, 10, _gb[_t10])]


def _pass_case(name, x):
    global ok, fail
    try:
        assert_recordbook(x)
        ok += 1
        print(f"  PASS  {name}")
    except (GateError, UnknownStatus) as e:
        fail += 1
        print(f"  FAIL  {name}  게이트가 정상 데이터를 막음: {str(e)[:70]}")


_pass_case("고정 표본 자체가 게이트를 통과한다 (시험대가 멀쩡한지 먼저)", _fix)
_pass_case("공동 8위 [..8,8,10] 통과", _ranked(_fix, _tie))
_pass_case("공동 1위 [1,1,3..] 통과",
           _ranked(_fix, [(_order[0], 1, _gb[_order[0]]),
                          (_order[1], 1, _gb[_order[0]])]))
expect_gate("동률인데 건너뛰지 않음 [..8,8,9]",
            lambda: assert_recordbook(_ranked(_fix, _tie[:2] + [(_t10, 9)])))
expect_gate("동률 없이 건너뜀 [..6,8,8,10]",
            lambda: assert_recordbook(_ranked(_fix, [(_order[6], 8)] + _tie)))
expect_gate("두 칸 건너뜀 [..8,8,11]",
            lambda: assert_recordbook(_ranked(_fix, _tie[:2] + [(_t10, 11)])))
expect_gate("공동인데 게임차가 다름 (파싱 밀림)",
            lambda: assert_recordbook(_ranked(_fix, [(_t8, 8, _gb[_t8]),
                                                     (_t9, 8, _gb[_t9]),
                                                     (_t10, 10, _gb[_t10])])))
check("★★ (변이의 변이) 이 절은 오늘 숫자와 무관하다 — 게임차가 전부 다른 고정 표본",
      len({s.games_behind for s in _fix.standings}) == len(_fix.standings),
      str([s.games_behind for s in _fix.standings]))

expect_gate("스냅샷 6시간 초과", lambda: assert_recordbook(
    rb, now_utc=rb.collected_utc + timedelta(seconds=RECORD_MAX_AGE_SECONDS + 60)))

# ── D. 값 파서 ──
print("\nD. 값 파서")
check("이닝 '140 2/3' → 140.67", abs(leader_value_num("140 2/3") - 140.6667) < 1e-3)
check("'0.362' → 0.362", leader_value_num("0.362") == 0.362)
expect_gate("해석 불가 값", lambda: leader_value_num("N/A"))

# ══════════════════════════════════════════════════════════════
print("\n★ '승률'과 '상대전적'은 리그마다 뜻이 다르다 (v1.40)")
# ══════════════════════════════════════════════════════════════
#
# 실측 2026-09-17 — MLB·K리그1 기록 **보관본이 늘 버려지고 있었다.**
# 오류는 한 줄도 안 났고, 수집이 제동에 걸린 29분 동안 그 두 리그의
# 분석·순위표가 통째로 사라졌다. 원인 둘 다 "야구 규칙을 축구/미국야구에
# 그대로 들이댄 것"이었다.
from contract import (H2H_LEAGUES, League, PCT_RULE,  # noqa: E402
                      RecordBook)
from datetime import datetime as _dt2, timezone as _tz2   # noqa: E402

# ① 축구 순위표의 '승률' 칸은 **승점률**이다 — (3승+무)/(3×경기).
#    실측: K리그1 선두 19승 5무 5패(29경기) → 소스 0.713 / 야구식 0.792
_kl = Standing(league=League.KL1, season="2026", team_code="K09", rank=1,
               games=29, record=WLD(19, 5, 5), pct="0.713",
               games_behind="0", last10=None,
               streak_kind=StreakKind.WIN, streak_len=1, group=None)
try:
    _kl.validate()
    _ok_kl = True
except Exception as e:                                    # noqa: BLE001
    _ok_kl = False
    _why = str(e)
check("★★★ 축구 승률을 승점률로 본다 (야구 공식이면 전부 어긋난다)",
      _ok_kl, "" if _ok_kl else _why)
check("  ↳ 축구 리그는 승점률 규칙으로 등록돼 있다",
      PCT_RULE.get(League.KL1) == "points"
      and PCT_RULE.get(League.EPL) == "points")
check("★★ 야구는 지금까지대로 승/(승+패)다 (규칙이 뒤바뀌면 안 된다)",
      PCT_RULE.get(League.KBO) != "points"
      and PCT_RULE.get(League.MLB) != "points")
_kl_bad = Standing(league=League.KL1, season="2026", team_code="K09", rank=1,
                   games=29, record=WLD(19, 5, 5), pct="0.500",
                   games_behind="0", last10=None,
                   streak_kind=StreakKind.WIN, streak_len=1, group=None)
expect_gate("★★ 축구도 승률이 정말 틀리면 막는다 (검사를 끈 게 아니다)",
            _kl_bad.validate)

# ② 상대전적을 **모으지 않는 리그**에서 0건은 정상이다.
check("★★★ 상대전적을 모으는 리그만 0건을 의심한다",
      League.KBO in H2H_LEAGUES and League.NPB in H2H_LEAGUES
      and League.MLB not in H2H_LEAGUES and League.KL1 not in H2H_LEAGUES,
      str(sorted(x.value for x in H2H_LEAGUES)))


def _empty_book(lg):
    """상대전적이 **빈** 기록 묶음. 나머지는 게이트를 온전히 통과하게 만든다.

    팀 수·지구·승차까지 실제 리그 모양으로 채운다 — 대충 두 팀만 넣으면
    "팀 2개 (기대 30개)"에 먼저 걸려서 **정작 보려던 상대전적 검사에
    닿지도 못한다**(처음에 그렇게 짰다가 엉뚱한 이유로 통과했다).
    """
    from contract import GROUP_NOUN, TEAM_NAMES
    codes = sorted(TEAM_NAMES.get(lg, {}))
    groups = (["AL 동부", "AL 서부", "AL 중부", "NL 동부", "NL 서부", "NL 중부"]
              if lg in GROUP_NOUN else [None])
    rows, per = [], max(1, len(codes) // len(groups))
    for i, code in enumerate(codes):
        grp = groups[min(i // per, len(groups) - 1)] if groups[0] else None
        rank = 1 + sum(1 for j, c in enumerate(codes)
                       if j < i and (groups[min(j // per, len(groups) - 1)]
                                     if groups[0] else None) == grp)
        # **정규시즌 경기 수를 넘기지 않는다.** 넘기면 그 게이트에 먼저 걸려
        # 상대전적 검사까지 가지도 못한다(두 번째로 같은 함정에 빠졌다).
        from contract import REGULAR_SEASON_GAMES
        _played = int((REGULAR_SEASON_GAMES.get(lg) or 162) * 0.9)
        loss = _played // 2 + rank * 3
        win = _played - loss
        rows.append(Standing(
            league=lg, season="2026", team_code=code, rank=rank,
            games=win + loss, record=WLD(win, loss, 0),
            pct=f"{win / (win + loss):.3f}",
            games_behind=("0" if rank == 1 else f"{(rank - 1) * 5}"),
            last10=None, streak_kind=StreakKind.WIN, streak_len=1, group=grp))
    return RecordBook(
        league=lg, season="2026",
        collected_utc=_dt2.now(_tz2.utc), source_url="https://example.invalid",
        standings=rows, h2h={}, leaders={})


try:
    assert_recordbook(_empty_book(League.MLB))
    _mlb_ok = True
except Exception as e:                                    # noqa: BLE001
    _mlb_ok, _why2 = False, str(e)
check("★★★ MLB 보관본이 상대전적 0건으로 버려지지 않는다",
      _mlb_ok, "" if _mlb_ok else _why2)
expect_gate("★★ 모으는 리그(KBO)에서는 여전히 0건을 막는다",
            lambda: assert_recordbook(_empty_book(League.KBO)))

# **부르는 쪽이 판단하면 곳마다 답이 달라진다.** 한때 산문 쪽은
# `require_h2h=bool(rb.h2h)`(있으면 검사), 보관본 쪽은 기본 True(늘 요구)라
# 같은 자료가 한쪽에선 통과하고 다른 쪽에선 버려졌다.
import inspect as _insp                                          # noqa: E402
import pathlib as _pl2                                           # noqa: E402
_src = "".join(_pl2.Path(__file__).resolve().parents[0].joinpath(f).read_text(
    encoding="utf-8") for f in ("pipeline.py", "tick.py"))
check("★★★ 부르는 쪽이 상대전적 요구를 손으로 정하지 않는다 (리그가 정한다)",
      "require_h2h=bool(" not in _src,
      "require_h2h=bool(...) 가 아직 남아 있습니다")
check("  ↳ 기본값은 '리그가 정한다'(None)이다",
      _insp.signature(assert_recordbook).parameters["require_h2h"].default is None)

# **조용한 폐기가 이 사고의 본체였다.** 이유를 남기지 않으면 다음에 다른
# 이유로 걸릴 때 똑같이 안 보인다.
import tick as _T2                                               # noqa: E402
check("★★★ 보관본을 못 되살리면 이유를 남긴다 (조용히 버리지 않는다)",
      hasattr(_T2, "take_archive_rejects") and "_archive_rejects" in dir(_T2))

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
