import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from datetime import datetime, timezone
from adapters.kbo_records import KboRecordAdapter
from adapters.kbo import KboAdapter, CODE_TEAM
import pipeline as P
from contract import Status, KST

OUT = pathlib.Path("dryrun"); OUT.mkdir(exist_ok=True)
rb = KboRecordAdapter().fetch(2026)
day = datetime.now(KST).strftime("%Y-%m-%d")

games = KboAdapter().fetch(2026, ["08", "09"])
todays = [g for g in games if g.sports_day == day]
print("오늘 경기", len(todays), [f"{CODE_TEAM[g.away.team_code]}@{CODE_TEAM[g.home.team_code]}" for g in todays])
pick = next((g for g in todays if g.status is Status.SCHEDULED), None) or \
       next(g for g in games if g.status is Status.SCHEDULED)

jobs = [
    ("standings", P.render_standings(rb, day, highlight=rb.standings[0].team_code)),
    ("leaders_hit", P.render_leaders(rb, day, 0)),
    ("leaders_pit", P.render_leaders(rb, day, 1)),
    ("matchup", P.render_matchup(rb, pick, day)),
]
for name, html in jobs:
    w, h, b = P.render_png(html, OUT / f"{name}.png")
    print(f"{name:12s} {w}x{h}  {b/1024:.0f}KB  aspect {h/w:.3f}")
