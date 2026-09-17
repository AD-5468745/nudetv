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
    # ★ **축구도 있다** (2026-09-18 정정).
    # 처음에 "축구엔 이 창구가 없다"고 적었는데, **유럽 경기로만 확인한**
    # 탓이었다. 국내 축구는 미리보기가 꽉 차 있다 — 순위·승무패·경기당
    # 득실 · 주목 선수(공격포인트) · BEST5 · 최근 맞대결 3경기.
    # 분류 이름도 `kleague1`이 아니라 **`kleague`**다(그것도 틀렸었다).
    League.KL1: ("kfootball", "kleague"),
    # 농구도 있다 — 오히려 야구보다 칸이 많다(상대 기준 야투율까지).
    # **비시즌(4~9월)에는 경기가 없어 아무 일도 안 한다.**
    League.KBL: ("kbasketball", "kbl"),
}

# 종목이 다르면 들어 있는 칸이 다르다. **칸 이름을 리그마다 짐작하지 않는다.**
BASEBALL_LEAGUES = frozenset({League.KBO, League.MLB, League.NPB})
FOOTBALL_LEAGUES = frozenset({League.KL1})
BASKETBALL_LEAGUES = frozenset({League.KBL})

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
    # WHIP·경기수·이닝·탈삼진도 **이미 오고 있었다**(실측 2026-09-18).
    # `inn2`는 야구 표기(`114 2/3`)라 그쪽을 먼저 쓴다.
    if ss.get("whip") is not None:
        out["whip"] = str(ss.get("whip"))
    if ss.get("gameCount") is not None:
        out["games"] = ss.get("gameCount")
    for k, src in (("w", ss.get("w")), ("l", ss.get("l")),
                   ("inn", ss.get("inn2") or ss.get("inn")),
                   ("kk", ss.get("kk"))):
        if src is not None:
            out[k] = src
    if vs.get("gameCount"):
        out["vs"] = {"games": vs.get("gameCount"), "era": str(vs.get("era") or ""),
                     "inn": vs.get("inn"), "w": vs.get("w"), "l": vs.get("l")}
    return out


# 이보다 적은 표본은 **숫자만 내놓으면 안 된다.** 2경기 평균자책 4.09는
# 실력이 아니라 우연에 가깝다. 같은 회사 사이트도 이렇게 적는다(참고
# 2026-09-18): *"두 기록 모두 3경기 이하 표본이라 단정하기 이르다"*.
SMALL_SAMPLE_GAMES = 3


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
    # 시즌 성적도 온다 — 최근 5경기만 보면 표본이 너무 작다(9타수 5안타로
    # 타율 0.556 같은 수가 나온다). 시즌 값을 함께 둬야 읽는 사람이 가늠한다.
    ss = d.get("currentSeasonStats") or {}
    season: dict = {}
    for k, ours in (("gameCount", "games"), ("hra", "hra"), ("hr", "hr"),
                    ("rbi", "rbi"), ("obp", "obp")):
        if ss.get(k) is not None:
            season[ours] = str(ss[k]) if ours in ("hra", "obp") else ss[k]
    if season:
        out["season"] = season
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


# ══════════════════════════════════════════════════════════════
# 축구 미리보기 (v1.46)
# ══════════════════════════════════════════════════════════════
#
# 야구와 **칸 이름이 완전히 다르다**(`hometeam_record` ↔ `homeStandings`).
# 그래서 뽑는 함수도 따로 둔다 — 한 함수에 두 종목을 넣으면 `or`가 늘어나고,
# 그러다 한쪽 칸이 사라지는 날 어느 종목이 빈 것인지 알 수 없게 된다.


def fb_team_record(pv: dict, side: str) -> dict:
    """축구 팀 기록 — 순위 · 승무패 · **경기당 득점/실점**. 없으면 빈 dict.

    경기당 득실은 우리 분석 카드가 자리(`gf`·`ga`)만 만들어 두고 한 번도
    채우지 못한 값이다 — 축구 팀 기록을 주는 곳이 없었기 때문이다.
    """
    d = (pv or {}).get(f"{side}team_record") or {}
    out: dict = {}
    if d.get("rank") is not None:
        out["rank"] = d["rank"]
    for k in ("won", "drawn", "lost"):
        if d.get(k) is not None:
            out[k] = d[k]
    if _num(d.get("gainGoalAvg")) is not None:
        out["gf"] = str(d["gainGoalAvg"])
    if _num(d.get("lossGoalAvg")) is not None:
        out["ga"] = str(d["lossGoalAvg"])
    return out


