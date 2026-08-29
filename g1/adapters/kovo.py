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
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener

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


_OPENER = make_opener()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    raw = _fetch(_OPENER, req, label="KOVO 일정", timeout=45)
    return json.loads(raw.decode("utf-8", "replace"))


class KovoAdapter:
    """gender: '1'=남자부(VLEAGUE_M) · '2'=여자부(VLEAGUE_W)."""

    def __init__(self, gender: str) -> None:
        if gender not in ("1", "2"):
            raise GateError(f"KOVO: gender는 '1'(남) 또는 '2'(여) ({gender!r})")
        self.gender = gender
        self.league = League.VLEAGUE_M if gender == "1" else League.VLEAGUE_W

    def _rows(self) -> list[dict]:
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
        return out

    def season_codes(self) -> list[tuple[str, str, str]]:
        """(seasonCode, seasonName, 마지막 경기일) — 최신순."""
        rows = self._rows()
        by: dict[tuple[str, str], list[str]] = {}
        for r in rows:
            by.setdefault((r["seasonCode"], r["seasonName"]), []).append(r["gdate"])
        return sorted(((c, n, max(ds)) for (c, n), ds in by.items()),
                      key=lambda x: x[2], reverse=True)

    def fetch(self, season_code: str | None = None) -> list[Game]:
        """season_code를 주지 않으면 가장 최근 시즌을 쓴다."""
        rows = self._rows()
        if season_code is None:
            season_code = self.season_codes()[0][0]
        picked = [r for r in rows if r.get("seasonCode") == season_code]
        if not picked:
            raise GateError(f"KOVO({self.league.value}): 시즌 {season_code} 0건")
        return [self._parse(r) for r in picked]

    def _parse(self, r: dict) -> Game:
        gd, gt = str(r.get("gdate") or ""), str(r.get("gstime") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
            raise UnknownStatus(f"KOVO: 날짜 해석 불가 {gd!r}")
        hh, mm = (gt.split(":") + ["0"])[:2] if ":" in gt else ("00", "00")
        start = datetime(int(gd[:4]), int(gd[5:7]), int(gd[8:10]),
                         int(hh), int(mm), tzinfo=KST)

        hs, as_ = r.get("hspoint"), r.get("aspoint")
        # 상태값이 따로 없다. 세트 스코어가 둘 다 0이고 결과 문자열이 비면 아직 안 한 경기다.
        played = bool(str(r.get("score") or "").strip()) or bool(hs or as_)
        status = Status.FINAL if played else Status.SCHEDULED
        score = (Score(int(hs), int(as_), ScoreUnit.SETS)
                 if played and hs is not None and as_ is not None else None)

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
