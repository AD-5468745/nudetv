"""점수 외부 대조 소스 (v1.11j 신설) — **발행 직전 안전장치**.

**왜 만들었나.** 2026-09-01 19:18, 진행 중이던 KBO 5경기가
"종료 · 전부 0:0 무승부"로 실제 채널에 나갔다. 원인(소스의 `relay` 칸을 안 봤다)은
`kbo.py`에서 고쳤지만, 고쳐도 남는 구조적 위험이 있다 —
**우리는 리그마다 소스를 하나만 본다.** 그 하나가 틀리면 우리도 그대로 틀린다.
어댑터를 아무리 촘촘히 짜도 "소스가 거짓말을 했다"는 우리 코드 안에서는 보이지 않는다.

그래서 **발행 직전에 다른 곳에 한 번 더 물어본다.** 점수·종료 여부가 어긋나면 막는다.

────────────────────────────────────────────────────────────────────
이 파일이 지키는 네 가지 원칙 (전부 09-01 사고에서 나왔다)
────────────────────────────────────────────────────────────────────
1. **대조는 안전장치이지 의존 대상이 아니다.**
   외부가 죽으면 "대조 못 함"이지 "어긋남"이 아니다. 절대 발행을 막지 않는다.
   이걸 헷갈리면 네이버가 점검하는 30분 동안 우리 채널이 통째로 멈춘다.
   → 이 모듈의 공개 함수는 **예외를 던지지 않는다.** 전부 잡아서 `sources`에 적는다.
2. **억지로 맞추지 않는다.** 팀 매핑이 안 되면 그 경기는 '대조 불가'다.
   억지 매칭은 엉뚱한 경기끼리 점수를 비교해 **없던 사고를 만든다.**
3. **묵은 대조 데이터는 쓰지 않는다.** `lck.py`는 리밋에 걸리면 캐시로 버티는데,
   그건 '일정이 없는 것보다 묵은 일정이 낫다'는 판단이었다. 여기는 정반대다 —
   30분 전 스냅샷으로 대조하면 **방금 끝난 경기가 전부 '외부는 진행 중'으로 보여**
   정상 발행을 막는다. 캐시는 같은 틱 안의 중복 요청을 없애는 용도(120초)뿐이다.
4. **틀린 방향에 따라 처분이 다르다.** 아래 `MismatchKind` 표 참조.

────────────────────────────────────────────────────────────────────
리그별 소스와 고른 이유 (전부 2026-09-02 실측)
────────────────────────────────────────────────────────────────────
· KBO      네이버 스포츠 `api-gw.sports.naver.com/schedule/games` (kbaseball/kbo)
           2026-03-01~11-30 **843건** 응답. 우리 1차 소스는 koreabaseball.com의
           ASMX(HTML 조각 JSON)이므로 완전히 다른 파이프라인이다.
           **팀 코드가 우리 것과 글자까지 같다**(LG·OB·KT·SK·NC·WO·HT·LT·SS·HH) —
           매핑 사고 여지가 가장 적다. ESPN에는 KBO가 없다(baseball/kbo·kor.kbo 둘 다 400).
· MLB      **ESPN** `site.api.espn.com/.../baseball/mlb/scoreboard`
           2026-08-01~09-02 **447건** 응답, 30팀 전부 확인.
           네이버에도 MLB(wbaseball/mlb, 558건)가 있지만 **ESPN을 골랐다** —
           우리 1차 소스가 MLB 공식 statsapi라서, 같은 공식 피드를 재가공했을
           가능성이 있는 소스로 대조하면 공식 피드의 오류를 둘 다 그대로 베낀다.
           ESPN은 자체 스탯 공급망을 쓴다. 약칭도 우리와 30개 중 29개가 같다
           (다른 것은 화이트삭스 CHW↔CWS 하나뿐).
· NPB      네이버 (wbaseball/npb) — 2026-03~11 **892건**.
           **여기가 가장 이득이 크다.** 우리 1차 소스 npb.jp는 전 리그 중 유일한
           HTML 파싱이고, `npb.py` 주석대로 **진행 중 신호가 아예 없어 시계로 추정**한다.
           네이버는 `statusCode`로 진행/종료를 직접 말해 준다.
· K리그1    네이버 (kfootball/kleague) — 2026-02~11 **198건 · 12팀**.
           우리 1차 소스(kleague.com)가 같은 기간에 내놓는 K리그1 정규리그 경기 수와
           **정확히 같다.** 팀 코드도 우리 코드에서 앞의 'K'만 뗀 형태다(04=제주=K04).
           ESPN은 soccer/kor.1이 **0건**을 돌려준다(2026-08-01~09-02 전 구간) —
           "필드가 있으니 괜찮다"의 반대 사례라 쓰지 않는다.
· LCK      네이버 e스포츠 `esports-api.game.naver.com/service/v1/schedule/month`
           2026-01~09 **189건**. `nameEngAcronym`이 우리 코드와 거의 같다
           (T1·GEN·DK·KT·HLE·NS·BFX·DNS·BRO 그대로, KIWOOM DRX만 KRX↔DRX).
           **단 보조 수단이다** — 아래 `LCK 한계` 주석 참조.
           ⚠️ **LCK에는 홈·원정이 없다.** 실측에서 최근 3주 13경기가 13경기 전부
           '홈·원정 뒤집힘'으로 잡혔다 — 양쪽 소스의 표시 순서가 반대일 뿐이다.
           `NEUTRAL_VENUE_LEAGUES` 주석 참조.
· 그 외     KBL·V리그·유럽 축구는 대조 소스를 두지 않았다. `PROVIDERS`에 없는 리그는
           언제나 '대조 불가'이며 절대 발행을 막지 않는다.

**자격증명이 필요한 소스는 하나도 쓰지 않는다.** 셋 다 무인증 공개 엔드포인트다.
**요청에 우리 정보를 싣지 않는다** — 쿼리스트링은 리그·날짜뿐이다.

────────────────────────────────────────────────────────────────────
네이버 응답에서 실제로 확인한 것 (여기를 안 보면 반드시 틀린다)
────────────────────────────────────────────────────────────────────
`statusCode` 도메인 (2026시즌 전수):
    KBO      RESULT 647 · BEFORE 196
    NPB      RESULT 722 · ENDED 33 · BEFORE 137
    K리그1    RESULT 155 · BEFORE 43
    MLB(참고) RESULT 447 · STARTED 2 · READY 2 · BEFORE 107

⚠️ **취소를 `statusCode`로 판정하면 안 된다.**
    KBO 취소 69건은 전부 `statusCode='BEFORE'` + `cancel=true`다 —
    `cancel`을 안 보면 **취소 경기가 '아직 시작 안 함'으로 읽힌다.**
    NPB 취소 33건은 `statusCode='ENDED'` + `cancel=true` + `statusInfo='경기중단'`이고
    그중 1건(20260426JLSF0)은 **점수까지 실려 있다**(0-1, 4회 중단).
    → `cancel`/`suspended`를 **statusCode보다 먼저** 본다. 취소·중단은 '대조 불가'다.

⚠️ **'진행 중' 코드는 MLB에서만 눈으로 봤다**(STARTED 2건, 마침 그 시각에 2경기 진행 중).
    KBO·NPB·K리그1은 같은 API·같은 필드를 쓰므로 같은 도메인으로 본다.
    그래도 **추측에 기대지 않게** 차단 규칙을 짰다 —
    `우리 종료 vs 외부가 종료가 아님`이면 그 코드가 STARTED든 BEFORE든 **똑같이 막는다.**
    처음 보는 코드는 '대조 불가'로 두고 `note()`로 운영에 올린다(조용히 넘기지 않는다).

정상으로 보이지만 실제로 정상인 값 (오탐으로 오해하지 말 것):
    KBO `RESULT`·비취소인데 0:0 — 1건(20260322HTOB0). 강우 콜드 무승부로 실재한다.
    K리그1 `RESULT` 0:0 — 18건. 무득점 무승부다.
"""
from __future__ import annotations

