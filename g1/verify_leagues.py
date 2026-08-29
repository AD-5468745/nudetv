"""전 리그 수집기 통합 검증 (v1.11).

리그를 늘릴 때마다 이 파일을 돌린다. 하나가 깨지면 여기서 잡힌다.
"""
import sys, pathlib, collections
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import datetime

from contract import (GateError, KST, League, LEAGUE_TEAM_COUNT, Status, TEAM_NAMES,
                      SOURCE_RESULTLESS_CATEGORIES, UnknownStatus, stale_unresolved,
                      team_name)
import pipeline as P

ok = fail = skip = 0
def rep(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"    PASS  {name}")
    else:    fail += 1; print(f"    FAIL  {name}  {detail}")

def check_games(tag, games, league):
    """어느 리그든 지켜야 하는 것들."""
    print(f"  [{tag}] {len(games)}경기 " + str(dict(collections.Counter(x.status.value for x in games))))
    rep("경기 1건 이상", len(games) > 0)
    if not games: return
    rep("전 경기 validate 통과", all(g.validate() is None for g in games))
    rep("game_id 유일", len({g.game_id for g in games}) == len(games),
        f"{len(games)-len({g.game_id for g in games})}건 중복")
    rep("리그가 전부 일치", all(g.league is league for g in games))
    rep("sports_day 형식", all(len(g.sports_day) == 10 and g.sports_day[4] == '-' for g in games))
    rep("종료 경기는 점수 있음",
        all(g.score for g in games if g.status is Status.FINAL))
    rep("예정 경기는 점수 없음",
        all(g.score is None for g in games if g.status is Status.SCHEDULED))
    # 묵은 '예정' 검사는 계약이 들고 있다 — 전 리그 공통 결함이라 여기 두면 리그마다 새로 짜야 한다.
    # 날짜 비교가 아니라 start_utc + 6시간 유예로 본다(어제 밤 경기가 오늘 새벽까지 가는 정상 케이스 제외).
    stale = stale_unresolved(games)
    rep("묵은 '예정/진행중' 경기 0건", not stale,
        f"{len(stale)}건 예) {stale[0].sports_day if stale else ''} {stale[0].source_key if stale else ''}")
    known = TEAM_NAMES.get(league, {})
    if known:
        unk = {g.home.team_code for g in games} | {g.away.team_code for g in games}
        miss = sorted(unk - set(known))
        rep(f"팀 이름 등록 ({len(unk)-len(miss)}/{len(unk)})", len(miss) <= 12, f"미등록 {miss[:6]}")
    # 카드가 실제로 그려지는가
    day = max(g.sports_day for g in games)
    same = [g for g in games if g.sports_day == day]
    try:
        w, h, b = P.render_png(P.render_result(same, day) if any(g.status is Status.FINAL for g in same)
                               else P.render_morning(same, day),
                               pathlib.Path("dryrun") / f"_v_{tag}.png")
        rep(f"카드 렌더 {w}x{h}", w == 1080 and h <= 2000)
    except Exception as e:
        rep("카드 렌더", False, f"{type(e).__name__}: {str(e)[:70]}")


print("=" * 62)
print("전 리그 수집기 통합 검증")
print("=" * 62)

from adapters.kbo import KboAdapter
from adapters.kbo_records import KboRecordAdapter
from adapters.mlb import MlbAdapter, MlbRecordAdapter
from adapters.kbl import KblAdapter
from adapters.kovo import KovoAdapter
from adapters.kleague import KLeagueAdapter
from adapters.npb import NpbAdapter
import adapters.lck as _L
from adapters.lck import LckAdapter, RateLimited, LCK_TEAM_COUNT
# 검증은 빨라야 한다. 운영의 긴 재시도(최대 6분)를 그대로 쓰면 점검이 20분이 된다.
# 리밋이면 캐시로 떨어지고, 캐시도 없으면 SKIP으로 가른다.
_L._RATELIMIT_WAITS = (20,)

_kbl = KblAdapter()

