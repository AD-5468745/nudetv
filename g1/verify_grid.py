"""격자 점검(`audit_grid`)의 적대적 검증 — v1.33.

이 검사의 합격 기준은 **"조용해졌는가"가 아니다.**
거짓 양성 세 개를 접으면서 **진짜 결함은 그대로 빨간불로 남는가**다.

그래서 이 파일의 중심은 §B다: 2026-09-10 아침의 KBO 킥오프 상황
(경기는 있는데 킥오프 0건)을 그대로 넣어 **지금도 빨간불이 뜨는지** 본다.
그게 안 되면 이 도구는 소음만 줄이고 눈을 가린 것이다(§7-139).
"""
import sys
import pathlib
import json
import subprocess
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import audit_grid as G                                        # noqa: E402
from audit_grid import MATERIAL, OK, UNEXPLAINED, UNKNOWN     # noqa: E402
from contract import (ContentType, DISABLED_CONTENT_TYPES,    # noqa: E402
                      DISABLED_LEAGUES, KST, League,
                      QUEUED_CONTENT_TYPES)
from pipeline import RECORD_SOURCE_LEAGUES                    # noqa: E402

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}  {detail}")


def ez(lg, ct, *, season=True, games=None, material=None):
    return G.explain_zero(lg, ct, in_season_flag=season,
                          games=games, material=material)


# ── A. 오늘 헛짚은 세 가지가 접히는가 ────────────────────────────────
print("A. 2026-09-12에 냈던 거짓 양성 세 개")

# ① 끈 콘텐츠 — start_alert·night_brief는 2026-09-07에 껐다
check("★★ 끈 콘텐츠의 0은 설명된다",
      all(ez(League.KBO, c)[0] == OK and ez(League.KBO, c)[1] == "끈 콘텐츠"
          for c in DISABLED_CONTENT_TYPES),
      str([ez(League.KBO, c) for c in DISABLED_CONTENT_TYPES]))
check("  ↳ 목록을 여기 다시 적지 않는다 — 계약에서 읽는다 (§7-45)",
      DISABLED_CONTENT_TYPES is G.DISABLED_CONTENT_TYPES)

# ② 기록 카드를 안 켠 리그 — MLB는 지구가 6개라 순위표를 안 켰다
check("★★ 기록 미대상 리그의 순위표·리더보드 0은 설명된다",
      ez(League.MLB, ContentType.STANDINGS, games=90)
      == (OK, "기록 카드 미대상 리그")
      and ez(League.MLB, ContentType.LEADERBOARD, games=90)[0] == OK,
      str(ez(League.MLB, ContentType.STANDINGS, games=90)))
check("  ↳ 대상 리그(KBO·NPB)는 이 사유로 접히지 않는다",
      ez(League.KBO, ContentType.STANDINGS, games=5)[0] != OK
      and ez(League.NPB, ContentType.STANDINGS, games=5)[0] != OK
      and ez(League.KBO, ContentType.LEADERBOARD, games=5)[0] != OK,
      str(ez(League.KBO, ContentType.LEADERBOARD, games=5)))
check("  ↳ 기록이 아닌 콘텐츠에는 이 관문을 안 건다",
      ez(League.MLB, ContentType.KICKOFF, games=90)[0] == UNEXPLAINED,
      str(ez(League.MLB, ContentType.KICKOFF, games=90)))

# ③ 비시즌 — KBL·V리그는 10월 개막
check("★★ 비시즌의 0은 설명된다",
      ez(League.KBL, ContentType.KICKOFF, season=False, games=0)
      == (OK, "비시즌"),
      str(ez(League.KBL, ContentType.KICKOFF, season=False, games=0)))

# ④ 발행 제외 리그
check("★★ 발행 제외 리그의 0은 설명된다",
      all(ez(l, ContentType.KICKOFF, games=9)[1] == "발행 제외 리그"
          for l in DISABLED_LEAGUES),
      str([ez(l, ContentType.KICKOFF, games=9) for l in DISABLED_LEAGUES]))

check("★ 그날 경기가 없으면 설명된다",
      ez(League.EPL, ContentType.KICKOFF, games=0) == (OK, "그날 경기 없음"))

# ⑤ 분석 카드를 안 켠 리그 — UCL이 31건을 내면서 분석만 0건이라 걸렸다
check("★★ 분석 미대상 리그의 분석 0은 설명된다",
      ez(League.UCL, ContentType.ANALYSIS, games=12)
      == (OK, "분석 카드 미대상 리그"),
      str(ez(League.UCL, ContentType.ANALYSIS, games=12)))
