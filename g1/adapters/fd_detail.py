# -*- coding: utf-8 -*-
"""유럽 축구 경기 **상세** — football-data.org (v1.48 신설).

대표님 지시(2026-09-18): *"작업부터 진행해. 그리고 나중에 유료전환 할거니까
미리 배선준비"*.

────────────────────────────────────────────────────────────────────
**무엇을 쓰나 — 실측 2026-09-18, 무료 등급.**

대표님이 직접 키를 넣어 재 본 결과다(`probe_football_data.py`):

    ✅ 맞대결(head2head)  누적 기록 + 최근 5경기
    ✅ 순위표             (이미 쓰는 중)
    ✅ 심판
    ❌ 라인업 · 득점자 · 경기 기록 · 경고/퇴장 · 교체 · 관중 · 경기장

그래서 지금 새로 쓰는 것은 **맞대결 하나**다. 그것만으로도 값이 있다 —
유럽 축구 분석 카드의 맞대결은 지금 *우리가 가진 스냅샷 범위*로만 세고 있어
얇다(MLB는 일주일치뿐인 적도 있었다). 공식 누적 기록으로 바꾼다.

**베팅 배당(`odds`)은 응답에 있어도 쓰지 않는다.** 우리 채널은 확률·추천을
쓰지 않는다는 원칙이 있고, 배당은 그 선을 넘는다.

────────────────────────────────────────────────────────────────────
★ **유료로 바꾸면 저절로 켜진다 — 스위치가 없다.**

유료 등급에서는 같은 창구에 라인업·득점자·경고가 더 얹혀 온다.
그래서 이 파일은 **"있으면 읽고 없으면 넘어간다"**로 썼다:

    detail(...)  →  {"h2h": …, "lineup": …, "goals": …, "bookings": …}
                    무료 등급에서는 뒤 셋이 없어서 그 칸이 안 생긴다.

나중에 등급을 올리면 **코드를 안 고쳐도** 그 칸이 채워지기 시작한다.
`upgraded()`가 그 순간을 알아채 운영 알림에 한 줄 남긴다 — 사람이
"이제 무엇을 더 쓸 수 있는지" 알아야 카드를 손볼 수 있기 때문이다.

끄고 싶으면 `FD_DETAIL_ENABLED = False` 하나면 전부 옛 모습으로 돌아간다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional

from contract import League

FD_DETAIL_ENABLED = True          # ← 이 하나를 False로 두면 전부 꺼진다

_API = "https://api.football-data.org/v4"
_TIMEOUT = 10
# 무료 등급은 **분당 10회**다. 우리 시계는 2분마다 도는데 유럽 경기가 몰리는
# 날이 있어, 한 틱에 두드리는 횟수를 손으로 묶어 둔다.
MAX_CALLS_PER_TICK = 4
REQUEST_GAP_SECONDS = 6.5
H2H_CACHE_SECONDS = 12 * 3600     # 맞대결은 하루에 한 번이면 충분하다

# 이 파일이 다루는 리그. `football_data.COMPETITION`과 **같은 표를 쓴다** —
# 두 벌로 두면 한쪽만 늘어나는 날이 온다.
try:
    from adapters.football_data import COMPETITION as _COMP
    LEAGUES = frozenset(_COMP.values())
except Exception:                                        # noqa: BLE001
    LEAGUES = frozenset()

_h2h_cache: dict = {}
_last_call = [0.0]
_calls = [0]
# 유료 전환을 알아채면 여기 쌓인다. 틱이 거둬 운영 알림에 싣는다.
_upgrades: list = []
_seen_extra: set = set()


def reset_tick() -> None:
    """틱마다 호출 수를 0으로. **안 부르면 한도를 넘는다.**"""
    _calls[0] = 0


def take_upgrades() -> list:
    out = list(_upgrades)
    _upgrades.clear()
    return out


def _token() -> str:
    try:
        from adapters.football_data import load_token
        return load_token() or ""
    except Exception:                                    # noqa: BLE001
        return ""


def _get(path: str, token: str) -> Optional[dict]:
    """조회 하나. 막히면 None — **막힌 것도 답이라 조용히 넘어간다.**"""
    if _calls[0] >= MAX_CALLS_PER_TICK:
        return None
    gap = REQUEST_GAP_SECONDS - (time.time() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_call[0] = time.time()
    _calls[0] += 1
    req = urllib.request.Request(
        _API + path, headers={"X-Auth-Token": token,
                              "User-Agent": "nudetv-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return None                       # 403(등급) · 429(한도) 모두 여기로
    except Exception:                                    # noqa: BLE001
        return None


# ── 유료 전환 감지 ─────────────────────────────────────────────
#
# 무료에서는 안 오는 칸들. 이 중 하나라도 오기 시작하면 등급이 올라간 것이다.
_PAID_FIELDS = ("lineup", "bench", "goals", "bookings", "substitutions",
                "statistics", "attendance")


def _note_upgrade(m: dict) -> None:
    for k in _PAID_FIELDS:
        if k in _seen_extra:
            continue
        here = m.get(k) or any(
            isinstance(x, dict) and x.get(k)
            for x in (m.get("homeTeam"), m.get("awayTeam")) if x)
        if here:
            _seen_extra.add(k)
            _upgrades.append(
                f"football-data에서 `{k}`가 오기 시작했습니다 — 유료 등급으로 "
                f"바뀐 것 같습니다. 이 값을 카드에 쓰려면 말씀해 주세요")


def head2head(match_id: int, *, limit: int = 10) -> Optional[dict]:
    """두 팀의 **공식 누적 맞대결**. 못 받으면 None.

    돌려주는 것:
        {"total": 경기수, "home": {"wins","draws","losses"},
         "away": {...}, "recent": [(연월일, 홈, 홈골, 원정골, 원정), …]}

    `home`/`away`는 **그 경기의** 홈·원정 팀 기준이다.
    """
    if not FD_DETAIL_ENABLED or not match_id:
        return None
    key = int(match_id)
    hit = _h2h_cache.get(key)
    if hit and time.time() - hit[0] < H2H_CACHE_SECONDS:
        return hit[1]
    tok = _token()
    if not tok:
        return None
    d = _get(f"/matches/{key}/head2head?limit={int(limit)}", tok)
    out = None
    if isinstance(d, dict):
        agg = d.get("aggregates") or {}
        h, a = agg.get("homeTeam") or {}, agg.get("awayTeam") or {}
        if agg.get("numberOfMatches"):
            out = {"total": int(agg["numberOfMatches"]),
                   "home": {"wins": int(h.get("wins") or 0),
                            "draws": int(h.get("draws") or 0),
                            "losses": int(h.get("losses") or 0)},
                   "away": {"wins": int(a.get("wins") or 0),
                            "draws": int(a.get("draws") or 0),
                            "losses": int(a.get("losses") or 0)},
                   "recent": _recent(d.get("matches") or [])}
        # 유료 전환 감지는 **여기서도** 한다 — 맞대결 응답의 경기들에도
        # 유료 칸이 실려 오기 시작한다.
        for m in (d.get("matches") or [])[:1]:
            if isinstance(m, dict):
                _note_upgrade(m)
    _h2h_cache[key] = (time.time(), out)
    return out


def _recent(matches: list) -> list:
    """`[(월.일, 홈이름, 홈골, 원정골, 원정이름)]` — **끝난 경기만.**

    ⚠️ 앞으로 열릴 경기가 섞여 들어오면 점수가 비거나 0-0으로 온다.
    야구 쪽에서 이미 그렇게 당했다(아직 안 한 경기가 0-0 무승부로 찍혔다).
    """
    out = []
    for m in matches:
        if str(m.get("status") or "") != "FINISHED":
            continue
        ft = ((m.get("score") or {}).get("fullTime") or {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None:
            continue
        d = str(m.get("utcDate") or "")[:10]
        out.append((f"{d[5:7]}.{d[8:10]}" if len(d) == 10 else "",
                    str((m.get("homeTeam") or {}).get("shortName")
                        or (m.get("homeTeam") or {}).get("name") or ""),
                    int(hg), int(ag),
                    str((m.get("awayTeam") or {}).get("shortName")
                        or (m.get("awayTeam") or {}).get("name") or "")))
    return out


def detail(match_id: int) -> Optional[dict]:
    """경기 하나의 **있는 것 전부**. 무료에서는 거의 비어 있다.

    ★ 유료로 바꾸면 여기 칸이 저절로 늘어난다 — 코드를 안 고쳐도 된다.
    """
    if not FD_DETAIL_ENABLED or not match_id:
        return None
    tok = _token()
    if not tok:
        return None
    m = _get(f"/matches/{int(match_id)}", tok)
    if not isinstance(m, dict):
        return None
    _note_upgrade(m)
    out: dict = {}
    for k in _PAID_FIELDS:
        if m.get(k):
            out[k] = m[k]
    if m.get("referees"):
        out["referees"] = [str(r.get("name") or "") for r in m["referees"]
                           if r.get("name")]
    return out or None


def supports(league: League) -> bool:
    """이 리그를 이 소스로 볼 수 있는가."""
    return FD_DETAIL_ENABLED and league in LEAGUES
