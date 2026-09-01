"""시계 — 한 번 깨어나서 할 일을 한다 (v1.11c 신설).

**설계의 전제: 이 프로그램은 정확한 시각에 깨어나지 않는다.**
무료 스케줄러(깃허브 액션 등)는 부하가 몰리면 몇 분에서 수십 분까지 늦는다.
그래서 "07:30에 정확히 쏜다"로 설계하면 반드시 깨진다.

대신 **자주 깨어나 큐를 훑고, 늦은 것은 스스로 버린다.**
계약이 이미 그렇게 만들어져 있다 — `is_late()`와 `REJUDGE_AT_SEND`가 그것이다.
"10분 뒤 경기 시작"이라고 쓴 카드가 20분 늦게 나가면 그건 거짓말이므로 안 보내는 게 맞다.

**실행 환경은 매번 새 컨테이너다(무상태).** 그래서 상태를 파일로 남긴다:

    state/ledger.jsonl        발송 대장 — 같은 것을 두 번 보내지 않게 하는 유일한 근거
    state/fetch.json          리그별 마지막 수집 시각
    state/games/<리그>.json   수집 스냅샷

대장이 사라지면 **이미 보낸 것을 다시 보낸다.** 구독자에게 같은 카드가 두 번 가는 사고는
되돌릴 수 없으므로, 대장 보존이 이 시스템에서 가장 중요한 파일이다.

**리그 하나가 죽어도 나머지는 돈다.** 수집 실패는 그 리그만 건너뛰고 알림을 남긴다 —
소스 한 곳이 점검 중이라고 그날 전체 발행이 멈추면 안 된다.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time as _time
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# **함수 안에서 import 하지 않는다.** 파이썬은 함수 어딘가에 `from x import Y`가
# 있으면 그 함수 전체에서 Y를 지역 이름으로 취급한다. 그래서 한쪽 분기에서만
# import 하고 다른 분기에서 쓰면, 그 분기는 실행되는 순간 NameError로 죽는다.
# 실제로 render_for가 그랬다 — Status를 결과카드 분기에서만 들여와서
# **시작 알림은 한 번도 렌더될 수 없었다.** 필요한 이름은 여기서 전부 들여온다.
from contract import (ContentType, GateError, KST, League, QueueItem, SendState,
                      Status, UnknownStatus, assert_home_away,
                      demote_impossible_finals,
                      assert_send_windows, assert_team_names_cover,
                      day_schedule_scope, is_late, lookahead_for,
                      stale_unresolved)
import pipeline as P
from sender import Ledger, Payload, Pacer, Secret, Sender, Transport, load_token

# ── 경로 ──────────────────────────────────────────────────────
ROOT = pathlib.Path(os.environ.get("NUDETV_STATE", "state")).resolve()
LEDGER = ROOT / "ledger.jsonl"
FETCH_LOG = ROOT / "fetch.json"
SNAP_DIR = ROOT / "games"

# ── 수집 주기 ─────────────────────────────────────────────────
# 매 틱마다 전 리그를 긁으면 소스에 부담이고 레이트리밋에 걸린다.
# 일정은 자주 안 바뀌므로 30분이면 충분하다. 단 경기 중에는 결과가 바뀌므로 짧게 본다.
FETCH_EVERY_SECONDS = 30 * 60
FETCH_EVERY_LIVE_SECONDS = 10 * 60      # 그 리그에 오늘 경기가 있으면

# **막힌 소스는 이만큼 쉬었다 다시 두드린다.**
# 위의 30분 제동은 '성공 기록'을 기준으로 하므로, 한 번도 성공 못한 소스에는
# 걸리지 않는다. 그대로 두면 5분마다 하루 288번을 두드려 차단을 부른다.
# 15분이면 5분 시계에서 세 틱에 한 번 — 회복은 충분히 빠르고 소스에는 예의가 된다.
RETRY_AFTER_FAIL_SECONDS = 15 * 60

# 큐에서 이만큼 앞선 것까지 이번 틱에 처리한다.
# **틱 간격과 맞춰야 한다.** 짧으면 다음 틱까지 못 기다리는 항목이 통째로 누락되고,
# 길면 필요 이상으로 일찍 보낸다. 정각 시계(무료 환경)면 60분이 맞다.
# 5분 시계로 옮기면 TICK_LOOKAHEAD_MINUTES=6 으로 줄이면 된다.
LOOKAHEAD_SECONDS = max(6, int(os.environ.get("TICK_LOOKAHEAD_MINUTES", "60"))) * 60

# **시계가 실제로 몇 분마다 도는가.** 설정한 값이 아니라 실측값을 적는다.
# 깃허브에 5분(*/5)을 걸어두었지만 실측 간격은 약 100분이었다(17시간에 10회).
# 이 값으로 아래 게이트가 "발송 창이 시계 간격보다 넓은가"를 매 실행 확인한다.
# 창이 좁으면 오류 없이 조용히 아무것도 안 나가므로, 사람이 눈치채기 전에 막는다.
TICK_INTERVAL_SECONDS = max(60, int(
    os.environ.get("TICK_INTERVAL_MINUTES", "100"))) * 60

# 리그 하나가 이보다 오래 걸리면 틱 전체가 밀린다. 넘으면 알림에 올린다.
SLOW_FETCH_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


TICK_LOG = None          # ROOT 확정 후 아래에서 채운다


def _tick_log_path() -> pathlib.Path:
    return ROOT / "ticks.json"


def _record_tick(now: datetime) -> None:
    """이 틱의 시각을 남긴다. 다음 틱이 '실제 간격'을 알기 위한 유일한 근거다."""
    p = _tick_log_path()
    try:
        hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (json.JSONDecodeError, OSError):
        hist = []
    hist.append(_iso(now))
    hist = hist[-30:]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(hist), encoding="utf-8")
    except OSError:
        pass


def _measured_interval_seconds(now: datetime) -> int:
    """**실측** 틱 간격(초). 기록이 모자라면 0.

    설정값(TICK_INTERVAL_MINUTES)은 사람이 적은 숫자라 현실을 모른다.
    깃허브 무료 스케줄러 실측은 30분~4시간 불규칙이었다.
    최근 기록 중 **가장 긴 간격**을 쓴다 — 최악을 기준으로 창을 검사해야 한다.
    """
    p = _tick_log_path()
    try:
        hist = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (json.JSONDecodeError, OSError):
        return 0
    ts = []
    for x in hist[-12:]:
        try:
            ts.append(datetime.fromisoformat(x))
        except ValueError:
            continue
    ts.append(now)
    if len(ts) < 3:
        return 0
    gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:]) if b > a]
    return int(max(gaps)) if gaps else 0


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def fetch_months(today: datetime) -> tuple[int, list[str]]:
    """월 단위로 일정을 주는 소스(KBO·K리그1·NPB)에 요청할 (연도, 월 목록).

    **월말에는 다음 달을 함께 긁는다 (v1.11f).**
    전에는 `이전 달 + 이번 달`뿐이었다. 그래서 **매달 말일에는 내일 경기를
    아예 볼 수 없었다** — 큐 지평선은 30시간인데 데이터 지평선이 0시간이 된다.
    2026-08-31 실측: KBO·K리그1·NPB 스냅샷의 마지막 편성일이 전부 08-30이고
    9월 편성이 한 건도 없었다. (그날이 마침 월요일 휴식일이라 티가 안 났다.)

    이전 달을 함께 두는 이유는 반대쪽이다 — 1일 아침에 어제(말일) 결과 카드를
    만들어야 한다.

    연도가 넘어가는 달은 넣지 않는다. 소스가 연도 하나만 받으므로 12→1월에
    1월을 넣으면 **작년 1월**을 긁는다. 세 리그 다 그때는 비시즌이라 잃는 것이 없고,
    엉뚱한 해를 긁는 것보다 안 긁는 편이 낫다.
    """
    y = today.year
    cands = [today.replace(day=1) - timedelta(days=1),   # 지난달
             today,
             today + timedelta(days=2)]                  # 월말이면 다음 달
    return y, sorted({f"{d.month:02d}" for d in cands if d.year == y})


# ── 수집 대상 ─────────────────────────────────────────────────
# 리그를 늘릴 때 여기만 고친다. 실패해도 다른 리그에 영향이 없도록 각각 독립이다.
def _jobs() -> dict[str, tuple[League, callable]]:
    from adapters.kbo import KboAdapter
    from adapters.mlb import MlbAdapter
    from adapters.kbl import KblAdapter
    from adapters.kovo import KovoAdapter
    from adapters.kleague import KLeagueAdapter
    from adapters.npb import NpbAdapter

    today = datetime.now(KST)
    y, months = fetch_months(today)

    jobs = {
        "KBO": (League.KBO, lambda: KboAdapter().fetch(y, months)),
        "MLB": (League.MLB, lambda: MlbAdapter().fetch(
            (today - timedelta(days=3)).strftime("%Y-%m-%d"),
            (today + timedelta(days=3)).strftime("%Y-%m-%d"))),
        "KBL": (League.KBL, lambda: KblAdapter().fetch(
            (today - timedelta(days=7)).strftime("%Y%m%d"),
            (today + timedelta(days=14)).strftime("%Y%m%d"))),
        "VLEAGUE_M": (League.VLEAGUE_M, lambda: KovoAdapter("1").fetch("023")),
        "VLEAGUE_W": (League.VLEAGUE_W, lambda: KovoAdapter("2").fetch("023")),
        "KL1": (League.KL1, lambda: KLeagueAdapter().fetch(y, months)),
        "NPB": (League.NPB, lambda: NpbAdapter().fetch(y, months)),
    }

    # LCK·국제는 Leaguepedia 쿼터가 빡빡하다.
    # **운영의 긴 재시도(최대 6분)를 시계에서 그대로 쓰면 틱 하나가 그것만으로 끝난다.**
    # 시계는 자주 깨어나는 것이 안전장치이므로, 여기서는 한 번만 더 시도하고
    # 안 되면 캐시로 버틴다(캐시도 없으면 그 리그만 이번 틱을 건너뛴다).
    try:
        import adapters.lck as _lck
        from adapters.lck import LckAdapter
        _lck._RATELIMIT_WAITS = (15,)
        # since를 '30일 전'처럼 매일 움직이는 값으로 두면 **캐시 키가 매일 바뀌어**
        # 캐시가 한 번도 안 맞는다. 리밋에 걸린 날 버티라고 만든 캐시가 무용지물이 된다.
        # 월초로 내려 고정하면 한 달에 한 번만 바뀐다.
        _s = (today.replace(day=1) - timedelta(days=32)).replace(day=1)
        since = _s.strftime("%Y-%m-%d")
        jobs["LCK"] = (League.LCK, lambda: LckAdapter(League.LCK).fetch(since))
        jobs["INTL_LOL"] = (League.INTL_LOL,
                            lambda: LckAdapter(League.INTL_LOL).fetch(since))
    except Exception:                                        # noqa: BLE001
        pass

    # 유럽 6개 — 키가 있을 때만 켜진다. 없으면 조용히 빠지는 게 아니라 아예 등록되지 않는다
    # (조용히 0건을 반환하면 '오늘 유럽 경기가 없다'로 오해된다).
    if os.environ.get("FOOTBALL_DATA_TOKEN", "").strip():
        from adapters.football_data import COMPETITION, FootballDataAdapter
        d0 = (today - timedelta(days=3)).strftime("%Y-%m-%d")
        d1 = (today + timedelta(days=7)).strftime("%Y-%m-%d")
        for code, lg in COMPETITION.items():
            jobs[lg.value] = (
                lg, (lambda _lg=lg: FootballDataAdapter(_lg).fetch(d0, d1)))
    return jobs


# ── 스냅샷 ────────────────────────────────────────────────────

def _snap_path(name: str) -> pathlib.Path:
    return SNAP_DIR / f"{name}.json"


class SnapshotWipe(GateError):
    """멀쩡하던 스냅샷을 0건으로 덮으려 한다 — 소스가 조용히 비었을 가능성이 높다."""


def _save_games(name: str, games: list) -> None:
    """스냅샷 저장. **있던 것을 0건으로 덮지 않는다 (v1.11h).**

    KBO·KBL 어댑터는 0건에 게이트가 없어 빈 리스트를 그대로 돌려준다
    (`json.loads(raw).get("rows", [])` — 응답 구조가 바뀌면 조용히 0건이 된다).
    그대로 저장하면 그 리그의 그날 콘텐츠가 통째로 사라진다.
    커버리지 감시가 뒤늦게 잡아도 그때는 이미 스냅샷이 지워진 뒤다.
    파일을 지키는 쪽이 언제나 안전하다 — 다음 틱에 제대로 수집되면 갱신된다.
    """
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    if not games:
        prev = _load_raw(name)
        if prev:
            raise SnapshotWipe(
                f"{name}: 수집 0건인데 기존 스냅샷은 {len(prev)}건입니다 — "
                f"덮어쓰지 않고 이전 것을 유지합니다(소스 응답 구조 변경 의심)")
    rows = [{
        "league": g.league.value, "season": g.season, "source_key": g.source_key,
        "home": g.home.team_code, "away": g.away.team_code,
        "start_utc": _iso(g.start_utc), "home_tz": g.home_tz,
        "status": g.status.value,
        "score": ([g.score.home, g.score.away, g.score.unit.value] if g.score else None),
        "venue": g.venue, "sports_day": g.sports_day,
        "start_rev": g.start_rev,
        # **meta를 세 필드만 저장하다가 카드가 거짓말을 했다 (v1.11h).**
        # decided_by·aggregate가 유실되면 `is_draw()`가 승부차기·합산 승부를
        # '무승부'로 판정한다. gender가 유실되면 V리그 남/여가 섞여
        # 되읽은 스냅샷 1,000건 중 252건이 validate에 실패했다.
        "cancel_reason": g.meta.cancel_reason, "best_of": g.meta.best_of,
        "season_category": g.meta.season_category,
        "gender": g.meta.gender,
        "decided_by": g.meta.decided_by.value if g.meta.decided_by else None,
        "aggregate": list(g.meta.aggregate) if g.meta.aggregate else None,
        "penalties": list(g.meta.penalties) if g.meta.penalties else None,
        "set_scores": [list(x) for x in g.meta.set_scores] if g.meta.set_scores else None,
        "doubleheader_seq": g.meta.doubleheader_seq,
        "is_dome": g.meta.is_dome,
    } for g in games]
    tmp = _snap_path(name).with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_snap_path(name))          # 원자적 교체 — 중간에 죽어도 반쪽 파일이 남지 않는다


def _load_raw(name: str) -> list:
    """스냅샷 원본(dict 목록). 존재·건수 확인용 — Game으로 되돌리지 않는다."""
    p = _snap_path(name)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _load_games(name: str) -> list:
    """스냅샷을 Game으로 되돌린다. 카드 렌더는 이것만 있으면 된다."""
    from contract import (DecidedBy, Game, GameMeta, Score, ScoreUnit,
                          Status, TeamRef)
    p = _snap_path(name)
    if not p.exists():
        return []
    out = []
    for d in json.loads(p.read_text(encoding="utf-8")):
        lg = League(d["league"])
        sc = (Score(d["score"][0], d["score"][1], ScoreUnit(d["score"][2]))
              if d.get("score") else None)
        out.append(Game(
            league=lg, season=d["season"], source_key=d["source_key"],
            home=TeamRef(lg, d["home"]), away=TeamRef(lg, d["away"]),
            start_utc=datetime.fromisoformat(d["start_utc"]),
            home_tz=d["home_tz"], status=Status(d["status"]), score=sc,
            venue=d.get("venue"), sports_day_fixed=d.get("sports_day"),
            start_rev=int(d.get("start_rev") or 0),
            meta=GameMeta(cancel_reason=d.get("cancel_reason"),
                          best_of=d.get("best_of"),
                          season_category=d.get("season_category"),
                          gender=d.get("gender"),
                          # 없으면 기본값(REGULAR)을 쓴다. None을 넣으면
                          # validate가 "결정 방식 불가"로 전건을 막는다.
                          decided_by=(DecidedBy(d["decided_by"])
                                      if d.get("decided_by") else DecidedBy.REGULAR),
                          aggregate=(tuple(d["aggregate"])
                                     if d.get("aggregate") else None),
                          penalties=(tuple(d["penalties"])
                                     if d.get("penalties") else None),
                          set_scores=[tuple(x) for x in (d.get("set_scores") or [])],
                          doubleheader_seq=d.get("doubleheader_seq"),
                          is_dome=bool(d.get("is_dome")))))

    # **게이트는 데이터가 들어오는 문이 아니라 카드가 나가는 문에 단다.**
    # 수집에만 게이트를 걸어두면, 30분 제동으로 수집을 건너뛴 틱이나
    # 예전 버전이 남긴 스냅샷은 게이트를 통과하지 않은 채 카드가 된다.
    # 실제로 2026-09-01 사고를 스냅샷 경로로 재현하면 카드가 그대로 만들어졌다.
    out, notes = demote_impossible_finals(out)
    for n in notes:
        print(f"  [스냅샷 보정] {n}")
    return out


# ── 수집 ──────────────────────────────────────────────────────

def _fetch_log() -> dict:
    if FETCH_LOG.exists():
        try:
            return json.loads(FETCH_LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}                       # 깨진 로그는 '수집한 적 없음'과 같게 다룬다
    return {}


# ── 일시적 실패 vs 진짜 고장 ──────────────────────────────────
#
# 5분마다 도는 시계에서 **모든 실패를 빨간불로 올리면 아무도 빨간불을 안 본다.**
# 레이트리밋·네트워크 끊김은 다음 틱(5분 뒤)에 저절로 풀린다. 반면 소스 구조가
# 바뀐 것(GateError)은 사람이 고쳐야 한다. 둘을 섞으면 진짜 사고를 놓친다.
#
# **그렇다고 조용히 넘기지는 않는다.** 일시적 실패도 로그·알림에는 그대로 남고,
# 며칠 이어지면 커버리지 감시가 "수집 성공 기록 없음"으로 잡아 빨간불을 올린다.
# 즉 '한 번 걸림'은 통과, '계속 걸림'은 실패 — 판단을 커버리지에 맡긴다.
TRANSIENT_ERROR_NAMES = frozenset({
    "RateLimited",        # 소스 호출 한도 — 기다리면 풀린다
    "Timeout", "TimeoutError", "ReadTimeout", "ConnectTimeout",
    "URLError", "HTTPError", "ConnectionError", "ConnectionResetError",
    "RemoteDisconnected", "IncompleteRead", "SSLError",
})


def _is_transient(exc: BaseException) -> bool:
    """이 오류가 '기다리면 풀리는 것'인가."""
    for cls in type(exc).__mro__:
        if cls.__name__ in TRANSIENT_ERROR_NAMES:
            return True
    return False


def collect(now: datetime, force: bool = False) -> tuple[dict, list[str], list[str]]:
    """수집이 필요한 리그만 긁는다.

    반환: (리그별 경기 수, 사람이 봐야 할 실패, 기다리면 풀릴 실패)
    """
    log = _fetch_log()
    counts, errors, soft = {}, [], []
    today = now.astimezone(KST).strftime("%Y-%m-%d")

    for name, (lg, fn) in _jobs().items():
        rec = log.get(name, {})
        last = rec.get("at")
        prev = _load_games(name)
        has_today = any(g.sports_day == today for g in prev)
        every = FETCH_EVERY_LIVE_SECONDS if has_today else FETCH_EVERY_SECONDS
        if not force and last:
            age = (now - datetime.fromisoformat(last)).total_seconds()
            if age < every:
                counts[name] = len(prev)
                continue
        # **실패한 소스를 5분마다 다시 때리지 않는다.**
        # 성공 기록이 없으면 위의 30분 제동이 걸리지 않아, 한 번 막힌 소스를
        # 하루 288번 두드리게 된다. 호출 한도에 걸린 상대에게 그건 차단을 부른다
        # (첫 실행에서 Leaguepedia가 정확히 이렇게 막혔다).
        failed_at = rec.get("failed_at")
        if not force and failed_at:
            since_fail = (now - datetime.fromisoformat(failed_at)).total_seconds()
            if since_fail < RETRY_AFTER_FAIL_SECONDS:
                counts[name] = len(prev)
                continue
        t0 = _time.monotonic()
        try:
            games = fn()
            # **홈/원정이 통째로 뒤집혔는지 저장 전에 본다.**
            # 뒤집혀도 카드는 멀쩡해 보인다(팀 두 개가 자리만 바꾼 것이다).
            # 점수까지 함께 뒤집히면 승패를 반대로 내보낸다 — 되돌릴 수 없는 오보다.
            # 기계가 볼 수 있는 근거는 경기장뿐이다: 홈구장이 홈팀과 어긋나면 뒤집힌 것.
            assert_home_away(games)
            # 표에 없는 팀 코드가 오면 카드에 코드가 그대로 찍힌다.
            # 팀이 바뀌는 일은 드물지 않다(페퍼저축은행 -> SOOP, LCK 네이밍 스폰서).
            assert_team_names_cover(games)
            # **'종료'라는데 아직 끝났을 리 없는 경기**를 되돌린다.
            # KBO 일정 페이지가 진행 중 경기에도 점수를 채우는 바람에
            # 18:30 시작 5경기가 19:18에 '종료 0:0'으로 카드가 나갔다(2026-09-01).
            # 막지 않고 고치는 이유: 예외를 던지면 그 경기 하나 때문에
            # 리그 전체가 그 틱을 통째로 건너뛴다 — 원래 사고보다 나쁘다.
            games, _demoted = demote_impossible_finals(games, now)
            for _n in _demoted:
                soft.append(f"{name}: {_n}")
            _save_games(name, games)
            dt = _time.monotonic() - t0
            log[name] = {"at": _iso(now), "count": len(games), "error": None,
                         "seconds": round(dt, 1)}
            counts[name] = len(games)
            if dt > SLOW_FETCH_SECONDS:
                errors.append(f"{name}: 수집이 {dt:.0f}초 걸렸습니다 "
                              f"(기준 {SLOW_FETCH_SECONDS}초) — 틱이 밀립니다")
        except (GateError, UnknownStatus) as e:
            # 소스가 바뀌었거나 게이트에 걸렸다. 그 리그만 건너뛴다.
            log[name] = {"at": rec.get("at"), "failed_at": _iso(now),
                         "count": len(prev), "error": str(e)[:200]}
            counts[name] = len(prev)
            msg = f"{name}: {type(e).__name__} {str(e)[:120]}"
            (soft if _is_transient(e) else errors).append(msg)
        except Exception as e:                               # noqa: BLE001
            log[name] = {"at": rec.get("at"), "failed_at": _iso(now),
                         "count": len(prev), "error": f"{type(e).__name__}: {e}"[:200]}
            counts[name] = len(prev)
            msg = f"{name}: 예상 못한 {type(e).__name__} {str(e)[:110]}"
            (soft if _is_transient(e) else errors).append(msg)

    ROOT.mkdir(parents=True, exist_ok=True)
    FETCH_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return counts, errors, soft


def all_games() -> dict[str, list]:
    return {name: _load_games(name) for name in _jobs()}


# ── 큐 ────────────────────────────────────────────────────────

def build_all_queues(snapshots: dict[str, list], now: datetime,
                     channel: str) -> list[QueueItem]:
    """리그별로 큐를 만들어 합친다. 한 리그가 깨져도 나머지는 큐에 남는다."""
    items: list[QueueItem] = []
    for name, games in snapshots.items():
        if not games:
            continue
        try:
            items += P.build_queue(games, now, channel, floor_hours=0)
        except Exception as e:                               # noqa: BLE001
            print(f"  [큐] {name} 생성 실패 {type(e).__name__}: {str(e)[:90]}")
    items.sort(key=lambda i: i.scheduled_utc)
    return items


# ── 렌더 ──────────────────────────────────────────────────────

def render_for(item: QueueItem, games: list) -> tuple[list, list[str]] | None:
    """큐 항목 → (사진들, 캡션 파트들). 만들 수 없으면 None."""
    day = item.sports_day
    if not day:
        return None                         # scope는 이제 '리그:날짜'라 날짜로 못 쓴다
    todays = [g for g in games if g.sports_day == day]
    if not todays:
        return None

    out = ROOT / "render"
    out.mkdir(parents=True, exist_ok=True)
    tag = item.idem_key.replace("|", "_").replace(":", "-")[:80]

    if item.content_type is ContentType.MORNING:
        # '오늘/내일'은 **보내는 순간** 기준으로 판정해야 한다 — 안 넘기면
        # MLB 모닝 브리핑이 내일 아침 경기를 "오늘"이라고 부른다.
        html = P.render_morning(todays, day, now=_now())
        parts = P.caption_morning(todays, day, as_parts=True, now=_now())
    elif item.content_type is ContentType.LEAGUE_RESULT:
        # **전 경기 취소된 날도 알려야 한다 (v1.11h).**
        # 전에는 FINAL이 0건이면 조용히 건너뛰었고, 큐 항목은 유예가 지나
        # "시각을 놓쳐 취소"로 사라졌다. 구독자는 취소 사실을 채널에서 못 본다
        # (모닝은 07:30에 나가고 취소는 그 뒤에 발표된다).
        # 실측: KBO 2026-08-05·06·07·09·28 — 5경기 전 경기 폭염취소.
        if not any(g.is_terminal for g in todays):
            return None                     # 아직 아무것도 안 끝났다 — 다음 틱에 다시 본다
        html = P.render_result(todays, day)
        parts = P.caption_result(todays, day, as_parts=True)
    elif item.content_type is ContentType.START_ALERT:
        # 시작 알림은 이제 '그 리그의 하루 시간표' 하나다 (v1.11c).
        same = [g for g in games
                if day_schedule_scope(g) == item.scope and g.status is Status.SCHEDULED]
        if not same:
            return None
        # 남은 시간을 지금 기준으로 계산해야 한다 — 안 넘기면 "몇 분 뒤"가 틀린다
        return [], [P.render_start_alert(same, _now())]   # 사진 없는 텍스트 콘텐츠
    else:
        return None

    path = out / f"{tag}.png"
    w, h, b = P.render_png(html, path)
    return [(path.name, path.read_bytes(), w, h)], parts


# ── 드라이런 렌더 시험 ────────────────────────────────────────

# 드라이런에서 실제로 그려볼 카드 수. 리그마다 색·아이콘·팀 표기가 달라
# 한 장만 보면 나머지가 깨진 걸 못 잡는다(전 리그 카드가 KBO 색이던 사고가 그랬다).
# 그렇다고 전부 그리면 틱이 길어진다 — 리그별로 한 장씩, 최대 이만큼.
DRYRUN_RENDER_MAX = 6


def _render_samples(items: list, snaps: dict) -> list[str]:
    """큐에서 리그별로 한 건씩 골라 실제로 그려본다. 실패 사유들을 돌려준다."""
    out_dir = ROOT / "render"
    seen: set = set()
    picked = []
    for it in items:
        key = (it.league, it.content_type)
        if it.league in seen:
            continue
        seen.add(it.league)
        picked.append(it)
        if len(picked) >= DRYRUN_RENDER_MAX:
            break

    fails = []
    for it in picked:
        games = next((g for g in snaps.values() if g and g[0].league is it.league), [])
        tag = f"{(it.league.value if it.league else 'none')}-{it.content_type.value}"
        try:
            made = render_for(it, games)
            if made is None:
                print(f"    · {tag}: 만들 내용이 없음 (정상일 수 있음)")
                continue
            photos, parts = made
            if photos:
                name, data, w, h = photos[0]
                dest = out_dir / f"{tag}.jpg"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                print(f"    · {tag}: 카드 {w}x{h} · {len(data) // 1024}KB · "
                      f"글 {len(parts)}조각 → {dest.name}")
            else:
                print(f"    · {tag}: 글만 {len(parts)}조각 "
                      f"({sum(len(p) for p in parts)}자)")
        except Exception as e:                               # noqa: BLE001
            fails.append(f"{tag} 렌더 실패: {type(e).__name__} {str(e)[:150]}")
    return fails


# ── 한 번의 틱 ────────────────────────────────────────────────

def tick(*, dry_run: bool = False, force_fetch: bool = False) -> int:
    now = _now()
    channel = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not channel:
        print("TELEGRAM_CHAT_ID 가 없습니다.", file=sys.stderr)
        return 2

    print(f"[틱] {now.astimezone(KST):%Y-%m-%d %H:%M} KST"
          + (" · 드라이런" if dry_run else ""))

    # **발송 창이 시계 간격보다 넓은지 먼저 본다.**
    # 좁으면 아무 오류 없이 조용히 아무것도 안 나간다 — 로그는 "지금 처리 0"으로
    # 평온해 보인다. 첫날 모닝 브리핑과 시작 알림이 이렇게 하루 종일 사라졌다.
    # **게이트가 틱을 죽이면 위반보다 나쁘다 (v1.11h).**
    # 전에는 여기서 GateError가 나면 수집·큐·발송·알림이 전부 실행되지 않았다
    # (alert는 함수 말미에 있다). 부분 누락을 전면 정지로 바꾸는 셈이다.
    # 그리고 검사 기준이 **사람이 손으로 적은 100분**이라 실측 30분~4시간을 못 봤다.
    # → 실측 간격으로 검사하고, 위반은 경고로 남겨 알림에 싣는다.
    window_warnings: list[str] = []
    measured = _measured_interval_seconds(now)
    try:
        assert_send_windows(max(measured, TICK_INTERVAL_SECONDS), LOOKAHEAD_SECONDS)
    except GateError as e:
        window_warnings.append(str(e))
        print(f"  ⚠️ [발송 창] {e}")
    _record_tick(now)

    counts, errors, soft = collect(now, force=force_fetch)
    print("  [수집] " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for e in errors:
        print(f"  [수집 실패] {e}")
    for e in soft:
        # 다음 틱에 저절로 풀릴 것. 빨간불로 올리지 않는다 —
        # 계속 이어지면 아래 커버리지가 잡아 올린다.
        print(f"  [일시적 실패 — 다음 틱 재시도] {e}")

    snaps = all_games()

    # 묵은 '예정'은 소스가 종결을 안 찍은 것이다. 카드에 실리기 전에 알린다.
    stale = []
    for name, games in snaps.items():
        stale += stale_unresolved(games, now_utc=now)

    # 커버리지 감시 — 리그가 조용히 사라지는 것을 잡는 눈.
    # 무인 운영에서 가장 위험한 실패는 에러가 아니라 침묵이다.
    import coverage as CV
    cov = CV.run(snaps, _fetch_log(), now)
    if cov.findings:
        print(f"  [커버리지] 이상 {len(cov.findings)}건 "
              f"(사람이 볼 것 {len(cov.hard)}건)")
        for line in cov.lines()[:6]:
            print(f"    ⚠️ {line}")
    else:
        print(f"  [커버리지] 리그 {cov.checked}개 이상 없음")

    items = build_all_queues(snaps, now, channel)
    # 앞창은 콘텐츠마다 다르다 — 시작 알림은 일찍 보내도 되고(문구가 실시간 계산),
    # 모닝 브리핑은 일찍 보내면 안 된다(대신 유예를 넓혀 늦게 보낸다).
    due = [i for i in items
           if i.scheduled_utc <= now + timedelta(
               seconds=lookahead_for(i.content_type, LOOKAHEAD_SECONDS))]
    print(f"  [큐] 전체 {len(items)} · 지금 처리 {len(due)}")

    if dry_run:
        print("  [예약된 큐 — 앞의 14건]")
        for i in items[:14]:
            mark = "▶" if i in due else " "
            hrs = (i.scheduled_utc - now).total_seconds() / 3600
            print(f"   {mark} {i.scheduled_utc.astimezone(KST):%m-%d %H:%M} "
                  f"({hrs:+5.1f}h) {i.content_type.value:14} "
                  f"{(i.league.value if i.league else '-'):10} {i.scope}")
        if stale:
            print(f"  [경고] 묵은 '예정' {len(stale)}건 — " +
                  ", ".join(f"{g.league.value} {g.sports_day}" for g in stale[:3]))

        # **카드를 실제로 그려본다.** 계획만 찍고 넘어가면 렌더는 첫 실제 발송에서
        # 처음 시험된다 — 그때 잘못되면 되돌릴 수 없다. 특히 폰트는 서버마다 다른데,
        # 없으면 크로미움이 조용히 두부(□□□)로 그린다. 숫자 검증은 그걸 못 잡는다.
        # 여기서 그린 그림은 아티팩트로 올라가 사람이 눈으로 확인한다.
        render_errors = _render_samples(items, snaps)
        if render_errors:
            for r in render_errors:
                print(f"    ❌ {r}")

        # 커버리지 이상은 '조용한 실패'이므로 드라이런에서도 빨간불로 올린다.
        if errors or render_errors or not cov.ok:
            return 1
        return 0

    led = Ledger(LEDGER)
    tr = Transport(load_token())
    # **알림 목적지가 없으면 구독 채널로 흘리지 않는다 (v1.11h).**
    # 전에는 비면 발행 채널로 폴백해서, 구독자가
    # "⚠️ 시계 점검 필요 — 수집 실패" 같은 내부 장애 메시지를 봤다.
    # 조용히 실패하는 설정이라 눈치채기도 어렵다.
    alert_to = os.environ.get("ALERT_CHAT_ID", "").strip() or None
    if not alert_to:
        print("  ⚠️ ALERT_CHAT_ID가 없습니다 — 오류 알림을 보내지 않습니다"
              "(구독 채널로 흘리지 않기 위해서입니다)")
    snd = Sender(tr, led, channel, alert_chat_id=alert_to,
                 worker_id=os.environ.get("WORKER_ID") or None)

    sent = skipped = failed = already = 0
    missed: list[str] = []
    for item in due:
        if is_late(item.scheduled_utc, now, item.content_type):
            # **버린 것을 조용히 넘기지 않는다.**
            # 늦은 것을 버리는 건 설계대로다("늦은 안내는 거짓말이다"). 그런데
            # 그걸 로그에 안 남기면, 시계가 뜸해져 매일 모닝 브리핑을 놓쳐도
            # 아무도 모른다 — 채널이 조용한 이유를 알 수 없다.
            # 첫날 실측에서 시계 간격이 5분이 아니라 73분이었다. 이런 상황에서
            # '조용히 건너뜀'은 시스템이 죽었는지 할 일이 없었는지를 구분 못 하게 한다.
            late = (now - item.scheduled_utc).total_seconds() / 60
            missed.append(f"{item.content_type.value} {item.scope} "
                          f"(예약보다 {late:.0f}분 늦어 취소)")
            print(f"    ⏰ 지각으로 버림 {item.content_type.value} {item.scope} "
                  f"— 예약 {item.scheduled_utc.astimezone(KST):%H:%M} "
                  f"· {late:.0f}분 지남")
            skipped += 1
            continue
        games = next((g for g in snaps.values() if g and g[0].league is item.league), [])
        try:
            made = render_for(item, games)
            if made is None:
                skipped += 1
                continue
            photos, parts = made
            payload = (Payload.from_parts(photos, parts) if photos
                       else Payload(text=parts[0]))
            res = snd.send(item, payload)
            if res.state is SendState.SENT and res.already:
                # 대장에 이미 있다 — 이번에는 아무것도 안 나갔다.
                # **'발송'으로 찍으면 로그가 거짓말을 한다.** 시계가 돌 때마다
                # 같은 줄이 찍혀, 채널에 카드가 쌓이는 것처럼 보인다.
                already += 1
                print(f"    이미 보냄 {item.content_type.value} {item.scope} "
                      f"→ {res.message_ids} (중복 방지)")
            elif res.state is SendState.SENT:
                sent += 1
                print(f"    발송 {item.content_type.value} {item.scope} → {res.message_ids}")
            elif res.state is SendState.NEEDS_HUMAN:
                failed += 1
                print(f"    ⚠️ 사람 확인 필요 {item.idem_key}: {res.reason}")
            else:
                skipped += 1
        except Exception as e:                               # noqa: BLE001
            failed += 1
            print(f"    실패 {item.content_type.value} {item.scope}: "
                  f"{type(e).__name__} {str(e)[:110]}")
            traceback.print_exc(limit=2)

    print(f"  [발송] 새로 보냄 {sent} · 이미 보냄 {already} · "
          f"건너뜀 {skipped} · 실패 {failed}"
          + (f" · ⏰ 시각 놓쳐 취소 {len(missed)}" if missed else ""))

    # 알림은 사람이 봐야 할 것만. 매 틱 시끄러우면 아무도 안 본다.
    lines = []
    if errors:
        lines += [f"수집 실패 — {e}" for e in errors[:5]]
    if window_warnings:
        lines += [f"발송 창 경고 — {w}" for w in window_warnings[:2]]
    if getattr(led, "broken_lines", 0):
        # 대장 줄을 건너뛰었다 = '이미 보냄'을 잃었다 = 중복 발송 위험이다.
        lines.append(f"⚠️ 발송 대장 {led.broken_lines}줄을 읽지 못했습니다 — "
                     f"중복 발송 위험. 사람이 확인해야 합니다.")
    _needs = led.needs_human() if hasattr(led, "needs_human") else []
    if _needs:
        lines.append(f"사람 확인 필요 {len(_needs)}건 (자동 재발송 금지 상태)")
    if soft:
        lines += [f"일시적 실패(자동 재시도) — {e}" for e in soft[:3]]
    if failed:
        lines.append(f"발송 실패 {failed}건 (로그 확인)")
    if missed:
        # 지각으로 버린 것은 '아무 일도 없었다'가 아니다. 채널이 조용한 이유다.
        # 시계가 뜸해지면 여기가 먼저 알려준다.
        lines.append(f"시각을 놓쳐 취소 {len(missed)}건 — " + " · ".join(missed[:2]))
    if stale:
        ex = stale[0]
        lines.append(f"묵은 '예정' {len(stale)}건 예) {ex.league.value} {ex.sports_day}")
    # 커버리지 이상은 '조용한 실패'라 알림이 없으면 며칠 뒤에야 알게 된다
    lines += cov.lines()[:4]
    if lines:
        try:
            snd.alert("시계 점검 필요", lines)
        except Exception:                                    # noqa: BLE001
            print("    (알림 발송도 실패)")

    # 빨간불의 뜻: **사람이 손대야 한다.**
    #   · errors  — 소스 구조 변경 등, 기다려도 안 풀린다
    #   · failed  — 발송 실패. 카드가 안 나갔다
    #   · not cov.ok — 리그가 조용히 사라졌다(가장 위험한 실패)
    # soft(레이트리밋 등)는 여기 없다. 다음 틱에 풀리고, 안 풀리면 cov가 잡는다.
    return 1 if (errors or failed or not cov.ok) else 0


if __name__ == "__main__":
    args = set(sys.argv[1:])
    sys.exit(tick(dry_run="--dry-run" in args, force_fetch="--force-fetch" in args))
