"""
공통 스키마 계약 (Contract) — 스포츠 콘텐츠 자동 발행 시스템 v1.9

이 파일이 계약이다. 파이프라인(선발·렌더·발송·틱)은 리그를 모르고, 어디서 도는지도 모른다.
계약만 안다.

v1.9 개정 — 2차 적대적 검수(2026-08-26)에서 재현된 결함 13건을 수정.
  · sports_day를 KST 고정 오프셋 → 현지 캘린더 날짜 (MLB 슬레이트가 반으로 쪼개지던 문제)
  · choose_send_method 균등 분배 (11장이 10+1로 갈려 1장짜리 앨범 API 400)
  · gender 값 일치 검증 (존재 검사만 하던 '이중검증')
  · is_draw가 aggregate 반영 (UCL 2차전 합산승리가 '무승부'로 나가던 문제)
  · parse_status / UnknownStatus 실제 발생 경로
  · 상태 전이 4종 추가 · validate 5종 추가 · 리그 딕셔너리 완전성 assert
  · 발송 기록에 claimed_by·league·sports_day·retry_count·poll_result
  · QueueItem 신설 · LEASE_SECONDS(< 유예) 신설 · assert_sendable 신설
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


# ─────────────────────────────────────────────────────────────────────
# 리그
# ─────────────────────────────────────────────────────────────────────

class League(str, Enum):
    KBO = "KBO"
    KBL = "KBL"
    VLEAGUE_M = "VLEAGUE_M"
    VLEAGUE_W = "VLEAGUE_W"
    KL1 = "KL1"                      # 보류: 미래 일정 소스 없음
    LCK = "LCK"                      # 보류: 라이엇 공식 API 미검증
    INTL_LOL = "INTL_LOL"
    MLB = "MLB"
    NPB = "NPB"
    EPL = "EPL"
    LALIGA = "LALIGA"
    SERIEA = "SERIEA"
    BUNDESLIGA = "BUNDESLIGA"
    LIGUE1 = "LIGUE1"
    UCL = "UCL"


class ScoreUnit(str, Enum):
    RUNS = "runs"
    GOALS = "goals"
    POINTS = "points"
    SETS = "sets"
    MAPS = "maps"


SCORE_UNIT_BY_LEAGUE: dict[League, ScoreUnit] = {
    League.KBO: ScoreUnit.RUNS, League.MLB: ScoreUnit.RUNS, League.NPB: ScoreUnit.RUNS,
    League.KBL: ScoreUnit.POINTS,
    League.VLEAGUE_M: ScoreUnit.SETS, League.VLEAGUE_W: ScoreUnit.SETS,
    League.LCK: ScoreUnit.MAPS, League.INTL_LOL: ScoreUnit.MAPS,
    League.KL1: ScoreUnit.GOALS, League.EPL: ScoreUnit.GOALS,
    League.LALIGA: ScoreUnit.GOALS, League.SERIEA: ScoreUnit.GOALS,
    League.BUNDESLIGA: ScoreUnit.GOALS, League.LIGUE1: ScoreUnit.GOALS,
    League.UCL: ScoreUnit.GOALS,
}

# 스코어 상한 — 파싱 오류가 그대로 카드에 인쇄되는 것을 막는다
SCORE_MAX_BY_UNIT: dict[ScoreUnit, int] = {
    ScoreUnit.RUNS: 50, ScoreUnit.GOALS: 20, ScoreUnit.POINTS: 250,
    ScoreUnit.SETS: 3, ScoreUnit.MAPS: 3,
}

# 시즌 표기 형식 — 자유 문자열이면 표기가 한 번만 흔들려도 game_id가 바뀌어
# 이미 발송된 콘텐츠가 전부 미발송으로 재판정된다(= 전량 중복 발송)
SEASON_SINGLE_YEAR = re.compile(r"^\d{4}$")
SEASON_SPAN_YEAR = re.compile(r"^\d{4}-\d{2}$")
SEASON_FORMAT_BY_LEAGUE: dict[League, re.Pattern] = {
    League.KBO: SEASON_SINGLE_YEAR, League.MLB: SEASON_SINGLE_YEAR,
    League.NPB: SEASON_SINGLE_YEAR, League.KL1: SEASON_SINGLE_YEAR,
    League.LCK: SEASON_SINGLE_YEAR, League.INTL_LOL: SEASON_SINGLE_YEAR,
    League.KBL: SEASON_SPAN_YEAR, League.VLEAGUE_M: SEASON_SPAN_YEAR,
    League.VLEAGUE_W: SEASON_SPAN_YEAR, League.EPL: SEASON_SPAN_YEAR,
    League.LALIGA: SEASON_SPAN_YEAR, League.SERIEA: SEASON_SPAN_YEAR,
    League.BUNDESLIGA: SEASON_SPAN_YEAR, League.LIGUE1: SEASON_SPAN_YEAR,
    League.UCL: SEASON_SPAN_YEAR,
}

GENDER_BY_LEAGUE: dict[League, str] = {
    League.VLEAGUE_M: "1",
    League.VLEAGUE_W: "2",
}


# ─────────────────────────────────────────────────────────────────────
# 상태 머신
# ─────────────────────────────────────────────────────────────────────

class Status(str, Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINAL = "final"
    CANCELED = "canceled"
    POSTPONED = "postponed"
    SUSPENDED = "suspended"


TERMINAL_STATUSES = frozenset({Status.FINAL, Status.CANCELED, Status.POSTPONED})


# ─────────────────────────────────────────────────────────────────────
# 소스 커버리지 경계 (v1.11c 신설)
# ─────────────────────────────────────────────────────────────────────
# "이 소스는 이 구간의 **결과**를 제공하지 않는다"를 계약 사실로 못박는다.
#
# 어댑터가 소스의 소관 밖까지 상태를 번역하면, '결과가 없다'가 '아직 안 끝났다'로
# 둔갑한다. 그러면 몇 달 묵은 경기가 오늘의 모닝 카드에 '오늘 예정'으로 실린다 —
# KBO 취소 7건 누락(그라운드사정)과 완전히 같은 계열의 사실 오류다.
#
# 실측 근거 (2026-08-28, KBL API 375행 전수 대조):
#   R 270 · PO 18 · CP 6 · AS 2 · D1 66  → 373/373 전부 isEnded=1,
#                                          gameEnd·gameTime·score 모두 채워짐
#   EA(EASL) 13                          → 13/13 전부 isEnded=0,
#                                          gameEnd='' · gameTime='' · score 0-0
#   2025-10-22 경기가 10개월 뒤에도 0-0이다. 지연이 아니라 소관 밖이다
#   (EASL 결과는 주최측이 관리하고 KBL은 편성표만 싣는다).
#
# 이 표에 오른 구간은 **편성(예정)은 신뢰하고 결과는 신뢰하지 않는다.**
# 미래 경기는 정상 발송하고(정보 누락 금지), 시작시각이 지난 것만 격리한다.
SOURCE_RESULTLESS_CATEGORIES: dict[League, frozenset[str]] = {
    League.KBL: frozenset({"EA"}),        # EASL — 국내 구단이 나가지만 결과는 KBL 소관 밖
}

# 경기 시작 후 이 시간이 지나면 어떤 종목도 끝나 있다(연장·중단 포함 넉넉히 잡은 값).
# 이 시간이 지나도 '예정'이면 소스가 종결을 안 찍은 것이므로 사람이 봐야 한다.
STALE_SCHEDULED_GRACE_SECONDS = 6 * 3600

# **소스가 결과를 늦게 올리는 리그는 유예를 따로 준다.**
# 2026-08-28 실측: NPB 상세 일정 페이지는 그날 경기가 끝나고 6.9시간이 지나도
# 점수 칸이 비어 있었다(8/27 경기는 채워져 있었다). 즉 결함이 아니라 **갱신 지연**이다.
# 여기를 6시간으로 두면 매일 밤 NPB 6경기가 경보로 뜨고, 그 소음에 진짜 결함이 묻힌다.
# 반대로 무한정 기다리면 KBL EASL 같은 '영원히 안 채워지는' 경우를 못 잡는다.
# 그래서 리그별로 '이 소스가 이만큼은 늦는다'를 적어둔다.
STALE_GRACE_BY_LEAGUE: dict[League, int] = {
    League.NPB: 18 * 3600,      # 상세 일정 페이지가 익일 갱신. 관측되면 줄인다.
}


def stale_grace_for(league: "League") -> int:
    return STALE_GRACE_BY_LEAGUE.get(league, STALE_SCHEDULED_GRACE_SECONDS)


# **리그가 실제로 경기를 하는 달** (1~12). 커버리지 감시가 이걸로 판단한다.
#
# 감시의 목적은 "리그가 조용히 사라진 것"을 잡는 것인데, 비시즌의 0건까지 경보로 올리면
# 8월마다 농구·배구가 경보를 띄우고 그 소음에 진짜 사고가 묻힌다.
# 반대로 이 표가 없으면 "시즌 중인데 0건"을 정상으로 넘긴다 —
# NPB 취소 0건, Leaguepedia 0건이 그렇게 지나갈 뻔했다.
#
# 플레이오프·시범경기를 넉넉히 포함한다. 좁게 잡으면 진짜 경기를 비시즌으로 오해한다.
SEASON_MONTHS: dict[League, frozenset[int]] = {
    League.KBO:       frozenset({3, 4, 5, 6, 7, 8, 9, 10, 11}),
    League.MLB:       frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    League.NPB:       frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    League.KL1:       frozenset({2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}),
    League.KBL:       frozenset({10, 11, 12, 1, 2, 3, 4, 5}),
    League.VLEAGUE_M: frozenset({10, 11, 12, 1, 2, 3, 4}),
    League.VLEAGUE_W: frozenset({10, 11, 12, 1, 2, 3, 4}),
    League.LCK:       frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}),
    # 국제 LoL은 리그가 아니라 **두 대회**다(MSI 4~7월 · 롤드컵 9~11월).
    # LCK와 같은 달로 넓게 잡아두면 8월·12월에 "시즌 중인데 경기 0건"이 매일 울고,
    # 그 소음에 진짜 사고가 묻힌다. 대회가 실제로 열리는 달만 시즌으로 본다.
    League.INTL_LOL:  frozenset({4, 5, 6, 7, 9, 10, 11}),
    League.EPL:       frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5}),
    League.LALIGA:    frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5}),
    League.SERIEA:    frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5}),
    League.BUNDESLIGA: frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5}),
    League.LIGUE1:    frozenset({8, 9, 10, 11, 12, 1, 2, 3, 4, 5}),
    League.UCL:       frozenset({7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6}),
}
assert set(SEASON_MONTHS) == set(League), (
    f"SEASON_MONTHS 누락: {[l.value for l in League if l not in SEASON_MONTHS]}")


def in_season(league: "League", when) -> bool:
    """그 리그가 이 달에 경기를 하는가. 커버리지 감시의 오탐을 줄인다."""
    return when.month in SEASON_MONTHS[league]


def stale_unresolved(games: "list[Game]", *, now_utc=None,
                     grace_seconds: "int | None" = None) -> "list[Game]":
    """시작시각이 한참 지났는데 아직 '예정'/'진행 중'인 경기를 골라낸다.

    리그를 가리지 않는다. KBL에서 처음 발견됐지만 원인(소스가 종결을 안 찍음)은
    어느 리그에서나 생길 수 있어서, 어댑터가 아니라 계약이 들고 있어야 한다.

    grace_seconds를 주면 전 리그에 그 값을 쓰고, 안 주면 리그별 유예를 적용한다
    (NPB처럼 소스가 늦는 리그가 있다 — STALE_GRACE_BY_LEAGUE 참조).
    """
    from datetime import datetime as _dt, timezone as _tz
    now = now_utc or _dt.now(_tz.utc)
    out = []
    for g in games:
        if g.status not in (Status.SCHEDULED, Status.LIVE):
            continue
        grace = grace_seconds if grace_seconds is not None else stale_grace_for(g.league)
        if (now - g.start_utc).total_seconds() > grace:
            out.append(g)
    return out


def assert_no_stale_scheduled(games: "list[Game]", *, now_utc=None,
                              grace_seconds: "int | None" = None) -> None:
    """묵은 '예정'이 하나라도 있으면 막는다 — 전 리그 공통 게이트.

    통과가 아니라 **차단**이 기본값이다. 조용히 넘기면 그 경기가 그대로 카드에 실린다.
    소스가 원래 결과를 안 주는 구간이면 어댑터가 SOURCE_RESULTLESS_CATEGORIES를 보고
    미리 걸러내야 하며, 여기까지 온 것은 예상 못 한 결함이다.
    """
    bad = stale_unresolved(games, now_utc=now_utc, grace_seconds=grace_seconds)
    if bad:
        d = ", ".join(f"{g.league.value}:{g.source_key}({g.sports_day})" for g in bad[:5])
        hrs = ((grace_seconds if grace_seconds is not None
                else stale_grace_for(bad[0].league)) // 3600)
        raise GateError(
            f"묵은 '예정' 경기 {len(bad)}건 — 시작 후 {hrs}시간이 지났는데 "
            f"종결되지 않았습니다. 소스가 결과를 안 주는 구간이면 어댑터에서 격리하고, "
            f"소스가 늦을 뿐이면 STALE_GRACE_BY_LEAGUE에 적어야 합니다. 예) {d}")


ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.SCHEDULED: frozenset({Status.LIVE, Status.CANCELED, Status.POSTPONED}),
    Status.LIVE: frozenset({
        Status.FINAL,
        Status.SUSPENDED,
        Status.CANCELED,     # 노게임(5회 미만 강우)
        Status.POSTPONED,    # 중단 후 익일 재편성
    }),
    Status.SUSPENDED: frozenset({
        Status.SCHEDULED,    # 재개 예정
        Status.FINAL,        # 재개 후 종료
        Status.CANCELED,     # v1.9: 잔여 이닝 미실시 확정
        Status.POSTPONED,    # v1.9: 중단 후 재편성 (소스가 중단을 먼저 찍는 경우)
    }),
    Status.POSTPONED: frozenset({
        Status.SCHEDULED,
        Status.CANCELED,     # v1.9: 연기 후 시즌 내 편성 실패 → 최종 취소
    }),
    Status.CANCELED: frozenset({
        Status.SCHEDULED,
        Status.POSTPONED,    # v1.9: 소스가 취소로 먼저 기록했다가 연기로 정정
    }),
    Status.FINAL: frozenset(),
}


class IllegalTransition(Exception):
    """6종 안에 있으나 허용되지 않은 전이. 발송만 차단하고 상태는 최신값으로 저장한다."""


class UnknownStatus(Exception):
    """소스가 6종 밖의 값을 줬다. 추측 매핑 금지 — 차단 + DM (D-13)."""


class GateError(Exception):
    """게이트 검증 실패. UnknownStatus와 구분해야 라우팅이 갈린다."""


def assert_transition(old: Status, new: Status) -> None:
    if new == old:
        return
    if new not in ALLOWED_TRANSITIONS[old]:
        raise IllegalTransition(f"{old.value} -> {new.value}")


# 리그별 상태 매핑 화이트리스트. 여기 없는 값은 UnknownStatus.
# NPB는 상태 표기 도메인이 미확보라 비어 있다 — 확보 전까지 NPB 활성화 금지.
STATUS_MAP: dict[League, dict[str, Status]] = {
    League.KL1: {
        "K21": "강원", "K22": "광주", "K35": "김천", "K10": "대전", "K26": "부천",
        "K09": "서울", "K27": "안양", "K01": "울산", "K18": "인천", "K05": "전북",
        "K04": "제주", "K03": "포항",
    },
    League.KBL: {
        "KT": "KT", "MOB": "현대모비스", "DB": "DB", "SS": "삼성", "LG": "LG",
        "SK": "SK", "KCC": "KCC", "KOGAS": "가스공사", "SONO": "소노", "KGC": "정관장",
    },
    League.VLEAGUE_M: {
        "KAL": "대한항공", "HDC": "현대캐피탈", "SFI": "삼성화재", "WOORI": "우리카드",
        "OK": "OK저축은행", "KEPCO": "한국전력", "KB": "KB손보",
    },
    League.VLEAGUE_W: {
        "HK": "흥국생명", "HDE": "현대건설", "GS": "GS칼텍스", "KEC": "도로공사",
        "IBK": "IBK기업은행", "KGC": "정관장", "PEPPER": "페퍼저축", "SOOP": "SOOP",
    },
    League.NPB: {
        "YOG": "요미우리", "DEN": "DeNA", "HAN": "한신", "CHU": "주니치",
        "YAK": "야쿠르트", "HIR": "히로시마", "SOF": "소프트뱅크", "NIP": "니혼햄",
        "LOT": "지바롯데", "RAK": "라쿠텐", "ORI": "오릭스", "SEI": "세이부",
        "CEN": "센트럴", "PAC": "퍼시픽",
    },
    League.MLB: {
        "scheduled": Status.SCHEDULED, "pre-game": Status.SCHEDULED, "warmup": Status.SCHEDULED,
        "in progress": Status.LIVE, "live": Status.LIVE,
        "final": Status.FINAL, "game over": Status.FINAL, "completed early": Status.FINAL,
        "cancelled": Status.CANCELED, "canceled": Status.CANCELED,
        "postponed": Status.POSTPONED, "suspended": Status.SUSPENDED,
    },
    # NPB — 2023~2026 3,615경기 전수 스캔으로 확정 (中止 116건, ノーゲーム 7건)
    League.NPB: {
        "中止": Status.CANCELED,          # 우천취소
        "ノーゲーム": Status.CANCELED,      # 노게임 — 기록 무효
        "延期": Status.POSTPONED,
        "サスペンデッド": Status.SUSPENDED,
    },
    # K리그 — schedule.do 렌더 코드에서 전 분기 확인. 취소·연기 코드는 존재하지 않는다.
    League.KL1: {
        "fe": Status.FINAL,
        "1e": Status.LIVE, "2e": Status.LIVE, "3e": Status.LIVE, "4e": Status.LIVE,
        "": Status.SCHEDULED,
    },
    # LCK — 라이엇 공식 API. 연기·취소 전용 상태값 없음(일정 이동으로 처리)
    League.LCK: {
        "unstarted": Status.SCHEDULED,
        "inprogress": Status.LIVE,
        "completed": Status.FINAL,
    },
}
STATUS_MAP[League.INTL_LOL] = STATUS_MAP[League.LCK]

# K리그 `1S12`(전반 12분) 처럼 접두 + 숫자인 진행중 코드
KL_LIVE_PREFIXES = ("1s", "2s", "3s", "4s")

# KBO는 상태 문자열이 아니라 취소 사유가 그대로 들어온다
# (2026 시즌 실측: 우천취소 25 · 폭염 30 · 기타 30 — 사유가 다양해 화이트리스트 불가)
# KBO 편성표의 마지막 '비고' 셀. 2026시즌 전수 조사(782행) 결과 값은 정확히 4종뿐이다.
#   '-' 716건(정상: 미래 예정 147 + 종료 569) · '폭염취소' 30 · '우천취소' 29 · '그라운드사정' 7
#
# v1.10 이전에는 "취소"라는 글자를 찾았다. 그래서 '그라운드사정' 7건을 통째로 놓쳤고,
# 12일 지난 경기가 '오늘 예정'으로 모닝 브리핑에 실려 실제 채널까지 나갔다.
# 사유 어휘는 KBO가 언제든 늘릴 수 있으므로 **키워드 목록을 늘리는 것은 답이 아니다.**
# 구조로 판정한다: 비고가 '-'가 아니면 그 경기는 그날 열리지 않았다.
KBO_NOTE_NORMAL = "-"
KBO_KNOWN_CANCEL_REASONS = frozenset({"우천취소", "폭염취소", "그라운드사정"})

# ★ 연기·취소를 상태값으로 감지할 수 없는 리그 — 스냅샷 diff가 유일한 수단
DIFF_ONLY_CANCELLATION = frozenset({League.KL1, League.LCK, League.INTL_LOL})

# KBL 시즌 카테고리 화이트리스트 (실측 확정)
# 🔴 블랙리스트("D로 시작하면 제외")였다면 PO·CP·AS·EA가 정규시즌과 섞여 발송됐다
KBL_SEASON_CATEGORY_ALLOW = {
    "R":  "정규시즌",
    "PO": "플레이오프",
    "CP": "챔피언결정전",
    "AS": "올스타게임",
    "EA": "EASL",           # 동아시아 슈퍼리그 — 별도 대회로 표기
}
KBL_SEASON_CATEGORY_DENY = {"D1": "D리그(2군)", "OM": "OPEN MATCH DAY(프리시즌)"}


# ── 리그 커버리지 감시 ("인기리그 절대 누락 금지") ──────────────
# 소스를 뚫는 것만으로는 부족하다. 조용히 빠지는 것을 막는 층이다.
#
# 대사 기준은 큐가 아니라 '선적재한 일정 캘린더'다.
# 큐 기준이면 큐 생성 실패를 영원히 자기 자신이 검출할 수 없다.
COVERAGE_HORIZON_DAYS = 30           # 캘린더 선적재 지평선
COVERAGE_AUDIT_TIMES_KST = ("09:20", "15:00", "23:45")
COVERAGE_TOLERANCE = 0               # 한 건이라도 어긋나면 DM

# 1차 실패 시 자동 전환할 2차 소스. 없는 리그는 명시적으로 None — 있는 척하지 않는다.
SOURCE_FALLBACK: dict[League, Optional[str]] = {
    League.LCK:        "leaguepedia_cargoquery",   # + 라이엇 키 재추출 루틴
    League.INTL_LOL:   "leaguepedia_cargoquery",
    League.KBO:        "playwright_html",          # ASMX 실패 시 기존 HTML 경로
    League.KL1:        "recent_match_result",      # 결과 전용(일정 없음)
    League.MLB: None, League.NPB: None, League.KBL: None,
    League.VLEAGUE_M: None, League.VLEAGUE_W: None,
    League.EPL: None, League.LALIGA: None, League.SERIEA: None,
    League.BUNDESLIGA: None, League.LIGUE1: None, League.UCL: None,
}

# 리그별 '정상 침묵 상한'(시간). 초과하면 시즌 중인데 콘텐츠가 0건이라는 뜻 → 경보
NORMAL_SILENCE_HOURS: dict[League, int] = {
    League.KBO: 30, League.MLB: 30, League.NPB: 30,
    League.KBL: 48, League.VLEAGUE_M: 72, League.VLEAGUE_W: 72,
    League.KL1: 96, League.LCK: 96, League.INTL_LOL: 96,
    League.EPL: 96, League.LALIGA: 96, League.SERIEA: 96,
    League.BUNDESLIGA: 96, League.LIGUE1: 96, League.UCL: 168,
}


def parse_status(raw: str, league: League) -> Status:
    """소스 문자열 → Status. 실패는 UnknownStatus로만 나간다(ValueError 아님).

    어댑터가 Status(raw)를 직접 호출하면 ValueError가 나서 D-13 라우팅이 못 잡고
    워커가 통째로 500으로 죽는다.
    """
    if raw is None:
        raise UnknownStatus(f"{league.value}: 상태값 없음")
    key = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    table = STATUS_MAP.get(league)
    if not table:
        raise UnknownStatus(f"{league.value}: 상태 매핑 미정의 (raw={raw!r})")
    if key not in table:
        raise UnknownStatus(f"{league.value}: 미지 상태값 {raw!r}")
    return table[key]


class DecidedBy(str, Enum):
    REGULAR = "regular"
    EXTRA_INNINGS = "extra_innings"   # v1.9: 야구 연장 — 무승부가 성립한다
    AET = "aet"                       # 축구 연장
    PSO = "pso"                       # 승부차기


# 리그별 허용 결정방식. 야구에 PSO가 오면 파싱 오류다.
DECIDED_BY_ALLOWED: dict[ScoreUnit, frozenset[DecidedBy]] = {
    ScoreUnit.RUNS: frozenset({DecidedBy.REGULAR, DecidedBy.EXTRA_INNINGS}),
    ScoreUnit.GOALS: frozenset({DecidedBy.REGULAR, DecidedBy.AET, DecidedBy.PSO}),
    ScoreUnit.POINTS: frozenset({DecidedBy.REGULAR, DecidedBy.EXTRA_INNINGS}),
    ScoreUnit.SETS: frozenset({DecidedBy.REGULAR}),
    ScoreUnit.MAPS: frozenset({DecidedBy.REGULAR}),
}

# 무승부가 존재하지 않는 단위 — is_draw()가 항상 False
NO_DRAW_UNITS = frozenset({ScoreUnit.SETS, ScoreUnit.MAPS})


# ─────────────────────────────────────────────────────────────────────
# 경기 모델
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TeamRef:
    league: League
    team_code: str

    def key(self) -> str:
        return f"{self.league.value}:{self.team_code}"


@dataclass(frozen=True)
class Score:
    home: int
    away: int
    unit: ScoreUnit


@dataclass(frozen=True)
class Weather:
    """자유 dict면 게이트가 검사할 스키마가 없다."""
    temp_c: Optional[float] = None
    precip_prob: Optional[int] = None      # 0~100
    wind_ms: Optional[float] = None
    observed_at_utc: Optional[datetime] = None


@dataclass
class PlayerLine:
    """코리안리거 데일리·박스스코어용 (v1.9 신설).

    카드 디자인(card-final-set.html)이 '이정후 2안타'를 이미 전제하고 있는데
    v1.8 계약에는 선수 단위 필드가 없어 그 자리를 채울 값이 없었다.
    """
    player_id: str
    name_ko: str
    team: TeamRef
    played: bool = True
    dnp_reason: Optional[str] = None
    batting: Optional[dict] = None    # AB H HR RBI BB SO SB AVG
    pitching: Optional[dict] = None   # IP H ER K BB ERA WHIP decision


@dataclass
class GameMeta:
    decided_by: DecidedBy = DecidedBy.REGULAR
    penalties: Optional[Score] = None
    aggregate: Optional[Score] = None
    set_scores: list[tuple[int, int]] = field(default_factory=list)

    cancel_reason: Optional[str] = None      # v1.10: 소스가 준 사유 원문 ('우천취소' 등)
    shortened_innings: Optional[int] = None
    doubleheader_seq: Optional[int] = None
    starting_pitchers: Optional[tuple[str, str]] = None

    gender: Optional[str] = None
    season_category: Optional[str] = None

    # v1.11c: 다전제 경기 수(BO3·BO5). e스포츠는 이게 없으면 '2-1'이 무슨 뜻인지 모른다.
    # 농구·야구의 단판과 달리 세트 스코어라, 카드에 'BO5 3-2'처럼 함께 적어야 정확하다.
    best_of: Optional[int] = None

    is_dome: bool = False
    weather: Optional[Weather] = None

    # v1.9 신설 — 인플레이 스코어보드
    period: Optional[int] = None            # 이닝/쿼터/세트 번호
    period_state: Optional[str] = None      # "top"/"bot"/"HT" 등
    line_score: list[tuple[int, int]] = field(default_factory=list)

    # v1.9 신설 — 코리안리거·리더보드
    player_lines: list[PlayerLine] = field(default_factory=list)

    analysis_metrics: dict = field(default_factory=dict)


@dataclass
class Game:
    league: League
    season: str
    source_key: str
    home: TeamRef
    away: TeamRef
    start_utc: datetime
    home_tz: str
    status: Status
    score: Optional[Score] = None
    meta: GameMeta = field(default_factory=GameMeta)
    start_rev: int = 0
    venue: Optional[str] = None

    # v1.9: sports_day는 최초 편성 시 확정되는 불변 필드다.
    # 계산값으로 두면 서스펜디드 재개로 start_utc가 갱신될 때 경기가 다른 날로 이동해,
    # 이미 발송된 결과 카드의 정정이 원본을 못 찾는다.
    sports_day_fixed: Optional[str] = None

    # ── 파생값 ──────────────────────────────────────────────────

    @property
    def game_id(self) -> str:
        return f"{self.league.value}:{self.season}:{self.source_key}"

    @property
    def start_kst(self) -> datetime:
        return self.start_utc.astimezone(KST)

    @property
    def start_local(self) -> datetime:
        return self.start_utc.astimezone(ZoneInfo(self.home_tz))

    @property
    def sports_day(self) -> str:
        """리그의 하루 = 홈 현지 캘린더 날짜.

        v1.8은 KST 고정 오프셋(유럽 6h)을 썼는데, 그 값을 MLB에 그대로 복사한 탓에
        같은 미국 날짜의 슬레이트가 KST 06:00 경계에서 정확히 반으로 쪼개졌다
        (동부 낮경기 → 전날 / 동부 야간 → 다음날). sports_day가 EPL에 대해
        고치려던 바로 그 증상을 MLB에 만든 것이다.

        현지 날짜는 EPL(런던 12:30~17:30)·MLB(ET 13:05~22:10)·NPB(JST 18:00)·
        국내 리그 전부에서 한 매치데이를 한 날짜로 묶는다.
        """
        if self.sports_day_fixed:
            return self.sports_day_fixed
        return self.start_local.strftime("%Y-%m-%d")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    # ── 검증 ────────────────────────────────────────────────────

    def validate(self) -> None:
        """게이트가 발송 전에 호출한다. 실패는 GateError."""
        # 식별자
        if not self.source_key or not self.source_key.strip():
            raise GateError(f"{self.league.value}: source_key가 비어 있다 (game_id 충돌 위험)")
        if ":" in self.source_key:
            raise GateError(f"{self.game_id}: source_key에 구분자 ':' 금지")
        pattern = SEASON_FORMAT_BY_LEAGUE[self.league]
        if not pattern.match(self.season):
            raise GateError(f"{self.game_id}: season 표기 형식 위반 ({self.season!r})")

        # 팀
        if self.home.league != self.league or self.away.league != self.league:
            raise GateError(f"{self.game_id}: 팀 리그가 경기 리그와 다르다")
        if self.home.team_code == self.away.team_code:
            raise GateError(f"{self.game_id}: 홈과 원정이 같은 팀이다 (파싱 오류)")

        # 시각
        if self.start_utc.tzinfo is None:
            raise GateError(f"{self.game_id}: naive datetime 금지")
        try:
            ZoneInfo(self.home_tz)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            raise GateError(f"{self.game_id}: home_tz가 유효하지 않다 ({self.home_tz!r})")

        # 스코어
        if self.score is not None:
            expected = SCORE_UNIT_BY_LEAGUE[self.league]
            if self.score.unit != expected:
                raise GateError(
                    f"{self.game_id}: score.unit 불일치 — {expected.value} 여야 하는데 {self.score.unit.value}")
            cap = SCORE_MAX_BY_UNIT[self.score.unit]
            for side, v in (("home", self.score.home), ("away", self.score.away)):
                if v < 0 or v > cap:
                    raise GateError(f"{self.game_id}: {side} 스코어 범위 이탈 ({v}, 상한 {cap})")
        if self.status == Status.FINAL and self.score is None:
            raise GateError(f"{self.game_id}: final인데 score가 없다")

        # 결정 방식
        unit = SCORE_UNIT_BY_LEAGUE[self.league]
        if self.meta.decided_by not in DECIDED_BY_ALLOWED[unit]:
            raise GateError(
                f"{self.game_id}: {unit.value} 리그에 {self.meta.decided_by.value}는 불가")
        if self.meta.decided_by == DecidedBy.PSO and self.meta.penalties is None:
            raise GateError(f"{self.game_id}: 승부차기인데 penalties가 없다")

        # 리그 구분자 — 값 일치까지 본다(v1.8은 존재 검사뿐이라 남자부에 여자 값이 통과했다)
        if self.league in GENDER_BY_LEAGUE:
            want = GENDER_BY_LEAGUE[self.league]
            if self.meta.gender != want:
                raise GateError(
                    f"{self.game_id}: gender 불일치 — {self.league.value}는 '{want}' 여야 하는데 "
                    f"{self.meta.gender!r} (남녀부 혼입)")

        # 날씨
        w = self.meta.weather
        if w is not None and w.precip_prob is not None and not (0 <= w.precip_prob <= 100):
            raise GateError(f"{self.game_id}: 강수확률 범위 이탈 ({w.precip_prob})")
        if self.meta.is_dome and w is not None:
            raise GateError(f"{self.game_id}: 돔구장에 날씨가 붙었다")

    def is_draw(self) -> bool:
        """무승부 판정.

        aggregate를 안 보면 UCL 2차전 1:1(합산 3:2 진출)이 '무승부'로 발송된다.
        decided_by를 안 보면 승부차기 경기가 '무승부'로 발송된다.
        """
        if self.score is None:
            return False
        if self.score.unit in NO_DRAW_UNITS:
            return False
        if self.meta.decided_by in (DecidedBy.AET, DecidedBy.PSO):
            return False
        if self.meta.aggregate is not None:
            return self.meta.aggregate.home == self.meta.aggregate.away
        return self.score.home == self.score.away


# ─────────────────────────────────────────────────────────────────────
# 콘텐츠 · 발송 계층
# ─────────────────────────────────────────────────────────────────────

class ContentType(str, Enum):
    MORNING = "morning"
    NIGHT_BRIEF = "night_brief"            # v1.9: 23:00 전 리그 통합 (카드 ② 디자인 복원)
    POLL = "poll"
    POLL_CLOSE = "poll_close"
    POLL_SETTLEMENT = "poll_settlement"    # v1.9: 정산 카드 (카드 ③ 디자인 복원)
    ANALYSIS = "analysis"
    START_ALERT = "start_alert"
    INPLAY_BOARD = "inplay_board"          # v1.9: 경기 중 침묵 해소, 경기당 1회
    FINAL_FLASH = "final_flash"
    LEAGUE_RESULT = "league_result"
    STANDINGS = "standings"                # v1.9: 일간 순위표
    KOREAN_DAILY = "korean_daily"          # v1.9: 코리안리거 데일리
    LEADERBOARD = "leaderboard"            # v1.9: 점심 리그 리더보드
    WEEKLY_PREVIEW = "weekly_preview"      # v1.9: 주간 프리뷰
    WEEKLY_COLUMN = "weekly_column"
    QUIZ = "quiz"                          # v1.9: 기록 퀴즈 Poll
    MILESTONE = "milestone"                # v1.9: 기록 접근 알림
    CORRECTION = "correction"
    EVERGREEN = "evergreen"


# 유예(초). None 금지 — 종결 상태에 도달 못 하는 항목이 생겨
# 대사가 매일 같은 구멍을 보고하고 졸업 기준의 '오탐 알림 0'을 자기가 깬다.
#
# 규칙: 유예 > 생존 감시 임계(6~7분). v1.8의 START_ALERT 180초는 이 규칙을 위반했다.
GRACE_SECONDS: dict[ContentType, int] = {
    ContentType.START_ALERT: 480,        # v1.9: 180 → 480 (감시 임계보다 크게)
    ContentType.POLL_CLOSE: 480,         # v1.9: 1800 → 480 (열린 투표를 오래 두면 안 된다)
    ContentType.INPLAY_BOARD: 600,
    ContentType.FINAL_FLASH: 900,
    ContentType.POLL: 1800,
    ContentType.ANALYSIS: 1800,
    ContentType.QUIZ: 1800,
    ContentType.MILESTONE: 1800,
    ContentType.MORNING: 3600,
    ContentType.NIGHT_BRIEF: 3600,
    ContentType.KOREAN_DAILY: 7200,
    ContentType.LEADERBOARD: 7200,
    ContentType.POLL_SETTLEMENT: 21600,
    ContentType.LEAGUE_RESULT: 21600,
    ContentType.STANDINGS: 21600,
    ContentType.WEEKLY_PREVIEW: 21600,
    ContentType.WEEKLY_COLUMN: 21600,
    ContentType.EVERGREEN: 21600,
    ContentType.CORRECTION: 86400,       # v1.9: 무제한 → 24h (무제한은 종결 불가 + 정지 진동 루프)
}

# 클레임 리스. 반드시 유예보다 짧아야 한다 —
# 리스가 길면 유예를 넘겨서까지 항목이 묶이고, 짧으면 정상 렌더 중에 needs_human으로 떨어진다.
# ── 시작 알림 리드타임 (v1.11c) ──────────────────────────────────────
#
# "경기 10분 전"은 시계가 5분마다 깨어날 때만 가능하다.
# 무료 실행 환경(깃허브 비공개 저장소)은 월 2,000분 한도 때문에 **매시 정각**에만 깨어난다.
# 그 상태로 10분 전 알림을 예약하면 18:20 알림을 18:00과 19:00 사이에 자느라 통째로 놓친다.
#
# 그래서 리드타임을 환경에 맞춰 정한다. 정각 시계면 60분(정시에 "이번 시간 경기" 안내),
# 5분 시계로 옮기면 10분으로 줄이면 된다 — 코드는 그대로다.
#
# 리드타임이 길어지면 유예도 함께 길어져야 한다. 유예가 짧으면 정각 틱이 조금만 늦어도
# 알림이 통째로 버려진다. 기준은 **경기 시작 5분 전까지는 보낼 가치가 있다**는 것이다.
import os as _os

START_ALERT_LEAD_MINUTES = max(5, int(_os.environ.get("START_ALERT_LEAD_MINUTES", "120")))

GRACE_SECONDS[ContentType.START_ALERT] = max(
    480, (START_ALERT_LEAD_MINUTES - 5) * 60)


def start_alert_lead_text() -> str:
    """카드·글에 적을 '몇 시간(분) 전 알림' 문구. **반드시 이걸 쓴다.**

    전에는 카드 아래에 "경기 시작 10분 전 알림"이 문자열로 박혀 있었다.
    리드타임을 2시간으로 바꾼 뒤에도 카드는 계속 10분이라고 말했다 —
    시스템이 하는 일과 카드가 하는 말이 어긋났고, 숫자 검증은 그걸 못 잡는다.
    (최소 폰트를 상수로만 두었다가 하루 만에 어긴 것과 같은 계열이다.)
    """
    m = START_ALERT_LEAD_MINUTES
    if m % 60 == 0:
        return f"경기 시작 {m // 60}시간 전 알림"
    if m > 60:
        return f"경기 시작 {m // 60}시간 {m % 60}분 전 알림"
    return f"경기 시작 {m}분 전 알림"

LEASE_SECONDS: dict[ContentType, int] = {ct: max(60, g // 3) for ct, g in GRACE_SECONDS.items()}

# 계획 발송 = 큐 등록분. 상한·폭주 대상이 아니라 페이서 대상이다.
UNPLANNED_CONTENT = frozenset({ContentType.CORRECTION})

# 페이서 우선순위 — 낮을수록 먼저. 문안에 시각이 박힌 것이 최우선.
# 이게 없으면 결과 카드 55메시지가 채널을 3분 점유하는 동안 시작 알림이 뒤에서 굶는다.
PACER_PRIORITY: dict[ContentType, int] = {
    ContentType.START_ALERT: 0,
    ContentType.POLL_CLOSE: 0,
    ContentType.FINAL_FLASH: 1,
    ContentType.INPLAY_BOARD: 1,
    ContentType.CORRECTION: 2,
    ContentType.POLL: 3,
    ContentType.ANALYSIS: 3,
    ContentType.MORNING: 4,
    ContentType.NIGHT_BRIEF: 4,
    ContentType.KOREAN_DAILY: 5,
    ContentType.LEADERBOARD: 5,
    ContentType.QUIZ: 5,
    ContentType.MILESTONE: 5,
    ContentType.LEAGUE_RESULT: 6,
    ContentType.STANDINGS: 6,
    ContentType.POLL_SETTLEMENT: 6,
    ContentType.WEEKLY_PREVIEW: 7,
    ContentType.WEEKLY_COLUMN: 7,
    ContentType.EVERGREEN: 8,
}

# 실제 전송 시각 기준으로 유예를 재판정하는 콘텐츠 —
# 문안에 시각이 박혀 있어 페이서 대기가 곧 거짓말이 되는 것들
REJUDGE_AT_SEND = frozenset({ContentType.START_ALERT, ContentType.POLL_CLOSE})


class ChangeKind(str, Enum):
    TIME_CHANGE = "time_change"
    CANCELED = "canceled"
    POSTPONED = "postponed"
    SUSPENDED = "suspended"
    RESUMED = "resumed"
    SCORE_FIX = "score_fix"
    PITCHER_CHANGE = "pitcher_change"
    MAPPING_FIX = "mapping_fix"      # v1.9: 매핑 갱신 후 생략 행 복구


class SendState(str, Enum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED_FINAL = "skipped_final"
    SKIPPED_PAST_AT_CREATION = "skipped_past_at_creation"
    SKIPPED_ALREADY_STARTED = "skipped_already_started"
    SUPERSEDED = "superseded"        # v1.9: 상위 revision이 생겨 무효화됨
    NEEDS_HUMAN = "needs_human"


# 대사가 '정상 종결'로 인정하는 상태. 여기 없으면 매 대사마다 같은 DM이 재발생한다.
SETTLED_STATES = frozenset({
    SendState.SENT, SendState.SKIPPED_FINAL, SendState.SKIPPED_PAST_AT_CREATION,
    SendState.SKIPPED_ALREADY_STARTED, SendState.SUPERSEDED,
})

IDEM_SEP = "|"   # game_id가 ':'를 포함하므로 ':'를 쓰면 키를 되돌려 파싱할 수 없다


def channel_ref(channel_id: str) -> str:
    """대장·멱등키에 남길 채널 식별자 — **실제 ID를 남기지 않는다** (v1.11c).

    발송 대장(`state/ledger.jsonl`)은 저장소에 커밋된다. 저장소를 공개로 두면
    그 파일이 그대로 공개되는데, 전에는 멱등키와 `chat_id` 두 군데에
    채널 ID가 평문으로 들어 있었다:

        {"idem_key": "-100XXXXXXXXXX|morning|KBO:2026-08-29|s0|r0",
         "chat_id": "-100XXXXXXXXXX", ...}          ← 실제 채널 ID가 그대로

    비공개 채널은 ID만으로 들어갈 수 없고 봇 토큰 없이는 글도 못 쓰므로 침입 위험은 낮다.
    그래도 어느 채널에 몇 시에 무엇을 보내는지가 통째로 드러나고, 운영 채널로 옮기면
    그 ID까지 노출된다. 대표님 작업 정보는 밖으로 내보내지 않는다는 원칙에 어긋난다.

    해시는 **단방향**이라 지문만으로는 원래 ID를 알 수 없고, 같은 채널이면 항상 같은
    지문이 나오므로 **중복 발송 방지는 그대로 작동한다**. 채널이 바뀌면 지문도 바뀌어
    새 채널에는 처음부터 다시 나간다(운영 채널 전환 시 의도한 동작이다).
    """
    import hashlib
    return "ch" + hashlib.sha256(str(channel_id).encode()).hexdigest()[:12]


def idem_key(
    channel_id: str,
    content_type: ContentType,
    scope: str,                       # game_id 또는 sports_day
    revision: int = 0,
    start_rev: int = 0,
    change_kind: Optional[ChangeKind] = None,
    part_no: Optional[int] = None,
) -> str:
    """멱등 키.

    start_rev와 revision은 서로 다른 개념이라 슬롯을 분리한다.
      · start_rev  — 경기 시작 시각이 바뀐 횟수. 순연 시 알림 키를 새로 여는 근거
      · revision   — 콘텐츠 정정 횟수. 정정이 경기당 평생 1회로 막히는 것을 푸는 근거
    v1.8은 슬롯이 하나라 어느 콘텐츠가 어느 것을 넣는지 미정의였다.
    """
    # 채널은 지문으로만 남긴다 — 대장이 공개 저장소에 커밋되기 때문이다(channel_ref 참조).
    parts = [channel_ref(channel_id), content_type.value, scope,
             f"s{start_rev}", f"r{revision}"]
    if change_kind is not None:
        parts.append(change_kind.value)
    if part_no is not None:
        parts.append(f"p{part_no}")
    return IDEM_SEP.join(parts)


# 텔레그램 API 하드 제약
TELEGRAM_ALBUM_MIN = 2
TELEGRAM_ALBUM_MAX = 10
TELEGRAM_CAPTION_MAX = 1024
TELEGRAM_TEXT_MAX = 4096
TELEGRAM_PHOTO_MAX_BYTES = 10 * 1024 * 1024
TELEGRAM_PHOTO_DIM_SUM_MAX = 10_000
TELEGRAM_PHOTO_RATIO_MAX = 20
TELEGRAM_POLL_CLOSE_MIN_S = 5
TELEGRAM_POLL_CLOSE_MAX_S = 2_628_000
TELEGRAM_POLL_OPTIONS_MAX = 12
TELEGRAM_POLL_QUESTION_MAX = 300
TELEGRAM_DELETE_WINDOW_S = 48 * 3600

# 자체 안전 마진
GATE_PHOTO_MAX_BYTES = 9 * 1024 * 1024
GATE_PHOTO_DIM_SUM_MAX = 9_500

# ── 카드 렌더 규격 (디자인 시스템 v2.0에서 확정) ──────────────
CARD_WIDTH_PX = 1080
# 높이 상한은 API 제약(9500-1080=8420)이 아니라 텔레그램 서버 리사이즈가 결정한다.
# 긴 변 1280px 초과 시 서버가 축소하고, 그 위에 버블 축소가 겹쳐 15% 추가 손실이 난다.
# v1.10b: 1400 → 2000. 대표님 지시 — "정사각형에 가까울 필요 없다, 내용이 많으면 세로를 늘려라".
#
# 판독성은 세로 길이와 무관하다. 텔레그램은 사진을 **폭 기준**으로 버블에 맞추므로
# 폭이 1080으로 고정인 한 글자의 실제 크기(pt)는 세로가 길어져도 변하지 않는다.
# 유일한 위험은 비율이 커질 때 클라이언트가 폭을 줄이는 것인데,
# 텔레그램 규격상 사진 비율 상한은 20이고 치수 합 상한은 10000이다.
# 1080×2000이면 비율 1.85, 치수 합 3080 — 두 제한 모두에서 크게 안쪽이다.
# ✅ 실발송 검증 완료 (2026-08-28, 테스트 채널):
#    1080×1620(비율 1.500)을 보냈더니 텔레그램이 1080×1620 그대로 돌려줬다.
#    리사이즈 없음. 1087·1093·1096·1528·1620 다섯 크기 전부 원본 유지.
#    → '폭 고정이면 세로는 판독성과 무관하다'는 전제가 실물로 확인됐다.
CARD_MAX_HEIGHT_PX = 2000
CARD_MAX_ASPECT = 1.85          # 텔레그램 상한 20의 1/10. 폭 고정이라 판독성은 불변
CARD_MIN_FONT_PX = 28           # 폰 실렌더 배율 0.294 → 8pt. 이 미만은 판독 불가
CARD_CAPTION_FONT_MAX_COUNT = 5 # 28~30px 캡션은 카드당 5개까지

# 발송 포맷 — 텔레그램은 어차피 JPEG로 재인코딩한다.
# 기본 4:2:0은 색차를 2x2로 뭉개 골드/초록 소형 글자를 번지게 한다(흰 글자의 1.8배 손상).
# 미리 4:4:4로 보내면 원천 차단된다. PNG 발송은 대역만 낭비하고 결과가 같다.
SEND_IMAGE_FORMAT = "JPEG"
# q92 유지 확정 (2026-08-28 실측).
# 라이트 테마로 바꾸면서 링잉이 커져 품질 상향을 검토했으나,
# q88→125.9% / q92→117.2% / q95→119.1% / q97→116.6%로 개선이 없었다.
# 지배 요인은 발송 품질이 아니라 텔레그램의 재압축이고 그건 통제할 수 없다.
# 파일만 67% 커지므로 올리지 않는다.
SEND_JPEG_QUALITY = 92
SEND_JPEG_SUBSAMPLING = 0       # 0 = 4:4:4

def assert_card_typography(samples: list[tuple[str, float, str]]) -> None:
    """카드 본문의 실제 렌더 폰트 크기를 검사한다.

    v1.10 감사에서 드러난 구멍: CARD_MIN_FONT_PX(28)는 상수로만 있고
    이를 강제하는 코드가 없었다. 그래서 신규 카드 3종이 곧바로 26px를 썼고
    규격 게이트(폭·높이·비율)는 그걸 통과시켰다.

    samples: (텍스트, 렌더된 font-size px, CSS 클래스). 워터마크는 제외하고 넘긴다.
    """
    if not samples:
        raise GateError("타이포 게이트: 검사할 텍스트 0건 (렌더 실패 의심)")
    small = [x for x in samples if x[1] < CARD_MIN_FONT_PX - 0.5]
    if small:
        head = ", ".join(f'"{t}" {fs:.0f}px(.{c})' for t, fs, c in small[:4])
        raise GateError(
            f"카드 최소 폰트 {CARD_MIN_FONT_PX}px 미달 {len(small)}건: {head}")


def assert_card_geometry(width: int, height: int) -> None:
    """렌더 직후 게이트가 호출한다."""
    if width != CARD_WIDTH_PX:
        raise GateError(f"카드 폭은 {CARD_WIDTH_PX}px 고정 (받은 값 {width})")
    if height > CARD_MAX_HEIGHT_PX:
        raise GateError(f"카드 높이 {height} > {CARD_MAX_HEIGHT_PX} (서버 추가 축소 발생)")
    if height / width > CARD_MAX_ASPECT:
        raise GateError(f"세로비 {height/width:.2f} > {CARD_MAX_ASPECT}")


# ── 리그 색 토큰 (디자인 시스템 v2.0) ────────────────────────
# --lg  = 면·스트립·레일용 / --lg_ink = 글자용(명도 76 고정)
# base를 그대로 글자에 쓰면 EPL·UCL·세리에A·분데스가 대비 3.6대로 판독 불가가 된다.
#
# ⚠️ 색만으로 리그를 구분시키지 않는다. pill에 항상 텍스트 라벨 + 픽토그램을 함께 단다.
#    14개를 전부 색차 14 이상으로 벌리는 것은 sRGB에서 불가능하고(이론 상한 11.8),
#    적록색약에서는 어떤 팔레트를 써도 7쌍이 붕괴한다.
LEAGUE_COLORS: dict[League, tuple[str, str]] = {
    # v4.0 파스텔 — (진한 잉크, 파스텔 워시)
    # 파스텔은 면에만 쓴다. 글자·테두리는 같은 계열의 진한 잉크를 쓴다.
    # 두 값 모두 밝은 배경(#e9eef6)과 서로에 대해 대비 4.5 이상을 실측으로 확인했다.
    # KBO는 살구(#ffe6cc)였는데 카드 바닥에서 연노란색으로 보여 교체(2026-08-28 대표님 지적).
    # 세이지 그린 — 기존 14개 리그 중 가장 가까운 KL1 민트와도 색상 38° 떨어져 구분된다.
    League.KBO:        ("#1f6b48", "#d9ecdf"),
    League.KBL:        ("#8d3806", "#ffdcc8"),
    League.MLB:        ("#a8382a", "#ffd9d2"),
    League.NPB:        ("#a82a6b", "#ffd6ea"),
    League.KL1:        ("#00695a", "#c9f0e4"),
    League.VLEAGUE_M:  ("#00657a", "#cceef5"),
    League.VLEAGUE_W:  ("#a02a80", "#fcd9f0"),
    League.LCK:        ("#5b34c4", "#e2dbfc"),
    League.INTL_LOL:   ("#5b34c4", "#e2dbfc"),
    League.EPL:        ("#5c2fb8", "#e7dcfa"),
    League.LALIGA:     ("#b02246", "#ffd8e2"),
    League.SERIEA:     ("#14549e", "#d4e6fc"),
    League.BUNDESLIGA: ("#b32127", "#ffd7d7"),
    League.LIGUE1:     ("#1a5aa8", "#d8e8ff"),
    League.UCL:        ("#3f34b8", "#dcd9fb"),
}

# 리그색을 넣는 4개 영역과 강도. 이 밖(점수·승패·순위 숫자)에는 절대 쓰지 않는다.
LEAGUE_TINT = {"strip": 1.00, "header_wash": 0.13, "pill_bg": 0.22, "pill_border": 0.42, "row": 0.16}

# 의미색 — 변경 금지
SEMANTIC_COLORS = {"gold": "#ffd23f", "win": "#4ade80", "lose": "#8595ad", "alert": "#ff8585"}

PACER_MSG_PER_SECOND = 1
PACER_MSG_PER_MINUTE = 20
BURST_WINDOW_S = 600
BURST_MAX_MESSAGES = 60
BURST_AUTO_RELEASE_S = 1800
BURST_CANARY_OBSERVE_S = 300     # v1.9: 해제 시 전량 재개 금지 — 1건만 내보내고 관찰
BURST_MAX_AUTO_RELEASES = 3      # v1.9: 3회 이상이면 수동 해제 전용


class SendMethod(str, Enum):
    PHOTO = "sendPhoto"
    MEDIA_GROUP = "sendMediaGroup"
    MESSAGE = "sendMessage"
    POLL = "sendPoll"


def plan_send_parts(photo_count: int) -> list[tuple[SendMethod, int]]:
    """장수 분기 + 균등 분배.

    v1.8은 (메서드, 파트수)만 반환해 11장이 10+1로 갈렸고,
    파트 2가 1장짜리 sendMediaGroup이 되어 API 400 → 재시도 3회 전부 실패했다.
    이 함수가 막으려던 바로 그 사고가 분할 경로에서 재발한 것이다.
    """
    if photo_count <= 0:
        raise GateError("보낼 사진이 없다")
    if photo_count == 1:
        return [(SendMethod.PHOTO, 1)]
    if photo_count <= TELEGRAM_ALBUM_MAX:
        return [(SendMethod.MEDIA_GROUP, photo_count)]

    parts = -(-photo_count // TELEGRAM_ALBUM_MAX)
    base, extra = divmod(photo_count, parts)
    sizes = [base + (1 if i < extra else 0) for i in range(parts)]
    out: list[tuple[SendMethod, int]] = []
    for s in sizes:
        assert s >= TELEGRAM_ALBUM_MIN, f"분배 오류: 1장짜리 앨범 {sizes}"
        out.append((SendMethod.MEDIA_GROUP, s))
    return out


def assert_sendable(caption: str, width: int, height: int, byte_len: int) -> None:
    """게이트의 '보낼 수 있는지' 검사. 상수만 두고 함수가 없으면 구현마다 제각각이 된다."""
    if len(caption) > TELEGRAM_CAPTION_MAX:
        raise GateError(f"캡션 {len(caption)}자 > {TELEGRAM_CAPTION_MAX}")
    if byte_len > GATE_PHOTO_MAX_BYTES:
        raise GateError(f"이미지 {byte_len}B > {GATE_PHOTO_MAX_BYTES}")
    if width + height > GATE_PHOTO_DIM_SUM_MAX:
        raise GateError(f"width+height {width + height} > {GATE_PHOTO_DIM_SUM_MAX}")
    lo, hi = sorted((width, height))
    if lo == 0 or hi / lo > TELEGRAM_PHOTO_RATIO_MAX:
        raise GateError(f"종횡비 초과 ({width}x{height})")


@dataclass
class PollResult:
    """stopPoll 응답의 Poll 객체에서 채운다 (v1.9).

    채널 Poll은 익명이라 poll_answer 업데이트가 오지 않지만,
    stopPoll 반환값에 total_voter_count와 옵션별 voter_count가 들어 있다.
    즉 채널 단위 투표 분포와 다수픽 적중 여부는 웹훅 없이 지금 만들 수 있다.
    웹훅이 필요한 것은 개인별 적중률·랭킹뿐이다.
    """
    total_voter_count: int
    option_counts: list[tuple[str, int]]
    correct_option_index: Optional[int] = None

    def majority_index(self) -> Optional[int]:
        if not self.option_counts:
            return None
        return max(range(len(self.option_counts)), key=lambda i: self.option_counts[i][1])

    def majority_hit(self) -> Optional[bool]:
        if self.correct_option_index is None:
            return None
        return self.majority_index() == self.correct_option_index


# ─────────────────────────────────────────────────────────────────────
# 텍스트 서식 (v2.0) — 인용블록 · 현지시간 병기
# ─────────────────────────────────────────────────────────────────────

TELEGRAM_PARSE_MODE = "HTML"          # 혼용 금지. MarkdownV2는 이스케이프 규칙이 달라 사고가 난다
BLOCKQUOTE_MIN_API = "7.0"            # <blockquote>
EXPANDABLE_BLOCKQUOTE_MIN_API = "7.4" # <blockquote expandable>
BLOCKQUOTE_NESTING_ALLOWED = False    # 공식 제약 — 중첩 불가

_HTML_ESCAPE = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def esc(text: str) -> str:
    """HTML 파스 모드에서 수집 데이터를 넣기 전 반드시 통과시킨다.

    팀명·선수명·구장명에 &, <, > 가 하나라도 들어가면 메시지 전체 파싱이 깨져
    발송이 400으로 실패한다. 사실 잠금 데이터일수록 이스케이프가 필수다.
    """
    if text is None:
        return ""
    out = str(text)
    for k, v in _HTML_ESCAPE.items():
        out = out.replace(k, v)
    return out


def quote(lines: list[str], expandable: bool = False) -> str:
    """인용블록. 중첩은 금지되어 있으므로 내부에 또 다른 quote를 넣지 않는다."""
    if not lines:
        raise GateError("빈 인용블록")
    body = "\n".join(lines)
    if "<blockquote" in body:
        raise GateError("인용블록 중첩 금지 (텔레그램 제약)")
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}{body}</blockquote>"


# 인용블록 안에 넣기에 너무 길면 접는다 — 채널 스크롤을 잡아먹지 않게
QUOTE_EXPANDABLE_THRESHOLD_LINES = 6


def needs_local_time(game: "Game") -> bool:
    """현지시간을 병기할지 판정.

    NPB(JST)·국내 리그는 KST와 오프셋이 같아 병기하면 같은 숫자가 두 번 나온다.
    오프셋이 실제로 다를 때만 병기한다.
    """
    return game.start_local.utcoffset() != game.start_kst.utcoffset()


# 요일 한글 표기
_WD = ["월", "화", "수", "목", "금", "토", "일"]


def format_kickoff(game: "Game", with_weekday: Optional[bool] = None) -> tuple[str, Optional[str]]:
    """(KST 표기, 현지 표기 또는 None).

    KST가 주(主)다 — 구독자가 한국인이므로. 현지시간은 보조 표기.
    날짜가 다르면 요일을 붙인다 — EPL 토요일 15:00 현지가 KST로는 일요일 00:00이라,
    요일 없이 '00:00'만 쓰면 구독자가 하루를 착각한다.
    """
    k, l = game.start_kst, game.start_local
    show_wd = with_weekday if with_weekday is not None else (k.date() != l.date())
    kst = f"{_WD[k.weekday()]} {k:%H:%M}" if show_wd else f"{k:%H:%M}"
    if not needs_local_time(game):
        return kst, None
    loc = f"{_WD[l.weekday()]} {l:%H:%M}" if show_wd else f"{l:%H:%M}"
    return kst, loc


def day_schedule_scope(game: "Game") -> str:
    """시작 알림의 범위 — **리그 하루 한 건** (v1.11c에서 바뀜).

    전에는 같은 시각(±5분) 경기를 묶어 시각마다 따로 보냈다. 리그가 아홉이 되자
    실측에서 **하루 26건**이 나왔고, 그중 17건이 MLB의 새벽 1~3시대였다.
    새벽에 알림이 열일곱 번 울리는 채널은 구독자가 나간다.

    그래서 알림 하나에 **그 리그의 오늘 시간표 전체**를 담고, 리그마다 하루 한 번만 보낸다
    (첫 경기 START_ALERT_LEAD_MINUTES 분 전). 하루 26건 → 4~5건.

    scope에 리그를 넣는 이유는 멱등키 사고와 같다 — 안 넣으면 아홉 리그가 서로를 덮는다.
    """
    return f"{game.league.value}:{game.sports_day}"


def start_alert_bucket(game: "Game", minutes: int = 5) -> str:
    """같은 시각에 시작하는 선발 경기를 한 메시지로 묶는 키.

    KBO 5경기가 전부 18:30 시작이면 알림 3건이 따로 나가는 대신 한 메시지로 묶인다.
    발송 수가 줄어 페이서(20msg/분) 압력도 함께 줄어든다.

    버킷은 시각으로 고정한다 — 묶음 안의 한 경기가 취소돼 내용이 바뀌어도
    키가 그대로여야 이미 나간 알림이 재발송되지 않는다(취소는 정정 카드가 담당).

    **리그를 반드시 포함한다 (v1.11c).** KBO 시절엔 시각만으로 충분했지만,
    리그가 9개가 되자 KBO 18:30과 NPB 18:30이 같은 버킷이 되어
    한 리그의 알림이 다른 리그의 알림을 덮어썼다. 시계를 만들면서 드러났다.
    """
    k = game.start_kst
    bucket = (k.minute // minutes) * minutes
    return f"{game.league.value}:{game.sports_day}@{k:%H}:{bucket:02d}"


class VoteChannel(str, Enum):
    """개인 식별이 가능한 참여 경로 (v1.9).

    채널 Poll은 익명이 강제된다 — 텔레그램 공식 버그 트래커가 "채널 투표는 익명으로만
    설계되어 있다"고 확인했고, 비익명 생성이 허용되던 것을 버그로 수정했다(2024-09).
    따라서 poll_answer는 채널 투표에서 절대 오지 않고, 개인별 적중률은 아래 셋 중 하나로만 된다.
    """
    INLINE_BUTTON = "inline_button"     # 채널 게시물 인라인 버튼 — D-0j로 수신 여부 실측 대기
    DISCUSSION_GROUP = "discussion_group"
    BOT_DM = "bot_dm"


@dataclass
class VoteRecord:
    """개인 참여 기록 (v1.9 신설).

    웹훅으로 받은 원문을 그대로 적재한 뒤 여기로 정규화한다.
    랭킹 규칙이 나중에 바뀌어도 원문이 있으면 과거 참여를 다시 계산할 수 있다.
    """
    user_id: str
    game_id: str
    choice_index: int
    voted_at_utc: datetime
    via: VoteChannel
    source_message_id: Optional[int] = None
    correct: Optional[bool] = None      # 경기 종료 후 채점


# 웹훅 — 공개 엔드포인트이므로 검증 없이는 누구나 가짜 업데이트를 넣을 수 있다
WEBHOOK_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"
WEBHOOK_ALLOWED_UPDATES = [
    "callback_query",     # 인라인 버튼 (개인 식별 — D-0j 검증 대상)
    "message",            # 봇 DM 예측 커맨드
    "poll",               # 채널 Poll 집계 (익명, 개인 식별 불가)
]


@dataclass
class QueueItem:
    """발송 큐 항목 (v1.9 신설).

    v1.8은 큐를 '살아있는 테이블'로 승격해 놓고 큐 항목 dataclass 자체가 계약에 없었다.
    is_late()가 요구하는 scheduled_utc도 어디에도 없었다.
    """
    idem_key: str
    content_type: ContentType
    scope: str
    scheduled_utc: datetime
    league: Optional[League] = None
    sports_day: Optional[str] = None
    game_id: Optional[str] = None
    state: SendState = SendState.QUEUED
    render_ref: Optional[str] = None       # GCS 경로 — 렌더는 발송 직전에, 큐 생성 시가 아니라
    render_at_utc: Optional[datetime] = None


@dataclass
class SendRecord:
    idem_key: str
    state: SendState
    chat_id: str
    content_type: ContentType
    # 조회 필드 — 키를 파싱해 되돌릴 수 없으므로 별도 컬럼으로 중복 저장한다
    league: Optional[League] = None
    sports_day: Optional[str] = None
    game_id: Optional[str] = None
    scheduled_utc: Optional[datetime] = None

    message_ids: list[int] = field(default_factory=list)
    media_group_id: Optional[str] = None
    file_ids: list[str] = field(default_factory=list)
    poll_result: Optional[PollResult] = None

    sent_at_utc: Optional[datetime] = None
    revision: int = 0
    start_rev: int = 0
    part_no: Optional[int] = None
    sent_count: int = 0

    # 클레임 소유 — 틱이 클레임하고 워커가 발송하므로 소유자 식별이 없으면
    # 워커가 '이미 claimed'인 자기 항목을 스스로 차단한다
    claimed_by: Optional[str] = None
    lease_expires_utc: Optional[datetime] = None

    retry_count: int = 0
    retry_429_count: int = 0        # 429는 재시도 카운터에서 제외되므로 별도 집계
    last_error: Optional[str] = None


def is_late(scheduled_utc: datetime, now_utc: datetime, content_type: ContentType) -> bool:
    """지각 판정. 기준은 큐에서 집행을 시작한 시각.

    단 REJUDGE_AT_SEND 콘텐츠는 실제 API 전송 직전에 이 함수를 다시 호출해야 한다 —
    페이서 대기 3분이 '10분 뒤 시작' 문안을 거짓말로 만들기 때문이다.
    """
    return (now_utc - scheduled_utc).total_seconds() > GRACE_SECONDS[content_type]


# ─────────────────────────────────────────────────────────────────────
# 팀 표시명 (v1.11 신설)
#
# v1.10까지 렌더러가 KBO 어댑터의 CODE_TEAM을 직접 임포트했다.
# 그러면 리그를 하나 추가할 때마다 파이프라인을 고쳐야 한다 —
# "파이프라인은 리그를 모르고 계약만 안다"는 원칙이 깨진다.
# 팀 코드 → 화면에 쓸 이름은 계약이 갖는다.
# ─────────────────────────────────────────────────────────────────────

TEAM_NAMES: dict[League, dict[str, str]] = {
    League.KBO: {
        "LG": "LG", "OB": "두산", "KT": "KT", "SK": "SSG", "NC": "NC",
        "WO": "키움", "HT": "KIA", "LT": "롯데", "SS": "삼성", "HH": "한화",
    },
    League.KL1: {
        "K21": "강원", "K22": "광주", "K35": "김천", "K10": "대전", "K26": "부천",
        "K09": "서울", "K27": "안양", "K01": "울산", "K18": "인천", "K05": "전북",
        "K04": "제주", "K03": "포항",
    },
    League.KBL: {
        "KT": "KT", "MOB": "현대모비스", "DB": "DB", "SS": "삼성", "LG": "LG",
        "SK": "SK", "KCC": "KCC", "KOGAS": "가스공사", "SONO": "소노", "KGC": "정관장",
    },
    League.VLEAGUE_M: {
        "KAL": "대한항공", "HDC": "현대캐피탈", "SFI": "삼성화재", "WOORI": "우리카드",
        "OK": "OK저축은행", "KEPCO": "한국전력", "KB": "KB손보",
    },
    League.VLEAGUE_W: {
        "HK": "흥국생명", "HDE": "현대건설", "GS": "GS칼텍스", "KEC": "도로공사",
        "IBK": "IBK기업은행", "KGC": "정관장", "PEPPER": "페퍼저축", "SOOP": "SOOP",
    },
    League.NPB: {
        "YOG": "요미우리", "DEN": "DeNA", "HAN": "한신", "CHU": "주니치",
        "YAK": "야쿠르트", "HIR": "히로시마", "SOF": "소프트뱅크", "NIP": "니혼햄",
        "LOT": "지바롯데", "RAK": "라쿠텐", "ORI": "오릭스", "SEI": "세이부",
        "CEN": "센트럴", "PAC": "퍼시픽",
    },
    League.MLB: {
        # 한국 중계·팬덤에서 쓰는 통칭
        "NYY": "양키스", "BOS": "레드삭스", "TB": "레이스", "TOR": "블루제이스", "BAL": "오리올스",
        "CLE": "가디언스", "MIN": "트윈스", "DET": "타이거스", "KC": "로열스", "CWS": "화삭스",
        "HOU": "애스트로스", "SEA": "매리너스", "TEX": "레인저스", "LAA": "에인절스", "ATH": "애슬레틱스",
        "ATL": "브레이브스", "NYM": "메츠", "PHI": "필리스", "MIA": "말린스", "WSH": "내셔널스",
        "MIL": "브루어스", "CHC": "컵스", "STL": "카디널스", "CIN": "레즈", "PIT": "파이리츠",
        "LAD": "다저스", "SD": "파드리스", "SF": "자이언츠", "ARI": "D-백스", "COL": "로키스",
    },
    # LCK — 표시명은 **중계에서 쓰는 공식 약어**다.
    # 소스 원문('Hanwha Life Esports')을 그대로 쓰면 카드에서 세 줄로 접혀 행이 깨진다
    # (2026-08-28 육안 점검에서 확인). 다른 리그가 '한화'·'두산' 두 글자인 것과 같은 이유로
    # 짧은 표기를 쓴다. 지어낸 이름이 아니라 LCK 중계 자막의 표준 약어다.
    # 'Kiwoom DRX'와 'DRX', 'HANJIN BRION'과 'BRION'은 네이밍 스폰서가 붙기 전후의
    # 같은 팀이라 한 코드로 모은다(어댑터의 LCK_TEAMS 참조).
    League.LCK: {
        "T1": "T1", "GEN": "젠지", "DK": "디플러스 기아", "KT": "KT",
        "HLE": "한화생명", "NS": "농심", "BFX": "BNK", "DNS": "DN",
        "DRX": "DRX", "BRO": "브리온",
    },
    # LoL 국제대회(MSI·Worlds)는 참가팀이 해마다 바뀐다. 고정 표를 둘 수 없어
    # 2026 MSI에서 실제로 관측된 팀만 적어둔다. 미등록 코드는 team_name()이
    # 코드를 그대로 보여주므로 카드가 죽지는 않는다.
    # 여기도 카드 폭에 맞는 짧은 표기를 쓴다.
    League.INTL_LOL: {
        "T1": "T1", "HANWHALIFEES": "한화생명", "G2ESPORTS": "G2",
        "TEAMLIQUID": "TL", "BILIBILIGAMI": "BLG", "TOPESPORTS": "TES",
        "KARMINECORP": "KC", "FURIA": "FURIA", "DEEPCROSSGAM": "DCG",
        "TEAMSECRETWH": "TSW", "LYON2024AMER": "Lyon",
    },
}

# 카드 한 행에 들어가는 팀 이름의 길이 상한.
# 넘으면 렌더 폭을 넘겨 줄바꿈이 생기고 행 정렬이 무너진다 — 규칙만 두면 반드시 어기므로
# assert_team_names()로 강제한다(최소 폰트 28px에서 배운 것).
TEAM_NAME_MAX_LEN = 8


CARD_PAPER = "#fffdfa"          # 카드 바탕색. v4.html의 --paper와 같아야 한다.
CARD_MIN_CONTRAST = 3.0         # 큰 글씨(28px 이상 굵게) 기준


def _srgb_luminance(hexcolor: str) -> float:
    c = hexcolor.lstrip("#")
    def lin(v: float) -> float:
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(c[i:i + 2], 16) / 255) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    a, b = sorted((_srgb_luminance(fg), _srgb_luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _mix(a: str, b: str, pct_a: float) -> str:
    """CSS color-mix(in srgb, a pct%, b) 근사."""
    ha, hb = a.lstrip("#"), b.lstrip("#")
    p = pct_a / 100
    return "#" + "".join(
        f"{round((int(ha[i:i+2],16) * p + int(hb[i:i+2],16) * (1 - p))):02x}"
        for i in (0, 2, 4))


def assert_league_color_contrast() -> None:
    """리그 색이 카드 면들 위에서 읽히는지 확인한다 — 리그를 추가할 때마다 자동으로 걸린다.

    v1.11c에서 승자 색을 초록 고정에서 **리그 잉크색**으로 바꿨다. 그러면 리그를 하나
    추가할 때마다 '그 색이 카드에서 읽히는가'가 새로 생기는 위험이 된다.
    눈으로 확인하는 규칙은 반드시 어기게 되므로(28px 폰트에서 배웠다) 여기서 강제한다.
    검사 면은 v4.html의 파생 토큰과 같은 비율로 만든다: 판 0% · 줄무늬 38% · 푸터 88%.
    """
    bad = []
    for lg, (ink, wash) in LEAGUE_COLORS.items():
        for label, pct in (("판", 0), ("줄무늬", 38), ("푸터", 88)):
            bg = _mix(wash, CARD_PAPER, pct) if pct else CARD_PAPER
            r = contrast_ratio(ink, bg)
            if r < CARD_MIN_CONTRAST:
                bad.append((lg.value, label, round(r, 2)))
    if bad:
        raise GateError(
            f"리그 색 대비 미달 {bad[:5]} (기준 {CARD_MIN_CONTRAST}) — "
            f"승자 이름·점수가 카드에서 안 읽힙니다.")


def assert_team_names() -> None:
    """표시명이 카드에 들어가는 길이인지 확인한다. 리그를 추가할 때마다 자동으로 걸린다."""
    bad = [(lg.value, code, name)
           for lg, table in TEAM_NAMES.items()
           for code, name in table.items()
           if len(name) > TEAM_NAME_MAX_LEN]
    if bad:
        raise GateError(
            f"팀 표시명이 {TEAM_NAME_MAX_LEN}자를 넘습니다 {bad[:5]} — "
            f"카드에서 줄바꿈이 생겨 행 정렬이 무너집니다.")


def team_name(team: "TeamRef") -> str:
    """화면에 쓸 팀 이름. 등록되지 않은 코드는 코드를 그대로 보여준다.

    여기서 예외를 던지면 팀 하나 때문에 카드 전체가 안 나간다.
    이름을 모르는 것은 사실 오류가 아니므로 코드로 대체하고 넘어간다.
    """
    return TEAM_NAMES.get(team.league, {}).get(team.team_code, team.team_code)


# ── 경기장 이름 (한국어 표기) ────────────────────────────────
#
# 소스가 주는 그대로 쓰면 NPB 카드에 "京セラD大阪 · ベルーナドーム"처럼 일본어가
# 박힌다. 한국 시청자에게는 읽히지 않는 글자다 — 카드에 넣을 이유가 없다.
# 아는 것만 바꾸고, 모르는 것은 원문을 그대로 둔다(빈칸보다는 낫다).
VENUE_NAMES: dict[str, str] = {
    # NPB 12구단 홈구장
    "バンテリンドーム": "반테린돔",
    "京セラD大阪": "교세라돔",
    "マツダスタジアム": "마쓰다스타디움",
    "エスコンＦ": "에스콘필드",
    "エスコンF": "에스콘필드",
    "横浜": "요코하마",
    "神宮": "진구",
    "東京ドーム": "도쿄돔",
    "ベルーナドーム": "베루나돔",
    "みずほPayPay": "미즈호페이페이돔",
    "楽天モバイル": "라쿠텐모바일파크",
    "ZOZOマリン": "조조마린",
    "甲子園": "고시엔",
    # 지방 개최 구장
    "ほっと神戸": "홋토모토고베",
    "那覇": "나하",
    "盛岡": "모리오카",
    "郡山": "고리야마",
    "富山": "도야마",
}


# ── 홈구장 표 — 홈/원정이 뒤집혔는지 잡는 유일한 자동 검사 ──
#
# 홈과 원정이 바뀌어도 카드는 멀쩡해 보인다. 팀 이름 두 개가 자리만 바꾼 것이라
# 숫자 검증은 전부 통과하고, 점수까지 함께 뒤집히면 **승패를 반대로 내보낸다.**
# 실제로 NPB가 그랬다 — 8/30 여섯 경기가 전부 뒤집혀 있었고, 카드를 눈으로 보다
# "고시엔에서 요미우리가 홈?"이 이상해서 발견했다.
#
# 사람 눈에 기대지 않으려면 기계가 볼 수 있는 근거가 필요하다. 그게 **경기장**이다.
# 홈팀의 홈구장과 경기장이 다르면 뒤집힌 것이다(지방 개최는 예외라 표에서 뺀다).
HOME_VENUES: dict[League, dict[str, frozenset[str]]] = {
    League.KBO: {
        "LG": frozenset({"잠실"}), "OB": frozenset({"잠실"}),
        "KT": frozenset({"수원"}), "SK": frozenset({"문학"}),
        "NC": frozenset({"창원"}), "WO": frozenset({"고척"}),
        "HT": frozenset({"광주"}), "LT": frozenset({"사직"}),
        "SS": frozenset({"대구"}), "HH": frozenset({"대전"}),
    },
    # 올스타(CEN·PAC)는 홈구장이 없다 — 표에 없으므로 판정에서 빠진다.
    League.NPB: {
        "NIP": frozenset({"エスコンＦ", "エスコンF"}),    # 니혼햄
        "LOT": frozenset({"ZOZOマリン"}),                 # 지바롯데
        "SOF": frozenset({"みずほPayPay"}),               # 소프트뱅크
        "ORI": frozenset({"京セラD大阪"}),                # 오릭스
        "SEI": frozenset({"ベルーナドーム"}),             # 세이부
        "RAK": frozenset({"楽天モバイル"}),               # 라쿠텐
        "DEN": frozenset({"横浜"}),                       # DeNA
        "CHU": frozenset({"バンテリンドーム"}),           # 주니치
        "YOG": frozenset({"東京ドーム"}),                 # 요미우리
        "HAN": frozenset({"甲子園"}),                     # 한신
        "YAK": frozenset({"神宮"}),                       # 야쿠르트
        "HIR": frozenset({"マツダスタジアム"}),           # 히로시마
    },
}


def home_venue_mismatches(games: "list[Game]") -> "list[tuple[Game, str]]":
    """홈팀의 홈구장과 경기장이 어긋나는 경기를 모은다.

    지방 개최(NPB의 나하·모리오카 등)는 표에 없으므로 걸리지 않는다 —
    **모르는 구장은 통과시킨다.** 여기서 잡고 싶은 것은 개별 예외가 아니라
    "한 리그가 통째로 뒤집힌" 상태다.
    """
    out = []
    for g in games:
        table = HOME_VENUES.get(g.league)
        if not table or not g.venue:
            continue
        want = table.get(g.home.team_code)
        if not want:
            continue
        key = "".join(str(g.venue).split()).replace("　", "")
        # 이 경기장이 **다른 팀의** 홈구장이면 뒤집힌 것이다.
        # 어느 팀 것도 아니면 지방 개최 — 넘어간다.
        owner = next((c for c, vs in table.items() if key in vs), None)
        if owner is None:
            continue
        if key not in want:
            out.append((g, owner))
    return out


def assert_home_away(games: "list[Game]", *, max_ratio: float = 0.3) -> None:
    """홈/원정이 통째로 뒤집혔으면 멈춘다.

    한두 건은 지방 개최나 소스의 개별 오류일 수 있으므로 통과시킨다.
    3할을 넘으면 개별 사고가 아니라 매핑이 반대인 것이다.
    """
    known = [g for g in games
             if g.venue and HOME_VENUES.get(g.league, {}).get(g.home.team_code)]
    if len(known) < 4:
        return                                   # 표본이 적으면 판정하지 않는다
    bad = home_venue_mismatches(known)
    if len(bad) / len(known) > max_ratio:
        g, owner = bad[0]
        raise GateError(
            f"{games[0].league.value}: 홈/원정이 뒤집힌 것으로 보입니다 — "
            f"{len(bad)}/{len(known)}경기에서 경기장이 홈팀과 어긋납니다. "
            f"예) 홈 {g.home.team_code} 인데 경기장 {g.venue}는 {owner}의 홈구장입니다.")


def venue_name(venue: "str | None") -> str:
    """화면에 쓸 경기장 이름.

    소스가 전각 공백이나 사이 공백을 섞어 준다("横 浜", "エスコンＦ").
    공백을 지우고 맞춰본 뒤, 모르면 원문을 그대로 돌려준다.
    """
    if not venue:
        return ""
    key = "".join(venue.split()).replace("　", "")
    return VENUE_NAMES.get(key, venue)


# ─────────────────────────────────────────────────────────────────────
# 기록·순위 모델 (v1.10 신설)
#
# v1.9까지 파이프라인이 아는 것은 "언제 누가 붙어서 몇 대 몇"뿐이었다.
# 그 위에 서 있던 콘텐츠(순위표·리더보드·분석·기록 한 줄·기록 감시)는
# 채울 데이터가 없어 설계만 있고 렌더가 불가능했다.
#
# 사실 잠금 원칙: 값은 소스 원문 문자열을 그대로 보관한다.
# 반올림·재계산은 게이트의 대조용으로만 쓰고, 표시에는 원문을 쓴다.
# ─────────────────────────────────────────────────────────────────────

# 정규시즌 총 경기수 — 잔여 경기 표시의 유일한 근거.
# 여기에 없는 리그는 잔여 경기를 표시하지 않는다(추측 금지).
REGULAR_SEASON_GAMES: dict[League, Optional[int]] = {
    League.KBO: 144, League.MLB: 162, League.NPB: 143,
    League.KBL: 54, League.VLEAGUE_M: 36, League.VLEAGUE_W: 30,
    League.KL1: 38, League.EPL: 38, League.LALIGA: 38, League.SERIEA: 38,
    League.BUNDESLIGA: 34, League.LIGUE1: 34,
    League.UCL: None, League.LCK: None, League.INTL_LOL: None,
}

# 리그별 팀 수 — 순위표 완전성 검사용. 한 팀이라도 빠지면 게이트가 막는다.
LEAGUE_TEAM_COUNT: dict[League, Optional[int]] = {
    League.KBO: 10, League.MLB: 30, League.NPB: 12,
    League.KBL: 10, League.VLEAGUE_M: 7, League.VLEAGUE_W: 7,
    League.KL1: 12, League.EPL: 20, League.LALIGA: 20, League.SERIEA: 20,
    League.BUNDESLIGA: 18, League.LIGUE1: 18,
    League.UCL: None, League.LCK: None, League.INTL_LOL: None,
}


class StreakKind(str, Enum):
    WIN = "W"
    LOSS = "L"
    DRAW = "D"
    NONE = "-"


@dataclass(frozen=True)
class WLD:
    """승-무-패. 소스마다 표기 순서가 달라(KBO 홈/방문은 승-무-패,
    상대전적 매트릭스는 승-패-무) 어댑터에서 반드시 이 형태로 정규화한다."""
    win: int
    loss: int
    draw: int

    @property
    def total(self) -> int:
        return self.win + self.loss + self.draw

    def mirrored(self) -> "WLD":
        return WLD(self.loss, self.win, self.draw)

    def __str__(self) -> str:
        return f"{self.win}-{self.loss}-{self.draw}"


@dataclass(frozen=True)
class Standing:
    """팀 순위 한 줄. 값은 소스가 준 것만 담는다."""
    league: League
    season: str
    team_code: str
    rank: int
    games: int
    record: WLD
    pct: str                     # 원문 그대로 ("0.607")
    games_behind: str            # 원문 그대로 ("0", "16.5")
    last10: Optional[WLD] = None
    streak_kind: StreakKind = StreakKind.NONE
    streak_len: int = 0
    home: Optional[WLD] = None
    away: Optional[WLD] = None

    @property
    def remaining(self) -> Optional[int]:
        total = REGULAR_SEASON_GAMES.get(self.league)
        return None if total is None else total - self.games

    def validate(self) -> None:
        if self.rank < 1:
            raise GateError(f"순위 이상: {self.team_code} rank={self.rank}")
        if self.record.total != self.games:
            raise GateError(
                f"{self.team_code}: 승+패+무({self.record.total}) != 경기({self.games})")
        for name, part in (("home", self.home), ("away", self.away)):
            if part and part.total > self.games:
                raise GateError(f"{self.team_code}: {name} 합계가 경기수 초과")
        if self.home and self.away:
            merged = WLD(self.home.win + self.away.win,
                         self.home.loss + self.away.loss,
                         self.home.draw + self.away.draw)
            if merged != self.record:
                raise GateError(
                    f"{self.team_code}: 홈+방문({merged}) != 전체({self.record})")
        if self.last10 and self.last10.total > 10:
            raise GateError(f"{self.team_code}: 최근10경기 합계 {self.last10.total}")
        if self.streak_len < 0:
            raise GateError(f"{self.team_code}: 연속 음수")
        # 승률 재계산 대조 — 무승부는 KBO/NPB 규칙대로 분모에서 뺀다
        denom = self.record.win + self.record.loss
        if denom and self.pct:
            calc = self.record.win / denom
            try:
                given = float(self.pct)
            except ValueError:
                raise GateError(f"{self.team_code}: 승률 파싱 불가 {self.pct!r}")
            if abs(calc - given) > 0.0015:
                raise GateError(
                    f"{self.team_code}: 승률 불일치 소스={given} 재계산={calc:.3f}")
        total = REGULAR_SEASON_GAMES.get(self.league)
        if total is not None and self.games > total:
            raise GateError(f"{self.team_code}: 경기수 {self.games} > 정규시즌 {total}")


@dataclass(frozen=True)
class LeaderEntry:
    """부문별 순위 한 줄. value는 소스 원문 문자열 — 절대 재포맷하지 않는다."""
    category: str                # "홈런"
    stat_key: str                # "HR_CN"
    rank: int
    player_id: str
    name: str
    team_code: str
    value: str

    def validate(self) -> None:
        if not self.player_id:
            raise GateError(f"{self.category} {self.rank}위: player_id 없음")
        if not self.name or not self.value:
            raise GateError(f"{self.category} {self.rank}위: 이름/값 비어 있음")


@dataclass
class RecordBook:
    """한 리그·한 시즌의 기록 스냅샷. 콘텐츠는 이것만 보고 렌더한다."""
    league: League
    season: str
    collected_utc: datetime
    source_url: str
    standings: list[Standing] = field(default_factory=list)
    # (팀, 상대) -> 그 팀 기준 전적
    h2h: dict[tuple[str, str], WLD] = field(default_factory=dict)
    # 부문명 -> 순위 목록
    leaders: dict[str, list[LeaderEntry]] = field(default_factory=dict)

    def team(self, code: str) -> Optional[Standing]:
        return next((s for s in self.standings if s.team_code == code), None)

    def between(self, a: str, b: str) -> Optional[WLD]:
        return self.h2h.get((a, b))

    def age_seconds(self, now_utc: datetime) -> float:
        return (now_utc - self.collected_utc).total_seconds()


# 기록 스냅샷 신선도 상한(초). 이보다 오래된 스냅샷으로는 렌더하지 않는다 —
# 묵은 순위표를 오늘 것처럼 내보내는 것이 가장 흔한 사실 오류다.
RECORD_MAX_AGE_SECONDS = 6 * 3600


def leader_value_num(raw: str) -> float:
    """부문 값 문자열을 수치로. 이닝의 '140 2/3' 같은 분수 표기를 처리한다."""
    t = raw.replace(",", "").strip()
    m = re.match(r"^(\d+)\s+(\d+)/(\d+)$", t)
    if m:
        return int(m.group(1)) + int(m.group(2)) / int(m.group(3))
    m = re.match(r"^(\d+)/(\d+)$", t)
    if m:
        return int(m.group(1)) / int(m.group(2))
    try:
        return float(t)
    except ValueError:
        raise GateError(f"부문 값 해석 불가 {raw!r}")


# 낮을수록 1위인 부문. 여기 없는 부문은 높을수록 1위로 본다.
ASCENDING_CATEGORIES = frozenset({"평균자책점", "WHIP", "피안타율"})


def assert_leader_order(category: str, entries: list["LeaderEntry"]) -> None:
    """순위와 값이 같은 방향으로 움직이는지 검사.

    파싱이 한 칸 밀리면 순위는 1,2,3인데 값이 뒤섞인다. 눈으로는 안 보이고
    카드에는 그대로 찍힌다 — 이 검사가 그걸 잡는다.
    """
    vals = [leader_value_num(e.value) for e in entries]
    asc = category.split(" ")[-1] in ASCENDING_CATEGORIES
    for i in range(1, len(entries)):
        prev_r, cur_r = entries[i - 1].rank, entries[i].rank
        dv = vals[i] - vals[i - 1]
        if prev_r == cur_r:
            if abs(dv) > 1e-9:
                raise GateError(
                    f"부문 {category}: 공동 {cur_r}위인데 값이 다름 "
                    f"({entries[i-1].value} vs {entries[i].value})")
        elif cur_r > prev_r:
            if (dv < -1e-9) if asc else (dv > 1e-9):
                raise GateError(
                    f"부문 {category}: 순위와 값이 반대로 움직임 "
                    f"({prev_r}위 {entries[i-1].value} → {cur_r}위 {entries[i].value})")


def assert_recordbook(rb: RecordBook, *, require_h2h: bool = True,
                      now_utc: Optional[datetime] = None) -> None:
    """기록 스냅샷 게이트. 하나라도 어긋나면 렌더를 막는다.

    핵심은 교차 대조다 — 순위표와 상대전적 매트릭스는 KBO가 각각 따로 만든 표인데
    둘의 승·패·무 합계가 반드시 같아야 한다. 파싱이 한 칸이라도 밀리면 여기서 걸린다.
    """
    if not rb.standings:
        raise GateError(f"{rb.league.value}: 순위표 0건 (0건은 항상 의심)")

    expect = LEAGUE_TEAM_COUNT.get(rb.league)
    if expect is not None and len(rb.standings) != expect:
        raise GateError(
            f"{rb.league.value}: 팀 {len(rb.standings)}개 (기대 {expect}개)")

    codes = [s.team_code for s in rb.standings]
    if len(set(codes)) != len(codes):
        raise GateError(f"{rb.league.value}: 순위표에 중복 팀")

    ranks = sorted(s.rank for s in rb.standings)
    if ranks != list(range(1, len(ranks) + 1)):
        raise GateError(f"{rb.league.value}: 순위 불연속 {ranks}")

    prev_gb = None
    for s in sorted(rb.standings, key=lambda x: x.rank):
        s.validate()
        try:
            gb = float(s.games_behind)
        except ValueError:
            raise GateError(f"{s.team_code}: 게임차 파싱 불가 {s.games_behind!r}")
        if prev_gb is not None and gb < prev_gb - 1e-9:
            raise GateError(f"{s.team_code}: 게임차 역전 {prev_gb} → {gb}")
        prev_gb = gb

    if require_h2h:
        if not rb.h2h:
            raise GateError(f"{rb.league.value}: 상대전적 0건 (0건은 항상 의심)")
        for (a, b), wld in rb.h2h.items():
            if a == b:
                raise GateError(f"상대전적에 자기 자신 {a}")
            if a not in codes or b not in codes:
                raise GateError(f"상대전적에 순위표에 없는 팀 {a}/{b}")
            back = rb.h2h.get((b, a))
            if back is None:
                raise GateError(f"상대전적 비대칭: {b}→{a} 없음")
            if back != wld.mirrored():
                raise GateError(f"상대전적 불일치 {a}vs{b} {wld} ↔ {b}vs{a} {back}")
        # 행 합계 == 순위표
        for s in rb.standings:
            rows = [w for (a, _), w in rb.h2h.items() if a == s.team_code]
            if len(rows) != len(codes) - 1:
                raise GateError(
                    f"{s.team_code}: 상대전적 {len(rows)}개 (기대 {len(codes)-1}개)")
            tot = WLD(sum(w.win for w in rows), sum(w.loss for w in rows),
                      sum(w.draw for w in rows))
            if tot != s.record:
                raise GateError(
                    f"{s.team_code}: 상대전적 합계({tot}) != 순위표({s.record})")

    for cat, entries in rb.leaders.items():
        if not entries:
            raise GateError(f"부문 {cat}: 0건 (0건은 항상 의심)")
        for e in entries:
            e.validate()
        rr = [e.rank for e in entries]
        if rr != sorted(rr):
            raise GateError(f"부문 {cat}: 순위 정렬 어긋남 {rr}")
        if rr[0] != 1:
            raise GateError(f"부문 {cat}: 1위 없음")
        assert_leader_order(cat, entries)

    if now_utc is not None and rb.age_seconds(now_utc) > RECORD_MAX_AGE_SECONDS:
        raise GateError(
            f"{rb.league.value}: 기록 스냅샷이 {rb.age_seconds(now_utc)/3600:.1f}시간 묵음")


# ─────────────────────────────────────────────────────────────────────
# 완전성 보증 — 리그·콘텐츠를 추가하고 딕셔너리를 빠뜨리면 임포트 시점에 터진다.
# (런타임에 터지면 그 리그에서 큐 생성 잡이 죽어 하루 전체 큐가 안 만들어진다)
# ─────────────────────────────────────────────────────────────────────

assert set(SCORE_UNIT_BY_LEAGUE) == set(League), "SCORE_UNIT_BY_LEAGUE 누락"
assert set(SEASON_FORMAT_BY_LEAGUE) == set(League), "SEASON_FORMAT_BY_LEAGUE 누락"
assert set(ALLOWED_TRANSITIONS) == set(Status), "ALLOWED_TRANSITIONS 누락"
assert set(SCORE_MAX_BY_UNIT) == set(ScoreUnit), "SCORE_MAX_BY_UNIT 누락"
assert set(DECIDED_BY_ALLOWED) == set(ScoreUnit), "DECIDED_BY_ALLOWED 누락"
assert set(GRACE_SECONDS) == set(ContentType), "GRACE_SECONDS 누락"
assert set(LEASE_SECONDS) == set(ContentType), "LEASE_SECONDS 누락"
assert set(PACER_PRIORITY) == set(ContentType), "PACER_PRIORITY 누락"
for _ct in ContentType:
    assert LEASE_SECONDS[_ct] < GRACE_SECONDS[_ct], f"{_ct.value}: 리스가 유예보다 길다"
assert CARD_WIDTH_PX + CARD_MAX_HEIGHT_PX <= GATE_PHOTO_DIM_SUM_MAX
assert set(REGULAR_SEASON_GAMES) == set(League), "REGULAR_SEASON_GAMES 누락"
assert set(LEAGUE_TEAM_COUNT) == set(League), "LEAGUE_TEAM_COUNT 누락"
