"""NPB(일본프로야구) 기록·순위 수집 어댑터 (v1.11l 신설).

경기 어댑터(`npb.py`)는 "언제 누가 붙어서 몇 대 몇"만 준다.
순위표·부문 리더보드·맞대결 분석 카드는 그 위에 얹히는 콘텐츠라
`kbo_records.py`와 같은 모양의 `RecordBook`이 따로 필요하다.

── 왜 이 소스인가 ────────────────────────────────────────────────
NPB 공식 `npb.jp/bis/{시즌}/stats/` 는 **무인증 정적 HTML**이고, 우리가 필요한 세 가지를
전부 한 도메인에서 준다. 자격증명·토큰·JS 렌더링이 필요 없다(2026-09-03 실측).

  1) 팀 순위    `std_c.html`(센트럴) / `std_p.html`(퍼시픽)  ─ 표0 `チーム勝敗表`
  2) 상대전적   같은 두 페이지의 표0(리그 내 5팀) + 표1(`交流戦チーム勝敗表`, 상대 리그 6팀)
  3) 부문 순위  `lb_<부문>_<리그>.html`(타자) / `lp_<부문>_<리그>.html`(투수)

**부문 순위를 `llb_c.html`(리더즈 모음 페이지)에서 뽑지 않는 이유** — 그 페이지는
동률을 `( 2 選手 )`로 접어버려 **이름이 사라진다**. 2026-09-03 실측: 센트럴 본루타
3위가 `( 2 選手 ) 22`로 나와 3~4위 선수 이름을 알 수 없다. 카드에 이름을 못 쓴다.
부문별 전용 페이지(`lb_hr_c.html`)는 같은 자리를 `牧 秀悟(デ) 22` / `ダルベック(巨) 22`로
펴서 준다. 그래서 부문 수만큼 요청이 늘어도 전용 페이지를 쓴다.

── 표기 함정 (실측으로 확인, 2026-09-03) ─────────────────────────
  · 전적은 `16-7` = 승-패. 무승부가 있으면 `9-8<BR>(1)` = 9승 8패 1무.
    KBO처럼 세 숫자를 하이픈으로 잇지 않는다. 괄호가 없으면 무승부 0이다.
  · 대각선(자기 자신) 칸은 `***`.
  · 홈/로드(`ホーム`/`ロード`)는 표0에서는 **교류전 포함 시즌 전체**다.
    실측: 阪神 홈 30-25(1) + 로드 39-25 = 69-50-1 = 전체 전적. 표1의 홈/로드는
    교류전만이라 쓰지 않는다.
  · 표0의 `交流戦` 열은 교류전 합계다 → 표1 행 합계와 대조하는 데 쓴다.
  · 상대 팀 열 머리글은 `対神`·`対デ` 같은 **한 글자 약칭**이다. 이 약칭 표를
    코드에 박지 않는다 — 표0의 `***` 대각선이 "이 열 = 이 행의 팀"을 알려주므로
    **약칭→팀코드 매핑을 페이지에서 유도한다**. 상대 리그 약칭은 상대 리그 페이지의
    대각선에서 유도한 것을 그대로 쓴다. 부문 순위의 `佐藤 輝明(神)`도 같은 약칭이라
    같은 표를 재사용한다. 약칭 표기가 바뀌어도 코드를 고칠 일이 없다.

── 두 리그를 하나의 12팀 순위표로 합치는 방법과 근거 ──────────────
`contract.LEAGUE_TEAM_COUNT[League.NPB] == 12`이고 `assert_recordbook`은
`ranks == [1..N]`을 **정확히** 요구한다(순위 불연속·중복 금지). 그런데 NPB는
센트럴 1위와 퍼시픽 1위가 둘 다 1위다. `Standing`에는 소속 리그를 담을 자리가
**없다**(league/season/team_code/rank/games/record/pct/games_behind/last10/
streak/home/away가 전부이고, `pct`·`games_behind`는 값이 검증되는 자리다).
`contract.py`는 고치지 말라는 지시가 있으므로 필드를 늘리는 선택지도 없다.

그래서 **승차(GB) 기준 통합 순위**로 합친다. 근거:

  · GB의 정의 GB_i = ((W₁−W_i) + (L_i−L₁))/2 를 풀면
        GB_i = (W₁−L₁)/2 − (W_i−L_i)/2
    즉 **GB는 (승−패)의 단조 함수**다. 기준 팀(1위)이 누구든 상수만 달라진다.
    따라서 `(승−패)` 내림차순으로 정렬하면 **GB는 반드시 비감소**가 되고
    `assert_recordbook`의 "게임차 역전" 게이트를 구조적으로 통과한다.
  · 승률 내림차순으로 정렬하면 이 보장이 없다. 두 리그의 소화 경기 수가 다르면
    (4월에 흔하다) 승률 순서와 GB 순서가 어긋나 게이트에 걸려 **NPB 순위 카드가
    통째로 빠진다**. 오늘 데이터로는 두 정렬이 우연히 같지만, 우연에 기대지 않는다.
  · 1위는 `(승−패)` 최대 팀으로 잡는다 → 모든 팀 GB ≥ 0, 1위 GB = 0.
  · 동률은 승률 → 승수 → 팀코드 순으로 깬다(결정론).

**검증**: 이렇게 만든 통합 GB의 '같은 리그 안 1위 대비 차'는 소스의 `差` 열과
정확히 일치해야 한다. 2026-09-03 실측으로 12팀 전부 일치했다
(센트럴 0/5/13/16/17/19, 퍼시픽 0/5/7/17.5/18/26.5). 이 대조를 게이트로 건다 —
GB 계산이 틀리면 여기서 막힌다.

원래 리그 순위는 버리지 않는다. `RecordBook`에 담을 자리가 없으므로
`adapter.sub_league_rank`와 `rb.npb_sub_league`(계약 밖 부가 주석, 게이트는
이것을 보지 않는다)에 `{팀코드: ("CEN"|"PAC", 리그내순위)}`로 남긴다.

── 부문 순위(leaders)에 관하여 ───────────────────────────────────
NPB는 부문 순위를 **리그별로** 낸다. 우리 `RecordBook`은 12팀 하나이므로
같은 데이터를 두 벌로 담는다:
  · `홈런`             → 12팀 통합 재순위(NPB 전체 1위)
  · `센트럴 홈런`/`퍼시픽 홈런` → 소스 그대로의 리그별 순위(공식 타이틀은 이쪽이다)
`contract.assert_leader_order`는 부문명의 **마지막 공백 토큰**으로 오름차순 부문을
판정하므로(`"센트럴 평균자책점".split(" ")[-1]`), 접두사를 붙여도 방어율이
오름차순으로 옳게 검사된다. 부문명은 KBO와 같은 한국어 어휘를 쓴다 —
`ASCENDING_CATEGORIES`가 한국어 이름을 키로 갖기 때문에 `防御率`이라고 적으면
게이트가 방어율을 내림차순으로 오판한다.

**없는 부문은 만들지 않는다.** `pipeline.LEADER_SETS`의 4세트 중
  · `타격 부문`(타율·홈런·타점·도루)   → NPB에 전부 있다 ✅
  · `투수 부문`(평균자책점·승리·탈삼진·세이브) → 전부 있다 ✅
  · `출루·장타 부문`(출루율·장타율·OPS·안타) → **OPS가 없다.**
    2026-09-03 실측: `lb_obp_c.html`·`lb_slg_c.html`·`lb_h_c.html`은 200,
    `lb_ops_c.html`은 **404**. NPB는 OPS 부문 순위를 내지 않는다.
  · `제구·이닝 부문`(WHIP·QS·이닝·피안타율) → **셋이 없다.**
    실측: `lp_whip_c`·`lp_qs_c`·`lp_baa_c` 전부 404. `lp_ip_c`(이닝)만 200인데
    NPB는 이닝을 `151.1`(=151과 1/3)로 적어 소수점 표기와 구분이 안 된다.
  두 세트 모두 성립하지 않으므로 수집하지 않는다. **없는 부문은 만들지 않는다.**
그래서 `leaders`는 8개 부문 × (통합 + 리그별 2) = 24키다. 요청 16회.

`LeaderEntry.player_id`는 **소스에 없다.** 부문 페이지는 선수 상세로 링크하지
않고 `佐藤 輝明(神)` 텍스트만 준다. 계약이 빈 값을 금지하므로
`"NPB:<팀코드>:<이름>"` 형태의 **합성 키**를 넣는다. KBO의 `playerId`처럼
소스가 준 식별자가 아니다 — 다른 시스템의 선수 ID와 대조하는 데 쓰면 안 된다.

── 시즌 ─────────────────────────────────────────────────────────
URL에 연도가 들어간다(`/bis/2026/`). NPB 정규시즌은 3월 말~10월이고 포스트시즌이
11월까지다. 1~2월에는 그 해 페이지가 아직 없다 → 그때는 **직전 해**를 본다.
자동 판정한 시즌이 404면 한 번만 전 해로 물러난다(호출자가 시즌을 명시했으면
물러나지 않는다 — 요청한 해가 없으면 없다고 막는 것이 맞다).
어느 경우든 페이지가 스스로 말하는 `2026年度`와 대조한다. 라벨이 틀린 기록은
없는 것보다 나쁘다.
"""
from __future__ import annotations