import gzip
import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import Game, GateError, League, Status

# 네이버가 돌려주는 시각 문자열에는 tz가 없다. KBO·NPB·K리그1·LCK 전부
# UTC+9(KST·JST 동일)라서 한 값으로 읽는다. 이 값은 **더블헤더 순서를 세우는 데만**
# 쓴다 — 대조 판정은 시각이 아니라 팀·점수·상태로 한다.
_KST = ZoneInfo("Asia/Seoul")


# ─────────────────────────────────────────────────────────────────────
# 예산 — 5분 시계에서 외부를 두드리는 것이므로 인색하게 잡는다
# ─────────────────────────────────────────────────────────────────────
#
# **결과 카드를 낼 때만 대조한다.** 모닝 카드·시작 알림은 점수가 없으므로 대조할 것도 없다.
# 결과 카드는 하루에 리그당 한두 번이니, 정상 운영에서 외부 요청은
# **(리그 × 날짜)당 1회 / 2분**을 넘지 않는다.
CACHE_TTL_SECONDS = 120.0        # 같은 틱 안의 중복 요청만 없앤다. 이보다 묵으면 버린다.
MIN_GAP_SECONDS = 1.5            # 같은 호스트 연속 호출 최소 간격
HTTP_TIMEOUT = 6.0               # 대조 때문에 틱이 늦어지면 안 된다
HTTP_RETRIES = 1                 # 재시도 1회. 안 되면 '대조 못 함'으로 넘어간다
MAX_TOTAL_SECONDS = 25.0         # check_results() 한 번의 총 예산. 넘으면 남은 리그는 건너뛴다

CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"

_UA = "nudetv-scoreref/1.0 (score cross-check; read-only)"


# ─────────────────────────────────────────────────────────────────────
# 대조용 상태 — 우리 Status와 일부러 분리한다
# ─────────────────────────────────────────────────────────────────────
# 외부 소스의 상태를 우리 `Status`로 번역하면 '취소'와 '연기'와 '모르겠음'이
# 한 값으로 뭉개진다. 대조에 필요한 것은 네 가지뿐이고, 그중 **UNKNOWN은
# 반드시 따로 있어야 한다** — 모르는 것을 아는 것처럼 다루면 잘못 막는다.
class RefStatus(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    OTHER = "other"          # 취소·중단·연기 — 대조 불가 (어긋남이 아니다)
    UNKNOWN = "unknown"      # 처음 보는 상태 코드 — 대조 불가 + 운영 보고


class Severity(str, Enum):
    BLOCK = "block"          # 발행 차단
    WARN = "warn"            # 알림만. 차단하지 않는다


# ─────────────────────────────────────────────────────────────────────
# 어긋남의 종류 — **여기가 이 파일의 핵심이다**
# ─────────────────────────────────────────────────────────────────────
#
#  우리                 외부                 처분        이유
#  ──────────────────────────────────────────────────────────────────────
#  종료·점수 A          종료·점수 B(≠A)      차단        둘 중 하나는 사실이 아니다.
#                                                        어느 쪽이 틀렸는지 우리는 모르므로,
#                                                        모르는 채로 내보내지 않는다.
#  종료                 진행 중              차단        **09-01 사고가 정확히 이것이다.**
#  종료                 시작 전              차단        시작도 안 한 경기의 결과를 냈다.
#                                                        (외부가 '진행 중' 코드를 뭐라고
#                                                         부르든 걸리게 하려고 한 칸에 묶었다)
#  홈·원정이 뒤집힘      —                    차단        점수를 홈:원정으로 인쇄하므로
#                                                        뒤집히면 승패가 뒤집혀 나간다.
#  진행 중              종료                 알림        우리가 늦은 것. 사실 오류가 아니다.
#                                                        (npb.jp가 실제로 이렇다 — 경기 중
#                                                         갱신을 안 한다. 매일 뜬다고 막으면
#                                                         NPB가 매일 침묵한다)
#  시작 전              진행 중·종료          알림        같은 이유로 우리가 늦은 것.
#  무엇이든              취소·중단·미확인      대조 불가    어긋남이 아니다.
#  외부에 그 경기 없음   —                    대조 불가    어긋남이 아니다.
#  팀 매핑 실패          —                    대조 불가    억지로 맞추지 않는다.
#  소스 실패·0건        —                    대조 불가    **절대 차단하지 않는다.**
class MismatchKind(str, Enum):
    SCORE = "score_differs"                  # 차단
    WE_FINAL_THEY_NOT = "we_final_they_not"  # 차단 (09-01)
    ORIENTATION = "home_away_swapped"        # 차단 — 단 아래 예외 리그 제외
    WE_BEHIND = "we_behind"                  # 알림


# **홈·원정이 실제로 존재하는 리그에서만 뒤집힘을 따진다.**
#
# 처음에는 전 리그에 똑같이 걸었다. 실측에서 바로 걸렸다 —
# LCK 최근 3주 13경기가 **13경기 전부** '뒤집힘'으로 차단됐다.
# 원인은 우리 오류가 아니라 **LCK에 홈·원정이 없다는 것**이다. 전 경기가
# 같은 장소(롤파크)에서 열리고, Leaguepedia의 Team1/Team2와 네이버의
# home/away는 둘 다 그냥 표시 순서라 서로 반대로 적힌다.
# 그대로 뒀으면 이 안전장치가 **LCK 결과 카드를 100% 막았을 것이다** —
# 사고를 막으려고 만든 것이 리그를 침묵시키는, 정확히 피하려던 실패다.
#
# 중립 구장 리그는 뒤집힘을 오류로 보지 않고 **외부 점수를 우리 방향에 맞춰 뒤집어**
# 점수만 비교한다(승패 판정은 방향과 무관하게 살아 있다).
NEUTRAL_VENUE_LEAGUES = frozenset({League.LCK, League.INTL_LOL})


SEVERITY: dict[MismatchKind, Severity] = {
    MismatchKind.SCORE: Severity.BLOCK,
    MismatchKind.WE_FINAL_THEY_NOT: Severity.BLOCK,
    MismatchKind.ORIENTATION: Severity.BLOCK,
    MismatchKind.WE_BEHIND: Severity.WARN,
}

# **몇 건이 어긋나야 막나 — 1건이다.**
#
# 비율로 두는 안(예: "30% 넘으면 차단")을 검토했고 버렸다. 비율 기준은
# "몇 건까지는 틀려도 내보낸다"는 뜻이 되는데, 그건 이 파일의 존재 이유와 정반대다.
# 09-01 사고는 5건이었지만 **1건이어도 허위 보도**다 — 카드에 찍힌 한 경기의
# 점수가 틀리면 그 카드는 틀린 카드다.
#
# 오탐 비용이 낮다는 점도 근거다. 틱이 5분마다 돌므로 잘못 막힌 카드는
# 다음 틱에 다시 판정된다. 반대로 잘못 나간 카드는 되돌릴 수 없다.
# (다만 **같은 카드가 계속 막히면** 그건 외부 소스가 우리보다 느린 것일 수 있다.
#  차단 건수는 `skipped_report()`로 운영에 올라가니, 반복되면 사람이 본다.)
BLOCK_THRESHOLD = 1

# 반대로 '우리가 늦음'은 **몇 건이든 막지 않는다.** 건수를 세서 알림에만 싣는다.


@dataclass(frozen=True)
class RefGame:
    """외부 소스가 말하는 경기 하나.

    `home_code`/`away_code`가 None이면 **우리 팀 코드로 매핑하지 못한 것**이다.
    그런 행은 대조에 쓰지 않는다(억지 매칭 금지). 원문 표기는 남겨 둔다 —
    운영이 '무엇을 못 읽었는지' 알아야 매핑 표를 고칠 수 있다.
    """
    league: League
    sports_day: str
    home_code: Optional[str]
    away_code: Optional[str]
    home_score: Optional[int]
    away_score: Optional[int]
    status: RefStatus
    raw_status: str
    raw_home: str
    raw_away: str
    start_utc: Optional[datetime] = None
    source: str = ""

    @property
    def mapped(self) -> bool:
        return bool(self.home_code and self.away_code)


@dataclass(frozen=True)
class Mismatch:
    kind: MismatchKind
    game_id: str
    league: League
    sports_day: str
    ours: str            # 사람이 읽는 우리 쪽 요약
    theirs: str          # 사람이 읽는 외부 쪽 요약
    source: str

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.kind]

    def line(self) -> str:
        return (f"[{self.league.value} {self.sports_day}] {self.game_id} — "
                f"우리 {self.ours} / {self.source} {self.theirs}")