check("  ↳ 대상 리그(KBO·NPB·MLB·KL1)는 이 사유로 접히지 않는다",
      all(ez(l, ContentType.ANALYSIS, games=12)[0] == UNEXPLAINED
          for l in (League.KBO, League.NPB, League.MLB, League.KL1)))
check("  ↳ 분석이 아닌 콘텐츠에는 이 관문을 안 건다",
      ez(League.UCL, ContentType.KICKOFF, games=12)[0] == UNEXPLAINED)


# ── B. ★★★ 진짜 결함은 그대로 빨간불인가 (이 도구의 존재 이유) ────
print("\nB. ★★★ 진짜 결함이 여전히 잡히는가")

# 2026-09-10 아침 상황을 그대로 넣는다: KBO 경기는 있는데 킥오프 0건.
# 이게 초록으로 접히면 이 도구는 눈을 가린 것이다.
v, why = ez(League.KBO, ContentType.KICKOFF, games=15)
check("★★★ 09-10 KBO 킥오프 상황(경기 15건·킥오프 0건)은 빨간불이다",
      v == UNEXPLAINED, f"{v} / {why}")
check("  ↳ 사유에 숫자가 들어간다 — 얼마나 빠졌는지 바로 보인다",
      "15경기" in why, why)

# 2026-09-10 NPB 종료 속보 상황
check("★★★ 09-10 NPB 종료 속보 상황도 빨간불이다",
      ez(League.NPB, ContentType.FINAL_FLASH, games=12)[0] == UNEXPLAINED)

# NPB 리더보드 — 이 도구가 만들어지자마자 찾아낸 칸이다.
# 조사해 보니 계약이 이미 답을 들고 있었다: NPB는 선수명이 한자·가나라
# 리더보드를 아예 만들지 않는다(v1.11m). **다섯 번째 거짓 양성이었다.**
check("★★ NPB 리더보드 0은 '선수 이름 한글 표기 없음'으로 설명된다 (v1.11m)",
      ez(League.NPB, ContentType.LEADERBOARD, games=12)
      == (OK, "선수 이름 한글 표기 없음"),
      str(ez(League.NPB, ContentType.LEADERBOARD, games=12)))
check("  ↳ KBO 리더보드는 이 사유로 접히지 않는다 (한글 표기가 있다)",
      ez(League.KBO, ContentType.LEADERBOARD, games=12)[0] == UNEXPLAINED)
check("  ↳ 순위표는 이 관문을 안 받는다 (팀 이름은 한글로 나간다)",
      ez(League.NPB, ContentType.STANDINGS, games=12)[0] == UNEXPLAINED,
      str(ez(League.NPB, ContentType.STANDINGS, games=12)))

# (변이) 계약 읽기를 빼면 거짓 양성이 돌아오는가 — 접는 값어치의 증거
_saved = G.RECORD_SOURCE_LEAGUES
try:
    G.RECORD_SOURCE_LEAGUES = frozenset(League)      # "전 리그가 대상"인 척
    check("★★ (변이) 기록 관문을 무력화하면 MLB 순위표가 빨간불로 돌아온다",
          ez(League.MLB, ContentType.STANDINGS, games=90)[0] == UNEXPLAINED)
finally:
    G.RECORD_SOURCE_LEAGUES = _saved
check("  ↳ 되돌린 뒤 다시 접힌다",
      ez(League.MLB, ContentType.STANDINGS, games=90)[0] == OK)


# ── C. 세 등급을 섞지 않는다 ────────────────────────────────────────
print("\nC. 등급을 섞지 않는다")

check("★★ 재료가 없으면 '재료없음' — 초록으로 접지 않는다",
      ez(League.KBO, ContentType.LINEUP, games=15, material=0)[0] == MATERIAL,
      str(ez(League.KBO, ContentType.LINEUP, games=15, material=0)))
check("  ↳ 사유에 재료 이름이 들어간다",
      "lineup" in ez(League.KBO, ContentType.LINEUP,
                     games=15, material=0)[1])
check("★★ 재료가 있는데 안 나갔으면 빨간불이다",
      ez(League.KBO, ContentType.LINEUP, games=15, material=9)[0]
      == UNEXPLAINED)
check("★★ 재료를 안 보는 콘텐츠는 재료 인자를 무시한다",
      ez(League.KBO, ContentType.KICKOFF, games=15, material=0)[0]
      == UNEXPLAINED)

