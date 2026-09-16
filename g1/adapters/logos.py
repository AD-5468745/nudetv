# -*- coding: utf-8 -*-
"""구단 엠블럼 — 앵커 카드의 대결 그림에 쓴다 (v1.38 신설).

대표님 지시(2026-09-17): *"해당 경기 팀들의 로고를 삽입하고, 서로 대결한다는
이미지로 만들자"*.

────────────────────────────────────────────────────────────────────
**이것은 수집기가 아니라 장식 보강기다.** 실패해도 아무 일도 일어나면 안 된다 —
로고를 못 받으면 카드는 **구단색 점**으로 그려지고 그대로 나간다. 그림 한 장
때문에 그 경기 앵커를 잃는 것이 훨씬 나쁘다(`게이트가 틱을 죽이면 위반보다
나쁘다`).

**권리 메모.** 구단 엠블럼은 상표·저작권 대상이다. 여기서는 **경기 주체를
식별하는 목적으로만** 쓰고, 변형·재배포하지 않으며, 출처는 국내 포털의 공개
스포츠 화면이 쓰는 이미지와 같은 것이다. 대표님이 2026-09-17에 로고 사용을
지시하셨고, 그 판단을 기록으로 남긴다. **쓰지 않기로 바뀌면 `LOGOS_ENABLED`
하나를 끄면 전 카드가 구단색 점으로 돌아간다.**

────────────────────────────────────────────────────────────────────
**캐시는 저장소에 넣지 않는다.** 실행 임시 폴더에만 둔다 — 한 실행이 5시간
살면서 5분마다 일하므로 팀당 한 번만 받으면 되고, 공개 저장소를 남의 이미지로
불리지 않는다.
"""
from __future__ import annotations

import base64
import json
import pathlib
import tempfile
import urllib.request

LOGOS_ENABLED = True              # ← 이 하나를 False로 두면 전부 구단색 점으로

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://m.sports.naver.com/"}
_TIMEOUT = 8                      # 그림 하나에 오래 매달리지 않는다
_MAX_BYTES = 400_000              # 이보다 크면 카드에 넣지 않는다

# 리그 → 네이버 종목/카테고리. **표에 없는 리그는 로고를 안 쓴다** —
# 모르는 리그에 아무 그림이나 붙이면 엉뚱한 팀 로고가 나간다.
_BASEBALL_CATEGORY = {"KBO": "kbo", "MLB": "mlb", "NPB": "npb"}
_FOOTBALL_UPPER = {
    "KL1": "kfootball",
    "EPL": "wfootball", "LALIGA": "wfootball", "SERIEA": "wfootball",
    "BUNDESLIGA": "wfootball", "LIGUE1": "wfootball",
    "UCL": "wfootball", "UEL": "wfootball", "MLS": "wfootball",
}

_url_cache: dict = {}             # (리그) → {팀키: 그림주소}
_data_cache: dict = {}            # 그림주소 → data URI
_failed: set = set()              # 한 번 실패한 주소는 다시 안 두드린다


def _tmpdir() -> pathlib.Path:
    d = pathlib.Path(tempfile.gettempdir()) / "nudetv-logos"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=_UA), timeout=_TIMEOUT) as r:
            return r.read(_MAX_BYTES + 1)
    except Exception:                                    # noqa: BLE001
        return None


def _baseball_map(league_value: str, season: str) -> dict:
    """야구 — 시즌 팀 통계에 팀 코드와 그림 주소가 함께 온다."""
    cat = _BASEBALL_CATEGORY.get(league_value)
    if not cat:
        return {}
    raw = _get("https://api-gw.sports.naver.com/statistics/categories/"
               f"{cat}/seasons/{season}/teams")
    if not raw:
        return {}
    try:
        rows = json.loads(raw)["result"]["seasonTeamStats"]
    except Exception:                                    # noqa: BLE001
        return {}
    out = {}
    for t in rows:
        u = t.get("teamImageUrl")
        if not u:
            continue
        # `?type=f92_88`은 92px로 줄인 판이다. 원본(184px)을 쓴다 —
        # 1080px 카드에서 92px을 늘리면 뭉개진다.
        u = u.split("?")[0]
        for key in (t.get("teamId"), t.get("teamName"), t.get("teamShortName")):
            if key:
                out[str(key)] = u
    return out