@dataclass(frozen=True)
class Unverifiable:
    """대조하지 못한 경기. **어긋남이 아니다.** 절대 차단하지 않는다."""
    game_id: str
    league: League
    sports_day: str
    reason: str


class RefUnavailable(Exception):
    """외부 소스에 못 닿았다 / 구조가 바뀌었다 / 0건이었다.

    `GateError`를 상속하지 않는다 — 이건 **게이트 실패가 아니다.**
    상속시켜 두면 언젠가 누군가 `except GateError`로 뭉뚱그려 잡아
    외부 소스 점검 때 발행이 멈춘다.
    """


# ─────────────────────────────────────────────────────────────────────
# 팀 매핑 — 외부 표기 → 우리 코드
# ─────────────────────────────────────────────────────────────────────
#
# **표에 없는 코드는 매핑하지 않는다.** 문자열 가공으로 유추하면
# (예: K리그를 'K'+코드로 만들면) 소스가 새 코드를 보낼 때 **없는 팀 코드가
# 조용히 만들어져** 엉뚱한 경기와 짝이 맞는다. 표는 손으로 적고, 빠지면 대조 불가.

# KBO — 네이버 코드가 우리 코드와 **글자까지 같다**(2026시즌 843건 전수 확인).
# 그래도 항등 매핑을 명시적으로 적는다: 'EA'(드림)·'WE'(나눔) 올스타전 두 코드가
# 실제로 섞여 오는데, 표를 안 두면 그것까지 팀 코드로 통과한다.
KBO_TEAMS = {
    "LG": "LG", "OB": "OB", "KT": "KT", "SK": "SK", "NC": "NC",
    "WO": "WO", "HT": "HT", "LT": "LT", "SS": "SS", "HH": "HH",
    # 'EA'(드림) · 'WE'(나눔) = 올스타전. 우리 발행 대상이 아니다 → 일부러 뺀다.
}

# NPB — 네이버 2글자 코드 → 우리 코드 (2026시즌 892건에서 관측된 14개 중 12개)
# 'CL'(센트럴리그) · 'PL'(퍼시픽리그)는 올스타전이라 뺀다.
# 주의: 네이버 NPB의 'SF'는 소프트뱅크다(MLB의 샌프란시스코가 아니다).
#       표를 리그별로 나눠 두는 이유가 이것이다.
NPB_TEAMS = {
    "YO": "YOG",   # 요미우리
    "YK": "DEN",   # 요코하마 DeNA — 네이버는 연고지, 우리는 구단명
    "HS": "HAN",   # 한신
    "JN": "CHU",   # 주니치
    "YA": "YAK",   # 야쿠르트
    "HI": "HIR",   # 히로시마
    "SF": "SOF",   # 소프트뱅크
    "NH": "NIP",   # 닛폰햄
    "JL": "LOT",   # 지바롯데
    "RT": "RAK",   # 라쿠텐
    "OX": "ORI",   # 오릭스
    "SE": "SEI",   # 세이부
}

# K리그1 — 네이버 2자리 숫자 → 우리 코드. 2026시즌 198건에서 12팀 전부 관측.
KL1_TEAMS = {
    "01": "K01",   # 울산
    "03": "K03",   # 포항
    "04": "K04",   # 제주
    "05": "K05",   # 전북
    "09": "K09",   # 서울
    "10": "K10",   # 대전
    "18": "K18",   # 인천
    "21": "K21",   # 강원
    "22": "K22",   # 광주
    "26": "K26",   # 부천
    "27": "K27",   # 안양
    "35": "K35",   # 김천
}

# MLB — ESPN 약칭 → 우리 코드. 2026-08 447경기에서 30팀 전부 확인.
# 다른 것은 화이트삭스 하나뿐이라 항등 매핑이지만, 위와 같은 이유로 전부 적는다.
MLB_TEAMS = {
    "ARI": "ARI", "ATH": "ATH", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CHW": "CWS", "CIN": "CIN", "CLE": "CLE", "COL": "COL",
    "DET": "DET", "HOU": "HOU", "KC": "KC", "LAA": "LAA", "LAD": "LAD",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NYM": "NYM", "NYY": "NYY",
    "PHI": "PHI", "PIT": "PIT", "SD": "SD", "SEA": "SEA", "SF": "SF",
    "STL": "STL", "TB": "TB", "TEX": "TEX", "TOR": "TOR", "WSH": "WSH",
    # ESPN이 옛 약칭을 되돌려 쓰는 경우가 있어 함께 받아둔다(관측되진 않았다).
    "CWS": "CWS", "OAK": "ATH", "WAS": "WSH",
}

