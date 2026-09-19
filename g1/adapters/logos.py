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
import re
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


def _get(url: str, limit: int | None = None) -> bytes | None:
    """`limit`은 **읽을 상한**이다. 그림은 `_MAX_BYTES`(400KB)를 넘으면 카드에
    안 넣으므로 딱 그만큼만 읽는다. 다만 주소를 되찾는 글자 파일은 그보다
    훨씬 커서(KOVO 묶음 3.3MB, 찾는 글자가 927KB 지점) 상한을 따로 준다 —
    상한이 모자라면 **있는 것을 못 찾고 없다고 말한다.**"""
    cap = (_MAX_BYTES if limit is None else limit) + 1
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=_UA), timeout=_TIMEOUT) as r:
            return r.read(cap)
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
    # 배구는 소스가 엠블럼을 안 준다 — **우리 코드로 바로 잇는다**(위 표).
    _vb = _VOLLEY_EMBLEM.get(league_value)
    if _vb is not None:
        slug = _vb.get(str(team_key))
        return _KOVO_EMBLEM.format(slug) if slug else None
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


def _sniff(blob: bytes) -> str | None:
    """받은 것이 **정말 그림인가**, 그렇다면 무슨 꼴인가 (v1.69).

    두 사고를 같이 막는다 —

    ① **꼴을 틀리게 적는 것.** v1.68까지 무엇을 받든 `image/png`라고 적어
       넣었다. 네이버 로고가 전부 PNG라 여태 안 걸렸는데, 리그 로고를
       모으면서 SVG가 넷 들어왔다(MLB·KBL·V리그 남·여). 거짓 꼬리표는
       브라우저가 봐주는 동안만 안전하다 — 봐주지 않으면 **깨진 그림**이다.

    ② **그림이 아닌 것을 그림이라 하는 것.** 요즘 사이트는 없는 주소에도
       404 대신 **200으로 안내 페이지**를 준다(KOVO가 그렇다 — 실측).
       그걸 그대로 박으면 카드에 깨진 그림이 나간다. 글자 라벨로 물러나는
       것이 **언제나 낫다.**
    """
    b = blob[:512]
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b.startswith(b"GIF8"):
        return "image/gif"
    if b.startswith(b"RIFF") and b[8:12] == b"WEBP":
        return "image/webp"
    if b.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    # SVG는 글자라 지문이 없다 — 앞머리에 `<svg`가 나오는지로 가른다.
    # (`<?xml …?>`나 주석이 먼저 오는 파일이 있어 앞 512바이트를 훑는다.)
    head = b.lstrip()[:512].lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in b.lower()):
        return "image/svg+xml"
    return None


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
        # **받아 놓기 전에 그림인지 본다** — 안내 페이지를 캐시에 넣으면
        # 그 실행 내내 깨진 그림이 나간다.
        if _sniff(blob) is None:
            _failed.add(url)
            return None
        try:
            fn.write_bytes(blob)
        except OSError:
            pass                                          # 캐시 실패는 무시한다
    mime = _sniff(blob)
    if mime is None:                                       # 캐시 파일이 상한 경우
        _failed.add(url)
        return None
    uri = f"data:{mime};base64," + base64.b64encode(blob).decode()
    _data_cache[url] = uri
    return uri


