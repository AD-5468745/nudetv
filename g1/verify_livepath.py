"""**검증이 한 번도 걸어보지 않은 길** — 실발송 경로와 정적 검사 (v1.11m).

2026-09-03, 검증 823건이 전부 통과한 코드가 실운영에서 **8시간 동안
발송 0건 · 알림 0건**이었다. 원인은 tick() 안의 한 줄이었다:

    records = _collect_records(now, fact_notes)     # ← 여기서 쓰고
    ...
    fact_notes: list[str] = []                      # ← 여기서 만든다

파이썬은 이것을 UnboundLocalError로 끝낸다. 그런데:

  · **드라이런은 이 줄에 닿기 전에 return 한다.** 검증은 전부 드라이런이었다.
  · **알림 코드는 tick() 맨 끝에 있다.** 그 앞에서 죽으니 알림도 안 나갔다.

즉 "돌아간다"를 확인한 검증이 823건 있었는데, 그중 **실제로 보내는 길을
걸어본 것은 0건**이었다. 이 파일이 그 구멍 둘을 막는다:

  ① 정적 검사 — 이름을 만들기 전에 쓰는 부류를 컴파일 없이 전부 잡는다
  ② 실발송 경로 스모크 — 가짜 전송기로 tick(dry_run=False)를 진짜로 돌린다

②는 네트워크를 쓰지 않는다(수집·기록·전송을 전부 가짜로 바꾼다). 목적은
'내용이 맞나'가 아니라 **'그 길을 끝까지 걸을 수 있나'**이므로 그것으로 충분하다.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ok = fail = 0


def check(n, c, d=""):
    global ok, fail
    if c:
        ok += 1
        print(f"  PASS  {n}")
    else:
        fail += 1
        print(f"  FAIL  {n}  {d}")


ROOT = pathlib.Path(__file__).resolve().parents[1]

# ─────────────────────────────────────────────────────────────
# ① 정적 검사 — "만들기 전에 쓰는" 부류
# ─────────────────────────────────────────────────────────────
# pyflakes를 쓴다. 실행하지 않고 이름의 흐름만 본다 —
# 이번 버그를 정확히 한 줄로 짚어낸다(undefined name 'fact_notes').
#
# **없으면 통과가 아니라 실패다.** 도구가 사라지면 게이트도 사라지는데,
# 그것이 조용히 초록불로 보이면 게이트가 없는 것보다 나쁘다.
#
# 치명으로 다루는 부류만 실패로 올린다. 'imported but unused' 같은
# 정리 사항까지 빨간불로 만들면 사람이 빨간불을 무시하게 된다.
FATAL = (
    "undefined name",                  # 만들기 전에 쓴다 / 오타 난 이름
    "local variable defined in enclosing scope",
    "referenced before assignment",
    "redefinition of unused",           # 같은 이름의 함수·클래스를 두 번 정의
    "syntax error",
)

TARGETS = sorted(
    [str(p) for p in (ROOT / "g1").glob("*.py")]
    + [str(p) for p in (ROOT / "g1" / "adapters").glob("*.py")]
    + [str(ROOT / "contract.py")]
)

try:
    proc = subprocess.run([sys.executable, "-m", "pyflakes", *TARGETS],
                          capture_output=True, text=True, timeout=180)
    available = "No module named" not in (proc.stderr or "")
except Exception as e:                                       # noqa: BLE001
    proc, available = None, False
    print(f"  (pyflakes 실행 실패: {e})")

check("정적 검사기(pyflakes)가 있다 — 없으면 이 게이트는 없는 것과 같다", available,
      "pip install pyflakes")

if available:
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    fatal = [ln for ln in lines if any(k in ln.lower() for k in FATAL)]
    check("만들기 전에 쓰는 이름이 없다 (undefined / before assignment)",
          not fatal, " · ".join(fatal[:4]))
    check("같은 이름을 두 번 정의한 곳이 없다",
          not [ln for ln in lines if "redefinition of unused" in ln.lower()],
          " · ".join(ln for ln in lines if "redefinition of unused" in ln.lower())[:200])
    print(f"  (정적 검사 대상 {len(TARGETS)}개 파일 · 참고 지적 {len(lines) - len(fatal)}건)")

# 이 검사가 **실제로 잡는지** 일부러 깨뜨려 확인한다.
# 게이트는 "통과했다"가 아니라 "깨뜨렸더니 막았다"로만 증명된다.
if available:
    _bad = pathlib.Path(tempfile.mkdtemp(prefix="pyflakes-mutant-")) / "bad.py"
    _bad.write_text("def f():\n    print(x)\n    x = 1\n", encoding="utf-8")
    _p = subprocess.run([sys.executable, "-m", "pyflakes", str(_bad)],
                        capture_output=True, text=True, timeout=60)
    check("변이시험 — 일부러 만든 '쓰고 나서 만들기'를 실제로 잡는다",
          any(k in (_p.stdout or "").lower() for k in FATAL),
          (_p.stdout or "")[:120])
    shutil.rmtree(_bad.parent, ignore_errors=True)


# ─────────────────────────────────────────────────────────────
# ② 실발송 경로 스모크 — tick(dry_run=False)를 진짜로 한 번 돌린다
# ─────────────────────────────────────────────────────────────
TMP = pathlib.Path(tempfile.mkdtemp(prefix="livepath-"))
os.environ["NUDETV_STATE"] = str(TMP)
os.environ["TELEGRAM_CHAT_ID"] = "-100live"
os.environ["ALERT_CHAT_ID"] = "-100alert"
os.environ["TELEGRAM_BOT_TOKEN"] = "0:테스트토큰아님"
os.environ["WORKER_ID"] = "livepath"
# 미루기 판정을 켠 상태로 걷는다 — 연속 운전과 같은 조건.
os.environ["TICK_LOOPS_LEFT"] = "5"
os.environ["TICK_LOOP_INTERVAL_SECONDS"] = "300"

import contract as C                                        # noqa: E402
from contract import ContentType, Game, GameMeta, League, Score, ScoreUnit, Status, TeamRef  # noqa: E402
import pipeline as P                                        # noqa: E402
import tick as T                                            # noqa: E402
from zoneinfo import ZoneInfo                               # noqa: E402

T.ROOT = TMP
T.LEDGER = TMP / "ledger.jsonl"
T.FETCH_LOG = TMP / "fetch.json"
T.SNAP_DIR = TMP / "games"
T.SNAP_DIR.mkdir(parents=True, exist_ok=True)


class FakeTransport:
    """텔레그램에 닿지 않는다. 무엇이 나갔는지만 적는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, payload: dict, files=None) -> dict:
        self.calls.append((method, payload))
        return {"message_id": 1000 + len(self.calls)}