# LCK — 네이버 e스포츠 `nameEngAcronym` → 우리 코드.
# 2026시즌 189건에서 관측된 13개 중 10개. 나머지는 일부러 뺀다:
#   'TBD'                        — 대진 미정 자리표시자
#   'CJ rolster'·'Samsung Telecom' — leagueId=lck_2026_event(이벤트 매치)
LCK_TEAMS = {
    "T1": "T1", "GEN": "GEN", "DK": "DK", "KT": "KT", "HLE": "HLE",
    "NS": "NS", "BFX": "BFX", "DNS": "DNS", "BRO": "BRO",
    "KRX": "DRX",   # KIWOOM DRX — 네이밍 스폰서가 붙은 이름. 우리는 DRX 한 코드로 모은다
    "DRX": "DRX",
}


# ─────────────────────────────────────────────────────────────────────
# HTTP — 짧게 두드리고 안 되면 포기한다
# ─────────────────────────────────────────────────────────────────────
_OPENER = make_opener()
_last_call: dict[str, float] = {}


def _get_json(url: str, *, label: str, sleep: Callable[[float], None] = time.sleep,
              monotonic: Callable[[], float] = time.monotonic) -> dict:
    """공개 JSON 하나를 읽는다. 실패는 전부 `RefUnavailable`로 바꿔서 올린다.

    호스트별 최소 간격을 지킨다 — 5분 시계에서 여러 리그가 같은 초에 몰려
    두드리면 차단당한다. 차단당하면 대조가 아니라 **우리가 사라진다.**
    """
    host = urllib.parse.urlsplit(url).netloc
    gap = MIN_GAP_SECONDS - (monotonic() - _last_call.get(host, -1e9))
    if gap > 0:
        sleep(gap)
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Accept": "application/json"})
    try:
        raw = _fetch(_OPENER, req, label=label,
                     timeout=HTTP_TIMEOUT, retries=HTTP_RETRIES, sleep=sleep)
    except GateError as e:
        raise RefUnavailable(str(e)) from e
    except Exception as e:                                   # noqa: BLE001
        # 여기서 새는 예외가 하나라도 있으면 외부 사이트 장애가 발행 장애가 된다.
        raise RefUnavailable(f"{label}: {type(e).__name__} {str(e)[:80]}") from e
    finally:
        _last_call[host] = monotonic()
    if raw[:2] == b"\x1f\x8b":          # ESPN이 상황에 따라 gzip으로 돌려준다
        try:
            raw = gzip.decompress(raw)
        except OSError as e:
            raise RefUnavailable(f"{label}: gzip 해제 실패 {e}") from e
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as e:
        raise RefUnavailable(f"{label}: JSON 아님 ({str(e)[:60]})") from e


# ─────────────────────────────────────────────────────────────────────
# 소스별 파서
# ─────────────────────────────────────────────────────────────────────
#
# 네이버 `statusCode` → RefStatus.
# **`cancel`/`suspended`를 먼저 본다** (파일 첫머리 ⚠️ 참조 — KBO 취소 69건이
# 전부 `BEFORE`로 오고, NPB 취소 33건이 `ENDED`로 온다).
_NAVER_STATUS = {
    "RESULT": RefStatus.FINAL,
    "BEFORE": RefStatus.SCHEDULED,
    "READY": RefStatus.SCHEDULED,     # MLB에서 관측 — '곧 시작'
    "STARTED": RefStatus.LIVE,        # MLB에서 실측(2건)
    "ENDED": RefStatus.OTHER,         # NPB 취소·중단에서만 관측(33/33)
    "CANCEL": RefStatus.OTHER,
    "POSTPONE": RefStatus.OTHER,
}


def _naver_status(row: dict) -> tuple[RefStatus, str]:
    code = str(row.get("statusCode") or "").strip().upper()
    if row.get("cancel") or row.get("suspended"):
        # 취소·중단은 점수가 실려 있어도 대조 대상이 아니다
        # (NPB 20260426JLSF0 — 4회 중단인데 0-1이 실려 있다).
        return RefStatus.OTHER, f"{code}/취소"
    return _NAVER_STATUS.get(code, RefStatus.UNKNOWN), code


def _naver_int(v) -> Optional[int]:
    return int(v) if isinstance(v, (int, float)) else None


def _fetch_naver(league: League, day: str, upper: str, cat: str,
                 teams: dict[str, str], **kw) -> list[RefGame]:
    url = ("https://api-gw.sports.naver.com/schedule/games"
           f"?fields=basic,statusNum&upperCategoryId={upper}&categoryId={cat}"
           f"&fromDate={day}&toDate={day}&size=200")
    data = _get_json(url, label=f"네이버 {cat} {day}", **kw)
    if not data.get("success"):
        raise RefUnavailable(f"네이버 {cat} {day}: success=false (code={data.get('code')})")
    result = data.get("result")
    if not isinstance(result, dict) or "games" not in result:
        # 구조가 바뀌면 조용히 0건이 된다 — 그 길을 막는다.
        raise RefUnavailable(f"네이버 {cat} {day}: 응답에 result.games 없음 "
                             f"(키: {sorted(data)[:5]})")
    out: list[RefGame] = []
    for row in result.get("games") or []:
        st, raw = _naver_status(row)
        hc, ac = str(row.get("homeTeamCode") or ""), str(row.get("awayTeamCode") or "")
        out.append(RefGame(
            league=league, sports_day=str(row.get("gameDate") or day),
            home_code=teams.get(hc), away_code=teams.get(ac),
            home_score=_naver_int(row.get("homeTeamScore")),
            away_score=_naver_int(row.get("awayTeamScore")),
            status=st, raw_status=raw,
            raw_home=f"{hc}({row.get('homeTeamName')})",
            raw_away=f"{ac}({row.get('awayTeamName')})",
            start_utc=_kst_to_utc(row.get("gameDateTime")),
            source="네이버"))
    return out


def _kst_to_utc(s) -> Optional[datetime]:
    """네이버의 tz 없는 현지 시각 문자열 → UTC. 더블헤더 순서를 세우는 데만 쓴다."""
    try:
        return datetime.fromisoformat(str(s)).replace(tzinfo=_KST).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


# ESPN — `status.type`으로 읽는다.
#
# **`state`/`completed`를 쓰는 이유.** ESPN의 `type.name`은 종류가 많고
# (STATUS_RAIN_DELAY·STATUS_DELAYED·STATUS_FORFEIT …) 새 값이 언제든 는다.
# 반면 `state`는 pre/in/post 셋뿐이고 `completed`는 불리언이라 도메인이 닫혀 있다.
# 다만 **취소·연기·서스펜디드는 `state='post'`인데 `completed=false`**라서
# 이름을 먼저 걸러야 한다. 그 순서를 뒤집으면 연기된 경기가 '진행 중'이 된다.
_ESPN_NOT_PLAYED = {
    "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED",
    "STATUS_SUSPENDED", "STATUS_FORFEIT", "STATUS_ABANDONED",
}