check("★★ 스냅샷을 못 보면 '확인못함' — 초록도 빨강도 아니다 (v1.32 규율)",
      ez(League.KBO, ContentType.KICKOFF, games=None)[0] == UNKNOWN,
      str(ez(League.KBO, ContentType.KICKOFF, games=None)))
check("  ↳ 재료를 못 보면 그것도 '확인못함'이다",
      ez(League.KBO, ContentType.LINEUP, games=15, material=None)[0]
      == UNKNOWN)
check("★ 확인 못 해도 계약이 먼저 답하면 접힌다 (스냅샷 없이도 판정된다)",
      ez(League.KBL, ContentType.KICKOFF, season=False, games=None)[0] == OK
      and ez(League.MLB, ContentType.STANDINGS, games=None)[0] == OK)


# ── D. 사유의 우선순위 ──────────────────────────────────────────────
print("\nD. 사유가 겹치면 더 많은 것을 설명하는 쪽")

check("★ 끈 콘텐츠가 발행 제외 리그보다 앞선다",
      ez(next(iter(DISABLED_LEAGUES)),
         next(iter(DISABLED_CONTENT_TYPES)))[1] == "끈 콘텐츠")
check("★ 발행 제외 리그가 비시즌보다 앞선다",
      ez(next(iter(DISABLED_LEAGUES)), ContentType.KICKOFF,
         season=False)[1] == "발행 제외 리그")
check("★ 비시즌이 '경기 없음'보다 앞선다 (사유가 더 정확하다)",
      ez(League.KBL, ContentType.KICKOFF, season=False, games=0)[1] == "비시즌")


# ── E. 격자를 접는 법 ───────────────────────────────────────────────
print("\nE. 격자 접기")

_now = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)
_grid = {"KBO": {"kickoff": 5, "final_flash": 8},
         "NPB": {"final_flash": 3}}
_rep = G.audit(_grid, days=3, games={"KBO": 15, "NPB": 12}, material=None,
               now=_now)
_cells = {(c["league"], c["content"]): c for c in _rep["cells"]}

check("★★ 0이 아닌 칸은 판정하지 않는다",
      ("KBO", "kickoff") not in _cells and ("KBO", "final_flash") not in _cells)
check("★ 발행된 칸 수를 따로 센다",
      _rep["counts"].get("발행") == 3, str(_rep["counts"]))
check("★★ NPB 킥오프 0건이 빨간불로 잡힌다",
      _cells[("NPB", "kickoff")]["verdict"] == UNEXPLAINED,
      str(_cells.get(("NPB", "kickoff"))))
check("★ 큐에 오르는 콘텐츠만 격자에 넣는다 (안 나가는 종류로 칸을 채우지 않는다)",
      {c["content"] for c in _rep["cells"]}
      <= {c.value for c in QUEUED_CONTENT_TYPES})
check("★ 모든 리그를 본다 — 빠진 리그가 없다",
      {c["league"] for c in _rep["cells"]} | {"KBO", "NPB"}
      == {l.value for l in League})
check("★ 등급 합이 칸 수와 같다 (어느 칸도 세다 흘리지 않는다)",
      sum(_rep["counts"].values())
      == len(League) * len(QUEUED_CONTENT_TYPES), str(_rep["counts"]))


# ── F. 대장 읽기 ────────────────────────────────────────────────────
print("\nF. 대장 읽기")

_tmp = pathlib.Path("/tmp/_vg_state")
(_tmp).mkdir(parents=True, exist_ok=True)
_k = (datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc))
_rows = [
    # 보낸 것
    {"idem_key": "ch1|kickoff|KBO:2026-09-11#0|s0|r0", "state": "sent",
     "content_type": "kickoff", "sent_at_utc": "2026-09-11T09:00:00+00:00"},
    # 안 보낸 것 — 세면 안 된다
    {"idem_key": "ch1|kickoff|KBO:2026-09-11#1|s0|r0", "state": "queued",
     "content_type": "kickoff", "sent_at_utc": None},
    # 기간 밖
    {"idem_key": "ch1|kickoff|KBO:2026-08-01#0|s0|r0", "state": "sent",
     "content_type": "kickoff", "sent_at_utc": "2026-08-01T09:00:00+00:00"},
    # 정정 — 키에 성분이 더 붙는다
    {"idem_key": "ch1|correction|KBO:2026-09-11|s0|r1|score", "state": "sent",
     "content_type": "correction", "sent_at_utc": "2026-09-11T10:00:00+00:00"},
    # 깨진 줄
    {"broken": True},
]
(_tmp / "ledger.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in _rows) + "\n깨진줄\n",
    encoding="utf-8")
