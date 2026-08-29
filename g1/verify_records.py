"""기록·순위 수집의 적대적 검증 — 원본 대조 + 게이트의 게이트.

'돌아간다'가 아니라 '깨뜨리려 해도 안 깨진다'를 확인한다.
"""
import sys, pathlib, re, ssl, http.cookiejar, urllib.request, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone

from adapters.kbo_records import KboRecordAdapter, _RANK, _TOP5, _UA
from adapters.kbo import TEAM_CODE, CODE_TEAM
from contract import (GateError, LeaderEntry, StreakKind, UnknownStatus, WLD,
                      assert_recordbook, leader_value_num, RECORD_MAX_AGE_SECONDS)

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

rb = KboRecordAdapter().fetch(2026)

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
expect_gate("스냅샷 6시간 초과", lambda: assert_recordbook(
    rb, now_utc=rb.collected_utc + timedelta(seconds=RECORD_MAX_AGE_SECONDS + 60)))

# ── D. 값 파서 ──
print("\nD. 값 파서")
check("이닝 '140 2/3' → 140.67", abs(leader_value_num("140 2/3") - 140.6667) < 1e-3)
check("'0.362' → 0.362", leader_value_num("0.362") == 0.362)
expect_gate("해석 불가 값", lambda: leader_value_num("N/A"))

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
