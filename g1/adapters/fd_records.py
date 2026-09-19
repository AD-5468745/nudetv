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
# ★ **전부 맞아야 쓴다** (v1.50 · 대표님: *"발송누락도 절대 없어야해"*).
#
# 전에는 80%였다. 그런데 **반쪽 순위표는 순위가 틀린 표**다 — 빠진 팀 위에
# 있던 팀들이 한 계단씩 올라가 보인다. 80%로 내보내는 것은 누락을 막는 게
# 아니라 **틀린 것을 내보내는** 것이다.
#
# 대신 **짝짓기가 스스로 끝까지 풀리게** 만들었다(`build_mapping`의 되풀이).
# 그래도 못 맞춘 팀이 남으면 **그 이름을 알림에 찍는다** — 몇 팀만 손으로
# 채우면 되도록.
MIN_MAPPED_RATIO = 1.0

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

    # ── 짝짓기 — **모르는 것을 아는 것으로 푼다** (v1.50) ──────────
    #
    # 처음엔 "한 시각에 경기가 하나인 날"만 썼다. 그러면 표가 아주 천천히
    # 차고, 다 차기 전까지 그 리그는 **아무것도 안 나간다**.
    # 대표님 지시(2026-09-18): *"발송누락도 절대 없어야해"*.
    #
    # 그래서 **풀어 나간다**: 한 시각에 경기가 여럿이어도, 그중 한 팀을
    # 이미 알고 있으면 같은 경기의 나머지 한 팀이 정해진다. 그것을 더는
    # 새로 정해지는 것이 없을 때까지 되풀이한다. 스무 팀짜리 리그는
    # 보통 한두 라운드면 전부 찬다.
    #
    # ⚠️ **추측은 한 걸음도 하지 않는다.** 정해지는 것은 언제나
    # "이 경기의 이 자리"가 한 쪽으로만 확정될 때뿐이다.
    known: dict = {}                       # TLA → 우리코드
    rev: dict = {}                         # 우리코드 → TLA
    slots = [(at, rows, src.get(at) or []) for at, rows in ours.items()]

    def _learn(tla, code) -> bool:
        """새로 알게 됐으면 True. **어긋나면 둘 다 버리고** False."""
        if tla in known or code in rev:
            if known.get(tla) != code or rev.get(code) != tla:
                # 이미 다른 짝이 있다 — 둘 중 하나는 거짓이다. 둘 다 지운다.
                _notes.append(f"{code}: 팀 짝짓기가 어긋나 버립니다")
                rev.pop(known.pop(tla, None), None)
                known.pop(rev.pop(code, None), None)
            return False
        known[tla] = code
        rev[code] = tla
        return True

    for _ in range(12):                    # 되풀이 상한 — 안 끝나면 그만둔다
        moved = False
        for at, rows, srows in slots:
            if not rows or len(rows) != len(srows):
                continue                   # 양쪽 경기 수가 다르면 못 믿는다
            if len(rows) == 1:
                (oh, oa), (sh, sa) = rows[0], srows[0]
                moved |= _learn(sh, oh)
                moved |= _learn(sa, oa)
                continue
            # 여럿이면 **이미 아는 팀으로 그 경기를 집어낸다.**
            for oh, oa in rows:
                cand = [(sh, sa) for sh, sa in srows
                        if rev.get(oh) in (None, sh) and rev.get(oa) in (None, sa)
                        and (rev.get(oh) == sh or rev.get(oa) == sa)]
                if len(cand) == 1:
                    sh, sa = cand[0]
                    moved |= _learn(sh, oh)
                    moved |= _learn(sa, oa)
        if not moved:
            break
    return dict(known)


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


# ── 우리 창고로 흐름을 센다 (v1.71) ──────────────────────────────
#
# football-data 무료 등급 순위표에 **`form`이 안 온다**(실측 2026-09-20:
# 1위 아스널 4경기 전승인데 `form: None`). 그래서 유럽 리그 앵커의
# `흐름` 줄이 통째로 비어 있었다 — 카드가 거짓말을 하진 않지만 대표님이
# *"실제 데이터가 너무 빈약해"* 라고 하신 바로 그 자리다.
#
# 재료는 이미 있다. 우리는 **유럽 경기를 45일치 갖고 있다**
# (`naver_football.FORM_HISTORY_DAYS`). 끝난 경기를 최근순으로 훑으면
# 연승·연패가 그대로 나온다.
#
# ★ **다만 창고 끝에 닿으면 세지 않는다.**
#   창고가 45일이라, 3연승이 창고 맨 앞 경기까지 이어지면 **그 앞이 어땠는지
#   우리는 모른다.** 진짜 6연승인데 "3연승"이라고 찍으면 그건 없는 사실을
#   만든 것이다(심각도 `높음` — 사람이 잘못 안다). 연속을 **끊는 경기를
#   실제로 봤을 때만** 길이를 주장한다. 종료 시각을 지어내지 않고 '우리가
#   안 시각'만 적는 것과 같은 규율이다(`GameMeta.first_final_at`).
STREAK_MIN_GAMES = 2              # 한 경기는 '연속'이 아니다
LAST10_EXACT = 10                 # '최근10'은 10경기일 때만 참이다

