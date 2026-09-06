"""경기 흐름 보강기 — 이닝·쿼터·세트·득점 시각 (v1.12 신설).

**이것은 새 수집기가 아니라 보강기다.** 경기 목록·점수·상태는 기존 리그 어댑터가
만든다. 이 파일은 이미 만들어진 `Game`에 **흐름 데이터만 얹는다.**

왜 나눴나 — 기존 어댑터를 건드리면 아홉 리그가 한꺼번에 흔들린다.
보강은 실패해도 카드가 나가야 한다: 흐름표가 빠진 결과 카드는 오늘까지 나가던 것과
같고, 어댑터가 죽으면 그날 리그가 통째로 사라진다. **위험의 크기가 다르다.**

────────────────────────────────────────────────────────────────────
실측 (2026-09-05) — 네 종목이 **경기당 요청 하나**로 끝난다.

  `/schedule/games/{gameId}` 하나에 종목별 흐름이 전부 들어 있다.

  야구  homeTeamScoreByInning ["0","0","0","0","3","0","1","0","0"]
        homeTeamRheb          [4, 10, 2, 4]        = R · H · E · B(볼넷)
        homeStarterName · winPitcherName · losePitcherName · weatherInfo
  농구  homeTeamScoreByQuarter · currentQuarter
  배구  currentScoreBySet     [{set:1, homeScore:25, awayScore:21}, …]
  축구  scorers               {home:[{time, addedTime, playerName, ownGoal}], away:[…]}

  → `record`·`preview`를 따로 부르지 않는다. 하루 30여 경기면 요청 30회다.

**팀명은 우리 표와 거의 그대로 맞는다** (실측):
  KBO 10/10 · NPB 12/12 · K리그1 12/12 · MLB 29/30.
  어긋나는 하나가 `시카고W`(우리는 `화이트삭스`) — 대표님이 정한 예외다.

────────────────────────────────────────────────────────────────────
**이 소스를 그대로 믿지 않는다.**

npb.py가 실측으로 남긴 사고가 있다 — 2026-09-03, 네이버 야구 피드가 진행 중이던
경기를 `6:2 → 2:2`로 **틀린 점수와 함께 RESULT로 선언**했다(공식 statsapi 확인
결과 6:2가 맞았다). 흐름표는 결과 카드의 얼굴이라 여기서 틀리면 카드가 거짓말을 한다.

그래서 보강 전에 **세 겹으로 막는다**:
  ① 우리 최종 점수와 네이버 최종 점수가 다르면 → 버린다
  ② 구간 합이 최종 점수와 다르면 → 버린다 (야구·농구)
  ③ 종료 경기만 보강한다 — 진행 중 경기는 값이 계속 바뀐다

막힌 경우 **아무 일도 하지 않는다.** 흐름표 없는 카드가 나가고, 알림에 이유가 실린다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (Goal, KST, League, ScoreUnit, SCORE_UNIT_BY_LEAGUE,
                      Status, TEAM_NAMES)

BASE = "https://api-gw.sports.naver.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
           "Referer": "https://m.sports.naver.com/"}

# 요청 간격. 운영 중인 시스템이 쓰는 소스라 **아끼는 쪽으로** 잡는다.
REQUEST_GAP_SECONDS = 1.2
SCHEDULE_CACHE_SECONDS = 30 * 60          # 그날 경기 목록 — 30분이면 충분하다
MAX_GAMES_PER_TICK = 40                   # 한 틱에 이 이상은 보강하지 않는다

# **캐시는 반드시 늙어 죽어야 한다 (fix51).**
#
# 종료 경기 원본을 디스크에 영구 보관하는데, 이 폴더(`g1/cache`)는 워크플로의
# 캐시 경로에 들어 있어 **실행이 바뀌어도 살아남는다.** 즉 지우는 사람이 없으면
# 시즌 내내 쌓인다 — 하루 40~60경기 × 수십 KB면 한 달에 100MB 단위다.
#
# 용량 자체보다 **캐시를 복원·저장하는 시간**이 문제다. 그 시간은 매 실행
# 앞뒤에 그대로 붙고, 시계가 5분마다 도는 시스템에서 1분은 크다.
# 흐름 보강은 '오늘 끝난 경기'에만 쓰므로 이틀이면 충분하고, 넉넉히 7일을 둔다.
CACHE_KEEP_DAYS = 7

# 리그 → (상위 카테고리, 카테고리). 실측으로 확인한 것만 적는다.
NAVER_LEAGUE: dict = {
    League.KBO: ("kbaseball", "kbo"),
    League.MLB: ("wbaseball", "mlb"),
    League.NPB: ("wbaseball", "npb"),
    League.KL1: ("kfootball", "kleague"),
    League.KBL: ("kbasketball", "kbl"),
    League.VLEAGUE_M: ("kvolleyball", "kovo"),
    League.VLEAGUE_W: ("kvolleyball", "wkovo"),
    League.EPL: ("wfootball", "epl"),
    League.LALIGA: ("wfootball", "primera"),
    League.SERIEA: ("wfootball", "seria"),
    League.BUNDESLIGA: ("wfootball", "bundesliga"),
    League.LIGUE1: ("wfootball", "ligue1"),
}

# 소스 표기 → 우리 표기.
#
# 실측(2026-09-05)으로 대조한 결과 대부분 그대로 맞았다 —
# KBO 10/10 · NPB 12/12 · K리그1 12/12 · MLB 29/30 · V리그 남 7/7 · 여 8/8
# (V리그 셋은 소스 표기를 우리 표로 가져와 맞췄다: 페퍼저축은행 · 한국도로공사 · KB손해보험).
#
# **어긋나는 자리는 두 종류뿐이고, 이유가 서로 반대다.**
#   MLB `시카고W` — 소스가 자리 때문에 줄인 축약이다. 대표님이 안 쓰기로 정했다.
#   KBL `원주 DB` — 소스가 연고지를 붙인다. 우리는 뗀다: 카드 팀명 칸이 212px인데
#     `대구 한국가스공사`는 273px이라 **구조적으로 안 들어간다**(실측). 국내 중계도
#     'DB'·'KCC'로 부른다. 이름을 늘리면 카드가 무너지고, 무너지면 뜻도 사라진다.
NAME_FIX: dict = {
    League.MLB: {"시카고W": "화이트삭스"},
    League.KBL: {"원주 DB": "DB", "부산 KCC": "KCC", "서울 SK": "SK",
                 "서울 삼성": "삼성", "수원 KT": "KT", "안양 정관장": "정관장",
                 "울산 현대모비스": "현대모비스", "창원 LG": "LG",
                 "고양 소노": "소노", "대구 한국가스공사": "가스공사"},
}

# 종목 판별 — 점수 단위가 곧 종목이다(계약에 이미 있다).
_BASEBALL = frozenset({League.KBO, League.MLB, League.NPB})
_BASKET = frozenset({League.KBL})
_VOLLEY = frozenset({League.VLEAGUE_M, League.VLEAGUE_W})
_FOOTBALL = frozenset({League.KL1, League.EPL, League.LALIGA, League.SERIEA,
                       League.BUNDESLIGA, League.LIGUE1})


def _trim_tail(rows: list) -> list:
    """**뒤쪽의 빈 구간을 잘라낸다.**

    소스는 안 한 구간도 자리를 채워 보낸다 — 농구는 연장 칸을 항상 하나 더 주고
    (실측: 4쿼터로 끝난 경기가 `[18,28,21,17,'-']`), 배구는 5세트를 늘 5개 준다.
    그대로 담으면 카드에 **빈 칸이 하나 더 생긴다** — 값이 없는 것은 사실이지만
    독자에게는 '망가진 표'로 읽힌다(약점 94).

    **중간의 빈 칸은 남긴다.** 야구 9회말을 안 친 것은 빈 칸 자체가 사실이다
    (홈팀이 이겨서 칠 필요가 없었다는 뜻이고, 스코어보드는 그것을 `-`로 적는다).
    그래서 자르는 것은 **양쪽이 다 비어 있는 뒤쪽 구간**뿐이다.
    """
    out = list(rows)
    while out and out[-1][0] is None and out[-1][1] is None:
        out.pop()
    return out


def _num(v):
    """소스는 이닝 점수를 **문자열**로 준다. 9회말을 안 치면 `"-"`가 온다."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in ("-", "—", "X", "x"):
        return None
    try:
        return int(s)
    except ValueError:
        return None