_g, _days = G.parse_ledger(_tmp / "ledger.jsonl", days=3, now=_k)

check("★★ 보낸 것만 센다 (큐에 있는 것은 발행이 아니다)",
      _g.get("KBO", {}).get("kickoff") == 1, str(dict(_g)))
check("★ 기간 밖은 안 센다", _g.get("KBO", {}).get("kickoff") == 1)
check("★★ 깨진 줄이 있어도 죽지 않는다", "KBO" in _g)
check("★ 성분이 더 붙은 키(정정)도 리그를 옳게 읽는다",
      _g.get("KBO", {}).get("correction") == 1, str(dict(_g)))
check("★ 본 날짜를 돌려준다 — 대장이 비었는지 바로 안다",
      _days == ["2026-09-11"], str(_days))
_g2, _d2 = G.parse_ledger(_tmp / "없는파일.jsonl", days=3, now=_k)
check("★★ 대장이 없으면 빈 격자로 돌아온다 (죽지 않는다)",
      not _g2 and _d2 == [])

check("★★ 스냅샷 폴더가 없으면 (None, None) — 없는 것을 0으로 세지 않는다",
      G.load_snapshot_counts(_tmp, days=3) == (None, None))


# ── G. 실물 — 배포본 대장으로 돌린다 ────────────────────────────────
print("\nG. 실물 실행")

_root = pathlib.Path(__file__).resolve().parents[1]
_state = _root / "state"
if (_state / "ledger.jsonl").exists():
    _r = subprocess.run([sys.executable, str(_root / "g1" / "audit_grid.py"),
                         "--days", "3", "--state", str(_state), "--json"],
                        capture_output=True, text=True, timeout=120)
    check("★★ 실제 대장으로 돌아간다 (종료코드 0 또는 1)",
          _r.returncode in (0, 1), f"rc={_r.returncode} {_r.stderr[:120]}")
    try:
        _j = json.loads(_r.stdout)
    except (ValueError, TypeError):
        _j = None
    check("★ --json이 읽히는 꼴로 나온다", isinstance(_j, dict) and "cells" in _j,
          _r.stdout[:120])
    if _j:
        check("★★ 실제 대장에서 세 거짓 양성이 전부 접혔다",
              all(c["verdict"] == OK for c in _j["cells"]
                  if c["league"] in ("KBL", "VLEAGUE_M", "VLEAGUE_W",
                                     "LCK", "INTL_LOL")),
              str([c for c in _j["cells"]
                   if c["league"] in ("KBL", "LCK") and c["verdict"] != OK][:2]))
        check("★★ 설명못함이 있으면 종료코드가 1이다 (예약 점검이 읽는다)",
              (_r.returncode == 1) == bool(_j["counts"].get(UNEXPLAINED, 0)),
              f"rc={_r.returncode} / {_j['counts']}")
else:
    print("  SKIP  실제 대장 없음 — 이 실행은 '실물에서 도는가'를 말하지 않습니다")

# 텍스트 표도 그려지는가
_txt = G.render(_rep, _grid)
check("★ 사람이 읽는 표가 그려진다",
      "리그 × 콘텐츠 격자" in _txt and "NPB" in _txt)
check("★★ 빨간불을 표 위에 먼저 둔다 (묻히지 않는다)",
      _txt.index("설명 못 하는 0") < _txt.index("숫자=발행 건수"))


# ── H. 발행 경로를 안 건드렸는가 ────────────────────────────────────
print("\nH. 발행 경로 무수정")

_src = (_root / "g1" / "audit_grid.py").read_text(encoding="utf-8")
check("★★★ 쓰기를 하지 않는다 (읽기 전용 도구다)",
      not any(w in _src for w in ("write_text(", "open(", "os.remove",
                                  "shutil.", "commit")),
      [w for w in ("write_text(", "open(", "os.remove") if w in _src])
check("★★ 발행 모듈을 들여오지 않는다 (tick·sender·render를 안 부른다)",
      not any(w in _src for w in ("import tick", "import sender",
                                  "render_v5", "cards_v5")))
check("★ 계약을 못 읽으면 죽는다 — 조용히 전부 초록으로 접지 않는다",
      "SystemExit(2)" in _src and "계약을 못 읽었습니다" in _src)


print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