# ── 리그 로고 (v1.69) ────────────────────────────────────────
#
# 대표님 지시(2026-09-20): *"각 리그로고를 수집해서 함께 사용하자."*
#
# 소스 경로: `sports-phinf.pstatic.net/league/<상위분류>/default/<분류>.png`
# 실측 2026-09-20 — **15개 중 8개만 있다.**
#     ✅ KBO · EPL · 라리가 · 세리에A · 분데스 · 리그1 · 챔스 · 유로파
#     ✗  MLB · NPB · K리그 · KBL · V리그 남녀 · MLS  (경로 변형 넷을 더 재봤지만 없다)
#
# **없는 리그에 아무 로고나 붙이지 않는다.** 엉뚱한 리그 로고가 붙으면
# 손님이 사실을 잘못 안다 — 없으면 지금처럼 글자 라벨만 나간다.
# **소스가 둘이다.** 네이버가 가진 것은 네이버에서(팀 엠블럼과 같은 자리),
# 없는 것은 그 리그 공식 자산에서 가져온다. 실측 2026-09-20 — 15개 중 14개.
_NV = "https://sports-phinf.pstatic.net/league/{}/default/{}.png"
LEAGUE_LOGO_URLS: dict[str, str] = {
    # ── 네이버 (팀 엠블럼과 같은 자리) ──
    "KBO":        _NV.format("kbaseball", "kbo"),
    "EPL":        _NV.format("wfootball", "epl"),
    "LALIGA":     _NV.format("wfootball", "primera"),
    "SERIEA":     _NV.format("wfootball", "seria"),
    "BUNDESLIGA": _NV.format("wfootball", "bundesliga"),
    "LIGUE1":     _NV.format("wfootball", "ligue1"),
    "UCL":        _NV.format("wfootball", "champs"),
    "UEL":        _NV.format("wfootball", "europa"),
    # ── 각 리그 공식 자산 (네이버에 없다) ──
    # MLB는 밝은 바탕용을 쓴다 — 카드가 밝은 톤이다(v1.69).
    "MLB":        "https://www.mlbstatic.com/team-logos/league-on-light/1.svg",
    "NPB":        "https://npb.jp/img/webclip.png",
    "KL1":        "https://www.kleague.com/assets/images/logo/logo.png",
    # 즐겨찾기 아이콘(`favicon.svg`)은 16px용이라 38px에서 뭉갠다 —
    # 머리말 로고는 **머리말용 워드마크**를 쓴다(실렌더로 확인, v1.69).
    "KBL":        "https://www.kbl.or.kr/assets/img/logo/logo-header.svg",
    "MLS":        "https://images.mlssoccer.com/image/upload/assets/logos/"
                  "apple-touch-icon.png",
    # ── V리그 남·여 (v1.69, 2026-09-20) ──────────────────────────
    # 대표님: *"없는리그는 있을 수 없어 · 수집하도록해."*
    #
    # KOVO 공식 사이트는 자바스크립트로 그려서 문서에 이미지 주소가 없다
    # (curl로는 3KB짜리 껍데기만 온다 — 그래서 v1.68까지 '못 찾음'이었다).
    # **브라우저로 실제로 열어** 주소를 뽑았다. 두 리그가 **같은 마크**를
    # 쓴다 — KOVO 하나가 남·여를 함께 주관한다(K리그와 같은 구조다).
    #
    # ⚠️ 주소 끝의 `BEV74V70`은 **빌드 지문**이라 KOVO가 사이트를 새로
    # 올리면 바뀐다. 그러면 이 주소는 404가 되고 카드는 글자 라벨로
    # 조용히 돌아간다 — 사고는 아니지만 **아무도 모른 채 로고가 사라진다.**
    # 그래서 아래 `_kovo_logo()`가 실패했을 때 **지금 지문을 다시 찾아온다.**
    "VLEAGUE_M":  "https://kovo.co.kr/assets/logo-kovo-BEV74V70.svg",
    "VLEAGUE_W":  "https://kovo.co.kr/assets/logo-kovo-BEV74V70.svg",
}