def fb_top_player(pv: dict, side: str) -> Optional[dict]:
    """축구 주목 선수 — 공격포인트·골·도움·출전. 없으면 None."""
    d = (pv or {}).get(f"{side}_team_top_player") or {}
    name = str(d.get("playerName") or "").strip()
    if not name:
        return None
    out: dict = {"name": name}
    for k, ours in (("goals", "goals"), ("assists", "assists"),
                    ("attackPoint", "points"), ("plays", "plays")):
        if d.get(k) is not None:
            out[ours] = d[k]
    return out


def fb_best5(pv: dict, side: str) -> list:
    """`[(이름, 골, 도움)]` — 공격포인트 상위 다섯. 없으면 빈 목록."""
    out = []
    for p in (pv or {}).get(f"{side}_team_best5_players") or []:
        nm = str(p.get("playerName") or "").strip()
        if not nm:
            continue
        out.append((nm, p.get("goals"), p.get("assists")))
    return out


def fb_recent_vs(pv: dict) -> list:
    """`[(연도, 홈이름, 홈골, 원정골, 원정이름)]` — 최근 맞대결.

    **점수 칸이 비면 버린다** — 아직 안 치른 경기가 섞여 들어오는 것을
    야구 쪽에서 이미 겪었다(0-0으로 찍혔다).
    """
    out = []
    for g in (pv or {}).get("team_vs_lately_game_resultlist") or []:
        hg, ag = g.get("homeGainGoal"), g.get("awayGainGoal")
        hn = str(g.get("homeTeamName") or "")
        an = str(g.get("awayTeamName") or "")
        if hg is None or ag is None or not (hn and an):
            continue
        out.append((str(g.get("meetYear") or ""), hn, int(hg), int(ag), an))
    return out


# ══════════════════════════════════════════════════════════════
# 농구 미리보기 (v1.47)
# ══════════════════════════════════════════════════════════════
#
# 실측 2026-09-18 (3월 경기 표본 — KBL은 지금 비시즌이다):
#   seasonTeamStats  순위 · 승패 · 평균 득점/실점 · 리바운드 · 어시스트 ·
#                    최근 5경기 승패
#   teamVsTeam       **이 상대 기준** 평균 득점 · 야투% · 3점% · 자유투%
#   teamTopPlayer    득점 1위 · 리바운드 1위 · 어시스트 1위
#
# ⚠️ **KBL은 기록(RecordBook)을 모으지 않는 리그다.** 그래서 분석 카드가
# 아예 안 만들어진다 — 이 장이 그 리그의 유일한 경기 전 콘텐츠가 된다.
# 순위·전적을 여기 싣는 이유가 그것이다(야구는 분석 카드가 이미 싣는다).

_BB_STATS = (("scoreAvg", "평균 득점", True),
             ("lostScoreAvg", "평균 실점", False),
             ("reboundAvg", "리바운드", True),
             ("assistAvg", "어시스트", True))


def bb_team_stats(pv: dict, side: str) -> dict:
    """농구 팀 기록. 없으면 빈 dict."""
    d = ((pv or {}).get("seasonTeamStats") or {}).get(side) or {}
    out: dict = {}
    if d.get("rank") is not None:
        out["rank"] = d["rank"]
    if d.get("totalWin") is not None and d.get("totalLose") is not None:
        out["record"] = f"{d['totalWin']}승 {d['totalLose']}패"
    for k, label, _ in _BB_STATS:
        if _num(d.get(k)) is not None:
            out[label] = str(d[k])
    return out


def bb_vs(pv: dict, side: str) -> dict:
    """**이 상대를 만났을 때** 평균 득점·야투율·3점율. 없으면 빈 dict."""
    d = ((pv or {}).get("teamVsTeam") or {}).get(side) or {}
    out: dict = {}
    for k, label in (("vsScoreAvg", "평균 득점"), ("vsFieldGoalsPct", "야투율"),
                     ("vsThreePointsPct", "3점율")):
        if _num(d.get(k)) is not None:
            out[label] = str(d[k]) + ("%" if k.endswith("Pct") else "")
    return out


def bb_top_players(pv: dict, side: str) -> list:
    """`[(무엇, 이름, 값)]` — 득점·리바운드·어시스트 1위."""
    d = ((pv or {}).get("teamTopPlayer") or {}).get(side) or {}
    out = []
    for key, label in (("teamTopScorePlayer", "득점"),
                       ("teamTopReboundPlayer", "리바운드"),
                       ("teamTopAssistPlayer", "어시스트")):
        p = d.get(key) or {}
        nm = str(p.get("playerName") or "").strip()
        if nm and _num(p.get("statValue")) is not None:
            out.append((label, nm, str(p.get("statValue"))))
    return out
