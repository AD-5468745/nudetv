"""NPB 수집 어댑터 (v1.11 신설).

전 리그 중 **유일하게 HTML을 파싱한다.** 공식 JSON API가 없다.
그래서 게이트를 다른 어댑터보다 촘촘히 건다 — 구조가 바뀌면 조용히 틀리는 대신 막힌다.

페이지: `https://npb.jp/games/{시즌}/schedule_{월}_detail.html`

한 경기 = `<tr id="date0801">` 한 행.
  · 날짜는 `<th rowspan>`에 그 날 첫 행에만 있다 → 이월해야 한다 (KBO와 같은 함정)
  · `team1`/`team2` · `score1`/`score2` · `place` · `time`
  · **취소는 `<div class="cancel">中止</div>` — 별도 클래스다.**
    G0 4차 보고서는 "state 자리에 中止가 들어간다"고 했는데 그것도 틀렸다.
    그대로 믿고 만들었더니 2026시즌 취소 33건을 전부 놓쳤다(0건으로 읽혔다).
    "0건은 항상 의심"이 아니었으면 그대로 나갈 뻔했다.
  · `state`는 진행 상태다: `-`(경기 전/종료) · `（甲子園）試合終了` · `（横浜）9回表`

cancel 칸에 실제로 들어오는 값 (2024~2026 전수): 中止 85 · ノーゲーム 5 · (予備日) 3
`(予備日)`는 취소가 아니라 **예비일 표시**다 — 경기 자체가 편성되지 않은 칸이라 건너뛴다.

**`pit`(선발투수)와 `weather`는 비어 있다.** 필드는 있지만 값이 없다 —
"필드 존재 ≠ 데이터 존재"의 사례다. 선발투수 매치업은 NPB에서 못 만든다.
"""
from __future__ import annotations

import re
import sys
import pathlib
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener

from contract import (GateError, Game, GameMeta, League, STATUS_MAP, Score, ScoreUnit,
                      Status, TeamRef, UnknownStatus)

JST = ZoneInfo("Asia/Tokyo")
_BASE = "https://npb.jp/games"
_OPENER = make_opener()

# 일본어 구단명 → 코드. 한글 표기는 계약의 TEAM_NAMES가 갖는다.
TEAM_CODE = {
    "巨人": "YOG", "ＤｅＮＡ": "DEN", "DeNA": "DEN", "阪神": "HAN",
    "中日": "CHU", "ヤクルト": "YAK", "広島": "HIR",
    "ソフトバンク": "SOF", "日本ハム": "NIP", "ロッテ": "LOT",
    "楽天": "RAK", "オリックス": "ORI", "西武": "SEI",
    # 올스타전은 리그 대항전이라 구단이 아니라 리그 이름이 팀 자리에 온다.
    # 게이트가 먼저 잡아줬다 — 실제 경기이므로 KBL 올스타와 같이 발송 대상으로 둔다.
    "セ・リーグ": "CEN", "パ・リーグ": "PAC",
}

_ROW = re.compile(r'<tr id="date(\d{4})"[^>]*>(.*?)</tr>', re.S)
_DIV = lambda cls: re.compile(rf'<div class="{cls}">(.*?)</div>', re.S)
_DATE_TH = re.compile(r'<th[^>]*>(\d{1,2})/(\d{1,2})', re.S)
_LINK = re.compile(r'href="(/scores/\d{4}/\d{4}/[^"]+)"')