def _football_map(league_value: str, day: str) -> dict:
    """축구 — 일정 응답에 엠블럼 주소가 경기마다 들어 있다."""
    upper = _FOOTBALL_UPPER.get(league_value)
    if not upper:
        return {}
    raw = _get("https://api-gw.sports.naver.com/schedule/games"
               f"?fields=basic&upperCategoryId={upper}"
               f"&fromDate={day}&toDate={day}&size=200")
    if not raw:
        return {}
    try:
        games = json.loads(raw)["result"]["games"]
    except Exception:                                    # noqa: BLE001
        return {}
    out = {}
    for g in games:
        for side in ("home", "away"):
            u = g.get(f"{side}TeamEmblemUrl")
            if not u:
                continue
            u = u.split("?")[0]
            for key in (g.get(f"{side}TeamCode"), g.get(f"{side}TeamName")):
                if key:
                    out[str(key)] = u
    return out


def _norm(x: str) -> str:
    """대조용 이름 — 공백·가운뎃점을 지운다."""
    return "".join(str(x or "").split()).replace("·", "")


def emblem_url(league_value: str, team_key: str, *, season: str = "",
               day: str = "", name: str = "") -> str | None:
    """그 팀의 엠블럼 주소. 모르면 None.

    **코드가 안 맞을 때 이름으로도 찾는다.** 우리 코드와 소스 코드가 같은
    리그(KBO)가 있는가 하면, 전혀 다른 리그(K리그: 우리 `K35` ↔ 소스 내부
    번호)도 있다. 코드만 대조하면 그런 리그는 **전부 로고가 빠진 채** 나가는데
    오류도 안 나고 회색 원판으로 조용히 대체된다 — 실제로 K리그가 그랬다.

    마지막 수단으로 **품은 이름**까지 본다(소스 `김천 상무` ⊃ 우리 `김천`).
    한 이름이 여럿에 걸리면 **쓰지 않는다** — 엉뚱한 팀 로고보다 없는 편이 낫다.
    """
    if not LOGOS_ENABLED or not (team_key or name):
        return None
    ck = (league_value, season, day)
    if ck not in _url_cache:
        if league_value in _BASEBALL_CATEGORY:
            _url_cache[ck] = _baseball_map(league_value, season or "2026")
        elif league_value in _FOOTBALL_UPPER:
            _url_cache[ck] = _football_map(league_value, day)
        else:
            _url_cache[ck] = {}
    table = _url_cache[ck]
    hit = table.get(str(team_key)) or table.get(str(name))
    if hit:
        return hit
    want = _norm(name) or _norm(team_key)
    if not want:
        return None
    exact = {v for k, v in table.items() if _norm(k) == want}
    if len(exact) == 1:
        return exact.pop()
    part = {v for k, v in table.items()
            if want and (want in _norm(k) or _norm(k) in want)}
    return part.pop() if len(part) == 1 else None


def data_uri(url: str | None) -> str | None:
    """그림을 카드에 **박아 넣을 수 있는 꼴**로. 못 받으면 None.

    카드는 오프라인에서 그려지므로 주소를 그대로 쓰면 렌더가 그림을 못 받는다.
    한 번 받아 파일로 두고, 두 번째부터는 파일에서 읽는다.
    """
    if not LOGOS_ENABLED or not url or url in _failed:
        return None
    if url in _data_cache:
        return _data_cache[url]
    name = url.rsplit("/", 2)
    fn = _tmpdir() / ("-".join(name[-2:]).replace("/", "-") or "logo.png")
    blob = None
    if fn.exists():
        try:
            blob = fn.read_bytes()
        except OSError:
            blob = None
    if blob is None:
        blob = _get(url)
        if blob is None or len(blob) > _MAX_BYTES:
            _failed.add(url)
            return None
        try:
            fn.write_bytes(blob)
        except OSError:
            pass                                          # 캐시 실패는 무시한다
    uri = "data:image/png;base64," + base64.b64encode(blob).decode()
    _data_cache[url] = uri
    return uri


def team_logo(league, team, *, season: str = "", day: str = "",
              name: str = "") -> str | None:
    """`Game.home`/`Game.away` 를 그대로 받아 data URI를 돌려준다. 없으면 None.

    `name`은 **카드에 찍히는 그 이름**이다. 코드가 소스와 다른 리그에서
    이것이 유일한 열쇠가 된다.
    """
    if league is None:
        return None
    code = getattr(team, "team_code", team)
    return data_uri(emblem_url(getattr(league, "value", str(league)), code,
                               season=season, day=day, name=name))
