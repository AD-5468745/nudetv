# -*- coding: utf-8 -*-
"""유럽 축구 순위 — football-data.org (v1.49 신설).

대표님 지시(2026-09-18): *"야구시즌 끝나면 단조로워질거야 · 무료로 가져올 수
있는 루트 확보하자 · 공신력 있는 정확한 데이터여야만해"*.

────────────────────────────────────────────────────────────────────
**무엇이 막혀 있었나.**

유럽 7개 대회(EPL·라리가·세리에A·분데스·리그앙·UCL·UEL)는 **순위 기록을 아예
안 모으고 있었다.** 그래서 분석 카드가 한 장도 안 만들어졌다 — 분석 카드는
순위표(`RecordBook`)가 있어야 만들어지기 때문이다.

네이버는 유럽 순위를 안 준다(실측 2026-09-18: 창구 8개 전부 403/400).
football-data는 준다(대표님이 직접 키로 확인).

────────────────────────────────────────────────────────────────────
★ **진짜 걸림돌은 데이터가 아니라 이름이었다.**

    football-data →  MCI · ARS · LIV       (영문 세 글자)
    우리 데이터   →  맨시티 · 아스널 · 리버풀  (네이버가 준 한글 약칭)

이걸 잇는 표가 7개 대회 약 140팀 분량이다. **손으로 적지 않는다** — 하나만
틀려도 그 팀 순위가 카드에 잘못 찍히고, 틀린 것을 알아챌 방법이 없다.

**대신 경기로 짝짓는다.** 같은 경기를 양쪽에서 받아 `날짜 + 홈/원정`으로
맞추면 `맨시티 ↔ MCI`가 저절로 나온다. 사람이 적지 않고, 기계가 센다.

    우리(네이버)  9/14 맨시티 vs 맨유
    football-data 9/14 MCI    vs MUN     →  맨시티=MCI · 맨유=MUN

⚠️ **한 번이라도 어긋나면 그 팀은 버린다.** 같은 한글 이름이 두 영문 코드에
붙으면(또는 그 반대) 둘 다 쓰지 않는다 — 엉뚱한 팀 순위를 싣느니 그 팀을
빼는 편이 낫다.

⚠️ **표가 덜 차면 그 리그 순위를 통째로 안 만든다.** 반쪽 순위표는 순위가
틀린 표다.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Optional

from contract import (GateError, League, RecordBook, Standing,
                      StreakKind, WLD, assert_recordbook)

FD_RECORDS_ENABLED = True         # ← 이 하나를 False로 두면 전부 옛 모습으로

_API = "https://api.football-data.org/v4"
_TIMEOUT = 12
REQUEST_GAP_SECONDS = 6.5         # 무료 등급 분당 10회 — 넉넉히 띄운다

# 대조표를 만들 때 몇 날짜치 경기를 보나. 넓을수록 잘 맞는다.
MATCH_WINDOW_DAYS = 21
# 이만큼은 짝이 맞아야 그 리그 순위를 쓴다(표의 팀 수 대비).
MIN_MAPPED_RATIO = 0.8

_last_call = [0.0]
_notes: list = []                 # 사람이 읽을 진단. 틱이 거둬 알림에 싣는다.


def take_notes() -> list:
    out = list(_notes)
    _notes.clear()
    return out


def _get(path: str, token: str) -> Optional[dict]:
    gap = REQUEST_GAP_SECONDS - (time.time() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_call[0] = time.time()
    req = urllib.request.Request(
        _API + path, headers={"X-Auth-Token": token,
                              "User-Agent": "nudetv-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        _notes.append(f"football-data {path.split('/')[2]}: 상태 {e.code}")
        return None
    except Exception as e:                               # noqa: BLE001
        _notes.append(f"football-data 조회 실패: {type(e).__name__}")
        return None


def _stamp(iso: str) -> str:
    """UTC ISO → `YYYY-MM-DDTHH:MM` (UTC). 못 읽으면 빈 문자열.

    ★ **날짜로 맞추지 않는다** (2026-09-18 시험이 잡았다).
    날짜는 소스마다 기준이 다르다 — 18:00 UTC 경기는 한국 날짜로 **다음 날**
    이다. 처음엔 한국 날짜로 맞췄더니 하루씩 밀려 `MCI → 아스널` 같은
    **엉뚱한 짝**이 나왔다. 오류도 안 나고 조용히 틀렸다.

    킥오프 **시각**은 두 소스가 같은 경기에 대해 같은 값을 준다 — 기준이
    끼어들 자리가 없다.
    """
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def build_mapping(code: str, token: str, our_games: list) -> dict:
    """`{영문TLA: 우리 팀코드}`. 못 만들면 빈 dict.

    우리 경기와 소스 경기를 **날짜 + 홈/원정 자리**로 맞춘다.
    같은 날 같은 자리에 경기가 여럿이면 그 날짜는 **통째로 건너뛴다** —
    맞을 수도 있지만 확인할 방법이 없고, 틀리면 조용히 틀린다.
    """
    lo = (datetime.now(timezone.utc) - timedelta(days=MATCH_WINDOW_DAYS))
    d = _get(f"/competitions/{code}/matches"
             f"?dateFrom={lo.strftime('%Y-%m-%d')}"
             f"&dateTo={(datetime.now(timezone.utc) + timedelta(days=7)).strftime('%Y-%m-%d')}",
             token)
    if not isinstance(d, dict):
        return {}
    # 소스 쪽: 킥오프 시각 → [(홈TLA, 원정TLA)]
    src: dict = {}
    for m in d.get("matches") or []:
        h = (m.get("homeTeam") or {}).get("tla")
        a = (m.get("awayTeam") or {}).get("tla")
        at = _stamp(m.get("utcDate"))
        if h and a and at:
            src.setdefault(at, []).append((h, a))
    # 우리 쪽: 킥오프 시각 → [(홈코드, 원정코드)]
    ours: dict = {}
    for g in our_games:
        at = g.start_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        ours.setdefault(at, []).append(
            (g.home.team_code, g.away.team_code))

    pairs: dict = {}                       # TLA → {우리코드: 본 횟수}
    for at, rows in ours.items():
        srows = src.get(at) or []
        # **같은 시각에 경기가 여럿이면 자리로 못 가른다** — 건너뛴다.
        # 유럽 대회는 단독 경기(월·화·금)가 꾸준히 있어 표가 결국 찬다.
        if len(rows) != 1 or len(srows) != 1:
            continue
        (oh, oa), (sh, sa) = rows[0], srows[0]
        for tla, ours_code in ((sh, oh), (sa, oa)):
            pairs.setdefault(tla, {})
            pairs[tla][ours_code] = pairs[tla].get(ours_code, 0) + 1

    # **한 TLA에 우리 코드가 둘 이상 붙으면 버린다.**
    out: dict = {}
    for tla, counts in pairs.items():
        if len(counts) == 1:
            out[tla] = next(iter(counts))
    # **거꾸로도 하나여야 한다** — 우리 코드 하나에 TLA가 둘이면 둘 다 버린다.
    rev: dict = {}
    for tla, ours_code in out.items():
        rev.setdefault(ours_code, []).append(tla)
    for ours_code, tlas in rev.items():
        if len(tlas) > 1:
            for t in tlas:
                out.pop(t, None)
    return out


def _streak_of(form: str) -> tuple:
    """`"W,W,L,D,W"` → (종류, 길이). 최근이 **앞**인지 뒤인지 모르면 안 쓴다.

    football-data의 `form`은 **오래된 것이 앞**이다(문서). 그래서 뒤에서 센다.
    모양이 다르면 아무것도 안 돌려준다 — 지어내지 않는다.
    """
    parts = [x.strip() for x in str(form or "").split(",") if x.strip()]
    if not parts or any(p not in ("W", "D", "L") for p in parts):
        return (StreakKind.NONE, 0)
    last = parts[-1]
    n = 0
    for p in reversed(parts):
        if p != last:
            break
        n += 1
    kind = {"W": StreakKind.WIN, "L": StreakKind.LOSS,
            "D": StreakKind.DRAW}.get(last, StreakKind.NONE)
    return (kind, n if kind is not StreakKind.NONE else 0)


def fetch(league: League, code: str, our_games: list,
          token: str) -> Optional[RecordBook]:
    """그 대회 순위표. 못 만들면 None — **반쪽 표는 안 만든다.**"""
    if not FD_RECORDS_ENABLED:
        return None
    d = _get(f"/competitions/{code}/standings", token)
    if not isinstance(d, dict):
        return None
    tables = d.get("standings") or []
    # 리그는 `TOTAL` 표 하나다. 조별 대회(UCL 조별리그)는 표가 여럿이라
    # **지금은 다루지 않는다** — 조 이름까지 맞춰야 순위가 참이 된다.
    total = [t for t in tables if str(t.get("type") or "") == "TOTAL"]
    if len(total) != 1:
        _notes.append(f"{code}: 순위표가 {len(total)}개라 건너뜁니다"
                      " (조별 대회는 아직 안 다룹니다)")
        return None
    rows = total[0].get("table") or []
    if not rows:
        return None

    mapping = build_mapping(code, token, our_games)
    need = int(len(rows) * MIN_MAPPED_RATIO)
    if len(mapping) < need:
        _notes.append(
            f"{code}: 팀 이름 대조가 {len(mapping)}/{len(rows)}밖에 안 돼 "
            f"순위를 안 만듭니다 (필요 {need}). 단독 경기가 쌓이면 채워집니다")
        return None

    season = str((d.get("season") or {}).get("startDate") or "")[:4] or "2026"
    lead_pts = None
    stands: list = []
    for r in rows:
        tla = (r.get("team") or {}).get("tla")
        ours_code = mapping.get(tla)
        if not ours_code:
            continue                       # 못 맞춘 팀은 뺀다(지어내지 않는다)
        won, draw, lost = (int(r.get("won") or 0), int(r.get("draw") or 0),
                           int(r.get("lost") or 0))
        games = int(r.get("playedGames") or (won + draw + lost))
        pts = int(r.get("points") or (won * 3 + draw))
        if lead_pts is None:
            lead_pts = pts
        stands.append(Standing(
            league=league, season=season, team_code=ours_code,
            rank=int(r.get("position") or (len(stands) + 1)),
            games=games, record=WLD(won, lost, draw),
            # 승점률 — 축구의 '승률' 칸이 이것이다(`contract.PCT_RULE`).
            pct=f"{pts / (games * 3):.3f}" if games else "0.000",
            # 승차 — 축구는 **승점 차**다(`contract.gap_label`).
            games_behind=str(lead_pts - pts),
            last10=None,
            streak_kind=_streak_of(r.get("form"))[0],
            streak_len=_streak_of(r.get("form"))[1], group=None))
    if not stands:
        return None

    rb = RecordBook(league=league, season=season,
                    collected_utc=datetime.now(timezone.utc),
                    source_url=f"{_API}/competitions/{code}/standings",
                    standings=stands, h2h={}, leaders={})
    # **게이트를 통과한 것만 내보낸다.** 여기서 막히면 그 리그는 이번에 없다 —
    # 틀린 순위표가 카드에 찍히는 것보다 낫다.
    try:
        assert_recordbook(rb, now_utc=datetime.now(timezone.utc))
    except GateError as e:
        _notes.append(f"{code}: 순위표가 게이트에 걸렸습니다 — {str(e)[:90]}")
        return None
    return rb