def _txt(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", s)).replace(" ", " ").strip()


class NpbAdapter:
    league = League.NPB

    def fetch(self, season: int, months: list[str]) -> list[Game]:
        out: list[Game] = []
        for mm in months:
            out += self._month(season, mm)
        if not out:
            raise GateError(f"NPB: {season}년 {months} 일정 0건 — 0건은 항상 의심")
        return out

    def _month(self, season: int, mm: str) -> list[Game]:
        url = f"{_BASE}/{season}/schedule_{mm}_detail.html"
        html = _fetch(_OPENER, url, label=f"NPB {season}-{mm}").decode("utf-8", "replace")

        rows = _ROW.findall(html)
        if not rows:
            # HTML 파싱은 구조 변경에 약하다. 조용히 0건을 반환하지 않는다.
            raise GateError(f"NPB {season}-{mm}: 경기 행 0건 — 페이지 구조 변경 의심")

        out: list[Game] = []
        for mmdd, body in rows:
            g = self._parse(season, mmdd, body)
            if g:
                out.append(g)
        return out

    def _parse(self, season: int, mmdd: str, body: str) -> Game | None:
        t1 = _txt(_DIV("team1").search(body).group(1)) if _DIV("team1").search(body) else ""
        t2 = _txt(_DIV("team2").search(body).group(1)) if _DIV("team2").search(body) else ""
        if not t1 or not t2:
            return None                      # 경기 없는 행(휴식일 등)

        if t1 not in TEAM_CODE or t2 not in TEAM_CODE:
            raise UnknownStatus(f"NPB: 미등록 팀명 {t1!r} / {t2!r}")

        cancel = _txt(_DIV("cancel").search(body).group(1)) if _DIV("cancel").search(body) else ""
        s1 = _txt(_DIV("score1").search(body).group(1)) if _DIV("score1").search(body) else ""
        s2 = _txt(_DIV("score2").search(body).group(1)) if _DIV("score2").search(body) else ""
        state = _txt(_DIV("state").search(body).group(1)) if _DIV("state").search(body) else ""
        place = _txt(_DIV("place").search(body).group(1)) if _DIV("place").search(body) else ""
        tm = _txt(_DIV("time").search(body).group(1)) if _DIV("time").search(body) else ""

        month, day = int(mmdd[:2]), int(mmdd[2:])
        hh, mi = (tm.split(":") + ["0"])[:2] if ":" in tm else ("00", "00")
        try:
            start = datetime(season, month, day, int(hh), int(mi), tzinfo=JST)
        except ValueError:
            raise UnknownStatus(f"NPB: 날짜·시각 해석 불가 {mmdd!r} {tm!r}")

        # 예비일은 경기가 아니다. 취소로 세면 없는 경기가 취소된 것처럼 나간다.
        if cancel and "予備" in cancel:
            return None

        # ── 상태 판정 ──
        # 1) cancel 칸이 있으면 그날 안 열렸다. 사유를 그대로 보관한다.
        # 2) 점수가 둘 다 있으면 종료.
        # 3) state에 회차가 찍혀 있으면 진행 중.
        # 4) 그 외는 예정. 단 과거 날짜인데 점수도 사유도 없으면 막는다 — 조용히 틀리지 않게.
        score = None
        if cancel:
            mapped = STATUS_MAP.get(League.NPB, {}).get(cancel)
            if mapped is None:
                raise UnknownStatus(f"NPB: 미등록 취소 표기 {cancel!r} ({mmdd})")
            status = mapped
        elif s1.isdigit() and s2.isdigit():
            status = Status.FINAL
            # **team1이 홈이다** (v1.11c에서 바로잡음).
            # 전에는 "화면 배치대로 team1=원정"으로 읽었다. 그 상태로 카드를 그렸더니
            # 8/30 여섯 경기가 전부 경기장과 어긋났다 — 에스콘필드(니혼햄 홈구장)
            # 경기의 홈팀이 지바롯데로, 고시엔(한신 홈) 경기의 홈팀이 요미우리로 찍혔다.
            # 여섯 경기 전부 어긋나므로 우연이 아니다. 점수도 함께 뒤집혀 있었으니
            # 결과 카드는 승패를 반대로 내보냈을 것이다.
            score = Score(int(s1), int(s2), ScoreUnit.RUNS)
        elif state and state != "-" and "終了" not in state:
            status = Status.LIVE                  # '（横浜）9回表' 형태
        else:
            status = Status.SCHEDULED

        link = _LINK.search(body)
        key = (link.group(1).strip("/").replace("/", "-") if link
               else f"{season}{mmdd}-{TEAM_CODE[t1]}-{TEAM_CODE[t2]}")

        g = Game(
            league=League.NPB, season=str(season), source_key=key,
            home=TeamRef(League.NPB, TEAM_CODE[t1]),
            away=TeamRef(League.NPB, TEAM_CODE[t2]),
            start_utc=start.astimezone(ZoneInfo("UTC")), home_tz="Asia/Tokyo",
            status=status, score=score, venue=place or None,
            meta=GameMeta(cancel_reason=cancel or None),
        )
        g.validate()
        return g