import re
import sys
import os
import pathlib
import time as _time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (ASCENDING_CATEGORIES, GateError, League, LeaderEntry,
                      RecordBook, Standing, StreakKind, UnknownStatus, WLD,
                      assert_recordbook, leader_value_num)

# 일본어 구단명 → 팀코드. **복사하지 않는다** — 경기 어댑터가 이미 갖고 있다.
# 한쪽만 고치면 두 어댑터가 다른 팀을 같은 코드로 부르게 된다.
from adapters.npb import TEAM_CODE as _GAME_TEAM_CODE

JST = ZoneInfo("Asia/Tokyo")
_BASE = "https://npb.jp/bis"
_OPENER = make_opener()

# 리그 페이지 접미사 → (내부 라벨, 페이지가 스스로 말하는 이름)
SUB_LEAGUES: dict[str, tuple[str, str]] = {
    "c": ("CEN", "セントラル"),
    "p": ("PAC", "パシフィック"),
}

# `TEAM_CODE`에는 올스타전용 리그 코드도 들어 있다(セ・リーグ→CEN, パ・リーグ→PAC).
# 구단이 아니므로 순위표 팀명 대조에서 제외한다.
_NOT_A_CLUB = {"CEN", "PAC"}

# 정식 명칭에 `TEAM_CODE`의 표기가 통째로 들어 있지 않은 유일한 구단.
# `読売ジャイアンツ`에는 '巨人'이라는 글자가 없다(경기 페이지는 '巨人'으로 쓴다).
# 나머지 11팀은 전부 부분 문자열로 걸린다 — 2026-09-03 12팀 전수 확인.
_FULLNAME_EXTRA = {"読売": "YOG"}

