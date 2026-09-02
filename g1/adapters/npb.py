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
cancel 칸에 실제로 들어오는 값 (2024~2026 전수): 中止 85 · ノーゲーム 5 · (予備日) 3
`(予備日)`는 취소가 아니라 **예비일 표시**다 — 경기 자체가 편성되지 않은 칸이라 건너뛴다.

── `state`에 관한 정정 (v1.11i 전수조사) ────────────────────────────
전에는 "`state`가 `（横浜）9回表` 같은 값을 주니 그걸로 진행 중을 판정한다"고 적혀 있었다.
**그런 값은 한 건도 없다.** 2026시즌 895행 전수 스캔 결과 `state`는
`'-'`(경기가 편성된 모든 행)와 `없음`(취소 행) 두 가지뿐이다. 점수가 있는 717행도
전부 `'-'`다. 즉 `state`로 LIVE를 판정하던 분기는 **실행 자체가 불가능한 코드**였다.
사고가 안 난 이유는 판정이 옳아서가 아니라 페이지가 늦게 갱신되기 때문이었다.

── 그럼 무엇이 '종료' 신호인가 (실제 HTML을 다시 뜯어 확인) ──────────
같은 행 안에 **세 신호가 함께** 들어온다. 2026시즌 895행 분포:
    717행  점수 O · `<div class="pit">勝：…/敗：…`(무승부면 `分：…`) · `/scores/…` 링크 O
    136행  점수 X · pit 없음 · 링크 X                       ← 미래 경기
      5행  점수 X · pit=`先発：…`(선발투수) · 링크 X          ← 오늘 편성된 경기
     34행  cancel 있음 (링크는 있음)                          ← 취소
      3행  cancel 있음 (링크 없음)
셋이 전부 같은 방향을 가리키고 예외가 0건이다. 그중 **결과투수 표기(勝/敗/分)**를
종결 신호로 쓴다 — 링크는 취소 행에도 붙지만 결과투수는 경기가 끝나야만 붙는다.
무승부까지 `分：`으로 표기되므로 "승리투수가 없으면 안 끝났다"는 함정도 피한다.

**LIVE 신호는 정말로 없다.** 오늘(2026-09-02) 18:00 JST 시작 5경기를 20:04 JST에
다시 긁었더니 점수·링크·결과투수가 전부 그대로 비어 있었다 — 이 페이지는
경기 중에 아무것도 갱신하지 않는다. 없는 신호를 있는 척할 수 없으므로
**진행 중은 시계로만 추정한다**(시작 시각이 지났는데 아직 종료 신호가 없으면 LIVE).
추정한 LIVE에는 **점수를 절대 싣지 않는다** — 실제로 아는 것이 없기 때문이다.