# ── V리그 팀 엠블럼 (v1.69) ──────────────────────────────────
#
# 배구만 소스가 다르다. 야구·축구는 네이버가 일정 응답에 엠블럼 주소를
# **경기마다** 실어 주는데, 배구는 그런 자리가 없다. 그래서 KOVO 공식
# 엠블럼을 쓴다 — 지문 없는 고정 주소라 썩지 않는다.
#
# ★ **이 표는 외워서 쓴 것이 아니다.** KOVO 화면을 브라우저로 열어
#   `그 그림이 실제로 어느 팀 이름 옆에 붙어 있는지`를 읽어 만들었다
#   (2026-09-20 실측). 닉네임을 기억으로 이으면 **엉뚱한 팀 로고**가
#   나가고 아무도 못 알아챈다 — 그게 로고를 안 붙이는 것보다 나쁘다.
#
#   jumbos      ← 인천 대한항공 점보스          skywalkers ← 천안 현대캐피탈
#   wooriwon    ← 서울 우리카드 우리WON         stars      ← 의정부 KB손해보험
#   vixtorm     ← 수원 한국전력 VIXTORM         okman      ← 부산 OK저축은행
#   bluefangs   ← 대전 삼성화재 블루팡스        kixx       ← GS칼텍스 서울Kixx
#   hipass      ← 김천 한국도로공사 하이패스    hillstate  ← 수원 현대건설
#   pinkspiders ← 인천 흥국생명                 altos      ← 화성 IBK기업은행
#   redsparks   ← 대전 정관장                   soopers    ← 전남광주 SOOP
#
# ⚠️ `PEPPER`(페퍼저축은행)는 **일부러 뺐다.** 지금 KOVO 명단에 없다 —
#    그 자리를 SOOP가 쓴다. 같은 연고를 이어받았다고 옛 이름에 새 로고를
#    붙이면 **없는 팀을 있는 것처럼** 만든다. 옛 경기는 글자로 나간다.
_KOVO_EMBLEM = "https://cdn.kovo.co.kr/emblems/{}.svg"
_VOLLEY_EMBLEM: dict[str, dict[str, str]] = {
    "VLEAGUE_M": {
        "KAL": "jumbos", "HDC": "skywalkers", "SFI": "bluefangs",
        "WOORI": "wooriwon", "OK": "okman", "KEPCO": "vixtorm",
        "KB": "stars",
    },
    "VLEAGUE_W": {
        "HK": "pinkspiders", "HDE": "hillstate", "GS": "kixx",
        "KEC": "hipass", "IBK": "altos", "KGC": "redsparks",
        "SOOP": "soopers",
    },
}

_KOVO_HOME = "https://www.kovo.co.kr/"
_JS_MAX_BYTES = 12_000_000        # 자바스크립트 묶음은 그림이 아니라 크다
_kovo_fixed: dict = {}


def _kovo_logo() -> str | None:
    """빌드 지문이 바뀌었을 때 **지금 주소**를 다시 찾는다. 못 찾으면 None.

    껍데기 HTML → 자바스크립트 묶음 → 그 안의 `assets/logo-kovo-*.svg`.
    세 걸음이라 깨질 데가 셋이지만, **평소에는 한 번도 안 돈다** —
    위 표의 주소가 살아 있는 동안은 이 함수가 불리지 않는다.
    """
    if "u" in _kovo_fixed:
        return _kovo_fixed["u"]
    _kovo_fixed["u"] = None
    shell = _get(_KOVO_HOME)
    if not shell:
        return None
    m = re.search(rb'src="(/assets/index-[A-Za-z0-9_.-]+\.js)"', shell)
    if not m:
        return None
    js = _get("https://www.kovo.co.kr" + m.group(1).decode(), limit=_JS_MAX_BYTES)
    if not js:
        return None
    m2 = re.search(rb'assets/logo-kovo-[A-Za-z0-9_.-]+\.svg', js)
    if m2:
        _kovo_fixed["u"] = "https://kovo.co.kr/" + m2.group(0).decode()
    return _kovo_fixed["u"]


def league_logo(league) -> str | None:
    """그 리그 엠블럼의 data URI. 없으면 None (= 글자 라벨만 나간다).

    **없는 리그에 아무 로고나 붙이지 않는다.** 엉뚱한 리그 로고가 붙으면
    손님이 사실을 잘못 안다 — 오류도 안 나고 아무도 못 알아챈다.
    """
    if league is None or not LOGOS_ENABLED:
        return None
    v = getattr(league, "value", str(league))
    u = LEAGUE_LOGO_URLS.get(v)
    if not u:
        return None
    got = data_uri(u)
    # KOVO만 주소에 빌드 지문이 박혀 있다 — 404가 되면 지금 지문을 찾아온다.
    if got is None and v.startswith("VLEAGUE"):
        fresh = _kovo_logo()
        if fresh and fresh != u:
            LEAGUE_LOGO_URLS[v] = fresh
            got = data_uri(fresh)
    return got


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
