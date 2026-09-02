"""V리그(KOVO) 수집 어댑터 — 남자부·여자부 (v1.11 신설).

공식 JSON API. `gender` 1=남 / 2=여로 리그가 갈린다.
계약에서 VLEAGUE_M / VLEAGUE_W 두 리그로 나눠 다루므로 어댑터도 성별을 받는다.

실측 (2026-08-28):
  · 남자부 3,202경기 · 여자부 3,000경기대 — 2005년부터 전부 들어 있다
  · 2026-2027 정규시즌 = seasonCode `023`, 남녀 각 126경기 (2026-10-31~2027-04-02)
  · 컵대회 = `824` (2026 여수·KOVO컵), 별도 대회로 표기
  · 미래 경기는 `hspoint`/`aspoint`가 0이고 `result`/`score`가 빈 문자열
  · 점수 단위는 **세트**(3-2 등). 세트별 점수는 hs1point~hs5point에 따로 있다

**취소·연기 상태값이 없다.** KBL과 같은 처지로 스냅샷 diff로만 감지된다.
"""
from __future__ import annotations

import json
import ssl
import sys
import pathlib
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (GateError, Game, GameMeta, League, Score, ScoreUnit, Status,
                      TeamRef, UnknownStatus)

KST = ZoneInfo("Asia/Seoul")
_API = "https://user-api.kovo.co.kr/stat/game-schedule"

# 팀 이름에 스폰서·도시·애칭이 다 붙는다("인천 대한항공 점보스").
# 구단을 특정하는 핵심어만 남긴다.
_TEAMS_M = {
    "대한항공": "KAL", "현대캐피탈": "HDC", "삼성화재": "SFI", "우리카드": "WOORI",
    "OK저축은행": "OK", "한국전력": "KEPCO", "KB손해보험": "KB",
}
_TEAMS_W = {
    "흥국생명": "HK", "현대건설": "HDE", "GS칼텍스": "GS", "한국도로공사": "KEC",
    "IBK기업은행": "IBK", "정관장": "KGC", "페퍼": "PEPPER", "SOOP": "SOOP",
}


def team_code(name: str, gender: str) -> str:
    table = _TEAMS_M if gender == "1" else _TEAMS_W
    n = (name or "").strip()
    for key, code in table.items():
        if key in n:
            return code
    return re.sub(r"\s+", "", n)[:12] or "UNK"


# 팀이 아니라 **자리표시자**가 팀 칸에 오는 경우가 있다 (v1.11i 실측).
#   컵대회 토너먼트: 'A조 1위' · 'B조 2위' · '준결승 A승'  (2026 여수·KOVO컵 3경기)
#   올스타전:       'K-스타' vs 'V-스타'                  (2026-01-25, seasonCode 022)
# 그대로 두면 `team_code`가 'A조1위' 같은 코드를 만들어 내고, 계약의
# `assert_team_names_cover`가 **V리그 수집 전체를 막는다**(KBL 올스타·EASL과 같은 함정).
# 대진이 확정되면 실제 구단명으로 바뀌므로, 확정 전 행만 건너뛰고 건수를 보고한다.
_PLACEHOLDER_TEAM = re.compile(
    r"(^|\s)([A-Z]조\s*\d+위|준결승\s*[A-Z]?승|결승\s*진출|승자|패자|"
    r"[A-Z]-스타|K-스타|V-스타)(\s|$)")


def _is_placeholder(name: str) -> bool:
    n = (name or "").strip()
    return not n or bool(_PLACEHOLDER_TEAM.search(n))


_OPENER = make_opener()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    raw = _fetch(_OPENER, req, label="KOVO 일정", timeout=45)
    return json.loads(raw.decode("utf-8", "replace"))


# ── 시즌 코드 ────────────────────────────────────────────────
#
# **tick이 `"023"`을 하드코딩해 쓰고 있었다 (v1.11i).** `season_codes()`가
# 이미 있는데도 쓰이지 않아서, 다음 시즌이 열리면 지난 시즌을 계속 발행하고
# 10월 KOVO컵은 아예 수집 대상 밖이었다.
#
# 실측 목록(2026-09-02, 남녀 동일):
#   ('023', '2026-2027 V-League',                 2026-10-31 ~ 2027-04-02)  ← 정규
#   ('824', '2026 Yeosu·KOVO Cup …',              2026-10-19 ~ 2026-10-25)  ← 컵
#   ('602', '2026 Korea Corporates Volleyball …', 2026-06-07 ~ 2026-06-16)  ← 실업연맹
#   ('022', 'JINAIR 2025-2026 V-League',          2025-10-20 ~ 2026-04-10)
#   ('823', '2025 Yeosu·NongHyup Cup …',          2025-09-13 ~ 2025-09-20)
#
# 코드의 **첫 자리가 대회 종류**다: `0xx`=V리그 정규 · `8xx`=컵대회 · `6xx`=실업연맹.
# `leagueCode`로는 못 가른다 — 컵대회와 실업연맹이 212/213/214를 똑같이 쓴다.
# 실업연맹(6xx)에는 '국군체육부대'·'부산광역시체육회'처럼 프로 구단이 아닌 팀이
# 나오므로 **어떤 경우에도 발행 대상이 아니다.**
_SEASON_KIND = {"0": "regular", "8": "cup", "6": "amateur"}