# ── 창고가 얼마나 깊은가 (실측 2026-09-20) ──────────────────────
#   수집기를 직접 돌려 셌다 (`FORM_HISTORY_DAYS = 45`):
#     EPL    끝난 경기 45건 · 08-21~09-19 · **팀당 5경기**
#     라리가 끝난 경기 62건 · 08-15~09-19 · **팀당 6~7경기**
#
# → `흐름`은 채워진다. 연속은 보통 2~4라 끊는 경기가 창 안에 들어온다.
# → `최근10`은 **지금은 못 채운다.** 다만 창이 좁아서가 아니라
#   **시즌이 5라운드밖에 안 지났기 때문이다**(football-data `playedGames: 4`).
#   창을 넓혀도 받아올 옛 경기가 아직 없다 — 그래서 **지금 넓히지 않는다.**
#   남의 소스를 더 두드려 봐야 얻는 것이 0이다.
#
#   ★ 다시 볼 조건: **시즌이 10라운드를 넘긴 뒤에도 `최근10`이 비면**,
#     그때는 창(45일)이 원인이다 — 주 1경기 기준 10경기는 약 75일이다.
#     그때 `naver_football.FORM_HISTORY_DAYS`를 올리면 된다.


def _result_for(game, code: str) -> Optional[str]:
    """그 팀에게 이 경기가 `W`/`D`/`L` 중 무엇이었나. 못 세면 None."""
    sc = getattr(game, "score", None)
    if sc is None or not getattr(game, "is_terminal", False):
        return None
    if game.home.team_code == code:
        mine, theirs = sc.home, sc.away
    elif game.away.team_code == code:
        mine, theirs = sc.away, sc.home
    else:
        return None
    return "W" if mine > theirs else ("L" if mine < theirs else "D")


def _recent_results(our_games: list, code: str) -> list:
    """그 팀의 끝난 경기 결과를 **최근이 앞**으로. 없으면 빈 목록."""
    rows = []
    for g in our_games or ():
        r = _result_for(g, code)
        if r:
            rows.append((g.start_utc, r))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in rows]


def _streak_from_archive(our_games: list, code: str) -> tuple:
    """우리 창고로 센 (종류, 길이). 확신 못 하면 `(NONE, 0)`.

    창고 끝까지 같은 결과로 이어지면 **그 앞을 모르므로 주장하지 않는다.**
    """
    res = _recent_results(our_games, code)
    if len(res) < STREAK_MIN_GAMES:
        return (StreakKind.NONE, 0)
    last = res[0]
    n = 0
    for r in res:
        if r != last:
            break
        n += 1
    if n == len(res):
        return (StreakKind.NONE, 0)   # 끊는 경기를 못 봤다 — 길이를 모른다
    if n < STREAK_MIN_GAMES:
        return (StreakKind.NONE, 0)
    kind = {"W": StreakKind.WIN, "L": StreakKind.LOSS,
            "D": StreakKind.DRAW}[last]
    return (kind, n)


def _last10_from_archive(our_games: list, code: str) -> Optional[WLD]:
    """우리 창고로 센 최근 10경기. **딱 10경기일 때만** 돌려준다 —
    7경기를 세어 놓고 '최근10'이라고 적으면 카드가 거짓말을 한다."""
    res = _recent_results(our_games, code)
    if len(res) < LAST10_EXACT:
        return None
    ten = res[:LAST10_EXACT]
    return WLD(ten.count("W"), ten.count("L"), ten.count("D"))


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
    _missing = [str((r.get("team") or {}).get("tla") or "?") for r in rows
                if (r.get("team") or {}).get("tla") not in mapping]
    if _missing:
        # **이름을 찍는다.** "3/20밖에 안 됩니다"로는 무엇을 고쳐야 할지 모른다.
        _notes.append(
            f"{code}: 아직 못 맞춘 팀 {len(_missing)}개 — "
            + ", ".join(sorted(_missing)[:8])
            + (" 외" if len(_missing) > 8 else "")
            + ". 그 리그 순위는 전부 맞을 때까지 안 내보냅니다"
              " (반쪽 표는 순위가 틀립니다)")
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
        _sk, _sn = _streak_of(r.get("form"))
        if _sk is StreakKind.NONE or _sn == 0:
            _sk, _sn = _streak_from_archive(our_games, ours_code)
        stands.append(Standing(
            league=league, season=season, team_code=ours_code,
            rank=int(r.get("position") or (len(stands) + 1)),
            games=games, record=WLD(won, lost, draw),
            # 승점률 — 축구의 '승률' 칸이 이것이다(`contract.PCT_RULE`).
            pct=f"{pts / (games * 3):.3f}" if games else "0.000",
            # 승차 — 축구는 **승점 차**다(`contract.gap_label`).
            games_behind=str(lead_pts - pts),
            # 소스가 주면 소스가 먼저, 안 주면 우리 창고로 센다.
            # **두 벌을 만들지 않는다** — 소스가 `form`을 주기 시작하면
            # 그 순간부터 자동으로 그쪽을 쓴다.
            last10=_last10_from_archive(our_games, ours_code),
            streak_kind=_sk, streak_len=_sn, group=None))
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