def _espn_status(t: dict) -> tuple[RefStatus, str]:
    name = str(t.get("name") or "").strip().upper()
    if name in _ESPN_NOT_PLAYED:
        return RefStatus.OTHER, name
    if t.get("completed") is True:
        return RefStatus.FINAL, name
    state = str(t.get("state") or "").strip().lower()
    if state == "in":
        return RefStatus.LIVE, name
    if state == "pre":
        return RefStatus.SCHEDULED, name
    return RefStatus.UNKNOWN, name or "(빈 상태)"


def _espn_int(v) -> Optional[int]:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _fetch_espn_mlb(league: League, day: str, **kw) -> list[RefGame]:
    # ESPN은 `dates=YYYYMMDD`를 **미국 현지 날짜**로 묶는다.
    # 우리 MLB `sports_day`는 statsapi의 `officialDate`(홈 현지 캘린더 날짜)이므로
    # 같은 기준이다 — 2026-09-01 실측에서 양쪽 다 15경기로 맞았다.
    url = ("https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
           f"?dates={day.replace('-', '')}&limit=200")
    data = _get_json(url, label=f"ESPN MLB {day}", **kw)
    if "events" not in data:
        raise RefUnavailable(f"ESPN MLB {day}: 응답에 events 없음 (키: {sorted(data)[:5]})")
    out: list[RefGame] = []
    for ev in data.get("events") or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        comp = comps[0]
        sides = {str(c.get("homeAway")): c for c in comp.get("competitors") or []}
        h, a = sides.get("home"), sides.get("away")
        if not h or not a:
            continue
        st, raw = _espn_status(((ev.get("status") or {}).get("type")) or {})
        ha = str((h.get("team") or {}).get("abbreviation") or "")
        aa = str((a.get("team") or {}).get("abbreviation") or "")
        out.append(RefGame(
            league=league, sports_day=day,
            home_code=MLB_TEAMS.get(ha), away_code=MLB_TEAMS.get(aa),
            home_score=_espn_int(h.get("score")), away_score=_espn_int(a.get("score")),
            status=st, raw_status=raw,
            raw_home=ha or "(약칭 없음)", raw_away=aa or "(약칭 없음)",
            start_utc=_espn_time(ev.get("date")), source="ESPN"))
    return out


def _espn_time(s) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


# ── LCK 한계 ────────────────────────────────────────────────────────
#
# 네이버 e스포츠는 **월 단위로만** 준다(`schedule/month`). 하루치 요청이 없어
# 한 달을 받아 그날만 고른다 — 응답이 크지만(2026-09 8건, 8월 40건) 결과 카드를
# 낼 때만 부르므로 예산 안이다.
#
# **관측하지 못한 것**: 2026-01~09 전 구간에서 `matchStatus`는
# `RESULT` 183 · `BEFORE` 6뿐이었다. **'진행 중' 코드를 한 번도 못 봤다.**
# 그래서 LCK는 "외부가 종료라고 말할 때의 점수 대조"까지만 신뢰한다.
# 처음 보는 코드는 UNKNOWN(=대조 불가)으로 두고 운영에 올린다 —
# 다만 차단 규칙이 `우리 종료 vs 외부가 종료 아님`이라, 그 코드가
# BEFORE로 오든 처음 보는 값으로 오든 **UNKNOWN만 아니면 걸린다.**
# UNKNOWN으로 새는 경우가 있을 수 있다는 것이 LCK의 남은 한계다.
_LCK_STATUS = {
    "RESULT": RefStatus.FINAL,
    "BEFORE": RefStatus.SCHEDULED,
    "READY": RefStatus.SCHEDULED,
    "STARTED": RefStatus.LIVE,     # 다른 네이버 API의 도메인. e스포츠에서는 미관측.
    "CANCEL": RefStatus.OTHER,
}


def _fetch_naver_lck(league: League, day: str, **kw) -> list[RefGame]:
    url = ("https://esports-api.game.naver.com/service/v1/schedule/month"
           f"?month={day[:7]}&topLeagueId=lck")
    data = _get_json(url, label=f"네이버 e스포츠 LCK {day[:7]}", **kw)
    if "content" not in data:
        raise RefUnavailable(f"네이버 LCK {day[:7]}: 응답에 content 없음 "
                             f"(키: {sorted(data)[:5]})")
    out: list[RefGame] = []
    for row in data.get("content") or []:
        start = _epoch_ms_to_utc(row.get("startDate"))
        if start is None:
            continue
        # LCK의 하루 = KST 캘린더 날짜 (lck.py의 sports_day와 같은 기준)
        kst_day = start.astimezone(_KST).strftime("%Y-%m-%d")
        if kst_day != day:
            continue
        code = str(row.get("matchStatus") or "").strip().upper()
        st = _LCK_STATUS.get(code, RefStatus.UNKNOWN)
        ht, at = row.get("homeTeam") or {}, row.get("awayTeam") or {}
        ha = str(ht.get("nameEngAcronym") or "")
        aa = str(at.get("nameEngAcronym") or "")
        out.append(RefGame(
            league=league, sports_day=kst_day,
            home_code=LCK_TEAMS.get(ha), away_code=LCK_TEAMS.get(aa),
            home_score=_naver_int(row.get("homeScore")),
            away_score=_naver_int(row.get("awayScore")),
            status=st, raw_status=code,
            raw_home=ha or str(ht.get("nameEng") or "?"),
            raw_away=aa or str(at.get("nameEng") or "?"),
            start_utc=start, source="네이버e스포츠"))
    return out


