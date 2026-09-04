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
from _notices import NoticeMixin

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


# ── 어느 대회를 K리그1로 발행할 것인가 ────────────────────────
#
# **`leagueId=1` 응답에 K리그1 정규리그만 오지 않는다 (v1.11i 전수조사).**
# 2024~2026 전 월 실측 — 같은 응답에 들어 있던 것:
#     leagueId=1 meetSeq=1  '하나은행 K리그1 20XX'      228·228·198경기  ← 정규리그
#     leagueId=1 meetSeq=3  '하나은행 K리그 승강 PO 20XX'  각 4경기       ← 다른 대회
#     leagueId=7 meetSeq=4  '쿠팡플레이 K리그 슈퍼컵 2026'    1경기       ← 다른 대회
# 필터가 없어서 **2026-02-21 슈퍼컵(전북 2-0 대전)이 `KL1 / final 2-0`으로 발행됐다.**
#
# 승강 PO는 더 나쁘다. K리그2 팀 코드가 섞여 들어온다 —
# 2024 PO는 K17(대구)·K31(서울E)·K34(충남아산), 2025 PO는 K02(수원)·K26·K29(수원FC).
# 계약의 `TEAM_NAMES[KL1]`에는 K리그1 12팀뿐이라 `assert_team_names_cover`가
# **K리그 수집 전체를 막는다.** `fetch_months`가 전월을 함께 긁으므로
# 11월 말~12월 말 내내 매 틱 K리그가 죽는다.
#
# 소스가 `leagueId`와 `meetSeq`로 구분 신호를 준다. 그것으로 정규리그만 남긴다.
# **팀명 게이트보다 먼저 돈다** — 필터가 뒤에 있으면 게이트가 이미 리그를 죽인 뒤다.
# (게이트는 계약이 수집 이후 단계에서 부르므로, 어댑터가 fetch에서 걸러내면
#  승강 PO 팀 코드가 애초에 게이트까지 가지 않는다.)
REGULAR_MEET_SEQ = 1


class KLeagueAdapter(NoticeMixin):
    league = League.KL1

    def __init__(self, league_id: int = 1) -> None:
        self.league_id = league_id      # 1=K리그1, 2=K리그2(2차 후보)

    def fetch(self, year: int, months: list[str]) -> list[Game]:
        out: list[Game] = []
        seen = 0
        self.reset_notices()
        for mm in months:
            d = _post({"leagueId": self.league_id, "year": str(year),
                       "month": mm, "teamId": "", "ticketYn": ""})
            if str(d.get("resultCode")) not in ("200", "0000", "S00"):
                raise GateError(f"K리그: resultCode={d.get('resultCode')} {d.get('resultMsg')}")
            rows = (d.get("data") or {}).get("scheduleList") or []
            seen += len(rows)
            for r in rows:
                # ── 정규리그만 남긴다 (파싱보다 먼저) ──
                # 여기서 안 거르면 승강 PO의 K리그2 팀 코드가 팀명 게이트까지 가서
                # 12월 한 달 내내 K리그 수집을 통째로 막는다.
                rid, rseq = r.get("leagueId"), r.get("meetSeq")
                if int(rid or 0) != int(self.league_id) or int(rseq or 0) != REGULAR_MEET_SEQ:
                    self.note_info(
                        "K리그1 정규리그가 아니어서 제외",
                        f"{r.get('meetName')} (leagueId={rid} meetSeq={rseq}) "
                        f"{r.get('gameDate')} {r.get('homeTeamName')}-{r.get('awayTeamName')}")
                    continue
                out.append(self._parse(r))
        if seen == 0:
            # 0건은 항상 의심한다 — 월 파라미터가 틀렸거나 구조가 바뀐 것일 수 있다
            raise GateError(f"K리그: {year}년 {months} 일정 0건 — 0건은 항상 의심")
        if not out and seen:
            # 행은 왔는데 남은 것이 없다 = 우리 필터가 전부 걷어냈다.
            # 비시즌(승강 PO만 있는 12월)에는 정상이지만, 소스가 `meetSeq` 규칙을
            # 바꿨을 때도 똑같이 보인다. 조용히 0건으로 두지 않고 사실을 남긴다.
            self.note_text("정규리그 0건",
                           f"응답 {seen}행이 전부 다른 대회였습니다 "
                           f"({year}년 {months}) — 비시즌이면 정상, 아니면 meetSeq 규칙 변경 의심")
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