def mkgame(lg, h, a, day, hh, status, score=None):
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, 0,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season, source_key=f"{lg.value}-{day}-{hh}-{h}{a}",
             home=TeamRef(lg, h), away=TeamRef(lg, a),
             start_utc=st.astimezone(timezone.utc), home_tz="Asia/Seoul",
             status=status, score=score, venue="테스트구장",
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.validate()
    return g


NOW = datetime.now(timezone.utc)
_day = NOW.astimezone(C.KST).strftime("%Y-%m-%d")
_games = [
    mkgame(League.KBO, "LG", "OB", _day, 18, Status.FINAL,
           Score(5, 3, ScoreUnit.RUNS)),
    mkgame(League.KBO, "SS", "KT", _day, 18, Status.FINAL,
           Score(2, 4, ScoreUnit.RUNS)),
]

_orig_collect, _orig_all, _orig_rec = T.collect, T.all_games, T._collect_records
_orig_render = T.render_for


def _fake_collect(now, force=False):
    return {"KBO": len(_games)}, [], []


def _fake_all():
    return {"KBO": list(_games)}


def _fake_records(now, notes):
    # 기록 수집은 네트워크를 탄다. 여기서는 '없음'으로 둔다 —
    # 이 검사의 목적은 경로를 걷는 것이지 기록 내용이 아니다.
    return {}


def _fake_render(item, games, **kw):
    # 카드는 브라우저로 그린다(느리고 폰트에 의존). 경로 검사에서는 가짜 이미지.
    # 반환 모양은 render_for와 같아야 한다 —
    # (사진들[(파일명, 바이트, 가로, 세로)], 캡션 파트들).
    return ([("livepath.jpg", b"\xff\xd8\xff\xe0" + b"0" * 4096, 1080, 1350)],
            ["테스트 캡션"])


T.collect, T.all_games, T._collect_records = _fake_collect, _fake_all, _fake_records
T.render_for = _fake_render
tr = FakeTransport()
T.Transport = lambda *a, **k: tr
T.load_token = lambda *a, **k: C.Secret("0:테스트") if hasattr(C, "Secret") else "0:테스트"
import sender as S                                          # noqa: E402
T.load_token = lambda *a, **k: S.Secret("0:테스트토큰아님")

# 커버리지는 리그가 하나뿐인 가짜 스냅샷을 '리그가 사라졌다'로 볼 수 있다.
# 종료 코드는 이 검사의 관심사가 아니다 — **끝까지 걸었는가**만 본다.
_rc = None
_err = None
try:
    _rc = T.tick(dry_run=False)
except BaseException as e:                                   # noqa: BLE001
    import traceback
    _err = traceback.format_exc()