JOBS = [
    ("KBO",     League.KBO,       lambda: KboAdapter().fetch(2026, ["07", "08", "09"])),
    ("MLB",     League.MLB,       lambda: MlbAdapter().fetch("2026-08-01", "2026-09-11")),
    ("KBL",     League.KBL,       lambda: _kbl.fetch("20251001", "20260430")),
    ("V리그 남", League.VLEAGUE_M, lambda: KovoAdapter("1").fetch("023")),
    ("V리그 여", League.VLEAGUE_W, lambda: KovoAdapter("2").fetch("023")),
    ("K리그1",  League.KL1,       lambda: KLeagueAdapter().fetch(2026, ["07", "08", "09"])),
    ("NPB",     League.NPB,       lambda: NpbAdapter().fetch(2026, ["07", "08", "09"])),
]
for tag, lg, fn in JOBS:
    print()
    try:
        check_games(tag, fn(), lg)
        if tag == "KBL":
            # 격리는 조용히 사라지면 안 된다. 몇 건을 왜 뺐는지 항상 찍는다.
            u = _kbl.unresolved
            print(f"    ── 격리 {len(u)}건 (소스 소관 밖) "
                  + (f"예) {u[0]['sports_day']} {u[0]['category']} — {u[0]['reason']}" if u else ""))
            rep("격리는 전부 결과 미제공 구간(EA)만",
                all(x["category"] in SOURCE_RESULTLESS_CATEGORIES.get(League.KBL, set()) for x in u),
                f"다른 구간 {[x['category'] for x in u if x['category'] != 'EA'][:3]}")
    except (GateError, UnknownStatus) as e:
        fail += 1; print(f"  [{tag}] FAIL  {type(e).__name__}: {str(e)[:90]}")
    except Exception as e:                                        # noqa: BLE001
        fail += 1; print(f"  [{tag}] FAIL  예상 못한 예외 {type(e).__name__}: {str(e)[:90]}")

print("\n  [기록·순위]")
for tag, fn, need_h2h in [("KBO", lambda: KboRecordAdapter().fetch(2026), True),
                          ("MLB", lambda: MlbRecordAdapter().fetch(2026), False)]:
    try:
        rb = fn()
        n = LEAGUE_TEAM_COUNT.get(rb.league)
        rep(f"{tag} 순위 {len(rb.standings)}팀 · 부문 {len(rb.leaders)}개",
            len(rb.standings) == n and len(rb.leaders) > 0)
        rep(f"{tag} 상대전적 {'있음' if rb.h2h else '없음'}", bool(rb.h2h) == need_h2h)
    except Exception as e:
        fail += 1; print(f"    FAIL  {tag} 기록 {type(e).__name__}: {str(e)[:70]}")

print("\n  [키 대기 중인 리그]")
from adapters.football_data import TOKEN_ENV, COMPETITION
import os
if os.environ.get(TOKEN_ENV):
    print("    키 있음 — 유럽 6개 대회 검증 가능")
else:
    skip += 6
    print(f"    SKIP  유럽 6개 대회 — {TOKEN_ENV} 미설정 (대표님 발급 대기)")
# LCK·국제는 Leaguepedia 시간당 쿼터에 걸릴 수 있다.
# 리밋은 '검증 못 함'이지 '깨짐'이 아니므로 SKIP으로 가른다 — 둘을 섞으면
# 진짜 결함이 리밋 소음에 묻힌다.
for tag, lg in (("LCK", League.LCK), ("LoL 국제", League.INTL_LOL)):
    a = LckAdapter(lg)
    try:
        gs = a.fetch("2026-01-01")
    except RateLimited:
        skip += 1
        print(f"    SKIP  {tag} — Leaguepedia 시간당 쿼터 (캐시도 없음)")
        continue
    except (GateError, UnknownStatus) as e:
        fail += 1
        print(f"    FAIL  {tag} {type(e).__name__}: {str(e)[:80]}")
        continue
    codes = {g.home.team_code for g in gs} | {g.away.team_code for g in gs}
    age = f" · 캐시 {a.cache_age_seconds/3600:.1f}시간" if a.cache_age_seconds else ""
    print(f"  [{tag}] {len(gs)}경기 · 자리표시자 {a.skipped_placeholder}건 제외{age}")
    rep(f"{tag} 경기 1건 이상", len(gs) > 0)
    if gs:
        rep(f"{tag} validate·id유일",
            all(g.validate() is None for g in gs) and len({g.game_id for g in gs}) == len(gs))
        rep(f"{tag} 묵은 '예정' 0건", not stale_unresolved(gs))
        if lg is League.LCK:
            # 팀이 10개를 넘으면 네이밍 스폰서 개명을 놓친 것이다 (한 팀이 두 팀으로 갈림)
            rep(f"{tag} 팀 {len(codes)}개 == {LCK_TEAM_COUNT}", len(codes) == LCK_TEAM_COUNT,
                f"별칭 누락 의심: {sorted(codes)}")

print(f"\n결과: {ok} PASS / {fail} FAIL / {skip} SKIP")
sys.exit(1 if fail else 0)
