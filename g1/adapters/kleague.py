"""K리그1 수집 어댑터 (v1.11 신설).

```
POST https://www.kleague.com/getScheduleList.do
Content-Type: application/json; charset=utf-8      ← 없으면 415
{"leagueId":1,"year":"2026","month":"09","teamId":"","ticketYn":""}
```
GET은 400(POST 전용), `month` 누락 시 500 — **월 단위로만 조회된다.**

**안정 키는 `gameId` 단독이 아니다.** G0 실측에서 2022~2026 사이 `gameId` 중복이 17건 나왔다.
`(year, leagueId, meetSeq, gameId)` 네 요소를 합쳐야 유니크하다(25/25 검증).

⚠️ **취소·연기 상태값이 없다.** `gameStatus` 도메인은 `FE`(종료)/`1S~4S`(진행)/`1E~4E`(피리어드 종료)/`""`(경기 전)뿐이고,
연맹 렌더링 코드에도 '연기'·'취소' 문자열이 없다. 연기는 gameDate/gameTime 덮어쓰기로만 반영되므로
**스냅샷 diff로 감지한다**(계약의 DIFF_ONLY_CANCELLATION).

⚠️ **34~38라운드(파이널 스플릿)는 미게시.** 스플릿이 확정된 뒤 추가되므로 매일 전월을 다시 조회해야 한다.
"""
from __future__ import annotations

import json
import re
import sys
import pathlib
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener

from contract import (GateError, Game, GameMeta, League, Score, ScoreUnit, Status,
                      TeamRef, UnknownStatus, parse_status)

KST = ZoneInfo("Asia/Seoul")
_API = "https://www.kleague.com/getScheduleList.do"
_OPENER = make_opener()


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        _API, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "Mozilla/5.0"})
    raw = _fetch(_OPENER, req, label=f"K리그 일정 {body.get('year')}-{body.get('month')}")
    return json.loads(raw.decode("utf-8", "replace"))


class KLeagueAdapter:
    league = League.KL1

    def __init__(self, league_id: int = 1) -> None:
        self.league_id = league_id      # 1=K리그1, 2=K리그2(2차 후보)

    def fetch(self, year: int, months: list[str]) -> list[Game]:
        out: list[Game] = []
        seen = 0
        for mm in months:
            d = _post({"leagueId": self.league_id, "year": str(year),
                       "month": mm, "teamId": "", "ticketYn": ""})
            if str(d.get("resultCode")) not in ("200", "0000", "S00"):
                raise GateError(f"K리그: resultCode={d.get('resultCode')} {d.get('resultMsg')}")
            rows = (d.get("data") or {}).get("scheduleList") or []
            seen += len(rows)
            for r in rows:
                out.append(self._parse(r))
        if seen == 0:
            # 0건은 항상 의심한다 — 월 파라미터가 틀렸거나 구조가 바뀐 것일 수 있다
            raise GateError(f"K리그: {year}년 {months} 일정 0건 — 0건은 항상 의심")
        return out

    def _parse(self, r: dict) -> Game:
        gd = str(r.get("gameDate") or "").replace(".", "-")
        gt = str(r.get("gameTime") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", gd):
            raise UnknownStatus(f"K리그: 날짜 해석 불가 {r.get('gameDate')!r}")
        hh, mm = (gt.split(":") + ["0"])[:2] if ":" in gt else ("00", "00")
        start = datetime(int(gd[:4]), int(gd[5:7]), int(gd[8:10]),
                         int(hh), int(mm), tzinfo=KST)

        raw_status = str(r.get("gameStatus") or "").strip()
        try:
            status = parse_status(raw_status, League.KL1)
        except UnknownStatus:
            # 계약의 STATUS_MAP에 없는 값이면 endYn으로 되짚는다.
            # 여기서 바로 막으면 새 코드 하나 때문에 그날 리그 전체가 죽는다.
            status = Status.FINAL if str(r.get("endYn")) == "Y" else Status.SCHEDULED

        hg, ag = r.get("homeGoal"), r.get("awayGoal")
        score = (Score(int(hg), int(ag), ScoreUnit.GOALS)
                 if status in (Status.FINAL, Status.LIVE)
                 and hg is not None and ag is not None else None)

        home_code = str(r.get("homeTeam") or "").strip()
        away_code = str(r.get("awayTeam") or "").strip()
        if not home_code or not away_code:
            raise UnknownStatus(f"K리그: 팀 코드 없음 {r.get('homeTeamName')!r}")

        # gameId 단독은 중복된다. 네 요소를 합친다.
        key = f"{r.get('year')}-{r.get('leagueId')}-{r.get('meetSeq')}-{r.get('gameId')}"

        g = Game(
            league=League.KL1, season=str(r.get("year") or start.year), source_key=key,
            home=TeamRef(League.KL1, home_code), away=TeamRef(League.KL1, away_code),
            start_utc=start.astimezone(ZoneInfo("UTC")), home_tz="Asia/Seoul",
            status=status, score=score,
            venue=r.get("fieldNameFull") or r.get("fieldName") or None,
            meta=GameMeta(season_category=r.get("codeName") or None),
        )
        g.validate()
        return g

    def team_names(self, year: int, month: str) -> dict[str, str]:
        """clubList에서 팀 코드 → 한글 구단명. 계약의 TEAM_NAMES를 채우는 데 쓴다."""
        d = _post({"leagueId": self.league_id, "year": str(year),
                   "month": month, "teamId": "", "ticketYn": ""})
        clubs = (d.get("data") or {}).get("clubList") or []
        return {c["teamId"]: (c.get("teamNameShort") or c.get("teamName"))
                for c in clubs if c.get("teamId")}