# 기록 캐시 신선 창. 순위·부문 기록은 경기가 끝나야 바뀐다.
# 시계가 5분마다 도는데 그때마다 18페이지를 긁을 이유가 없다(실측 수집 22.9초).
RECORD_CACHE_TTL_SECONDS = 30 * 60

# ── 캐시를 인스턴스가 아니라 **모듈**에 둔 이유 (실측으로 확인) ──────
# `tick._record_jobs()`는 `lambda: KboRecordAdapter().fetch()`처럼 **매 틱 어댑터를
# 새로 만든다.** 그래서 인스턴스에 붙은 캐시(`self._rb_cache`)는 다음 틱에서
# 이미 사라지고 없다 — `_collect_records`의 "보관은 어댑터 캐시에 맡긴다"는 주석과
# 실제 동작이 어긋나 있다. NPB는 한 번 수집에 18페이지·22.9초라 이 차이가 크다
# (5분마다면 하루 288회 × 18페이지 = 5184요청. 차단당할 만하다).
# 호출자를 고치지 않고도 제동이 걸리도록 캐시를 모듈에 둔다.
# 키는 (시즌, 부문 포함 여부). 프로세스 안에서만 산다.
_RB_CACHE: dict[tuple[int, bool], tuple[float, RecordBook, dict]] = {}

# 부문 순위는 몇 위까지 담을 것인가. KBO Top5와 맞춘다.
LEADER_TOP_N = 5

# (한국어 부문명, NPB URL 키, 'b'=타자/'p'=투수)
# 한국어 이름을 쓰는 이유는 파일 머리말 참조 — `ASCENDING_CATEGORIES`가 한국어 키다.
LEADER_CATEGORIES: list[tuple[str, str, str]] = [
    ("타율", "avg", "b"),
    ("홈런", "hr", "b"),
    ("타점", "rbi", "b"),
    ("도루", "sb", "b"),
    ("평균자책점", "era", "p"),
    ("승리", "w", "p"),
    ("탈삼진", "so", "p"),
    ("세이브", "sv", "p"),
]

_TAG = re.compile(r"<[^>]+>")
_TABLE = re.compile(r'<table class="tablefix2".*?</table>', re.S)
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
_THEAD = re.compile(r"<thead[^>]*>(.*?)</thead>", re.S)
_TBODY = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.S)
_H4 = re.compile(r"<h4[^>]*>(.*?)</h4>", re.S)
_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)

# `16-7` · `9-8 (1)` — 괄호는 무승부. 괄호가 없으면 무승부 0.
_WL = re.compile(r"^(\d+)\s*-\s*(\d+)(?:\s*\(\s*(\d+)\s*\))?$")
# 부문 순위 항목 `佐藤 輝明(神)` — 괄호 안은 팀 한 글자 약칭.
_LEADER_NAME = re.compile(r"^(.+?)\s*[（(]\s*(.+?)\s*[)）]$")