class NaverGameAdapter(NoticeMixin):
    """경기 흐름 보강기. 실패해도 예외를 던지지 않는다 — 보강은 부가 기능이다."""

    def __init__(self, *, sleep=time.sleep, opener=None, cache_dir=None):
        self._sleep = sleep
        self._op = opener or make_opener()
        self._cache_dir = pathlib.Path(cache_dir) if cache_dir else (
            pathlib.Path(__file__).resolve().parents[1] / "cache" / "naver_game")
        self._sched: dict = {}            # (리그, 날짜) → (받은시각, {(원정,홈): gameId})
        self._last_call = 0.0
        self._pruned = False
        self.reset_notices()

    def _prune_cache(self) -> None:
        """오래된 경기 원본을 지운다. **한 프로세스에 한 번만.**

        실패해도 아무 일도 없다 — 청소가 본 작업을 죽이면 안 된다(v1.11h).
        """
        if self._pruned:
            return
        self._pruned = True
        try:
            cutoff = time.time() - CACHE_KEEP_DAYS * 86400
            gone = 0
            for f in self._cache_dir.glob("*.json"):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        gone += 1
                except OSError:
                    pass
            if gone:
                self.note_info("묵은 경기 캐시 정리", f"{gone}건")
        except OSError:
            pass

    # ── 요청 ──────────────────────────────────────────────────
    def _get(self, path: str, *, label: str) -> dict:
        gap = REQUEST_GAP_SECONDS - (time.time() - self._last_call)
        if gap > 0:
            self._sleep(gap)
        req = urllib.request.Request(BASE + path, headers=HEADERS)
        raw = _fetch(self._op, req, label=label, sleep=self._sleep)
        self._last_call = time.time()
        return json.loads(raw.decode("utf-8"))

    # ── 그날 경기 목록 → gameId 찾기 ───────────────────────────
    def _schedule(self, league: League, day: str) -> dict:
        """`{(원정팀명, 홈팀명): gameId}`. 팀명은 **우리 표기로 고쳐서** 담는다."""
        key = (league, day)
        hit = self._sched.get(key)
        if hit and time.time() - hit[0] < SCHEDULE_CACHE_SECONDS:
            return hit[1]
        upper, cat = NAVER_LEAGUE[league]
        q = urllib.parse.urlencode({"fields": "basic", "upperCategoryId": upper,
                                    "fromDate": day, "toDate": day, "size": 100})
        d = self._get(f"/schedule/games?{q}", label=f"naver_game:{league.name}:schedule")
        fix = NAME_FIX.get(league, {})
        out = {}
        for g in (d.get("result") or {}).get("games") or []:
            if g.get("categoryId") != cat:
                continue
            aw = fix.get(g.get("awayTeamName"), g.get("awayTeamName"))
            hm = fix.get(g.get("homeTeamName"), g.get("homeTeamName"))
            if aw and hm and g.get("gameId"):
                out[(aw, hm)] = g["gameId"]
        self._sched[key] = (time.time(), out)
        return out

    # ── 경기 하나 ─────────────────────────────────────────────
    def _game(self, gid: str, league: League) -> dict:
        """종료된 경기는 값이 더 바뀌지 않는다 — **디스크에 영구 캐시**한다.

        5분마다 도는 시계에서 같은 경기를 다시 받으면 하루 수백 회가 된다.
        """
        self._prune_cache()
        f = self._cache_dir / f"{gid}.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass                       # 깨진 캐시는 없는 것과 같게 다룬다
        d = self._get(f"/schedule/games/{gid}", label=f"naver_game:{league.name}:game")
        g = (d.get("result") or {}).get("game") or {}
        if g.get("statusCode") == "RESULT":
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        return g

    # ── 종목별 채우기 ─────────────────────────────────────────
    #
    # 넷 다 같은 자리(`meta.line_score`)에 담는다. 순서는 **언제나 (홈, 원정)**.
    # 카드는 함수 하나로 넷을 그린다 — 골격이 하나이므로 검사도 하나다.

    def _fill_baseball(self, meta, g, want_home, want_away) -> bool:
        hi = [_num(x) for x in (g.get("homeTeamScoreByInning") or [])]
        ai = [_num(x) for x in (g.get("awayTeamScoreByInning") or [])]
        if not hi or not ai or len(hi) != len(ai):
            self.note("이닝 점수 없음", g.get("gameId"))
            return False
        # ② 구간 합 검사 — 9회말을 안 친 경기는 `None`이 섞이므로 걸러서 더한다
        if sum(v for v in hi if v) != want_home or sum(v for v in ai if v) != want_away:
            self.note("이닝 합이 최종 점수와 다름", g.get("gameId"))
            return False
        meta.line_score = _trim_tail(list(zip(hi, ai)))
        hr = g.get("homeTeamRheb") or []
        ar = g.get("awayTeamRheb") or []
        if len(hr) >= 3 and len(ar) >= 3:
            # R·H·E. 볼넷(4번째)은 넣지 않는다 — 칸이 늘면 두 자리 수가 접힌다.
            meta.line_totals = {"R": (hr[0], ar[0]), "H": (hr[1], ar[1]),
                                "E": (hr[2], ar[2])}
        sp_h, sp_a = g.get("homeStarterName"), g.get("awayStarterName")
        if sp_h and sp_a:
            meta.starting_pitchers = (str(sp_h), str(sp_a))
        win, lose = g.get("winPitcherName"), g.get("losePitcherName")
        if win and lose:
            meta.highlights = tuple(meta.highlights) + (
                ("승 · 패", f"{win} · {lose}"),)
        return True

    def _fill_basketball(self, meta, g, want_home, want_away) -> bool:
        hq = [_num(x) for x in (g.get("homeTeamScoreByQuarter") or [])]
        aq = [_num(x) for x in (g.get("awayTeamScoreByQuarter") or [])]
        if not hq or not aq or len(hq) != len(aq):
            self.note("쿼터 점수 없음", g.get("gameId"))
            return False
        if sum(v for v in hq if v) != want_home or sum(v for v in aq if v) != want_away:
            self.note("쿼터 합이 최종 점수와 다름", g.get("gameId"))
            return False
        meta.line_score = _trim_tail(list(zip(hq, aq)))
        return True

    def _fill_volleyball(self, meta, g, want_home, want_away) -> bool:
        """**소스는 안 한 세트도 `0:0`으로 항상 5개를 준다.** 거르지 않으면
        3-0 경기가 `25:21 25:18 25:20 0:0 0:0`으로 나간다."""
        sets = g.get("currentScoreBySet") or []
        rows = [(s.get("homeScore"), s.get("awayScore")) for s in sets
                if (s.get("homeScore") or 0) or (s.get("awayScore") or 0)]
        if not rows:
            self.note("세트 점수 없음", g.get("gameId"))
            return False
        # 세트 스코어(3:1)와 실제로 이긴 세트 수가 맞는지 본다
        hw = sum(1 for h, a in rows if h > a)
        aw = sum(1 for h, a in rows if a > h)
        if hw != want_home or aw != want_away:
            self.note("이긴 세트 수가 세트 스코어와 다름", g.get("gameId"))
            return False
        meta.line_score = rows
        return True

    def _fill_football(self, meta, g, want_home, want_away) -> bool:
        sc = g.get("scorers") or {}
        goals = []
        for side in ("home", "away"):
            for s in sc.get(side) or []:
                nm = (s.get("playerName") or "").strip()
                t = s.get("time")
                if not nm or t is None:
                    continue
                goals.append(Goal(minute=int(t), side=side, name=nm,
                                  own_goal=bool(s.get("ownGoal")),
                                  added=int(s.get("addedTime") or 0)))
        # **득점자 수가 점수와 맞아야 한다.** 안 맞으면 타임라인이 거짓말을 한다.
        # 0-0 경기는 득점자가 없는 것이 정상이라 통과한다.
        if (sum(1 for x in goals if x.side == "home") != want_home
                or sum(1 for x in goals if x.side == "away") != want_away):
            self.note("득점자 수가 점수와 다름", g.get("gameId"))
            return False
        meta.goals = tuple(sorted(goals, key=lambda x: (x.minute, x.added)))
        return True

    # ── 바깥에서 부르는 것 ────────────────────────────────────
    def enrich(self, games: list, league: League, *, limit: int | None = None) -> int:
        """종료된 경기에 흐름을 채운다. 채운 개수를 돌려준다.

        **예외를 던지지 않는다.** 보강이 실패해도 결과 카드는 나가야 한다 —
        오늘까지 나가던 것과 같아질 뿐이다.
        """
        if league not in NAVER_LEAGUE:
            return 0
        done = 0
        todo = [g for g in games
                if g.status is Status.FINAL and g.score is not None
                and not g.meta.line_score and not g.meta.goals]
        # **한 틱에 몇 개까지 볼지는 부르는 쪽이 정한다 (fix49).**
        # 예전엔 tick이 이 모듈의 전역 `MAX_GAMES_PER_TICK`을 덮어썼다 —
        # 한 번 덮으면 되돌아오지 않아, 같은 프로세스의 다른 사용처(검증·기록)도
        # 조용히 그 값으로 돌았다. 인자로 받으면 그런 옆효과가 없다.
        for game in todo[:(limit or MAX_GAMES_PER_TICK)]:
            try:
                day = game.start_utc.astimezone(KST).strftime("%Y-%m-%d")
                sched = self._schedule(league, day)
                aw = TEAM_NAMES.get(league, {}).get(
                    getattr(game.away, "team_code", game.away))
                hm = TEAM_NAMES.get(league, {}).get(
                    getattr(game.home, "team_code", game.home))
                gid = sched.get((aw, hm))
                if not gid:
                    # 한국 날짜로 못 찾으면 하루 앞뒤를 본다 — 현지 날짜가 다를 수 있다
                    for delta in (-1, 1):
                        d2 = (game.start_utc.astimezone(KST)
                              + timedelta(days=delta)).strftime("%Y-%m-%d")
                        gid = self._schedule(league, d2).get((aw, hm))
                        if gid:
                            break
                if not gid:
                    self.note("소스에서 같은 경기를 못 찾음", f"{aw} vs {hm}")
                    continue
                g = self._game(gid, league)
                if g.get("statusCode") != "RESULT":
                    continue               # ③ 진행 중이면 손대지 않는다
                # ① 최종 점수 대조 — 이 소스가 틀린 점수를 준 실측 사례가 있다
                nh, na = _num(g.get("homeTeamScore")), _num(g.get("awayTeamScore"))
                if nh != game.score.home or na != game.score.away:
                    self.note("최종 점수가 우리와 다름 — 보강하지 않음",
                              f"{aw} {na}:{nh} {hm}")
                    continue
                if league in _BASEBALL:
                    ok = self._fill_baseball(game.meta, g, nh, na)
                elif league in _BASKET:
                    ok = self._fill_basketball(game.meta, g, nh, na)
                elif league in _VOLLEY:
                    ok = self._fill_volleyball(game.meta, g, nh, na)
                elif league in _FOOTBALL:
                    ok = self._fill_football(game.meta, g, nh, na)
                else:
                    ok = False
                done += 1 if ok else 0
            except Exception as e:                            # noqa: BLE001
                # 보강 실패가 그 리그 수집을 죽이지 않는다
                self.note("보강 실패", f"{e.__class__.__name__}")
        if done:
            self.note_info("흐름 보강", count=done)
        return done