def _epoch_ms_to_utc(v) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(float(v) / 1000.0, timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


# 리그 → 대조 소스. **여기 없는 리그는 언제나 '대조 불가'이며 절대 막지 않는다.**
PROVIDERS: dict[League, Callable[..., list[RefGame]]] = {
    League.KBO: lambda day, **kw: _fetch_naver(
        League.KBO, day, "kbaseball", "kbo", KBO_TEAMS, **kw),
    League.NPB: lambda day, **kw: _fetch_naver(
        League.NPB, day, "wbaseball", "npb", NPB_TEAMS, **kw),
    League.KL1: lambda day, **kw: _fetch_naver(
        League.KL1, day, "kfootball", "kleague", KL1_TEAMS, **kw),
    League.MLB: lambda day, **kw: _fetch_espn_mlb(League.MLB, day, **kw),
    League.LCK: lambda day, **kw: _fetch_naver_lck(League.LCK, day, **kw),
}

SOURCE_NAME: dict[League, str] = {
    League.KBO: "네이버 스포츠(kbo)",
    League.NPB: "네이버 스포츠(npb)",
    League.KL1: "네이버 스포츠(kleague)",
    League.MLB: "ESPN(mlb)",
    League.LCK: "네이버 e스포츠(lck)",
}


# ─────────────────────────────────────────────────────────────────────
# 판정 결과
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ScoreRefVerdict:
    compared: int = 0                                  # 실제로 대조에 성공한 경기 수
    agreed: int = 0                                    # 그중 어긋나지 않은 수
    # **점수까지 맞춰 본 것만 따로 센다.** 전체 일치율은 '우리가 늦음'(WE_BEHIND)에
    # 끌려간다 — npb.jp는 경기 중 갱신을 안 해서 매일 밤 5~6건이 여기 걸린다.
    # 그 값을 품질 지표로 쓰면 "NPB 때문에 일치율 60%"라는 무의미한 숫자가 나오고,
    # 진짜 점수 오류가 그 소음에 묻힌다. 사실 오류를 재는 자는 아래쪽이다.
    score_compared: int = 0                            # 양쪽 다 '종료'라서 점수를 맞춰 본 수
    score_agreed: int = 0                              # 그중 점수가 같았던 수
    mismatches: list[Mismatch] = field(default_factory=list)
    unverifiable: list[Unverifiable] = field(default_factory=list)
    # (리그, 날짜) → 소스 상태 문장. 실패 사유가 여기 남는다.
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def blocking(self) -> list[Mismatch]:
        return [m for m in self.mismatches if m.severity is Severity.BLOCK]

    @property
    def warnings(self) -> list[Mismatch]:
        return [m for m in self.mismatches if m.severity is Severity.WARN]

    @property
    def blocked(self) -> bool:
        """**tick이 보는 값.** True면 이번 결과 카드를 내보내지 않는다."""
        return len(self.blocking) >= BLOCK_THRESHOLD

    @property
    def agreement_rate(self) -> float:
        """대조에 성공한 것 중 일치 비율. 대조가 0건이면 0.0이 아니라 -1.0이다.

        0.0으로 두면 '전부 틀렸다'와 '대조 못 했다'가 같은 값이 된다 —
        이 파일이 통째로 막으려는 혼동이 정확히 그것이다.
        """
        return (self.agreed / self.compared) if self.compared else -1.0

    @property
    def score_agreement_rate(self) -> float:
        """양쪽이 모두 '종료'라고 말한 경기의 점수 일치율. 0건이면 -1.0."""
        return (self.score_agreed / self.score_compared) if self.score_compared else -1.0

    @property
    def block_reason(self) -> str:
        if not self.blocked:
            return ""
        head = self.blocking[0]
        more = f" 외 {len(self.blocking) - 1}건" if len(self.blocking) > 1 else ""
        return f"외부 대조 불일치: {head.line()}{more}"

    def lines(self) -> list[str]:
        """운영 알림에 그대로 넣는 사람 읽는 요약."""
        out: list[str] = []
        rate = ("대조 0건" if self.compared == 0
                else f"대조 {self.compared}건 · 일치 {self.agreed}건 "
                     f"({self.agreement_rate * 100:.1f}%)")
        if self.score_compared:
            rate += (f" · 점수 대조 {self.score_compared}건 "
                     f"({self.score_agreement_rate * 100:.1f}%)")
        out.append(rate)
        for m in self.blocking:
            out.append(f"차단 · {m.line()}")
        for m in self.warnings:
            out.append(f"알림 · {m.line()}")
        if self.unverifiable:
            u = self.unverifiable[0]
            out.append(f"대조 불가 {len(self.unverifiable)}건 "
                       f"예) {u.game_id} — {u.reason}")
        for k, v in sorted(self.sources.items()):
            out.append(f"소스 {k}: {v}")
        return out


# ─────────────────────────────────────────────────────────────────────
# 본체
# ─────────────────────────────────────────────────────────────────────
class ScoreReference(NoticeMixin):
    """리그·날짜를 주면 그날 경기의 (팀, 점수, 종료 여부)를 돌려주는 대조용 소스.

    `NoticeMixin`을 쓴다 — 대조하지 못한 것·매핑 못 한 팀·죽은 소스가
    조용히 사라지면 이 안전장치는 '항상 통과'로 굳는다.
    (LCK가 48시간 묵은 캐시로 렌더하던 그 사고의 재발 경로다.)
    """

    def __init__(self, *, cache_dir: Optional[pathlib.Path] = None,
                 cache_ttl: float = CACHE_TTL_SECONDS,
                 providers: Optional[dict] = None,
                 now: Callable[[], float] = time.time,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else CACHE_DIR
        self.cache_ttl = float(cache_ttl)
        self.providers = PROVIDERS if providers is None else providers
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._mem: dict[tuple[str, str], tuple[float, list[RefGame]]] = {}

    # ── 캐시 ────────────────────────────────────────────────────
    #
    # **묵은 캐시로 버티지 않는다.** `lck.py`와 정반대다.
    # 저기는 '일정이 통째로 빠지는 것'을 막는 캐시라 36시간까지 버티지만,
    # 여기 캐시가 묵으면 **방금 끝난 경기가 전부 '외부는 진행 중'으로 보여
    # 정상 카드를 막는다.** 안전장치가 사고를 만드는 길이라 아예 끊었다.
    # TTL(120초)의 목적은 오로지 '같은 틱 안에서 두 번 안 부르기'다.
    def _cache_file(self, league: League, day: str) -> pathlib.Path:
        return self.cache_dir / f"scoreref_{league.value}_{day}.json"

    def _cache_get(self, league: League, day: str) -> Optional[list[RefGame]]:
        hit = self._mem.get((league.value, day))
        if hit and (self._monotonic() - hit[0]) <= self.cache_ttl:
            return hit[1]
        path = self._cache_file(league, day)
        try:
            age = self._now() - path.stat().st_mtime
            if age > self.cache_ttl:
                return None
            rows = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        try:
            return [RefGame(league=League(r["league"]), sports_day=r["sports_day"],
                            home_code=r["home_code"], away_code=r["away_code"],
                            home_score=r["home_score"], away_score=r["away_score"],
                            status=RefStatus(r["status"]), raw_status=r["raw_status"],
                            raw_home=r["raw_home"], raw_away=r["raw_away"],
                            start_utc=(datetime.fromisoformat(r["start_utc"])
                                       if r.get("start_utc") else None),
                            source=r["source"]) for r in rows]
        except (KeyError, TypeError, ValueError):
            return None                       # 형식이 바뀐 캐시는 없는 것으로 친다

    def _cache_put(self, league: League, day: str, rows: list[RefGame]) -> None:
        self._mem[(league.value, day)] = (self._monotonic(), rows)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_file(league, day).write_text(json.dumps([{
                "league": r.league.value, "sports_day": r.sports_day,
                "home_code": r.home_code, "away_code": r.away_code,
                "home_score": r.home_score, "away_score": r.away_score,
                "status": r.status.value, "raw_status": r.raw_status,
                "raw_home": r.raw_home, "raw_away": r.raw_away,
                "start_utc": r.start_utc.isoformat() if r.start_utc else None,
                "source": r.source} for r in rows], ensure_ascii=False))
        except OSError:
            pass                               # 캐시 쓰기 실패는 대조 실패가 아니다
        self._prune()

    def _prune(self, max_age: float = 3600.0) -> None:
        """묵은 대조 캐시를 지운다.

        TTL이 120초라 1시간 지난 파일은 **영원히 안 읽힌다.** 그런데 파일명이
        (리그, 날짜)라서 지우지 않으면 시즌 내내 수천 개가 쌓인다.
        `lck.py` 캐시(`lck_*.json`)는 건드리지 않는다 — 그쪽은 36시간까지 쓴다.
        """
        try:
            now = self._now()
            for p in self.cache_dir.glob("scoreref_*.json"):
                if now - p.stat().st_mtime > max_age:
                    p.unlink()
        except OSError:
            pass

    # ── 공개: 하루치 대조 데이터 ─────────────────────────────────
    def games_for(self, league: League, sports_day: str) -> list[RefGame]:
        """그 리그·그 날의 외부 경기 목록. 못 가져오면 `RefUnavailable`."""
        fn = self.providers.get(league)
        if fn is None:
            raise RefUnavailable(f"{league.value}: 대조 소스 없음")
        cached = self._cache_get(league, sports_day)
        if cached is not None:
            return cached
        rows = fn(sports_day, sleep=self._sleep, monotonic=self._monotonic)
        if not rows:
            # **0건은 항상 의심한다.** 구조가 바뀌거나 파라미터가 틀리면
            # 200 + 빈 배열로 온다(ESPN soccer/kor.1이 실제로 그렇다).
            # 다만 '의심'은 '차단'이 아니다 — 대조 못 한 것으로 처리된다.
            raise RefUnavailable(f"{league.value} {sports_day}: 외부 0건 — 0건은 항상 의심")
        self._cache_put(league, sports_day, rows)
        return rows

    # ── 공개: 발행 직전 대조 ────────────────────────────────────
    def check(self, games: Iterable[Game], *,
              deadline_seconds: float = MAX_TOTAL_SECONDS) -> ScoreRefVerdict:
        """**결과 카드를 내기 직전에 부른다.** 예외를 던지지 않는다.

        `deadline_seconds`는 이 호출 전체의 예산이다. 넘으면 남은 리그는
        대조하지 않고 '대조 못 함'으로 남긴다 — 대조 때문에 틱이 밀리면
        그 자체가 발송 지연 사고다.
        """
        self.reset_notices()
        v = ScoreRefVerdict()
        started = self._monotonic()

        by_day: dict[tuple[League, str], list[Game]] = {}
        for g in games:
            by_day.setdefault((g.league, g.sports_day), []).append(g)

        for (league, day), ours in sorted(by_day.items(),
                                          key=lambda kv: (kv[0][0].value, kv[0][1])):
            tag = f"{league.value} {day}"
            if league not in self.providers:
                for g in ours:
                    v.unverifiable.append(Unverifiable(
                        g.game_id, league, day, "이 리그는 대조 소스가 없다"))
                v.sources[tag] = "대조 소스 없음"
                # V리그·KBL은 대조 소스가 없다 — 구조적이라 사람이 할 일이 없다.
                self.note_info("대조 소스가 없는 리그", tag, count=len(ours))
                continue
            if self._monotonic() - started > deadline_seconds:
                for g in ours:
                    v.unverifiable.append(Unverifiable(
                        g.game_id, league, day, "대조 예산(시간) 초과"))
                v.sources[tag] = f"예산 {deadline_seconds:.0f}초 초과로 건너뜀"
                self.note("대조 예산 초과로 건너뜀", tag, count=len(ours))
                continue
            try:
                refs = self.games_for(league, day)
            except (RefUnavailable, OSError) as e:
                # **여기가 3번 원칙이 사는 자리다.** 소스가 죽어도 막지 않는다.
                # `OSError`를 함께 잡는 이유: 타임아웃·연결 끊김이 `_get_json`을
                # 거치지 않고 새어 나오면 아래 '대조기 예외'로 분류돼
                # **외부 사이트 장애가 우리 버그로 보고된다.** 운영이 엉뚱한 곳을 판다.
                # (TimeoutError·ConnectionResetError는 전부 OSError의 하위다)
                for g in ours:
                    v.unverifiable.append(Unverifiable(
                        g.game_id, league, day, f"외부 소스 실패: {str(e)[:90]}"))
                v.sources[tag] = f"실패 — {str(e)[:120]}"
                self.note("외부 소스 실패(차단 아님)", f"{tag}: {str(e)[:90]}")
                continue
            except Exception as e:                                    # noqa: BLE001
                # 파서에서 예상 못 한 예외가 나도 발행을 막지 않는다.
                for g in ours:
                    v.unverifiable.append(Unverifiable(
                        g.game_id, league, day,
                        f"대조기 예외: {type(e).__name__} {str(e)[:70]}"))
                v.sources[tag] = f"대조기 예외 — {type(e).__name__} {str(e)[:90]}"
                self.note("대조기 예외(차단 아님)", f"{tag}: {type(e).__name__}")
                continue

            v.sources[tag] = f"{SOURCE_NAME.get(league, '외부')} {len(refs)}건"
            self._compare_day(league, day, ours, refs, v)

        for m in v.mismatches:
            self.note(f"{m.kind.value}({m.severity.value})", m.line())
        if v.unverifiable:
            self.note("대조 불가", v.unverifiable[0].reason,
                      count=len(v.unverifiable))
        self.note_text_info("대조 요약",
                       f"{v.compared}건 대조 · {v.agreed}건 일치 · "
                       f"점수 대조 {v.score_compared}건({v.score_agreed}건 일치) · "
                       f"차단 {len(v.blocking)} · 알림 {len(v.warnings)} · "
                       f"불가 {len(v.unverifiable)}")
        return v

    # ── 짝짓기 ──────────────────────────────────────────────────
    #
    # **키는 (그 날, 두 팀의 집합)이다.** 홈·원정을 키에 넣으면 홈·원정이
    # 뒤집힌 경우가 '외부에 그 경기 없음'(=대조 불가)으로 조용히 새어나간다 —
    # 뒤집힘은 승패가 뒤집혀 나가는 심각한 오류라 반드시 잡혀야 한다.
    #
    # 더블헤더(같은 날 같은 두 팀이 두 경기)는 양쪽을 **시작 시각 순으로 세워
    # 순서대로 짝짓는다.** 짝이 없는 쪽은 대조 불가로 남긴다.
    @staticmethod
    def _pair_key(a: str, b: str) -> tuple[str, str]:
        return tuple(sorted((a, b)))                              # type: ignore[return-value]

    def _compare_day(self, league: League, day: str,
                     ours: list[Game], refs: list[RefGame],
                     v: ScoreRefVerdict) -> None:
        unmapped = [r for r in refs if not r.mapped]
        if unmapped:
            self.note("외부 팀 표기를 우리 코드로 매핑 못 함",
                      f"{league.value} {unmapped[0].raw_home} vs {unmapped[0].raw_away}",
                      count=len(unmapped))

        idx: dict[tuple[str, str], list[RefGame]] = {}
        for r in refs:
            if not r.mapped:
                continue                     # 억지로 맞추지 않는다 (원칙 2)
            idx.setdefault(self._pair_key(r.home_code, r.away_code), []).append(r)
        for lst in idx.values():
            lst.sort(key=lambda r: (r.start_utc or datetime.min.replace(tzinfo=timezone.utc)))

        taken: dict[tuple[str, str], int] = {}
        for g in sorted(ours, key=lambda x: (x.start_utc, x.source_key)):
            key = self._pair_key(g.home.team_code, g.away.team_code)
            lst = idx.get(key) or []
            i = taken.get(key, 0)
            if i >= len(lst):
                reason = ("외부 일정에 그 경기가 없다"
                          if not unmapped else
                          f"외부 일정에 그 경기가 없다 (매핑 못 한 외부 경기 {len(unmapped)}건 있음 "
                          f"— 억지 매칭 금지)")
                v.unverifiable.append(Unverifiable(g.game_id, league, day, reason))
                continue
            taken[key] = i + 1
            self._compare_one(g, lst[i], v)

    # ── 한 경기 대조 ────────────────────────────────────────────
    def _compare_one(self, g: Game, r: RefGame, v: ScoreRefVerdict) -> None:
        src = SOURCE_NAME.get(g.league, "외부")
        day = g.sports_day

        def unver(reason: str) -> None:
            v.unverifiable.append(Unverifiable(g.game_id, g.league, day, reason))

        def bad(kind: MismatchKind, ours: str, theirs: str) -> None:
            v.compared += 1
            v.mismatches.append(Mismatch(kind, g.game_id, g.league, day,
                                         ours, theirs, src))

        # 외부가 '모르겠다'고 말하는 것은 어긋남이 아니다.
        if r.status is RefStatus.OTHER:
            unver(f"외부가 취소·중단으로 표기({r.raw_status})")
            return
        if r.status is RefStatus.UNKNOWN:
            self.note("처음 보는 외부 상태 코드", f"{g.league.value} {r.raw_status!r}")
            unver(f"외부 상태 코드를 모른다({r.raw_status!r})")
            return

        # 홈·원정 뒤집힘 — 점수보다 먼저 본다. 뒤집힌 채로 점수를 비교하면
        # '점수 불일치'로 보고돼 원인이 가려진다.
        # 중립 구장 리그(LCK 등)는 방향이 표시 순서일 뿐이므로 맞춰서 읽는다.
        their_home, their_away = r.home_score, r.away_score
        if r.home_code != g.home.team_code:
            if g.league not in NEUTRAL_VENUE_LEAGUES:
                bad(MismatchKind.ORIENTATION,
                    f"홈 {g.home.team_code} / 원정 {g.away.team_code}",
                    f"홈 {r.home_code} / 원정 {r.away_code}")
                return
            their_home, their_away = r.away_score, r.home_score
            # LoL은 홈이 없어 표시 순서가 소스마다 다르다 — 맞춰 읽으면 끝이다.
            self.note_info("중립 구장 리그의 표시 순서가 반대 — 점수를 맞춰 읽음",
                      f"{g.league.value} {g.game_id}")

        # 우리가 종결하지 않은 경기(취소·연기·중단)는 대조 대상이 아니다.
        if g.status in (Status.CANCELED, Status.POSTPONED, Status.SUSPENDED):
            unver(f"우리가 {g.status.value}로 종결 — 대조 대상 아님")
            return

        if g.status is Status.FINAL:
            if r.status is not RefStatus.FINAL:
                # **09-01 사고가 정확히 이 칸이다.**
                # 외부가 LIVE든 SCHEDULED든 한 칸으로 묶는다 — '진행 중' 코드를
                # 소스마다 다르게 부르더라도 걸리게 하려는 것이다.
                bad(MismatchKind.WE_FINAL_THEY_NOT,
                    f"종료 {_score_text(g)}",
                    f"{r.status.value}({r.raw_status})")
                return
            if g.score is None or their_home is None or their_away is None:
                unver("양쪽 중 한쪽에 점수가 없다")
                return
            v.compared += 1
            v.score_compared += 1
            if (g.score.home, g.score.away) == (their_home, their_away):
                v.agreed += 1
                v.score_agreed += 1
            else:
                v.mismatches.append(Mismatch(
                    MismatchKind.SCORE, g.game_id, g.league, day,
                    f"종료 {g.score.home}:{g.score.away}",
                    f"종료 {their_home}:{their_away}", src))
            return

        # 여기부터는 우리가 아직 종결하지 않은 경기다.
        #
        # **진행 중 점수는 비교하지 않는다.** 두 소스를 몇 초 차이로 읽으므로
        # 이닝 하나 차이로 늘 다르다. 그걸 불일치로 세면 경보가 소음이 되고,
        # 소음이 되면 진짜 09-01이 묻힌다.
        if g.status is Status.LIVE:
            if r.status is RefStatus.FINAL:
                bad(MismatchKind.WE_BEHIND, "진행 중", f"종료 {their_home}:{their_away}")
            else:
                v.compared += 1
                v.agreed += 1
            return

        if g.status is Status.SCHEDULED:
            if r.status in (RefStatus.LIVE, RefStatus.FINAL):
                bad(MismatchKind.WE_BEHIND, "예정", f"{r.status.value}")
            else:
                v.compared += 1
                v.agreed += 1
            return

        unver(f"우리 상태 {g.status.value}는 대조 규칙이 없다")


def _score_text(g: Game) -> str:
    return f"{g.score.home}:{g.score.away}" if g.score else "점수 없음"


# 모듈 공유 인스턴스 — 캐시와 호스트 간격을 프로세스 전체에서 공유한다.
# tick이 리그마다 새로 만들면 최소 간격이 무의미해진다.
SCOREREF = ScoreReference()


def check_results(games: Iterable[Game], *,
                  ref: Optional[ScoreReference] = None,
                  deadline_seconds: float = MAX_TOTAL_SECONDS) -> ScoreRefVerdict:
    """**tick이 결과 카드를 내보내기 직전에 부르는 함수.**

        v = check_results(games_of_this_result_card)
        if v.blocked:
            ...발행 보류 + DM(v.block_reason, v.lines())...
        else:
            ...발행... (v.warnings·v.unverifiable은 알림에만)

    예외를 던지지 않는다. 외부가 죽어도 `v.blocked`는 False다.
    """
    r = ref or SCOREREF
    try:
        return r.check(games, deadline_seconds=deadline_seconds)
    except Exception as e:                                        # noqa: BLE001
        # 최후의 그물. 이 모듈의 어떤 버그도 발행을 막지 못하게 한다.
        v = ScoreRefVerdict()
        v.sources["대조기"] = f"예외 — {type(e).__name__} {str(e)[:120]}"
        return v