# 정규시즌이 끝난 뒤에도 며칠은 그 시즌을 붙들고 있는다 —
# 마지막 경기 다음 날 아침에 결과 카드를 만들어야 하기 때문이다(모닝은 어제를 싣는다).
SEASON_TAIL_DAYS = 7


class KovoAdapter(NoticeMixin):
    """gender: '1'=남자부(VLEAGUE_M) · '2'=여자부(VLEAGUE_W)."""

    def __init__(self, gender: str) -> None:
        if gender not in ("1", "2"):
            raise GateError(f"KOVO: gender는 '1'(남) 또는 '2'(여) ({gender!r})")
        self.gender = gender
        self.league = League.VLEAGUE_M if gender == "1" else League.VLEAGUE_W
        self._rows_cache: list[dict] | None = None

    def _rows(self) -> list[dict]:
        # 전체 목록은 한 요청에 3천 행이 넘게 온다. 같은 인스턴스가 시즌 선택과
        # 수집에 두 번 부르므로 한 번만 받아 재사용한다.
        if self._rows_cache is not None:
            return self._rows_cache
        out: list[dict] = []
        for page in range(4):                       # 안전 상한
            d = _get(f"{_API}?gender={self.gender}&size=2000&page={page}")
            payload = d.get("payload") or {}
            content = payload.get("content") or []
            out += content
            if len(content) < 2000:
                break
        if not out:
            raise GateError(f"KOVO({self.league.value}): 0건 — 0건은 항상 의심")
        self._rows_cache = out
        return out

    def season_codes(self) -> list[tuple[str, str, str]]:
        """(seasonCode, seasonName, 마지막 경기일) — 최신순."""
        rows = self._rows()
        by: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            by.setdefault((r["seasonCode"], r["seasonName"]), []).append(r["gdate"])
        return sorted(((c, n, max(ds)) for (c, n), ds in by.items()),
                      key=lambda x: x[2], reverse=True)

    def seasons(self) -> list[dict]:
        """대회 하나당 한 줄. `season_codes()`에 종류와 시작일을 더한 것."""
        rows = self._rows()
        by: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            by.setdefault((r["seasonCode"], r["seasonName"]), []).append(r["gdate"])
        out = []
        for (code, name), ds in by.items():
            out.append({"code": code, "name": name,
                        "first": min(ds), "last": max(ds),
                        "kind": _SEASON_KIND.get(str(code)[:1], "unknown")})
        return sorted(out, key=lambda s: s["last"], reverse=True)

    # ── 오늘 날짜에 맞는 시즌 고르기 ─────────────────────────
    def season_code_for(self, today=None, *, include_cup: bool = False) -> str:
        """오늘(또는 주어진 날짜)에 해당하는 시즌 코드.

        **tick이 `"023"`을 하드코딩하지 않아도 되게 하려고 만들었다.**
        규칙 — 실측 목록(위 `_SEASON_KIND` 주석)에 붙여 확인했다:
          ① 실업연맹(6xx)은 후보에서 뺀다. 프로 구단이 아닌 팀이 나온다.
          ② 컵대회(8xx)는 `include_cup=True`일 때만 후보다.
             기본을 False로 둔 이유: 컵대회 토너먼트 행의 팀 칸에
             'A조 1위'·'준결승 A승' 같은 자리표시자가 온다(2026 여수컵 3경기).
             `_parse` 쪽에서 걸러내지만, 켜는 것은 운영이 결정할 일이다.
          ③ 오늘이 대회 기간(첫 경기 ~ 마지막 경기 + 여유 7일) 안이면 그 대회.
             둘 이상 겹치면 **먼저 끝나는 쪽**(=지금 진행 중인 단기 대회)을 고른다.
          ④ 진행 중인 대회가 없으면 **다음에 시작하는 대회**를 고른다.
             비시즌에 지난 시즌을 붙들고 있으면 끝난 경기를 매일 다시 발행한다.
          ⑤ 다음 대회도 없으면 가장 최근에 끝난 대회로 떨어진다.
        """
        from datetime import date as _date
        if today is None:
            today = datetime.now(KST).date()
        elif isinstance(today, datetime):
            today = today.date()
        elif not isinstance(today, _date):
            today = datetime.strptime(str(today)[:10], "%Y-%m-%d").date()

        kinds = {"regular"} | ({"cup"} if include_cup else set())
        cands = [s for s in self.seasons() if s["kind"] in kinds]
        if not cands:
            raise GateError(
                f"KOVO({self.league.value}): 발행 대상 대회가 목록에 없습니다 "
                f"— 0건은 항상 의심 (본 대회 종류: "
                f"{sorted({s['kind'] for s in self.seasons()})})")

        def d(s: str):
            return datetime.strptime(s, "%Y-%m-%d").date()

        running = [s for s in cands
                   if d(s["first"]) <= today <= d(s["last"]) + timedelta(days=SEASON_TAIL_DAYS)]
        if running:
            return min(running, key=lambda s: s["last"])["code"]
        upcoming = [s for s in cands if d(s["first"]) > today]
        if upcoming:
            return min(upcoming, key=lambda s: s["first"])["code"]
        last = max(cands, key=lambda s: s["last"])
        # 진행 중도 예정도 없다 = 소스에 다음 시즌이 아직 안 올라왔다.
        # 끝난 시즌을 계속 발행하게 되므로 조용히 넘기지 않는다.
        self.note_text(
            "다음 시즌 미게시",
            f"{last['code']}({last['name']})가 {last['last']}에 끝났는데 "
            f"다음 대회가 목록에 없습니다 — 끝난 시즌을 계속 붙들고 있습니다")
        return last["code"]

    def fetch(self, season_code: str | None = None) -> list[Game]:
        """season_code를 주지 않으면 오늘 날짜에 맞는 시즌을 스스로 고른다."""
        self.reset_notices()
        rows = self._rows()
        if season_code is None:
            season_code = self.season_code_for()
        picked = [r for r in rows if r.get("seasonCode") == season_code]
        if not picked:
            raise GateError(f"KOVO({self.league.value}): 시즌 {season_code} 0건")

        out: list[Game] = []
        for r in picked:
            # 대진 미확정 자리표시자는 팀이 아니다(위 `_PLACEHOLDER_TEAM` 주석).
            if _is_placeholder(r.get("hname")) or _is_placeholder(r.get("aname")):
                self.note("대진 미확정·가상팀이라 건너뜀",
                          f"{r.get('gdate')} {r.get('hname')} - {r.get('aname')}")
                continue
            out.append(self._parse(r))
        if not out:
            raise GateError(f"KOVO({self.league.value}): 시즌 {season_code} "
                            f"파싱 후 0건 — 0건은 항상 의심")
        self.note_text("선택한 시즌", f"{season_code} ({len(out)}경기)")
        return out

    def _parse(self, r: dict) -> Game:
        gd, gt = str(r.get("gdate") or ""), str(r.get("gstime") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
            raise UnknownStatus(f"KOVO: 날짜 해석 불가 {gd!r}")
        hh, mm = (gt.split(":") + ["0"])[:2] if ":" in gt else ("00", "00")
        start = datetime(int(gd[:4]), int(gd[5:7]), int(gd[8:10]),
                         int(hh), int(mm), tzinfo=KST)

        # **세트 하나 땄다고 끝난 것이 아니다 (v1.11h).**
        #
        # 전에는 `hspoint`(획득 세트 수)가 0이 아니면 FINAL이었다. 그러면
        # 1세트가 끝나는 순간(보통 25~30분) "1:0 종료"가 성립한다.
        # 배구 하한 50분을 넘긴 2세트 중반부터는 안전망 게이트도 못 막는다.
        #
        # 소스에는 종결 신호가 따로 있다: `getime`(경기 종료 시각).
        # 실측(남자부 3,202행): 완료 경기 3,034행이 (점수·score문자열·getime)을
        # 동시에 갖고, 미래 경기 126행은 셋 다 비어 있다.
        hs, as_ = r.get("hspoint"), r.get("aspoint")
        ended = bool(str(r.get("getime") or "").strip())
        has_score = bool(str(r.get("score") or "").strip()) or bool(hs or as_)
        if ended:
            status = Status.FINAL
        elif has_score:
            status = Status.LIVE          # 세트는 진행됐지만 경기는 안 끝났다
        else:
            status = Status.SCHEDULED
        score = (Score(int(hs), int(as_), ScoreUnit.SETS)
                 if status is Status.FINAL and hs is not None and as_ is not None
                 else None)

        sets: list[tuple[int, int]] = []
        for i in range(1, 6):
            h, a = r.get(f"hs{i}point"), r.get(f"as{i}point")
            if h or a:
                sets.append((int(h or 0), int(a or 0)))

        key = f"{r.get('seasonCode')}-{r.get('gnum')}-{gd}"
        g = Game(
            league=self.league, season=_season(start), source_key=key,
            home=TeamRef(self.league, team_code(r.get("hname"), self.gender)),
            away=TeamRef(self.league, team_code(r.get("aname"), self.gender)),
            start_utc=start.astimezone(ZoneInfo("UTC")), home_tz="Asia/Seoul",
            status=status, score=score, venue=r.get("place") or None,
            meta=GameMeta(gender=self.gender,          # 계약은 '1'/'2'를 쓴다
                          season_category=str(r.get("leagueCode") or "") or None,
                          set_scores=sets),
        )
        g.validate()
        return g


def _season(dt: datetime) -> str:
    y = dt.year if dt.month >= 7 else dt.year - 1
    return f"{y}-{str(y + 1)[2:]}"