**`weather`는 비어 있다.** 필드는 있지만 값이 없다 —
"필드 존재 ≠ 데이터 존재"의 사례다.
"""
from __future__ import annotations

import re
import sys
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

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
_PIT = re.compile(r'<div class="pit">(.*?)</div>', re.S)

# 결과투수 표기 — 경기가 끝나야만 붙는다. `先発`(선발)은 경기 전에도 붙으므로 제외한다.
# 무승부는 승리투수가 없고 `分：`이 붙는다(2026시즌 12건 실측: 4/05·4/25·5/05·5/14·
# 5/27·6/05·6/06·6/07·6/28·7/02·7/03·8/20 전부 `分：`).
_RESULT_PITCHER = ("勝：", "敗：", "分：")

# 시작 시각이 지났는데 종료 신호가 없을 때 '진행 중'으로 볼 최소 경과 시간(초).
# 페이지가 경기 중에 갱신되지 않으므로 이 판정은 **시계 추정**이다. 0으로 두면
# 예정 시각 1초 뒤부터 '진행 중'이 되는데, 우천 지연으로 아직 시작도 안 한 경기가
# 그렇게 찍힌다. 야구는 지연이 흔하므로 30분 여유를 둔다.
LIVE_AFTER_SECONDS = 30 * 60


def _txt(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", s)).replace(" ", " ").strip()


class NpbAdapter(NoticeMixin):
    league = League.NPB

    def fetch(self, season: int, months: list[str],
              *, now_utc: datetime | None = None) -> list[Game]:
        """now_utc는 '진행 중' 추정에만 쓴다(소스에 LIVE 신호가 없다 — 파일 머리말 참조)."""
        self.reset_notices()
        now = now_utc or datetime.now(timezone.utc)
        out: list[Game] = []
        for mm in months:
            out += self._month(season, mm, now)
        if not out:
            raise GateError(f"NPB: {season}년 {months} 일정 0건 — 0건은 항상 의심")
        return out

    def _month(self, season: int, mm: str, now: datetime) -> list[Game]:
        url = f"{_BASE}/{season}/schedule_{mm}_detail.html"
        html = _fetch(_OPENER, url, label=f"NPB {season}-{mm}").decode("utf-8", "replace")

        rows = _ROW.findall(html)
        if not rows:
            # HTML 파싱은 구조 변경에 약하다. 조용히 0건을 반환하지 않는다.
            raise GateError(f"NPB {season}-{mm}: 경기 행 0건 — 페이지 구조 변경 의심")

        out: list[Game] = []
        for mmdd, body in rows:
            g = self._parse(season, mmdd, body, now)
            if g:
                out.append(g)
        return out

    def _parse(self, season: int, mmdd: str, body: str, now: datetime) -> Game | None:
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
        #
        # 신호의 근거는 파일 머리말의 895행 전수 분포에 있다. 요약하면
        #   종료  = 점수 두 칸이 숫자 + `pit`에 결과투수(勝/敗/分)
        #   그 외 = 소스가 아무 말도 하지 않는다(진행 중 표기 자체가 없다)
        # `state`는 어느 행에서도 `-` 하나뿐이라 판정에 쓸 수 없다.
        #
        # 1) cancel 칸이 있으면 그날 안 열렸다. 사유를 그대로 보관한다.
        # 2) 점수 + 결과투수가 함께 있으면 종료.
        # 3) 점수는 있는데 결과투수가 없으면 **종료라고 말하지 않는다.**
        #    2026시즌 실측에는 그런 행이 0건이라, 나오면 그 자체가 구조 변경 신호다.
        #    끝났다고 단정하면 v1.11g의 "0:0 다섯 경기 종료" 사고와 같은 계열이 된다.
        # 4) 점수가 없으면 시계로 가른다 — 시작 시각 + 30분이 지났으면 진행 중,
        #    아니면 예정. 진행 중에는 점수를 싣지 않는다(아는 것이 없다).
        pit = " ".join(_txt(p) for p in _PIT.findall(body))
        decided = any(mark in pit for mark in _RESULT_PITCHER)
        has_score = s1.isdigit() and s2.isdigit()

        # `state`는 판정에 쓰지 않는다(895행 전부 '-' 또는 없음). 다만 값이 생기면
        # 소스가 진행 상태를 싣기 시작했다는 뜻이므로 — LIVE를 시계로 추정하지 않아도
        # 되는 날이 온 것이다 — 조용히 넘기지 않고 알린다.
        if state and state != "-":
            self.note("state 칸에 새 값이 보임(진행 신호가 생겼을 수 있음)",
                      f"{season}-{mmdd} {state!r}")

        score = None
        if cancel:
            mapped = STATUS_MAP.get(League.NPB, {}).get(cancel)
            if mapped is None:
                raise UnknownStatus(f"NPB: 미등록 취소 표기 {cancel!r} ({mmdd})")
            status = mapped
        elif has_score and decided:
            status = Status.FINAL
            # **team1이 홈이다** (v1.11c에서 바로잡음).
            # 전에는 "화면 배치대로 team1=원정"으로 읽었다. 그 상태로 카드를 그렸더니
            # 8/30 여섯 경기가 전부 경기장과 어긋났다 — 에스콘필드(니혼햄 홈구장)
            # 경기의 홈팀이 지바롯데로, 고시엔(한신 홈) 경기의 홈팀이 요미우리로 찍혔다.
            # 여섯 경기 전부 어긋나므로 우연이 아니다. 점수도 함께 뒤집혀 있었으니
            # 결과 카드는 승패를 반대로 내보냈을 것이다.
            score = Score(int(s1), int(s2), ScoreUnit.RUNS)
        elif has_score:
            # 점수만 있고 결과투수가 없다 — 2026시즌 895행에 0건인 조합이다.
            status = Status.LIVE
            self.note("점수는 있는데 결과투수 표기가 없어 '종료'로 안 봄",
                      f"{season}-{mmdd} {t1} {s1}:{s2} {t2} pit={pit[:40]!r}")
        elif (now - start.astimezone(timezone.utc)).total_seconds() >= LIVE_AFTER_SECONDS:
            status = Status.LIVE
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
