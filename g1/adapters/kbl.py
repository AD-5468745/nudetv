"""KBL 수집 어댑터 (v1.11 신설).

공식 JSON API. 필수 헤더 `Channel: WEB` + `TeamCode: XX`가 없으면 응답하지 않는다.

**팀 코드를 그대로 쓰면 안 된다 — 실측에서 나온 함정.**
KBL은 시즌 카테고리마다 팀 코드 체계가 다르다.
  · 정규시즌(R): 06=수원 KT · 10=울산 현대모비스 · 50=창원 LG
  · 플레이오프(PO): 18=KT · 31=현대모비스 · 36=LG      ← 같은 팀, 다른 코드
  · D리그(D1): 18=KT · 19=SK                          ← 같은 코드, 다른 팀(2군)
코드로 팀을 식별하면 플레이오프 KT와 D리그 KT가 같은 팀이 된다.
그래서 **팀 이름을 정규화해 코드를 만든다.**

시즌 카테고리는 화이트리스트다(G0 D-0e에서 확정).
  발송: R(정규) · PO(플레이오프) · CP(챔피언결정전) · AS(올스타) · EA(EASL)
  제외: D1(2군) · OM(오픈매치)
블랙리스트("D로 시작하면 제외")였다면 PO·CP·AS·EA가 정규시즌과 섞여 나갔을 것이다.

**취소·연기 상태값이 없다.** 연맹이 gameDate/gameTime을 덮어쓰는 방식이라
스냅샷 diff로만 감지된다(계약의 DIFF_ONLY_CANCELLATION과 같은 취급).

**EASL(EA)은 결과가 영원히 안 채워진다 (v1.11c에서 발견).**
2026-08-28 전수 대조: R·PO·CP·AS·D1 373건은 373/373 전부 isEnded=1인데
EA 13건은 13/13 전부 isEnded=0 · gameEnd='' · score 0-0이다.
2025-10-22 경기가 10개월 뒤에도 0-0이면 지연이 아니라 **소관 밖**이다 —
KBL은 EASL 편성만 싣고 결과는 주최측이 관리한다.
`isEnded=0`을 곧이곧대로 '예정'으로 번역하면 묵은 경기가 오늘의 모닝 카드에 실린다.
그래서 **지난 EASL 경기는 격리하고(self.unresolved), 앞으로 열릴 것은 그대로 발송한다.**
계약의 SOURCE_RESULTLESS_CATEGORIES가 이 경계를 들고 있다.
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

from contract import (GateError, Game, GameMeta, KBL_SEASON_CATEGORY_ALLOW,
                      KBL_PUBLISH_CATEGORIES, League,
                      SOURCE_RESULTLESS_CATEGORIES, STALE_SCHEDULED_GRACE_SECONDS,
                      Score, ScoreUnit, Status, TeamRef, UnknownStatus)

KST = ZoneInfo("Asia/Seoul")
_API = "https://api.kbl.or.kr/match/list"
_HEAD = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
         "Channel": "WEB", "TeamCode": "XX"}

# 팀 이름(도시명이 붙기도 하고 안 붙기도 한다) → 안정적인 코드
_KBL_TEAMS = {
    "KT": "KT", "현대모비스": "MOB", "DB": "DB", "삼성": "SS", "LG": "LG",
    "SK": "SK", "KCC": "KCC", "한국가스공사": "KOGAS", "소노": "SONO", "정관장": "KGC",
}


def team_code(name: str) -> str:
    """도시명을 떼고 구단명만 남긴다. 못 찾으면 이름 자체를 코드로 쓴다.

    EASL 외국팀·올스타 가상팀은 매핑이 없다 — 그대로 두는 것이 맞다.
    억지로 매핑하면 없는 팀을 만들어낸다.
    """
    n = (name or "").strip()
    for key, code in _KBL_TEAMS.items():
        if key in n:
            return code
    return re.sub(r"\s+", "", n)[:12] or "UNK"


_OPENER = make_opener()


def _get(url: str) -> list:
    req = urllib.request.Request(url, headers=_HEAD)
    raw = _fetch(_OPENER, req, label="KBL 일정")
    data = json.loads(raw.decode("utf-8", "replace"))
    if not isinstance(data, list):
        raise GateError(f"KBL: 응답이 목록이 아니다 ({type(data).__name__}) — 구조 변경")
    return data


class KblAdapter:
    league = League.KBL

    def __init__(self) -> None:
        # 소관 밖이라 격리한 경기. 조용히 사라지면 안 되므로 호출자가 꺼내 볼 수 있게 둔다.
        self.unresolved: list[dict] = []

    def fetch(self, start: str, end: str, *, now_utc: datetime | None = None) -> list[Game]:
        """start/end는 YYYYMMDD."""
        rows = _get(f"{_API}?fromDate={start}&toDate={end}")
        now = now_utc or datetime.now(ZoneInfo("UTC"))
        resultless = SOURCE_RESULTLESS_CATEGORIES.get(League.KBL, frozenset())
        out: list[Game] = []
        self.unresolved = []
        self.skipped_categories = 0
        for r in rows:
            # **1군인지 먼저 본다 (v1.11h).**
            # `seasonCategory`만으로는 D리그를 못 거른다 — D리그가 PO·CP 코드를
            # 그대로 쓰기 때문이다. 실측(2025-26 전 시즌): grade=2(D리그)에
            # PO 5경기 · CP 1경기가 있고, 그 경기들이 카테고리 필터를 통과해
            # 상무(2군 팀)가 KBL 경기로 들어왔다.
            # 구분 신호는 `seasonGrade`(1=KBL · 2=D리그)와 `seasonName1`이다.
            if int(r.get("seasonGrade") or 1) != 1:
                self.skipped_categories += 1
                continue
            cat = (r.get("seasonCategory") or "").strip()
            if cat not in KBL_SEASON_CATEGORY_ALLOW:
                continue                       # 오픈매치 등은 여기서 걸러진다
            # **EASL·올스타는 발행 대상이 아니다 (v1.11h).**
            # 외국 구단(뉴타이베이킹스…)·가상팀(팀아시아…)이 등장해
            # `assert_team_names_cover`가 **KBL 리그 전체 수집을 막는다.**
            # 수집 창이 21일이라 그 경기 하나가 3주간 KBL을 침묵시킨다.
            if cat not in KBL_PUBLISH_CATEGORIES:
                self.skipped_categories += 1
                continue
            g = self._parse(r, cat)

            # ── 소스 소관 밖의 지난 경기는 '예정'으로 내보내지 않는다 ──
            # KBL은 EASL 편성만 싣고 결과는 관리하지 않는다(계약의 실측 근거 참조).
            # 그대로 두면 10개월 묵은 경기가 오늘의 '예정'이 되어 모닝 카드에 실린다.
            # 앞으로 열릴 EASL 경기는 편성표로서 정확하므로 그대로 발송한다 — 정보를 죽이지 않는다.
            if (cat in resultless and g.status is Status.SCHEDULED
                    and (now - g.start_utc).total_seconds() > STALE_SCHEDULED_GRACE_SECONDS):
                self.unresolved.append({
                    "source_key": g.source_key, "sports_day": g.sports_day,
                    "category": cat, "home": g.home.team_code, "away": g.away.team_code,
                    "reason": "KBL API가 EASL 결과를 제공하지 않음(편성만 게시)"})
                continue
            out.append(g)
        return out

    def _parse(self, r: dict, cat: str) -> Game:
        gd, gs = str(r.get("gameDate") or ""), str(r.get("gameStart") or "")
        if not re.fullmatch(r"\d{8}", gd) or not re.fullmatch(r"\d{3,4}", gs):
            raise UnknownStatus(f"KBL: 날짜·시각 해석 불가 {gd!r} {gs!r}")
        gs = gs.zfill(4)
        start = datetime(int(gd[:4]), int(gd[4:6]), int(gd[6:8]),
                         int(gs[:2]), int(gs[2:]), tzinfo=KST)

        started, ended = int(r.get("isStarted") or 0), int(r.get("isEnded") or 0)
        hs, as_ = r.get("scoreH"), r.get("scoreA")
        if ended:
            status = Status.FINAL
        elif started:
            status = Status.LIVE
        else:
            status = Status.SCHEDULED
        score = (Score(int(hs), int(as_), ScoreUnit.POINTS)
                 if status in (Status.FINAL, Status.LIVE)
                 and hs is not None and as_ is not None else None)

        key = r.get("gmkey") or f"{gd}{r.get('gameNo')}"
        q = str(r.get("playingQuarter") or "").strip()

        g = Game(
            league=League.KBL, season=_season(start), source_key=str(key),
            home=TeamRef(League.KBL, team_code(r.get("tnameH"))),
            away=TeamRef(League.KBL, team_code(r.get("tnameA"))),
            start_utc=start.astimezone(ZoneInfo("UTC")), home_tz="Asia/Seoul",
            status=status, score=score, venue=r.get("stadiumname") or None,
            meta=GameMeta(season_category=cat,
                          period=int(q) if q.isdigit() else None),
        )
        g.validate()
        return g


def _season(dt: datetime) -> str:
    """농구·배구는 해를 걸친다. 10월 이후면 그 해가 시작 연도다."""
    y = dt.year if dt.month >= 7 else dt.year - 1
    return f"{y}-{str(y + 1)[2:]}"