def _text(s: str) -> str:
    """태그를 지우고 공백을 하나로. 전각공백·NBSP도 보통 공백으로 만든다."""
    t = _TAG.sub(" ", s).replace("　", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _table_rows(table_html: str) -> tuple[list[str], list[list[str]]]:
    """(머리글, 본문 행들). NPB 표는 thead/tbody가 나뉘어 있다."""
    head_m = _THEAD.search(table_html)
    body_m = _TBODY.search(table_html)
    if not head_m or not body_m:
        raise GateError("NPB 기록: 표에 thead/tbody가 없습니다 — 페이지 구조 변경")
    head = [_text(c) for c in _CELL.findall(head_m.group(1))]
    body = [[_text(c) for c in _CELL.findall(tr)] for tr in _TR.findall(body_m.group(1))]
    return head, [r for r in body if r]


def _wl(raw: str, where: str) -> WLD:
    m = _WL.match(raw)
    if not m:
        raise UnknownStatus(f"NPB {where}: 전적 표기 해석 불가 {raw!r}")
    return WLD(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _club_code(fullname: str) -> str:
    """`阪神タイガース` 같은 정식 명칭 → 팀코드. 경기 어댑터의 표를 재사용한다."""
    hits = {code for name, code in _GAME_TEAM_CODE.items()
            if code not in _NOT_A_CLUB and name in fullname}
    hits |= {code for name, code in _FULLNAME_EXTRA.items() if name in fullname}
    if len(hits) == 1:
        return hits.pop()
    if not hits:
        # 조용히 넘기지 않는다. 한 팀이 빠지면 12팀 게이트에서 막히지만,
        # 그때는 "왜 빠졌는지"가 안 보인다. 여기서 이름을 들고 막는 것이 낫다.
        raise GateError(f"NPB 순위: 미등록 구단명 {fullname!r} — "
                        f"adapters/npb.py의 TEAM_CODE에 없습니다")
    raise GateError(f"NPB 순위: 구단명 {fullname!r}이 여러 코드에 걸립니다 {sorted(hits)}")


def _season_for(now_utc: datetime | None = None) -> int:
    """오늘 날짜(JST)에서 시즌을 정한다.

    NPB 정규시즌은 3월 말~10월, 포스트시즌이 11월까지다. 1~2월에는 그 해
    `/bis/{연도}/` 가 아직 없다 → 직전 해(막 끝난 시즌)를 본다.
    """
    now = (now_utc or datetime.now(timezone.utc)).astimezone(JST)
    return now.year - 1 if now.month <= 2 else now.year


def _rank_with_ties(items: list, value_of) -> list[tuple[int, object]]:
    """정렬된 목록에 동률을 반영한 순위(1,2,3,3,5)를 붙인다."""
    out: list[tuple[int, object]] = []
    for i, it in enumerate(items):
        if i and abs(value_of(it) - value_of(items[i - 1])) < 1e-9:
            out.append((out[-1][0], it))
        else:
            out.append((i + 1, it))
    return out


class NpbRecordAdapter(NoticeMixin):
    """NPB 12팀 순위표 + 상대전적 + 부문 순위.

    `kbo_records.KboRecordAdapter`와 같은 모양이다. 다른 점은 두 가지뿐 —
    소스가 리그별 두 페이지라는 것과, 시즌을 URL로 **실제로 고를 수 있다**는 것.
    """

    league = League.NPB

    def __init__(self) -> None:
        self._op = _OPENER
        # {팀코드: ("CEN"|"PAC", 리그 안 순위)} — 통합 순위가 지운 정보를 남긴다.
        self.sub_league_rank: dict[str, tuple[str, int]] = {}

    # ── HTTP ──────────────────────────────────────────────────

    def _get(self, path: str) -> str:
        url = f"{_BASE}/{path}"
        return _fetch(self._op, url, label=f"NPB 기록 {path}").decode("utf-8", "replace")

    # ── 1) 리그별 순위 페이지 ──────────────────────────────────

    def _std_page(self, season: int, lg: str) -> dict:
        """`std_c.html` / `std_p.html` 한 장을 뜯는다."""
        label, jp_name = SUB_LEAGUES[lg]
        html = self._get(f"{season}/stats/std_{lg}.html")

        # 페이지가 스스로 말하는 시즌·리그와 대조한다. 라벨이 틀린 기록은
        # 없는 것보다 나쁘다.
        heads3 = [_text(h) for h in _H3.findall(html)]
        decl = next((h for h in heads3 if "年度" in h), "")
        m = re.search(r"(\d{4})\s*年度", decl)
        if not m:
            raise GateError(f"NPB 순위({label}): 페이지가 시즌을 안 알려줍니다 — "
                            f"어느 해 순위인지 모른 채 라벨을 붙이지 않습니다")
        if int(m.group(1)) != season:
            raise GateError(f"NPB 순위({label}): {season} 시즌을 요청했는데 "
                            f"페이지는 {m.group(1)}년도라고 말합니다")
        if jp_name not in decl:
            raise GateError(f"NPB 순위: std_{lg}.html이 {jp_name}가 아닙니다 ({decl!r}) — "
                            f"두 리그가 뒤바뀌면 12팀 통합이 통째로 틀립니다")

        # 표를 위치(0번·1번)가 아니라 **제목**으로 고른다. 페이지에 표가 하나
        # 늘거나 순서가 바뀌어도 엉뚱한 표를 순위표라고 읽지 않는다.
        tables = _TABLE.findall(html)
        titles = [_text(h) for h in _H4.findall(html)]
        if len(tables) != len(titles):
            raise GateError(f"NPB 순위({label}): 표 {len(tables)}개 / 제목 {len(titles)}개 "
                            f"— 페이지 구조 변경")
        by_title = dict(zip(titles, tables))
        if "チーム勝敗表" not in by_title:
            raise GateError(f"NPB 순위({label}): 'チーム勝敗表'가 없습니다 {titles}")

        std = self._parse_std_table(by_title["チーム勝敗表"], label)
        inter_tbl = by_title.get("交流戦チーム勝敗表")
        std["inter"] = (self._parse_inter_table(inter_tbl, label)
                        if inter_tbl is not None else None)
        if inter_tbl is None:
            # 교류전은 5~6월에만 열린다. 그 전에는 표 자체가 없을 수 있다.
            self.note_text(f"교류전 표 없음({label})",
                           "리그 간 상대전적을 0승0패0무로 둡니다")
        return std

    def _parse_std_table(self, table: str, label: str) -> dict:
        head, rows = _table_rows(table)
        idx = {name: i for i, name in enumerate(head)}
        for need in ("チーム", "試合", "勝利", "敗北", "引分", "勝率", "差",
                     "ホーム", "ロード"):
            if need not in idx:
                raise GateError(f"NPB 순위({label}): 열 '{need}' 없음 {head}")
        opp_cols = [(i, h[1:]) for i, h in enumerate(head)
                    if h.startswith("対") and len(h) > 1]
        if len(opp_cols) != 6:
            raise GateError(f"NPB 순위({label}): 상대 팀 열 {len(opp_cols)}개 (기대 6개)")
        if len(rows) != 6:
            raise GateError(f"NPB 순위({label}): 팀 {len(rows)}개 (기대 6개) — "
                            f"0건·부족은 항상 의심")

        teams: list[dict] = []
        abbrev: dict[str, str] = {}
        for lg_rank, c in enumerate(rows, start=1):
            if len(c) != len(head):
                raise GateError(f"NPB 순위({label}): 칸 {len(c)}개 (머리글 {len(head)}개)")
            code = _club_code(c[idx["チーム"]])
            games, win, loss, draw = (int(c[idx["試合"]]), int(c[idx["勝利"]]),
                                      int(c[idx["敗北"]]), int(c[idx["引分"]]))
            row = {
                "code": code, "sub": label, "lg_rank": lg_rank,
                "games": games, "record": WLD(win, loss, draw),
                "pct": c[idx["勝率"]],
                "src_gb": c[idx["差"]],
                "home": _wl(c[idx["ホーム"]], f"{label} 홈"),
                "away": _wl(c[idx["ロード"]], f"{label} 로드"),
                "intra": {},
                "inter_total": None,
            }
            if "交流戦" in idx and _WL.match(c[idx["交流戦"]] or ""):
                row["inter_total"] = _wl(c[idx["交流戦"]], f"{label} 교류전 합계")

            # 대각선(`***`)이 "이 열 = 이 팀"을 알려준다 → 약칭 표를 코드에 박지 않는다.
            diag = [ab for i, ab in opp_cols if c[i] == "***"]
            if len(diag) != 1:
                raise GateError(f"NPB 상대전적({label}): {code} 행의 대각선(***)이 "
                                f"{len(diag)}개 — 열/행 정렬이 어긋났습니다")
            abbrev[diag[0]] = code
            for i, ab in opp_cols:
                if c[i] == "***":
                    continue
                row["intra"][ab] = _wl(c[i], f"{label} 상대전적 {code}")
            teams.append(row)

        if len(abbrev) != 6 or set(abbrev.values()) != {t["code"] for t in teams}:
            raise GateError(f"NPB 상대전적({label}): 약칭 매핑 {abbrev} — "
                            f"6팀과 1:1로 맞지 않습니다")
        return {"teams": teams, "abbrev": abbrev}

    def _parse_inter_table(self, table: str, label: str) -> dict[str, dict[str, WLD]]:
        """교류전 표 — 행은 우리 리그 6팀, 열은 **상대 리그** 6팀 약칭."""
        head, rows = _table_rows(table)
        idx = {name: i for i, name in enumerate(head)}
        opp_cols = [(i, h[1:]) for i, h in enumerate(head)
                    if h.startswith("対") and len(h) > 1]
        if len(opp_cols) != 6 or "チーム" not in idx:
            raise GateError(f"NPB 교류전({label}): 상대 팀 열 {len(opp_cols)}개 (기대 6개)")
        out: dict[str, dict[str, WLD]] = {}
        for c in rows:
            code = _club_code(c[idx["チーム"]])
            out[code] = {ab: _wl(c[i], f"{label} 교류전 {code}") for i, ab in opp_cols}
        if len(out) != 6:
            raise GateError(f"NPB 교류전({label}): 팀 {len(out)}개 (기대 6개)")
        return out

    # ── 2) 12팀 통합 ───────────────────────────────────────────

    def _merge(self, season: int, pages: dict[str, dict]
               ) -> tuple[list[Standing], dict[tuple[str, str], WLD]]:
        rows = [r for lg in SUB_LEAGUES for r in pages[lg]["teams"]]
        if len(rows) != 12:
            raise GateError(f"NPB: 팀 {len(rows)}개 (기대 12개) — 0건·부족은 항상 의심")
        codes = {r["code"] for r in rows}
        if len(codes) != 12:
            raise GateError("NPB: 순위표에 중복 팀 — 두 페이지가 같은 리그일 수 있습니다")

        # 승차 기준 통합 정렬 (근거는 파일 머리말).
        def diff(r) -> int:
            return r["record"].win - r["record"].loss

        def sort_key(r):
            return (-diff(r), -float(r["pct"] or 0), -r["record"].win, r["code"])

        rows.sort(key=sort_key)
        top_diff = diff(rows[0])

        standings: list[Standing] = []
        gb_by_code: dict[str, float] = {}
        for rank, r in enumerate(rows, start=1):
            gb = (top_diff - diff(r)) / 2.0
            gb_by_code[r["code"]] = gb
            standings.append(Standing(
                league=League.NPB, season=str(season), team_code=r["code"],
                rank=rank, games=r["games"], record=r["record"],
                pct=r["pct"], games_behind=f"{gb:.1f}",
                # 이 소스는 최근 10경기·연속 기록을 주지 않는다. 없는 것을 만들지 않는다.
                last10=None, streak_kind=StreakKind.NONE, streak_len=0,
                home=r["home"], away=r["away"]))
            self.sub_league_rank[r["code"]] = (r["sub"], r["lg_rank"])

        self._check_gb_against_source(pages, gb_by_code)

        # 상대전적 — 리그 내 5팀(표0) + 상대 리그 6팀(표1) = 11팀
        abbrev_all: dict[str, str] = {}
        for lg in SUB_LEAGUES:
            for ab, code in pages[lg]["abbrev"].items():
                if abbrev_all.setdefault(ab, code) != code:
                    raise GateError(f"NPB 상대전적: 약칭 {ab!r}이 두 팀에 걸립니다")

        h2h: dict[tuple[str, str], WLD] = {}
        for lg, other in (("c", "p"), ("p", "c")):
            for r in pages[lg]["teams"]:
                me = r["code"]
                for ab, wld in r["intra"].items():
                    h2h[(me, pages[lg]["abbrev"][ab])] = wld
                inter = pages[lg]["inter"]
                if inter is None:
                    # 교류전 표가 아직 없다 → 아직 한 경기도 안 붙었다는 뜻.
                    # 0-0-0은 지어낸 값이 아니라 '0경기'라는 사실이다. 틀렸다면
                    # assert_recordbook의 행 합계 대조에서 바로 걸린다.
                    for opp in pages[other]["abbrev"].values():
                        h2h[(me, opp)] = WLD(0, 0, 0)
                    continue
                if me not in inter:
                    raise GateError(f"NPB 교류전: {me} 행이 없습니다")
                got = WLD(0, 0, 0)
                for ab, wld in inter[me].items():
                    opp = pages[other]["abbrev"].get(ab)
                    if opp is None:
                        raise GateError(f"NPB 교류전: 약칭 {ab!r}을 상대 리그에서 "
                                        f"찾을 수 없습니다")
                    h2h[(me, opp)] = wld
                    got = WLD(got.win + wld.win, got.loss + wld.loss,
                              got.draw + wld.draw)
                # 표0의 `交流戦` 열과 표1 행 합계 대조 — 두 표는 NPB가 따로 만든다.
                if r["inter_total"] is not None and r["inter_total"] != got:
                    raise GateError(f"NPB 교류전: {me} 표0 합계({r['inter_total']}) != "
                                    f"표1 행 합계({got})")
        return standings, h2h

    def _check_gb_against_source(self, pages: dict[str, dict],
                                 gb: dict[str, float]) -> None:
        """통합 GB의 '같은 리그 1위 대비 차'가 소스의 `差` 열과 같은지 대조.

        GB 계산이 틀리면 여기서 막힌다. 2026-09-03 실측으로 12팀 전부 일치했다.
        """
        for lg in SUB_LEAGUES:
            teams = pages[lg]["teams"]
            base = gb[teams[0]["code"]]          # 그 리그 1위(소스 순서의 첫 행)
            for r in teams:
                raw = (r["src_gb"] or "").strip()
                if raw in ("--", "---", "-", ""):
                    src = 0.0
                else:
                    try:
                        src = float(raw)
                    except ValueError:
                        # 표기 변형 하나로 순위 카드를 통째로 막지는 않는다.
                        # 이 열은 대조용이고, 우리가 내보내는 값이 아니다.
                        self.note(f"게임차 대조 건너뜀({r['sub']})", raw)
                        continue
                ours = gb[r["code"]] - base
                if abs(ours - src) > 1e-6:
                    raise GateError(
                        f"NPB 게임차: {r['code']} 소스 {src} != 재계산 {ours:.1f} — "
                        f"통합 순위 계산이 소스와 어긋납니다")

    # ── 3) 부문 순위 ───────────────────────────────────────────

    def _leader_page(self, season: int, key: str, side: str, lg: str,
                     abbrev: dict[str, str], category: str) -> list[tuple[str, str, str]]:
        """한 리그·한 부문 → [(선수명, 팀코드, 값 원문)] 순위 순서 그대로."""
        prefix = "lb" if side == "b" else "lp"
        html = self._get(f"{season}/stats/{prefix}_{key}_{lg}.html")
        tables = _TABLE.findall(html)
        if len(tables) != 1:
            raise GateError(f"NPB 부문({category}/{lg}): 표 {len(tables)}개 (기대 1개)")
        out: list[tuple[str, str, str]] = []
        for tr in _TR.findall(tables[0]):
            c = [_text(x) for x in _CELL.findall(tr)]
            if len(c) != 3 or not c[0].isdigit():
                continue
            m = _LEADER_NAME.match(c[1])
            if not m:
                # 이름이 없는 항목(동률 접기 등)은 그 항목만 뺀다.
                self.note(f"부문 항목 건너뜀({category})", c[1])
                continue
            team = abbrev.get(m.group(2))
            if team is None:
                raise GateError(f"NPB 부문({category}): 미등록 팀 약칭 "
                                f"{m.group(2)!r} — 조용히 넘기지 않습니다")
            out.append((m.group(1).strip(), team, c[2].strip()))
        if not out:
            raise GateError(f"NPB 부문({category}/{lg}): 0건 — 0건은 항상 의심")
        return out

    def _fetch_leaders(self, season: int,
                       abbrev: dict[str, str]) -> dict[str, list[LeaderEntry]]:
        out: dict[str, list[LeaderEntry]] = {}
        for category, key, side in LEADER_CATEGORIES:
            asc = category in ASCENDING_CATEGORIES
            per_league: dict[str, list[tuple[str, str, str]]] = {}
            for lg, label_ko in (("c", "센트럴"), ("p", "퍼시픽")):
                per_league[lg] = self._leader_page(season, key, side, lg,
                                                   abbrev, category)
                # 리그별은 소스가 이미 순위 순으로 준다 → 재정렬하지 않는다
                # (공식 타이틀은 리그별이다. 소스의 순서를 그대로 보존한다).
                self._put(out, f"{label_ko} {category}", key,
                          per_league[lg], asc, rerank=False)
            merged = per_league["c"] + per_league["p"]
            self._put(out, category, key, merged, asc, rerank=True)
        if not out:
            raise GateError("NPB 부문 순위: 0건 (0건은 항상 의심)")
        return out

    def _put(self, out: dict, category: str, key: str,
             items: list[tuple[str, str, str]], asc: bool, *, rerank: bool) -> None:
        if rerank:
            items = sorted(items, key=lambda t: (leader_value_num(t[2])
                                                 * (1 if asc else -1)))
        ranked = _rank_with_ties(items, lambda t: leader_value_num(t[2]))
        entries: list[LeaderEntry] = []
        for rank, (name, team, value) in ranked:
            if rank > LEADER_TOP_N:
                break
            entries.append(LeaderEntry(
                category=category, stat_key=key.upper(), rank=rank,
                # 소스에 선수 ID가 없다 → 합성 키. 외부 ID와 대조하면 안 된다.
                player_id=f"NPB:{team}:{name.replace(' ', '')}",
                name=name, team_code=team, value=value))
        if entries:
            out[category] = entries

    # ── 공개 API ───────────────────────────────────────────────

    def fetch(self, season: "int | str | None" = None, *,
              with_leaders: bool = True) -> RecordBook:
        """NPB 12팀 기록 스냅샷.

        `season`을 주면 그 해 페이지를 실제로 받는다(KBO와 달리 URL로 고를 수 있다).
        안 주면 오늘 날짜(JST)로 정하고, 그 해 페이지가 아직 없으면 한 번만
        직전 해로 물러난다. 어느 경우든 페이지가 스스로 말하는 연도와 대조한다.
        """
        self.reset_notices()
        self.sub_league_rank = {}

        want = None if season is None else int(str(season).strip())
        target = want if want is not None else _season_for()

        # ── 메모리 캐시 ────────────────────────────────────────
        # 순위·부문 기록은 경기가 끝나야 바뀐다. 실측 수집 약 25초(18페이지)를
        # 5분마다 반복하는 것은 소스에 대한 예의가 아니고 차단을 부른다.
        now = _time.time()
        # 부문 없이 받아둔 스냅샷은 부문까지 필요한 호출을 채우지 못한다.
        # 반대로 부문까지 있는 스냅샷은 부문 없는 요청도 채운다.
        for want_leaders in ([True] if with_leaders else [False, True]):
            hit = _RB_CACHE.get((target, want_leaders))
            if hit and (now - hit[0]) < RECORD_CACHE_TTL_SECONDS:
                # 신선 창 안의 캐시는 정상 동작이다 — 알림에 싣지 않는다.
                # (`note_cache_age`는 '묵은 데이터로 버티는 중'을 알리는 자리다.
                #  정상 히트까지 실으면 매 틱 같은 줄이 쌓여 진짜 경고가 묻힌다.)
                self.sub_league_rank = dict(hit[2])
                return hit[1]

        pages, used = self._load_pages(target, auto=(want is None))
        standings, h2h = self._merge(used, pages)
        abbrev = {ab: code for lg in SUB_LEAGUES
                  for ab, code in pages[lg]["abbrev"].items()}
        leaders = self._fetch_leaders(used, abbrev) if with_leaders else {}

        rb = RecordBook(
            league=League.NPB, season=str(used),
            collected_utc=datetime.now(timezone.utc),
            source_url=f"{_BASE}/{used}/stats/std_c.html",
            standings=standings, h2h=h2h, leaders=leaders)
        # 계약 밖 부가 주석. 게이트는 이것을 보지 않는다(통합 순위가 지운
        # '센트럴 3위' 같은 정보를 카드에서 되살릴 수 있게 남겨 둔다).
        rb.npb_sub_league = dict(self.sub_league_rank)

        assert_recordbook(rb)                    # 교차 대조 게이트
        # 게이트를 통과한 것만 캐시에 넣는다 — 막힌 스냅샷을 30분 동안
        # 다시 꺼내 쓰면 같은 오류를 30분 동안 반복한다.
        entry = (now, rb, dict(self.sub_league_rank))
        _RB_CACHE[(used, with_leaders)] = entry
        if used != target:
            # 시즌 되돌림이 일어났으면 '요청한 해'로도 걸어둔다. 안 그러면
            # 다음 틱이 또 없는 해를 찔러 404를 받고 또 물러난다.
            _RB_CACHE[(target, with_leaders)] = entry
        return rb

    def _load_pages(self, target: int, *, auto: bool) -> tuple[dict[str, dict], int]:
        """두 리그 페이지. 자동 판정한 시즌이 아직 없으면 한 번만 전 해로 물러난다."""
        try:
            return {lg: self._std_page(target, lg) for lg in SUB_LEAGUES}, target
        except GateError as e:
            if not auto or "404" not in str(e):
                raise
            prev = target - 1
            self.note_text("시즌 되돌림", f"{target}년 페이지가 없어 {prev}년을 봅니다")
            return {lg: self._std_page(prev, lg) for lg in SUB_LEAGUES}, prev


# ─────────────────────────────────────────────────────────────────
# 자체 시험 — 실제 소스에 붙어서 돈다.
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import contract as C

    a = NpbRecordAdapter()
    t0 = _time.time()
    rb = a.fetch()
    print(f"수집 {_time.time() - t0:.1f}초 · 시즌 {rb.season} · "
          f"{len(rb.standings)}팀 · 상대전적 {len(rb.h2h)}쌍 · 부문 {len(rb.leaders)}개")

    names = C.TEAM_NAMES[League.NPB]
    print(f"\n{'순위':<4}{'팀':<12}{'리그':<6}{'경기':>5}{'승':>5}{'패':>5}{'무':>4}"
          f"{'승률':>7}{'승차':>7}{'홈':>10}{'방문':>10}")
    for s in sorted(rb.standings, key=lambda x: x.rank):
        sub, lr = rb.npb_sub_league[s.team_code]
        print(f"{s.rank:<4}{names[s.team_code]:<12}{sub}{lr}위{'':<2}"
              f"{s.games:>5}{s.record.win:>5}{s.record.loss:>5}{s.record.draw:>4}"
              f"{s.pct:>7}{s.games_behind:>7}{str(s.home):>10}{str(s.away):>10}")

    # 팀 코드 대조
    got = {s.team_code for s in rb.standings}
    known = set(names) - {"CEN", "PAC"}
    assert got == known, f"팀 코드 불일치 {got ^ known}"
    print(f"\n팀 코드: contract.TEAM_NAMES[NPB] 12팀과 정확히 일치 ✅")

    # 상식 범위
    for s in rb.standings:
        assert 0.3 <= float(s.pct) <= 0.7, f"{s.team_code} 승률 {s.pct}"
        assert 0 < s.games <= C.REGULAR_SEASON_GAMES[League.NPB]
    print("승률 0.3~0.7 · 경기수 1~143 ✅")

    # 게이트
    C.assert_recordbook(rb)
    C.assert_recordbook(rb, require_h2h=False)
    print("assert_recordbook 통과 (require_h2h=True / False 둘 다) ✅")

    # 부문 순위
    for cat, es in rb.leaders.items():
        head = es[0]
        print(f"  {cat:<16} 1위 {head.name}({names[head.team_code]}) {head.value}"
              f"  [{len(es)}명]")
    if not rb.leaders:
        print("  부문 순위 없음")

    print(f"\n버린 것: {a.skipped_report() or '없음'}")

    # 캐시 확인 — **어댑터를 새로 만들어도** 걸려야 한다
    # (tick은 매 틱 어댑터를 새로 만든다. 파일 위 `_RB_CACHE` 주석 참조).
    t1 = _time.time()
    rb2 = NpbRecordAdapter().fetch()
    print(f"\n새 인스턴스로 두 번째 fetch {_time.time() - t1:.2f}초 "
          f"(30분 모듈 캐시) · 같은 객체 {rb2 is rb}")

    # 렌더까지 확인
    import pipeline as P
    # 개발 컴퓨터 경로를 코드에 남기지 않는다 — 공개 저장소이고
    # `verify_public.py`가 이것을 막는다(실제로 걸려서 고쳤다).
    import tempfile
    outdir = pathlib.Path(os.environ.get("NPB_CARD_OUT") or tempfile.mkdtemp())
    outdir.mkdir(parents=True, exist_ok=True)
    DAY = "2026-09-04"
    html = P.render_standings(rb, DAY)
    w, h, nbytes = P.render_png(html, outdir / "npb-standings.png")
    ok = h <= C.CARD_MAX_HEIGHT_PX and w + h <= C.GATE_PHOTO_DIM_SUM_MAX
    print(f"순위표 PNG {w}x{h} {nbytes/1024:.0f}KB "
          f"(높이 상한 {C.CARD_MAX_HEIGHT_PX} · 변 합 상한 {C.GATE_PHOTO_DIM_SUM_MAX}) "
          f"{'OK' if ok else '초과!'}")
    assert ok, "카드 크기 상한 초과"
    for i in (0, 1):
        lh = P.render_leaders(rb, DAY, i)
        lw, lhh, _ = P.render_png(lh, outdir / f"npb-leaders-{i}.png")
        print(f"리더보드 세트{i} '{P.leader_set(rb, i)[0]}' PNG {lw}x{lhh}")
    print(f"카드 저장: {outdir}")
