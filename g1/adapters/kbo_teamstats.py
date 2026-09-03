"""KBO 팀 단위 기록 수집 어댑터 — 분석 카드 ②'팀 컨디션' 블록용 (신설).

**왜 이 소스인가.**
순위·승률은 이미 `kbo_records.py`(RecordBook)가 준다. 카드 ②블록이 더 필요로 하는 것은
"이 팀이 요즘 얼마나 잘 치고 얼마나 잘 막나" — 팀타율·팀홈런·득점·타점·팀ERA·WHIP 같은
**팀 단위 누적 기록**이다. KBO 공식 사이트의 팀기록 페이지가 이것을 통째로 준다.

  · 팀 타격 Basic1  Record/Team/Hitter/Basic1.aspx    AVG G PA AB R H 2B 3B HR TB RBI SAC SF
  · 팀 타격 Basic2  Record/Team/Hitter/Basic2.aspx    AVG BB IBB HBP SO GDP SLG OBP OPS MH RISP PH-BA
  · 팀 투구 Basic1  Record/Team/Pitcher/Basic1.aspx   ERA G W L SV HLD WPCT IP H HR BB HBP SO R ER WHIP
  · 팀 투구 Basic2  Record/Team/Pitcher/Basic2.aspx   ERA CG SHO QS BSV TBF NP AVG 2B 3B SAC SF IBB WP BK
  · 팀 수비        Record/Team/Defense/Basic.aspx    G E PKO PO A DP FPCT PB SB CS CS%
  · 팀 주루        Record/Team/Runner/Basic.aspx     G SBA SB CS SB% OOB PKO

**무인증 정적 HTML이다 (2026-09-03 실측).** 쿠키도 세션도 로그인도 필요 없다.
GET 한 번에 `<tbody>` 안에 10팀이 그대로 들어 있다. 실제로 받은 값:
    타격 Basic1 → 1위 KT 0.280(115경기·639득점·89홈런) · 2위 삼성 0.280(110홈런)
    투구 Basic1 → 1위 두산 ERA 3.63 / WHIP 1.33 · 2위 삼성 ERA 4.14 / WHIP 1.37
경기 어댑터(kbo.py)가 쓰는 ASMX(`ws/Schedule.asmx`)와 달리 세션 쿠키를 요구하지 않으므로
`_ensure_session` 같은 예열 단계가 없다. 그래도 HTTP는 `_http.fetch`를 그대로 쓴다 —
KBO는 `Connection reset by peer`를 실제로 낸 소스라 재시도·백오프가 필요하다.

**Basic1 둘만 필수인 이유.** 카드가 반드시 쓰는 6지표(팀타율·홈런·득점·타점·ERA·WHIP)는
전부 Basic1에 있다. Basic2·수비·주루는 OPS·QS·도루·수비율 같은 '있으면 좋은' 지표라,
그 페이지 하나가 바뀌었다고 팀 컨디션 블록 전체를 죽이는 것은 과하다.
**대신 조용히 넘기지 않는다** — 빠지면 `NoticeMixin`으로 보고한다
(빠진 줄 모르고 카드가 비는 것이 최악이다).

**0건은 항상 의심한다.** 10팀이 다 안 나오면 게이트로 막는다. KBO는 10구단 고정이고
이 표는 규정타석 같은 필터가 없으므로, 9팀이 나왔다면 그것은 '한 팀이 쉰 날'이 아니라
**응답 구조가 바뀐 것**이다. 실제로 `Record/Player/.../Basic1.aspx`(선수 표)는 규정 미달자를
빼기 때문에 같은 가정이 성립하지 않는다 — 그래서 선수 표가 아니라 팀 표를 쓴다.

**시즌 선택은 실제로 먹는다 (2026-09-03 실측).** `kbo_records.py`의 `TeamRank.aspx`는
연도 포스트백을 쳐도 표가 안 바뀌었지만, 이 페이지는 `ddlSeason` 포스트백이 진짜로 듣는다:
    기본(2026) → 1위 KT 115경기 0.280
    ddlSeason=2025 → 1위 LG 144경기 0.278 (완주한 시즌 수치)
그래서 여기서는 `season` 인자를 **정말로 선택값으로** 받는다. 다만 받은 페이지가 스스로
말하는 시즌(`ddlSeason`의 selected)과 대조해 다르면 막는다 — 거짓 라벨은 없느니만 못하다.
`ddlSeries`(0=정규시즌)도 매번 확인한다. 3월에 기본값이 시범경기로 바뀌어 있으면
시범경기 팀타율이 정규시즌 자리에 실린다.

**캐시를 두는 이유.** 팀 누적 기록은 그날 경기가 끝나야 움직인다. 틱은 하루에 수십 번
도는데 그때마다 4페이지를 긁을 이유가 없다. 신선 창(`CACHE_TTL_SECONDS`) 안이면 파일
캐시를 그대로 쓰고, 수집이 실패하면 상한(`CACHE_MAX_AGE_SECONDS`)까지 묵은 캐시로 버티되
**그때는 나이를 달아 보고한다**(`note_cache_age`). 상한을 넘긴 캐시는 쓰지 않는다 —
이틀 전 팀타율을 오늘 것처럼 카드에 싣느니 블록을 비우는 편이 낫다.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))   # contract.py
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))   # adapters 패키지
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))       # _http / _notices

from _http import fetch as _fetch, make_opener as _make_opener
from _notices import NoticeMixin
from contract import GateError, League, TEAM_NAMES, UnknownStatus

# 팀명 정규화 표는 **복사하지 않고 읽어 온다.** 표가 두 벌이 되면 한쪽만 고쳐지고
# 그날부터 두 어댑터가 다른 팀 코드를 낸다(SSG의 코드가 'SK'인 것 같은 함정이 실재한다).
from adapters.kbo import TEAM_CODE

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_BASE = "https://www.koreabaseball.com"

# ASP.NET 컨트롤 이름 접두사. 마스터 페이지 중첩 깊이에서 나온다
# (`kbo_records.py`와 같은 깊이지만 컨트롤 이름이 `$ddlSeason$ddlSeason`으로 한 겹 더 있다).
_CTL = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$"
_DDL_SEASON = _CTL + "ddlSeason$ddlSeason"
_DDL_SERIES = _CTL + "ddlSeries$ddlSeries"
_SERIES_REGULAR = "0"          # 0=정규시즌 · 1=시범경기 · 3/4/5/7=포스트시즌

# (URL, 키 접두사, 필수인가)
#   접두사로 타격/투구/수비/주루를 가른다. 안 가르면 AVG·HR·H·R·2B·SAC·SF·IBB·SB·CS·PKO가
#   정면충돌한다 — 특히 투구 Basic2의 AVG는 '피안타율'이라 타격 AVG로 덮이면 숫자가 조용히
#   뒤집히고, 수비 SB(허용 도루)와 주루 SB(성공 도루)는 뜻이 정반대다.
#   Basic1 둘만 필수다. 나머지는 '있으면 좋은' 지표라 그 페이지 하나 때문에
#   팀 컨디션 블록 전체를 죽이지 않는다(대신 빠지면 반드시 알린다).
_PAGES: tuple[tuple[str, str, bool], ...] = (
    (_BASE + "/Record/Team/Hitter/Basic1.aspx",  "bat", True),
    (_BASE + "/Record/Team/Pitcher/Basic1.aspx", "pit", True),
    (_BASE + "/Record/Team/Hitter/Basic2.aspx",  "bat", False),
    (_BASE + "/Record/Team/Pitcher/Basic2.aspx", "pit", False),
    (_BASE + "/Record/Team/Defense/Basic.aspx",  "def", False),
    (_BASE + "/Record/Team/Runner/Basic.aspx",   "run", False),
)

# 값에 점이 없어도 반드시 비율(float)로 읽을 열.
# 나머지는 문자열 모양으로 판별한다('.'이 있으면 float, 순수 숫자면 int).
# 모양만으로 판별하면 SB%가 '100'인 날 int 100이 되어 타입이 그날만 달라진다.
_RATIO_HEADERS = {"AVG", "OBP", "SLG", "OPS", "ERA", "WHIP", "WPCT",
                  "FPCT", "RISP", "PH-BA", "SB%", "CS%"}

# 카드 ②블록이 실제로 쓰는 지표. 이 중 최소 `MIN_CARD_METRICS`개는 10팀 전부에
# 있어야 한다 — 없으면 블록을 반쯤 비운 카드를 내보내느니 게이트로 막는다.
CARD_METRICS: tuple[str, ...] = (
    "bat_avg", "bat_hr", "bat_r", "bat_rbi", "pit_era", "pit_whip",
)
MIN_CARD_METRICS = 5

# 우리가 계산해 붙이는 리그 내 순위(`*_rank`). **소스의 '순위' 열이 아니다** —
# 그 열은 그 페이지의 정렬 기준(hfOrderByCol) 하나만 반영하므로 열 하나에만 뜻이 있고,
# 정렬 기본값이 바뀌면 뜻이 통째로 변한다. 값에서 직접 매기는 편이 안전하다.
_RANKED: tuple[str, ...] = CARD_METRICS + ("bat_ops", "bat_obp", "bat_slg",
                                           "pit_qs", "run_sb", "def_fpct")
_RANK_LOWER_IS_BETTER = {"pit_era", "pit_whip"}

CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"
CACHE_TTL_SECONDS = 3 * 3600         # 신선 창. 이 안이면 다시 안 긁는다
CACHE_MAX_AGE_SECONDS = 30 * 3600    # 수집 실패 시 버틸 상한. 넘으면 안 쓴다

_HIDDEN = re.compile(r'<input[^>]*type="hidden"[^>]*>', re.I)
_FRACTION = re.compile(r"^(\d+)\s+(\d+)/(\d+)$")     # 이닝 '1071 2/3'


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", html)).strip()


def _cells(tr_html: str) -> list[str]:
    return [_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, re.S)]


def _hidden_fields(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _HIDDEN.finditer(html):
        tag = m.group(0)
        n = re.search(r'name="([^"]*)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n:
            out[n.group(1)] = v.group(1) if v else ""
    return out


def _selected(html: str, name: str) -> str:
    """드롭다운에서 지금 선택된 값. 페이지가 스스로 말하는 시즌/시리즈다."""
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name),
                  html, re.S)
    if not m:
        return ""
    body = m.group(1)
    o = (re.search(r'<option[^>]*selected="selected"[^>]*value="([^"]*)"', body)
         or re.search(r'<option[^>]*value="([^"]*)"[^>]*selected', body))
    return o.group(1) if o else ""


def _options(html: str, name: str) -> list[str]:
    """드롭다운이 실제로 고를 수 있는 값들 (2026-09-03 실측: 시즌은 2001~2026)."""
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name),
                  html, re.S)
    return re.findall(r'<option[^>]*value="([^"]*)"', m.group(1)) if m else []


def metric_key(prefix: str, header: str) -> str:
    """'PH-BA' → 'bat_ph_ba' · 'CS%' → 'def_cs_pct' · '2B' → 'bat_2b'."""
    h = header.strip().lower().replace("%", "_pct")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    return f"{prefix}_{h}" if h else ""


def _parse_number(raw: str, header: str) -> "float | int | None":
    """숫자 하나. 못 읽으면 None — 지어내지 않고 그 지표만 뺀다.

    타입은 값의 모양으로 정한다. 비율 열 목록(`_RATIO_HEADERS`)은 모양과 무관하게 float다.
    이닝만 예외적으로 '1071 2/3' 꼴이라 분수를 풀어 float로 만든다
    (문자열로 두면 카드에서 비교도 계산도 못 한다).
    """
    s = (raw or "").strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    m = _FRACTION.match(s)
    if m:
        whole, num, den = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return whole + (num / den if den else 0.0)
    try:
        if "." in s or header in _RATIO_HEADERS:
            return float(s)
        return int(s)
    except ValueError:
        return None


class KboTeamStatsAdapter(NoticeMixin):
    """KBO 10구단의 팀 단위 누적 기록을 `{팀코드: {지표: 값}}`으로 준다.

    팀코드는 `contract.TEAM_NAMES[League.KBO]`의 키와 정확히 같다(생성 시 대조한다).
    """

    league = League.KBO

    def __init__(self, *, cache_dir: "pathlib.Path | None" = None,
                 cache_ttl: float = CACHE_TTL_SECONDS,
                 cache_max_age: float = CACHE_MAX_AGE_SECONDS) -> None:
        # **쿠키를 받는다 (2026-09-03 실측으로 바로잡음).** 표를 그냥 읽기만 할 때는
        # 쿠키가 필요 없지만, 시즌을 바꾸는 `__doPostBack`은 세션 없이 던지면
        # **HTTP 200에 '에러 | KBO홈페이지' 페이지**가 돌아온다(3.4KB). ASP.NET이
        # ViewState를 세션에 묶어 검증하기 때문이다. 200이라 `_http.fetch`도 못 걸러낸다.
        self._op = _make_opener(cookies=True)
        self._op.addheaders = [("User-Agent", _UA)]
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_ttl = float(cache_ttl)
        self.cache_max_age = float(cache_max_age)

        # 이번 수집이 실제로 어느 시즌 표를 읽었는지. fetch가 채운다.
        self.season: str = ""
        self.source_urls: list[str] = []
        self.collected_utc: "datetime | None" = None

        # 팀 코드 표가 계약과 어긋나면 여기서 끝낸다. 카드에 붙은 뒤 알면 늦다.
        expected = set(TEAM_NAMES[League.KBO])
        got = set(TEAM_CODE.values())
        if got != expected:
            raise GateError(
                f"KBO 팀기록: 팀 코드 표가 계약과 다릅니다 "
                f"(어댑터에만 있음 {sorted(got - expected)} · "
                f"계약에만 있음 {sorted(expected - got)})")

    # ── HTTP ──────────────────────────────────────────────────

    def _get(self, url: str) -> str:
        return _fetch(self._op, url,
                      label=f"KBO 팀기록 {url.rsplit('/', 2)[-2]}/{url.rsplit('/', 1)[-1]}"
                      ).decode("utf-8", "replace")

    def _post(self, url: str, html: str, fields: dict[str, str]) -> str:
        form = _hidden_fields(html)
        form.update(fields)
        req = urllib.request.Request(
            url, data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": url, "User-Agent": _UA})
        return _fetch(self._op, req, label="KBO 팀기록 포스트백").decode("utf-8", "replace")

    def _page_html(self, url: str, season: "str | None") -> tuple[str, str]:
        """(HTML, 페이지가 말하는 시즌). 시즌·시리즈를 확인하고 어긋나면 바로잡는다."""
        html = self._get(url)
        page_season = _selected(html, _DDL_SEASON)
        page_series = _selected(html, _DDL_SERIES)
        want_season = str(season).strip() if season is not None else page_season

        if not re.fullmatch(r"\d{4}", page_season):
            raise GateError(
                f"KBO 팀기록({url}): 페이지가 시즌을 안 알려줍니다 "
                f"(ddlSeason={page_season!r}) — 어느 해 기록인지 모른 채 라벨을 붙이지 않습니다")

        if page_season != want_season or page_series != _SERIES_REGULAR:
            # 드롭다운에 없는 연도로 포스트백을 치면 소스는 200에 에러 페이지를 준다.
            # 아래 '드롭다운 없음' 검사에도 걸리지만, 그러면 "구조가 바뀐 것 같다"는
            # 잘못된 진단이 남는다. 여기서 먼저 걸러 이유를 정확히 적는다.
            years = _options(html, _DDL_SEASON)
            if years and want_season not in years:
                raise GateError(
                    f"KBO 팀기록({url}): {want_season} 시즌은 소스에 없습니다 "
                    f"(선택 가능 {years[0]}~{years[-1]})")
            if page_series != _SERIES_REGULAR:
                # 3월에 기본값이 시범경기로 서 있으면 시범경기 팀타율이 정규시즌 자리에 실린다.
                self.note_text("표 구분 보정",
                               f"기본값이 ddlSeries={page_series!r}였습니다 — 정규시즌으로 다시 요청")
            html = self._post(url, html, {
                "__EVENTTARGET": _DDL_SEASON, "__EVENTARGUMENT": "",
                _DDL_SEASON: want_season, _DDL_SERIES: _SERIES_REGULAR})
            page_season = _selected(html, _DDL_SEASON)
            page_series = _selected(html, _DDL_SERIES)
            if not (page_season and page_series):
                # 포스트백이 실패하면 소스는 **HTTP 200에 에러 페이지**를 준다.
                # 상태 코드로는 안 걸리므로 '드롭다운이 사라졌다'로 잡는다.
                raise GateError(
                    f"KBO 팀기록({url}): 포스트백 응답이 기록 페이지가 아닙니다 "
                    f"(드롭다운 없음, {len(html)}바이트) — 소스가 에러 페이지를 200으로 줍니다")

        if page_series != _SERIES_REGULAR:
            raise GateError(
                f"KBO 팀기록({url}): 정규시즌 표를 못 받았습니다 (ddlSeries={page_series!r}) — "
                f"시범경기 기록을 정규시즌 기록으로 내보내지 않습니다")
        if page_season != want_season:
            raise GateError(
                f"KBO 팀기록({url}): {want_season} 시즌을 요청했지만 소스가 준 것은 "
                f"{page_season} 시즌입니다 — 거짓 시즌 라벨을 붙이지 않습니다")
        return html, page_season

    # ── 파싱 ──────────────────────────────────────────────────

    def _parse_table(self, html: str, prefix: str, label: str,
                     out: dict[str, dict]) -> int:
        """표 하나를 읽어 `out`에 합친다. 반환값은 읽어낸 팀 수."""
        tables = re.findall(r"<table[^>]*>.*?</table>", html, re.S)
        if not tables:
            raise GateError(f"KBO 팀기록({label}): 표가 없습니다 (페이지 구조 변경)")
        table = tables[0]

        head = [_text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]
        if len(head) < 3 or head[1] != "팀명":
            raise GateError(
                f"KBO 팀기록({label}): 머리글 구조가 다릅니다 {head[:4]} "
                f"(기대: ['순위','팀명', …] — 페이지 구조 변경)")

        body = re.search(r"<tbody[^>]*>(.*?)</tbody>", table, re.S)
        if not body:
            raise GateError(f"KBO 팀기록({label}): tbody가 없습니다 (페이지 구조 변경)")

        seen = 0
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
            cells = _cells(tr)
            if not cells:
                continue
            if len(cells) != len(head):
                raise GateError(
                    f"KBO 팀기록({label}): 열 {len(cells)}개 (머리글 {len(head)}개) {cells[:3]}")
            name = cells[1]
            if name not in TEAM_CODE:
                # 조용히 넘기지 않는다. 넘기면 아래 10팀 게이트가 이유 없이 터진다.
                self.note(f"{label}: 미등록 팀명", name)
                continue
            code = TEAM_CODE[name]
            row = out.setdefault(code, {})
            seen += 1

            for header, raw in zip(head[2:], cells[2:]):     # 순위·팀명 열은 버린다
                key = metric_key(prefix, header)
                if not key:
                    self.note(f"{label}: 열 이름 해석 불가", header)
                    continue
                val = _parse_number(raw, header)
                if val is None:
                    # 그 지표만 빼고 보고한다. 0으로 채우면 카드가 '0홈런'을 사실로 쓴다.
                    self.note(f"{label}: 값 해석 불가", f"{name} {header}={raw!r}")
                    continue
                prev = row.get(key)
                if prev is not None and prev != val:
                    # Basic1·Basic2에 같은 열이 겹친다(AVG·ERA). 값이 다르면 둘 중 하나가
                    # 다른 시즌/시리즈 표라는 뜻이라, 덮어쓰지 않고 알린다.
                    self.note(f"{label}: 같은 지표 값 충돌", f"{name} {key} {prev}≠{val}")
                    continue
                row[key] = val
        return seen

    def _add_ranks(self, teams: dict[str, dict]) -> None:
        """리그 내 순위를 우리가 매겨 붙인다(동률은 같은 등수, 다음 등수는 건너뛴다)."""
        for key in _RANKED:
            vals = [(c, r[key]) for c, r in teams.items() if isinstance(r.get(key), (int, float))]
            if len(vals) != len(teams):
                continue                      # 한 팀이라도 없으면 순위를 매기지 않는다
            asc = key in _RANK_LOWER_IS_BETTER
            vals.sort(key=lambda cv: cv[1], reverse=not asc)
            rank = 0
            prev_val = None
            for i, (code, v) in enumerate(vals, 1):
                if v != prev_val:
                    rank, prev_val = i, v
                teams[code][f"{key}_rank"] = rank

    # ── 캐시 ──────────────────────────────────────────────────

    def _cache_file(self, season: str) -> pathlib.Path:
        return self.cache_dir / f"kbo_teamstats_{season or 'current'}.json"

    def _cache_read(self, season: str, max_age: float) -> "tuple[dict, float] | None":
        path = self._cache_file(season)
        try:
            age = time.time() - path.stat().st_mtime
            if age > max_age:
                return None
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        teams = blob.get("teams")
        if not isinstance(teams, dict) or set(teams) != set(TEAM_NAMES[League.KBO]):
            return None                       # 반쪽 캐시는 없느니만 못하다
        # 캐시로 답할 때도 '어느 시즌·언제 걷은 것'인지가 같이 살아나야 한다.
        # 안 그러면 호출자가 `self.season`이 빈 문자열인 것을 보고 라벨을 못 붙인다.
        self.season = str(blob.get("season") or season)
        self.source_urls = list(blob.get("source_urls") or [])
        stamp = blob.get("collected_utc")
        try:
            self.collected_utc = datetime.fromisoformat(stamp) if stamp else None
        except (TypeError, ValueError):
            self.collected_utc = None
        return teams, age

    def _cache_write(self, season: str, teams: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # 파일 이름은 요청 키(season 인자), 안에 적는 시즌은 **페이지가 말한 시즌**이다.
            # 인자를 안 주면 요청 키가 빈 문자열('current')이라, 여기에 그대로 적으면
            # 캐시로 답한 날 `self.season`이 비어 라벨을 못 붙인다.
            self._cache_file(season).write_text(json.dumps(
                {"season": self.season or season, "source_urls": self.source_urls,
                 "collected_utc": (self.collected_utc or datetime.now(timezone.utc)).isoformat(),
                 "teams": teams}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass                              # 캐시 실패는 수집 실패가 아니다

    # ── 게이트 ────────────────────────────────────────────────

    def _gate(self, teams: dict[str, dict]) -> None:
        expected = set(TEAM_NAMES[League.KBO])
        got = set(teams)
        if got != expected:
            raise GateError(
                f"KBO 팀기록: 10팀이 아니라 {len(got)}팀입니다 "
                f"(빠짐 {sorted(expected - got)} · 처음 봄 {sorted(got - expected)}) — "
                f"KBO는 10구단 고정이므로 이것은 응답 구조 변경 신호입니다")
        for code, row in sorted(teams.items()):
            have = [m for m in CARD_METRICS if isinstance(row.get(m), (int, float))]
            if len(have) < MIN_CARD_METRICS:
                raise GateError(
                    f"KBO 팀기록: {code}의 카드 지표가 {len(have)}개뿐입니다 "
                    f"(최소 {MIN_CARD_METRICS}개 필요, 빠진 것 "
                    f"{[m for m in CARD_METRICS if m not in have]}) — "
                    f"반쯤 빈 팀 컨디션 블록을 내보내지 않습니다")

    # ── 공개 API ──────────────────────────────────────────────

    def fetch(self, season: "int | str | None" = None, *,
              use_cache: bool = True) -> dict[str, dict]:
        """`{팀코드: {지표: 값}}`. 팀코드는 `contract.TEAM_NAMES[League.KBO]`의 키.

        `season`을 주면 그 시즌 표를 요청하고, 페이지가 말하는 시즌과 대조해 다르면 막는다.
        안 주면 페이지의 현재 시즌을 그대로 쓴다(그리고 그 값이 `self.season`에 남는다).
        """
        self.reset_notices()
        want = str(season).strip() if season is not None else ""

        if use_cache:
            hit = self._cache_read(want, self.cache_ttl)
            if hit is not None:
                # 신선 창 안이다. 정상 동작이므로 '묵은 캐시' 경보를 울리지 않는다.
                return hit[0]

        teams: dict[str, dict] = {}
        self.source_urls = []
        # 4페이지가 **같은 시즌**을 봐야 한다. 인자를 안 받았으면 첫 페이지가 말한 시즌을
        # 그대로 나머지 페이지에 못 박는다 — 안 그러면 12월 31일 밤에 첫 두 페이지는
        # 올해, 나머지 두 페이지는 내년 표를 읽고 그 둘이 한 팀 안에서 섞인다.
        pin = want
        try:
            for url, prefix, required in _PAGES:
                label = f"{url.rsplit('/', 2)[-2]} {url.rsplit('/', 1)[-1].split('.')[0]}"
                try:
                    html, pin = self._page_html(url, pin or None)
                    n = self._parse_table(html, prefix, label, teams)
                except (GateError, UnknownStatus, OSError) as e:
                    if required:
                        raise
                    # 보조 페이지는 빠져도 카드를 죽이지 않는다. 대신 반드시 알린다.
                    self.note_text(f"{label} 수집 실패(보조 지표 제외)", str(e))
                    continue
                if n != len(TEAM_NAMES[League.KBO]):
                    msg = (f"{label}: 10팀 중 {n}팀만 읽었습니다 "
                           f"(0건·결번은 항상 구조 변경 신호)")
                    if required:
                        raise GateError("KBO 팀기록 " + msg)
                    self.note_text(f"{label} 불완전(보조 지표 제외)", msg)
                self.source_urls.append(url)

            self.season = pin
            self.collected_utc = datetime.now(timezone.utc)
            self._add_ranks(teams)
            self._gate(teams)
        except Exception:
            # 수집이 깨졌다. 상한 안의 캐시가 있으면 나이를 달고 버틴다.
            if use_cache:
                stale = self._cache_read(want, self.cache_max_age)
                if stale is not None:
                    self.note_cache_age(stale[1])
                    return stale[0]
            raise

        if use_cache:
            self._cache_write(want, teams)
        return teams


# ── 자체 시험 ────────────────────────────────────────────────────
if __name__ == "__main__":                                     # pragma: no cover
    a = KboTeamStatsAdapter()
    teams = a.fetch(use_cache=False)                           # 실측이므로 캐시를 끈다

    print(f"시즌 {a.season} · 소스 {len(a.source_urls)}페이지 · 팀 {len(teams)}개")

    # 1) 팀 코드가 계약과 정확히 일치하는가
    expected = set(TEAM_NAMES[League.KBO])
    assert set(teams) == expected, sorted(set(teams) ^ expected)
    print("팀코드 대조: contract.TEAM_NAMES[League.KBO]와 완전 일치 ✓")

    # 2) 값이 상식적인가 (타율 0.2~0.3 · ERA 2~6)
    for code in sorted(teams, key=lambda c: teams[c]["pit_era"]):
        r = teams[code]
        assert 0.20 <= r["bat_avg"] <= 0.32, (code, r["bat_avg"])
        assert 2.0 <= r["pit_era"] <= 6.5, (code, r["pit_era"])
        assert 0.9 <= r["pit_whip"] <= 1.8, (code, r["pit_whip"])
        assert isinstance(r["bat_hr"], int) and 0 < r["bat_hr"] < 400
        print(f"  {code:>2} {TEAM_NAMES[League.KBO][code]:>3} "
              f"AVG {r['bat_avg']:.3f} HR {r['bat_hr']:>3} R {r['bat_r']:>3} "
              f"RBI {r['bat_rbi']:>3} OPS {r.get('bat_ops', '-')} "
              f"ERA {r['pit_era']:.2f} WHIP {r['pit_whip']:.2f} "
              f"QS {r.get('pit_qs', '-'):>2} SB {r.get('run_sb', '-'):>3} "
              f"E {r.get('def_e', '-'):>3}")
    print("값 상식 검사 ✓")

    # 3) 지표 목록
    keys = sorted({k for r in teams.values() for k in r})
    print(f"지표 {len(keys)}개: {keys}")
    missing = [k for k in keys if any(k not in r for r in teams.values())]
    print("모든 팀에 있는가:", "✓" if not missing else f"✗ 일부 팀에만: {missing}")

    # 4) 알림 계약
    print("skipped_report:", a.skipped_report() or "(버린 것 없음)")
    print("notices:", a.notices or "(없음)")
    print("cache_age_seconds:", a.cache_age_seconds)

    # 5) 캐시 왕복 — 캐시를 켠 첫 호출이 파일을 남기고, 그다음 호출은 네트워크 없이 끝나야 한다
    t0 = time.time(); warm = a.fetch(); t_net = time.time() - t0
    t0 = time.time(); again = a.fetch(); t_cache = time.time() - t0
    assert warm == teams and again == teams, "캐시가 원본과 다릅니다"
    assert a._cache_file("").exists(), "캐시 파일이 안 만들어졌습니다"
    assert t_cache < t_net / 4, f"캐시를 안 탄 것 같습니다 ({t_cache:.2f}s vs {t_net:.2f}s)"
    print(f"캐시 재사용 ✓ 수집 {t_net:.2f}s → 캐시 {t_cache * 1000:.0f}ms "
          f"(TTL {CACHE_TTL_SECONDS // 3600}시간 · 실패 시 버팀 상한 "
          f"{CACHE_MAX_AGE_SECONDS // 3600}시간)")

    # 6) 게이트가 진짜 막는가 — 9팀짜리 응답을 흉내 낸다
    b = KboTeamStatsAdapter()
    try:
        b._gate({k: v for k, v in list(teams.items())[:9]})
    except GateError as e:
        print(f"10팀 게이트 ✓ {str(e)[:70]}…")
    else:
        raise AssertionError("9팀인데 게이트가 안 막았습니다")
    try:
        b._gate({k: {"bat_avg": v["bat_avg"]} for k, v in teams.items()})
    except GateError as e:
        print(f"카드 지표 게이트 ✓ {str(e)[:70]}…")
    else:
        raise AssertionError("지표 1개인데 게이트가 안 막았습니다")
