# -*- coding: utf-8 -*-
"""경기 전 미리보기 — 야구 전용 (v1.44 신설 · 2차).

대표님 지시(2026-09-18): *"수집가능한 모든 데이터를 각 경기 토론방에서
알려줘야해"*.

────────────────────────────────────────────────────────────────────
**무엇이 새로 들어오나.**

지금까지 우리는 일정·점수·라인업만 받았다. 같은 소스에 `/preview` 창구가
따로 있고, 거기에 **경기 전에 알 수 있는 거의 전부**가 들어 있다
(2026-09-18 실측, KBO SSG-NC):

    선발투수    시즌성적 · **그 상대 팀 상대 성적** · 구종별 기록
    주목 타자   최근 5경기 · 핫콜드존 · 상대 팀 상대 성적
    예상 라인업 타순 10명 · 포지션
    불펜        16명
    팀 기록     타율 · 평균자책 · 홈런 · 순위
    맞대결      시즌 전적
    최근 5경기  상대·점수·승패

**축구에는 이 창구가 없다**(응답이 빈다 — 실측). 그래서 야구 셋만 받는다.

────────────────────────────────────────────────────────────────────
⚠️ **이것은 보강기다. 실패해도 아무 일도 일어나면 안 된다.**
미리보기를 못 받으면 분석 카드는 지금까지처럼(순위·승률·최근5·맞대결) 나간다.
자료 한 덩어리 때문에 그 경기 분석을 통째로 잃는 것이 훨씬 나쁘다
(`게이트가 틱을 죽이면 위반보다 나쁘다`).

⚠️ **경기 번호를 지어내지 않는다.** 우리 `source_key`와 소스의 `gameId`는
리그마다 다르다 — KBO는 뒤에 시즌이 더 붙고(`20260917SKNC0` + `2026`),
MLB는 아예 다른 번호다(우리 `823334` ↔ 소스 `20260917CWCL0`). 그래서
**그날 일정에서 대진으로 찾아낸다** — `naver_game`이 쓰는 방법 그대로다.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Optional

from contract import KST, League

PREVIEW_ENABLED = True            # ← 이 하나를 False로 두면 전부 옛 모습으로

BASE = "https://api-gw.sports.naver.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
           "Referer": "https://m.sports.naver.com/"}
TIMEOUT = 8
REQUEST_GAP_SECONDS = 1.2         # 남의 소스다 — 아끼는 쪽으로
SCHEDULE_CACHE_SECONDS = 30 * 60
PREVIEW_CACHE_SECONDS = 20 * 60   # 경기 전 자료라 자주 안 바뀐다

# 미리보기가 **있는** 리그. 표에 없으면 아무것도 하지 않는다.
# (축구·농구·배구는 이 창구가 비어 있다 — 2026-09-18 실측)
PREVIEW_LEAGUES: dict = {
    League.KBO: ("kbaseball", "kbo"),
    League.MLB: ("wbaseball", "mlb"),
    League.NPB: ("wbaseball", "npb"),
}

_sched: dict = {}                 # (리그, 날짜) → (받은시각, {(원정,홈): [(시각, id)]})
_cache: dict = {}                 # gameId → (받은시각, 미리보기)
_last_call = [0.0]


def _get(path: str) -> Optional[dict]:
    gap = REQUEST_GAP_SECONDS - (time.time() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_call[0] = time.time()
    try:
        req = urllib.request.Request(BASE + path, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:                                    # noqa: BLE001
        return None


def _norm(x) -> str:
    return "".join(str(x or "").split()).replace("·", "")


def game_id(league: League, game) -> Optional[str]:
    """그 경기의 **소스 번호**. 못 찾으면 None.

    그날 일정을 받아 **대진과 시작 시각**으로 맞춘다. 같은 날 같은 대진이
    두 번 열리는 날(더블헤더)이 있으므로 시각까지 본다 — 시각을 안 보면
    1차전 자리에 2차전 자료가 들어간다(`naver_game`이 이미 당한 병이다).
    """
    pair = PREVIEW_LEAGUES.get(league)
    if not pair:
        return None
    upper, cat = pair
    day = game.sports_day
    key = (league, day)
    hit = _sched.get(key)
    if not (hit and time.time() - hit[0] < SCHEDULE_CACHE_SECONDS):
        q = urllib.parse.urlencode({"fields": "basic", "upperCategoryId": upper,
                                    "fromDate": day, "toDate": day, "size": 100})
        d = _get(f"/schedule/games?{q}") or {}
        table: dict = {}
        for g in ((d.get("result") or {}).get("games") or []):
            if g.get("categoryId") != cat or not g.get("gameId"):
                continue
            k = (_norm(g.get("awayTeamName")), _norm(g.get("homeTeamName")))
            table.setdefault(k, []).append(
                (str(g.get("gameDateTime") or ""), g["gameId"]))
        _sched[key] = (time.time(), table)
        hit = _sched[key]
    table = hit[1]
    # 우리 이름과 소스 이름이 다를 수 있다 — 이름으로 못 찾으면 포기한다.
    # **엉뚱한 경기의 선발투수를 싣느니 아무것도 안 싣는 편이 낫다.**
    from cards_v5 import _nm
    want = (_norm(_nm(league, game.away)), _norm(_nm(league, game.home)))
    rows = table.get(want)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0][1]
    # 더블헤더 — 시작 시각이 가장 가까운 것을 고른다.
    want_hm = game.start_utc.astimezone(KST).strftime("%H:%M")
    rows2 = sorted(rows, key=lambda r: abs(
        int((r[0][11:13] or "0") + (r[0][14:16] or "0") or 0)
        - int(want_hm.replace(":", "") or 0)))
    return rows2[0][1]


def fetch(league: League, game) -> Optional[dict]:
    """그 경기 미리보기 원본. 못 받으면 None. **실패는 조용히 넘어간다.**"""
    if not PREVIEW_ENABLED:
        return None
    gid = game_id(league, game)
    if not gid:
        return None
    hit = _cache.get(gid)
    if hit and time.time() - hit[0] < PREVIEW_CACHE_SECONDS:
        return hit[1]
    d = _get(f"/schedule/games/{gid}/preview") or {}
    pv = (d.get("result") or {}).get("previewData")
    _cache[gid] = (time.time(), pv if isinstance(pv, dict) else None)
    return _cache[gid][1]


# ── 뽑아내기 ───────────────────────────────────────────────────
#
# **원본을 그대로 넘기지 않는다.** 렌더가 소스 칸 이름(`hra`·`era`·`wra`)을
# 알게 되면, 소스가 칸을 바꾸는 날 카드가 조용히 빈다. 여기서 우리 말로
# 바꿔 담고, **없는 값은 칸 자체를 안 만든다**(빈칸을 지어내지 않는다).


def _num(x) -> Optional[float]:
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None


def starter(pv: dict, side: str) -> Optional[dict]:
    """선발투수 한 명. `side`는 'home' | 'away'. 없으면 None.

    `vs`는 **그 상대 팀을 상대로 한 올 시즌 성적**이다 — 미리보기에서
    가장 값진 칸이다. 다른 데서는 무료로 못 구한다.
    """
    d = (pv or {}).get(f"{side}Starter") or {}
    info = d.get("playerInfo") or {}
    name = str(info.get("name") or "").strip()
    if not name:
        return None
    ss = d.get("currentSeasonStats") or {}
    vs = d.get("currentSeasonStatsOnOpponents") or {}
    out: dict = {"name": name}
    if ss.get("era") is not None:
        out["era"] = str(ss.get("era"))
    for k, src in (("w", ss.get("w")), ("l", ss.get("l")),
                   ("inn", ss.get("inn")), ("kk", ss.get("kk"))):
        if src is not None:
            out[k] = src
    if vs.get("gameCount"):
        out["vs"] = {"games": vs.get("gameCount"), "era": str(vs.get("era") or ""),
                     "inn": vs.get("inn"), "w": vs.get("w"), "l": vs.get("l")}
    return out


def team_stats(pv: dict, side: str) -> dict:
    """그 팀의 시즌 팀 기록 — 타율 · 평균자책 · 홈런. 없는 칸은 안 담는다."""
    d = (pv or {}).get(f"{side}Standings") or {}
    out: dict = {}
    if _num(d.get("hra")) is not None:
        out["팀타율"] = str(d.get("hra"))
    if _num(d.get("era")) is not None:
        out["팀평균자책"] = str(d.get("era"))
    if d.get("hr") is not None:
        out["팀홈런"] = str(d.get("hr"))
    return out


# 라인업 목록에 섞여 있는 **투수** 자리. 타순이 아니다.
_NOT_A_BATTER = ("선발투수", "투수")


def lineup(pv: dict, side: str) -> list:
    """예상 타순. `[(타순, 포지션, 이름)]`. 없으면 빈 목록.

    ★ **선발투수를 빼고 센다** (2026-09-18 실렌더로 잡음).
    원본 `fullLineUp`은 **투수를 맨 앞에 넣은 10명**이다. 그대로 번호를
    매겼더니 카드에 `1번 이로운 선발투수`가 찍혔다 — 지명타자를 쓰는
    리그에서 투수는 타순에 없다. 한 칸씩 밀린 타순이 **전부 거짓**이 된다.

    투수를 뺀 뒤 **아홉 명이 아니면 빈 목록**을 돌려준다. 여덟 명짜리
    타순을 그럴듯하게 그리느니 이 블록을 안 그리는 편이 낫다.
    """
    d = ((pv or {}).get(f"{side}TeamLineUp") or {}).get("fullLineUp") or []
    out = []
    for p in d:
        nm = str(p.get("playerName") or "").strip()
        pos = str(p.get("positionName") or "").strip()
        if not nm or pos in _NOT_A_BATTER:
            continue
        out.append((len(out) + 1, pos, nm))
    return out if len(out) == 9 else []


def bullpen_count(pv: dict, side: str) -> int:
    """불펜에 올라온 투수 수. 모르면 0."""
    d = ((pv or {}).get(f"{side}TeamLineUp") or {}).get("pitcherBullpen") or []
    return len(d) if isinstance(d, list) else 0


def top_player(pv: dict, side: str) -> Optional[dict]:
    """주목 타자 — 이름과 최근 5경기. 없으면 None."""
    d = (pv or {}).get(f"{side}TopPlayer") or {}
    name = str((d.get("playerInfo") or {}).get("name") or "").strip()
    if not name:
        return None
    r5 = d.get("recentFiveGamesStats") or {}
    out: dict = {"name": name}
    if r5.get("hra") is not None:
        out["hra"] = str(r5.get("hra"))
    for k in ("hit", "hr", "rbi", "ab"):
        if r5.get(k) is not None:
            out[k] = r5.get(k)
    return out


# ══════════════════════════════════════════════════════════════
# 경기 **후** 기록 — `/record` (3차 · v1.45)
# ══════════════════════════════════════════════════════════════
#
# 같은 파일에 두는 이유는 `game_id()` 때문이다. 경기 번호를 찾아내는 일이
# 이 통합에서 가장 까다로운 부분이고(리그마다 번호 체계가 다르다),
# 그것을 두 벌로 만들면 한쪽만 고치는 날이 반드시 온다(약점 45·110).

_rec_cache: dict = {}
RECORD_CACHE_SECONDS = 10 * 60


def fetch_record(league: League, game) -> Optional[dict]:
    """그 경기 기록 원본. 못 받으면 None."""
    if not PREVIEW_ENABLED:
        return None
    gid = game_id(league, game)
    if not gid:
        return None
    hit = _rec_cache.get(gid)
    if hit and time.time() - hit[0] < RECORD_CACHE_SECONDS:
        return hit[1]
    d = _get(f"/schedule/games/{gid}/record") or {}
    rd = (d.get("result") or {}).get("recordData")
    _rec_cache[gid] = (time.time(), rd if isinstance(rd, dict) else None)
    return _rec_cache[gid][1]


# 오늘의 기록 — 소스 칸 → 우리 말. **순서가 곧 카드 줄 순서다.**
_KEY_STATS = (("hit", "안타"), ("hr", "홈런"), ("kk", "삼진"),
              ("sb", "도루"), ("err", "실책"))


def key_stats(rec: dict) -> list:
    """`[(이름, 원정값, 홈값)]`. 양쪽 다 있는 칸만."""
    tk = (rec or {}).get("todayKeyStats") or {}
    a, h = tk.get("away") or {}, tk.get("home") or {}
    out = []
    for k, label in _KEY_STATS:
        if a.get(k) is None or h.get(k) is None:
            continue
        out.append((label, str(a[k]), str(h[k])))
    return out


# 투수 결과 표기. `wls`가 소스의 코드다.
_WLS = {"W": "승", "L": "패", "S": "세이브", "H": "홀드"}


def pitching_result(rec: dict) -> list:
    """`[(이름, 무엇, 시즌표기)]` — 승·패·세이브·홀드 투수.

    **홀드는 뺀다.** 한 경기에 여럿 나와 줄이 길어지는데, 승·패·세이브만큼
    궁금한 값이 아니다. 필요해지면 `_WLS`에서 다시 열면 된다.
    """
    out = []
    for p in (rec or {}).get("pitchingResult") or []:
        what = _WLS.get(str(p.get("wls") or ""))
        name = str(p.get("name") or "").strip()
        if not what or not name or what == "홀드":
            continue
        season = f"{p.get('w', 0)}승 {p.get('l', 0)}패"
        if p.get("s"):
            season += f" {p['s']}세이브"
        out.append((name, what, season))
    # 승 → 패 → 세이브 차례. 소스 순서는 들쭉날쭉하다.
    order = {"승": 0, "패": 1, "세이브": 2}
    return sorted(out, key=lambda x: order.get(x[1], 9))


# 진기록에서 **빼는** 항목. 경기 내용이 아니라 운영 정보다.
_ETC_SKIP = ("심판", "관중", "경기시간")


def etc_records(rec: dict) -> list:
    """`[(무엇, 내용)]` — 결승타 · 홈런 · 2루타 · 도루 …

    야구 중계에서 가장 많이 읽히는 칸이고, 우리가 여태 한 번도 안 쓴 값이다.
    """
    out = []
    for e in (rec or {}).get("etcRecords") or []:
        how = str(e.get("how") or "").strip()
        what = str(e.get("result") or "").strip()
        if not how or not what or how in _ETC_SKIP:
            continue
        out.append((how, what))
    return out


def _ymd(n) -> str:
    s = str(n or "")
    return f"{s[4:6]}.{s[6:8]}" if len(s) == 8 else ""


def next_games(rec: dict, side: str) -> list:
    """`[(월.일, 원정, 홈, 구장)]` — 그 팀의 다음 경기들."""
    out = []
    for g in (rec or {}).get(f"{side}TeamNextGames") or []:
        d = _ymd(g.get("gdate"))
        an, hn = str(g.get("aName") or ""), str(g.get("hName") or "")
        if not (d and an and hn):
            continue
        out.append((d, an, hn, str(g.get("stadium") or "")))
    return out