check("실발송 경로를 예외 없이 끝까지 걷는다 (tick(dry_run=False))",
      _err is None, (_err or "").strip().splitlines()[-1][:160] if _err else "")
check("실발송 경로가 종료 코드를 돌려준다", _rc in (0, 1, 2), f"rc={_rc}")
check("가짜 전송기로 실제 발송 호출이 일어났다 (경로가 죽어 있으면 0건)",
      len(tr.calls) > 0, f"호출 {len(tr.calls)}건")
check("발송 대장에 기록이 남았다",
      T.LEDGER.exists() and T.LEDGER.read_text(encoding="utf-8").strip() != "",
      "대장이 비었다")

# 이 검사가 **실제로 잡는지** — 일부러 죽여서 확인한다.
def _boom(now, notes):
    raise RuntimeError("일부러 낸 예외")


T._collect_records = _boom
_caught = False
try:
    T.tick(dry_run=False)
except BaseException:                                        # noqa: BLE001
    _caught = True
check("변이시험 — 실발송 경로가 죽으면 이 검사가 실제로 잡는다", _caught)
T._collect_records = _fake_records

# ─────────────────────────────────────────────────────────────
# ③ 예외 그물 — 죽더라도 알림은 나가야 한다
# ─────────────────────────────────────────────────────────────
check("틱에 최상위 예외 그물이 있다 (_crash_alert)",
      hasattr(T, "_crash_alert"))
if hasattr(T, "_crash_alert"):
    def _net_test():
        # **같은 자리에서** 두 번 던진다 — 되풀이 방지는 내용(추적 정보 포함)이
        # 같을 때만 걸린다. 줄 번호가 다르면 '새 문제'이므로 다시 나가는 것이 맞다.
        try:
            raise RuntimeError("그물 시험")
        except RuntimeError:
            T._crash_alert()

    tr.calls.clear()
    _net_test()
    _sent = [p for m, p in tr.calls if m == "sendMessage"]
    check("예외 그물이 알림을 실제로 보낸다", len(_sent) == 1, f"{len(_sent)}건")
    check("예외 그물 알림이 구독 채널이 아니라 알림 채널로 간다",
          all(str(p.get("chat_id")) == "-100alert" for p in _sent),
          str([p.get("chat_id") for p in _sent]))
    # 같은 예외로 5분마다 도배하지 않는다
    tr.calls.clear()
    _net_test()
    check("같은 예외를 되풀이해 도배하지 않는다",
          len([p for m, p in tr.calls if m == "sendMessage"]) == 0)

T.collect, T.all_games, T._collect_records = _orig_collect, _orig_all, _orig_rec
T.render_for = _orig_render

# ─────────────────────────────────────────────────────────────
# ④ 큐에 오르는 종류는 전부 '만들 수 있는' 종류여야 한다
# ─────────────────────────────────────────────────────────────
# 종류를 늘리면서 render_for에 분기를 안 넣으면, 그 종류는 큐에 오르고
# 처리 대상이 되고도 조용히 `else: return None`로 떨어진다. 로그에는
# '만들 내용 없음' 숫자 하나만 늘 뿐이라 몇 주도 모를 수 있다.
_src = pathlib.Path(T.__file__).read_text(encoding="utf-8")
_body = _src.split("def render_for(", 1)[1].split("\ndef ", 1)[0]
_missing = [ct.value for ct in C.QUEUED_CONTENT_TYPES
            if f"ContentType.{ct.name}" not in _body]
check("큐에 오르는 콘텐츠는 전부 render_for에 분기가 있다",
      not _missing, " · ".join(_missing))

# **나이트 브리핑은 리그가 없는 유일한 카드다** — 부르는 쪽이 item.league로
# 고른 리그 스냅샷은 항상 비어 있다. 그 상태로도 만들어져야 한다.
# (v1.11m 이전에는 문지기에 걸려 한 장도 못 만들었다.)
_nb_day = datetime.now(timezone.utc).astimezone(C.KST).strftime("%Y-%m-%d")
_nb_games = [mkgame(League.KBO, "LG", "OB", _nb_day, 18, Status.FINAL,
                    Score(5, 3, ScoreUnit.RUNS))]
_nb_item = next((i for i in T.build_all_queues({"KBO": _nb_games},
                                               datetime.now(timezone.utc), "chTEST")
                 if i.content_type is ContentType.NIGHT_BRIEF), None)
check("나이트 브리핑이 큐에 오른다", _nb_item is not None)
if _nb_item is not None:
    check("나이트 브리핑은 리그 스냅샷이 비어도 만들어진다 (league=None)",
          T.render_for(_nb_item, [], all_games=_nb_games) is not None,
          "None이 돌아왔다 — 리그 문지기에 걸린다")

print(f"\n결과: {ok} PASS / {fail} FAIL")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
