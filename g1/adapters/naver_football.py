"""유럽 축구 수집기 — 네이버 해외축구 (v1.15 신설).

EPL · 라리가 · 세리에A · 분데스리가 · 리그1 · **챔피언스리그 · 유로파리그** 7개.
대표님 지시(2026-09-07): *"유에파 챔피언스리그 유로파리그도 이제 추가해야할때가
된것같아"* · 범위는 **5대리그 + UCL·유로파**로 확정.

────────────────────────────────────────────────────────────────────
**왜 football-data.org가 아니라 이쪽인가 (2026-09-07 실측으로 결정).**

football-data.org 무료 등급은 5대리그와 UCL을 주지만 **유로파리그를 안 준다**
(실측: `/v4/competitions/EL/teams` → 403 · 유료 구독 필요). 그리고 팀을
`MCI`·`ARS` 같은 영문 세 글자 코드로만 주므로, 켜려면 **약 200개 구단의 한글
표기표를 손으로 채워야** 한다. 유럽 대항전은 해마다 참가팀이 바뀌므로 그 표는
매년 다시 손을 대야 한다.

네이버는 셋을 한 번에 푼다:
  · 유로파리그(`europa`)·챔피언스리그(`champs`)를 **무료로** 준다
  · **팀 이름을 이미 한글로 준다** (`우니온 베를린`·`AT 마드리드`)
    → 표기표가 통째로 필요 없다. 실측 135개 구단 전부 `is_readable_ko` 통과.
  · 우리가 **이미 쓰는 소스**다(`naver_game.py`가 흐름 보강에 쓴다) — 새 공급망이
    늘지 않는다

**대가는 하나다: 비공식 소스다.** 카드 꼬리말에 "공식 기록"이라고 쓸 수 없다
(약점 107 — 'LCK 공식 결과'라 적었는데 실제로는 팬 위키였다).
그래서 이 리그들은 `pipeline.UNOFFICIAL_SOURCE_LEAGUES`에 넣는다.

────────────────────────────────────────────────────────────────────
**소스 구조 (실측 2026-09-07).**

    /schedule/games?upperCategoryId=wfootball&categoryId=epl
                   &fromDate=..&toDate=..&size=..
      → gameId · categoryId · gameDateTime(**한국시각**, tz 없음) ·
        home/awayTeamName(한글) · home/awayTeamScore · statusCode ·
        winner · cancel · suspended

    ⚠️ `categoryId`를 **반드시 함께 보낸다.** 빼면 해외축구 18개 대회가 통째로
    와서 45일 조회가 `size=500` 상한에 그대로 닿았다(실측 2026-09-07:
    mls 60 · england2 59 · u20여자월드컵 51 … 합 500 = 잘림).
    상한에 닿으면 **어느 경기가 빠졌는지 알 수 없다** — 우리가 원한 7개 중
    하나가 조용히 사라진다. 카테고리를 지정하면 같은 45일이 41건으로 줄어
    상한과 무관해지고, 남의 소스 부담도 1/12로 준다.

    /schedule/games/{gameId}
      → 위 + stadium(한글) · categoryName · roundCode · seasonYear

목록 조회 한 번으로 여러 날을 받고, 경기장은 **필요한 경기만** 상세로 채운다.
무료로 쓰는 남의 소스이므로 요청을 아낀다.

⚠️ `gameDateTime`에는 시간대가 없다. **한국시각이다** — `naver_game.py`가 같은
필드를 그렇게 쓰고 있고, 분데스 03:30(독일 현지 20:30)로 실측 확인했다.
UTC로 오해하면 모든 경기가 9시간 어긋난다.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (KST, GateError, Game, GameMeta, Goal, League,
                      LINEUP_ENABLED, LINEUP_WATCH_SECONDS, PlayerLine, Score,
                      ScoreUnit, Status, TeamRef, UnknownStatus, is_readable_ko)

BASE = "https://api-gw.sports.naver.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
           "Referer": "https://m.sports.naver.com/"}

# 리그 → 네이버 카테고리. **실측으로 확인한 것만 적는다.**
# (2026-09-07: wfootball 카테고리를 전수 조회해 경기 수까지 확인했다.)
CATEGORY: dict = {
    League.EPL: "epl",
    League.LALIGA: "primera",
    League.SERIEA: "seria",
    League.BUNDESLIGA: "bundesliga",
    League.LIGUE1: "ligue1",
    League.UCL: "champs",
    League.UEL: "europa",
    League.MLS: "mls",
}
LEAGUE_BY_CATEGORY = {v: k for k, v in CATEGORY.items()}

# ── 한국 선수만 보는 리그 (v1.15) ────────────────────────────────
#
# 대표님 지시(2026-09-07): MLS는 **"한국선수 출전경기만 포함"**.
# 리그 전체를 발행하지 않는다 — 그래서 거르는 자리를 **수집**으로 잡았다.
# 파이프라인에 리그 예외를 만들면 "리그마다 따로 논다"가 되살아난다
# (대표님 불만 ③). 파이프라인은 MLS를 다른 리그와 똑같이 다루고,
# 애초에 적은 경기만 받는다.
KOREAN_PLAYER_ONLY: frozenset = frozenset({League.MLS})

# **이 표는 사람이 관리한다. 자동으로 못 만든다.**
#
# 이름으로 국적을 추정해 보려다 실패한 기록을 남긴다(2026-09-07 실측):
# 한 라운드 라인업 239명에서 '한국식 성씨 + 2~4자 한글'로 후보를 뽑았더니
# 30명이 걸렸는데, 실제 한국 선수는 **둘뿐**이었다. 나머지는 전부 외국
# 이름의 한글 표기였다 — 구트만·고메즈·설리반·오르다스·지머먼·나바로….
# 소스는 국적을 주지 않고(선수 상세 API는 403/404), 이름은 국적이 아니다.
#
# 그래서 표를 사람이 적는다. **표에 없는 선수는 못 잡는다** — 이건 결함이
# 아니라 이 설계의 값이다. 지어내는 것보다 못 잡는 편이 정직하다.
# 새 선수가 오면 여기 한 줄 추가한다.
#
# `team`은 **1차 거름망**이다. 경기 목록만 보고 후보를 좁혀 라인업 조회를
# 아낀다(13일치 전 경기를 다 조회하면 65요청, 팀으로 좁히면 4~6개다).
# 이적하면 라인업에서 다른 팀으로 잡히고, 그때 아래 `_note_transfer`가 알린다.
#
# **v1.17c (2026-09-08) — 대표님 지시로 유럽까지 넓혔다.**
# *"한국선수 해외리그 출전하는 경기는 꼭 알림이 필요한데"*
#
# ⚠️ **`id`는 이제 선택이다.** 예전에는 MLS 둘뿐이라 실측으로 id까지 읽어
# 적어 두었는데, 그 방식은 **선수를 못 만나면 표에 못 넣는다**는 뜻이었다.
# 실제로 이강인(AT 마드리드)이 표에 없어서 못 잡고 있었고, 그것을 발견한
# 것은 우리 감시가 아니라 라인업을 눈으로 훑다가였다.
# 이름만 있으면 라인업에서 만나는 순간 소스가 id를 준다 — 표가 id를
# 기다릴 이유가 없다.
#
# ⚠️ **`team`은 1차 거름망일 뿐 판정 기준이 아니다.** MLS는 이것으로
# 조회를 아끼지만(전 경기를 다 볼 수 없으므로), 유럽은 어차피 전 경기
# 라인업을 받으므로 **이름만으로 찾는다.** 그래서 이적해도 자동으로 따라가고,
# 팀이 어긋나면 `_note_transfer`가 알린다.
#
# 출처: 2026-09-08 기사 두 건(스포츠경향 09-02 · 뉴스핌 08-26)에서 명단을
# 얻고, **우리 소스의 라인업에서 표기를 실제로 확인한 것에 ✓를 붙였다.**
# 확인 못 한 선수는 그 경기에 선발이 아니었을 뿐이라 그대로 둔다 —
# 라인업에서 만나면 그때 잡힌다. **못 잡는 것은 거짓이 아니지만,
# 지어낸 표기를 적으면 그건 거짓이다.**
KOREAN_PLAYERS: dict = {
    # ── MLS (2026-09-07 실측 — id·팀 모두 실제 응답에서 읽었다) ──
    "손흥민": {"id": "439351", "team": "LAFC"},
    "김기희": {"id": "PxcfTi3R", "team": "시애틀"},
    # ── 유럽 (2026-09-08) ──
    "김민재": {"team": "바이에른 뮌헨"},      # ✓ 라인업 실측 확인
    "이재성": {"team": "마인츠"},             # ✓ 라인업 실측 확인
    "이강인": {"team": "AT 마드리드"},        # ✓ 라인업 실측 확인 (2026-09-07)
    "정우영": {"team": "우니온 베를린"},
    "설영우": {"team": "아우크스부르크"},
    "황희찬": {"team": "샬케"},
    "홍현석": {"team": "마인츠"},
    "김지수": {"team": "브렌트퍼드"},
    "박승수": {"team": "뉴캐슬"},
    # 대항전(UCL·유로파)으로 우리 카드에 들어오는 선수들.
    # 소속 리그 자체는 우리가 발행하지 않지만, 대항전 경기는 발행한다.
    "황인범": {"team": "포르투"},
    "이한범": {"team": "클럽 브뤼헤"},
    "오현규": {"team": "베식타시"},
}

# 라인업을 조회할 경기 수 상한(한 틱). 남의 소스다 — 필요한 것만 본다.
LINEUP_MAX_PER_TICK = 8
# 라인업은 킥오프 직전에야 채워진다(실측: 3일 전 조회 시 빈 객체).
# 이 시각 이후의 경기만 조회한다 — 그전엔 있어도 없다.
LINEUP_LOOKAHEAD_SECONDS = 3 * 3600
# 득점자를 조회할 경기 수 상한(한 틱). 라인업과 따로 둔다 — 대상이 다르다
# (라인업은 킥오프 전후, 골은 진행·종료 경기).
GOALS_MAX_PER_TICK = 8

# 소스 상태값 → 계약 상태. **문서화된 도메인 전체를 적는다.**
# 모르는 값이 오면 `UnknownStatus`로 올려 **그 경기만** 건너뛴다 —
# 상태값 하나가 대회 전체를 죽이면 안 된다(약점 111·MLB에서 겪은 것).
_STATUS = {
    "BEFORE": Status.SCHEDULED,
    "READY": Status.SCHEDULED,
    "STARTED": Status.LIVE,
    "LIVE": Status.LIVE,
    "PLAYING": Status.LIVE,
    "RESULT": Status.FINAL,
    "END": Status.FINAL,
    "CANCEL": Status.CANCELED,
    "CANCELED": Status.CANCELED,
    "POSTPONE": Status.POSTPONED,
    "POSTPONED": Status.POSTPONED,
    "SUSPENDED": Status.SUSPENDED,
}

# **8자를 넘는 이름만 줄인다.** 카드 팀명 칸이 그 이상을 못 담는다
# (`contract.TEAM_NAME_MAX_LEN`). 실측 135개 중 둘뿐이고, 둘 다 국내 중계가
# 쓰는 짧은 표기가 이미 있다 — 지어내는 것이 아니다.
NAME_FIX: dict = {
    "PSV 아인트호벤": "PSV",
    "알크마르 잔스트리크": "AZ 알크마르",
}

REQUEST_GAP_SECONDS = 1.2          # 남의 소스다 — 아끼는 쪽으로
SCHEDULE_WINDOW_DAYS = 30          # 한 번에 받는 날짜 폭
VENUE_MAX_PER_TICK = 12            # 경기장은 필요한 것만, 한 틱에 이만큼까지

# ⚠️ **대항전은 앞을 멀리 봐야 한다 — 이건 취향이 아니라 감시의 전제다.**
#
# 커버리지 감시(`coverage.run`)는 "시즌 중인데 스냅샷이 0건이면 소스 구조가
# 바뀐 것"으로 읽는다. 그런데 챔피언스리그·유로파는 **시즌 중에도 라운드
# 사이가 통째로 빈다**(실측: 최대 41·42일, 2026-09-07 전수 조회).
# 앞을 7일만 보면 1년의 절반 넘는 날에 스냅샷이 0건이 되고,
# 그 날마다 헛경보가 울린다 — 그러면 진짜 사고가 소음에 묻힌다.
#
# 앞을 50일 보면 **어떤 날에 조회해도 다음 라운드가 창 안에 들어온다**
# (최대 공백 42일 + 여유 8일). 그러면 0건이 다시 '진짜 신호'가 된다.
# 침묵 상한을 늘려 경보를 무디게 하는 대신, 창을 넓혀 침묵 자체를 없앤 것이다.
#
# 비용은 작다: 카테고리를 지정하므로 대항전 50일 응답이 27건 안팎이고,
# 30일씩 두 번이면 끝난다. 5대리그는 매주 도니까 넓힐 이유가 없다.
AHEAD_DAYS: dict = {League.UCL: 50, League.UEL: 50}
AHEAD_DAYS_DEFAULT = 10


def _int(v) -> int:
    """소스가 숫자를 **문자열로** 준다(`"goal": "0"`). 못 읽으면 0으로 본다."""
    try:
        return int(str(v or 0))
    except (TypeError, ValueError):
        return 0


def _card_label(pl: dict) -> "str | None":
    """경고·퇴장. **소스 필드 두 개가 뜻이 다르다.**

    `card`는 경고 수, `yellowRedCard`는 경고 누적 퇴장이다.
    둘을 합쳐 읽으면 경고 한 장이 퇴장으로 나간다.
    """
    if _int(pl.get("yellowRedCard")):
        return "퇴장"
    n = _int(pl.get("card"))
    return "경고" if n else None


def _kst_to_utc(text: str) -> datetime:
    """`2026-09-12T03:30:00` → aware UTC.

    **시간대가 없는 값이다. 한국시각으로 읽는다** (파일 맨 위 주석).
    UTC로 오해하면 전 경기가 9시간 어긋나고, 그건 카드가 아니라 사실이 틀리는 것이다.
    """
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=KST).astimezone(timezone.utc)


class NaverFootballAdapter(NoticeMixin):
    """한 리그를 수집한다. 목록 응답은 대회가 섞여 오므로 카테고리로 거른다."""

    def __init__(self, league: League) -> None:
        if league not in CATEGORY:
            raise GateError(f"naver_football: 지원하지 않는 리그 {league.value}")
        self.league = league
        self.category = CATEGORY[league]
        self._opener = make_opener()
        self._last = 0.0
        NoticeMixin.__init__(self)

    # ── HTTP ──────────────────────────────────────────────────
    def _get(self, path: str, label: str) -> dict:
        gap = time.monotonic() - self._last
        if gap < REQUEST_GAP_SECONDS:
            time.sleep(REQUEST_GAP_SECONDS - gap)
        self._last = time.monotonic()
        req = urllib.request.Request(BASE + path, headers=HEADERS)
        return json.loads(_fetch(self._opener, req, label=label))

    # ── 수집 ──────────────────────────────────────────────────
    def fetch(self, today: datetime | None = None, *,
              back_days: int = 3, ahead_days: int | None = None) -> list[Game]:
        """[오늘−back, 오늘+ahead] 구간의 그 리그 경기.

        뒤를 3일 보는 이유: 소스가 결과를 늦게 채우는 날이 있고, 결과 카드는
        마감이 지나도 큐에 남기 때문이다(약점 21).
        앞을 얼마나 보는지는 리그마다 다르다 — `AHEAD_DAYS` 주석 참고.
        """
        if ahead_days is None:
            ahead_days = AHEAD_DAYS.get(self.league, AHEAD_DAYS_DEFAULT)
        now = today or datetime.now(timezone.utc)
        d0 = (now.astimezone(KST) - timedelta(days=back_days)).strftime("%Y-%m-%d")
        d1 = (now.astimezone(KST) + timedelta(days=ahead_days)).strftime("%Y-%m-%d")
        raw = self._schedule(d0, d1)

        games: list[Game] = []
        for g in raw:
            try:
                built = self._build(g)
            except UnknownStatus as e:
                # **그 경기만** 건너뛴다. 대회 전체를 죽이지 않는다.
                self.note("모르는 경기 상태값", str(e))
                continue
            except (KeyError, TypeError, ValueError) as e:
                self.note("경기 하나를 해석하지 못함", f"{type(e).__name__}: {e}")
                continue
            if built is not None:
                games.append(built)

        if self.league in KOREAN_PLAYER_ONLY:
            games = self._korean_only(games, now)

        # **0건은 항상 의심한다** (약점 7). 비시즌이면 정상이지만, 그 판정은
        # 이 어댑터가 아니라 커버리지 감시가 요일·시즌으로 한다.
        if not games:
            self.note_text_info("수집 0건", f"{self.league.value} {d0}~{d1}")
        return games

    def _schedule(self, d0: str, d1: str) -> list[dict]:
        """날짜 구간을 나눠 받는다 — `size`에 걸려 조용히 잘리지 않게(약점 13)."""
        out: list[dict] = []
        cur = datetime.strptime(d0, "%Y-%m-%d")
        end = datetime.strptime(d1, "%Y-%m-%d")
        while cur <= end:
            stop = min(cur + timedelta(days=SCHEDULE_WINDOW_DAYS - 1), end)
            q = urllib.parse.urlencode({
                "fields": "basic", "upperCategoryId": "wfootball",
                # **이 한 줄이 상한 잘림을 막는다** (파일 맨 위 주석).
                "categoryId": self.category,
                "fromDate": cur.strftime("%Y-%m-%d"),
                "toDate": stop.strftime("%Y-%m-%d"), "size": 500})
            d = self._get(f"/schedule/games?{q}",
                          label=f"naver_football:{self.league.name}:schedule")
            got = (d.get("result") or {}).get("games") or []
            if len(got) >= 500:
                # 상한에 닿으면 잘렸을 수 있다. 조용히 넘기지 않는다.
                self.note("일정 응답이 상한에 닿음 — 잘렸을 수 있습니다",
                          f"{cur:%Y-%m-%d}~{stop:%Y-%m-%d} {len(got)}건")
            out += [g for g in got if g.get("categoryId") == self.category]
            cur = stop + timedelta(days=1)
        return out

    def _build(self, g: dict) -> Game | None:
        code = str(g.get("statusCode") or "")
        if code not in _STATUS:
            raise UnknownStatus(f"{self.league.value} statusCode={code!r}")
        status = _STATUS[code]
        # 플래그가 상태값보다 강하다 — 소스가 상태는 그대로 두고 플래그만 켤 때가 있다.
        if g.get("cancel"):
            status = Status.CANCELED
        elif g.get("suspended"):
            status = Status.SUSPENDED

        home = self._name(g.get("homeTeamName"))
        away = self._name(g.get("awayTeamName"))
        if not (home and away):
            return None
        gid = g.get("gameId")
        when = g.get("gameDateTime")
        if not (gid and when):
            return None

        # **점수는 종결된 경기에만 붙인다.**
        # 소스가 경기 전에도 0을 채운다(실측: BEFORE인데 0:0). 그대로 쓰면
        # '종료 0:0 무승부'가 나간다 — KBO에서 실제로 겪은 사고다(약점 47).
        score = None
        if status in (Status.FINAL, Status.LIVE):
            hs, as_ = g.get("homeTeamScore"), g.get("awayTeamScore")
            if isinstance(hs, int) and isinstance(as_, int):
                score = Score(hs, as_, ScoreUnit.GOALS)
        if status is Status.FINAL and score is None:
            # 종료라는데 점수가 없다 — 카드에 빈 점수를 내보내느니 예정으로 되돌린다.
            self.note("종료인데 점수가 없어 예정으로 되돌림", f"{away} vs {home}")
            status = Status.SCHEDULED

        season = self._season(when)
        return Game(
            league=self.league, season=season,
            source_key=f"naver-{self.category}-{gid}",
            home=TeamRef(self.league, home), away=TeamRef(self.league, away),
            start_utc=_kst_to_utc(when),
            # **홈팀 현지 시간대를 모른다.** 소스가 나라를 안 준다.
            # 지어내지 않고 한국시각만 쓴다 — 카드는 KST가 주(主)이므로
            # 현지 병기가 없을 뿐 틀린 값이 찍히지는 않는다.
            home_tz="Asia/Seoul",
            status=status, score=score, venue=None,
            meta=GameMeta())

    # ── 한국 선수 경기만 (v1.15) ──────────────────────────────
    def _korean_only(self, games: list[Game], now: datetime) -> list[Game]:
        """한국 선수가 뛰는 경기만 남긴다. 두 단계로 좁힌다.

        ① **소속팀**으로 후보를 고른다 — 경기 목록만으로 되므로 요청이 0이다.
        ② 라인업을 조회해 **명단에 있는지** 확인한다.

        ②에서 명단에 없으면 **뺀다**(결장). 하지만 라인업이 아직 비어 있으면
        **그대로 남긴다** — 대표님 결정(2026-09-07): *"그냥 보낸다 (출전 언급
        없이)"*. 라인업은 킥오프 직전에야 채워지므로(실측: 3일 전 빈 객체),
        비었다고 빼면 소스가 늦은 날 **실제로 뛰는데도 카드가 사라진다.**
        모를 때는 경기 자체를 알리되 **출전을 말하지 않는다** — 카드가
        `meta.player_lines`가 비어 있는 것을 보고 그렇게 한다.
        """
        teams = {p["team"] for p in KOREAN_PLAYERS.values()}
        cand = [g for g in games
                if g.home.team_code in teams or g.away.team_code in teams]
        if not cand:
            # 팀 표기가 바뀌면 여기서 전부 0이 된다 — 조용히 넘기면
            # '오늘 한국 선수 경기가 없다'로 읽힌다(약점 7과 같은 얼굴).
            if games:
                # ⚠️ 알림 문구는 **한 줄로 적는다.** `verify_claims`가 소스에서
                # 첫 문자열 리터럴만 읽어 등급 표와 대조하므로, 줄을 나누면
                # 등록해 둔 문구와 안 맞아 "등록되지 않은 새 알림"으로 잡힌다.
                self.note("한국 선수 소속팀이 경기 목록에 없음 (팀 표기 변경 의심)",
                          f"찾는 팀 {sorted(teams)}")
            return []

        # 라인업은 **임박·종료 경기만** 본다. 먼 미래는 조회해도 비어 있다.
        limit = now + timedelta(seconds=LINEUP_LOOKAHEAD_SECONDS)
        looked = 0
        out: list[Game] = []
        for g in sorted(cand, key=lambda x: x.start_utc):
            if looked >= LINEUP_MAX_PER_TICK or g.start_utc > limit:
                out.append(g)                 # 아직 이르다 — 판단하지 않고 남긴다
                continue
            looked += 1
            lines = self._lineup_korean(g)
            if lines is None:
                out.append(g)                 # 라인업이 없다 — 대표님 결정대로 남긴다
                continue
            if not lines:
                # 라인업이 **있는데** 한국 선수가 없다 → 결장. 뺀다.
                self.note_text_info(
                    "한국 선수 결장이라 제외",
                    f"{g.away.team_code} vs {g.home.team_code}")
                continue
            g.meta.player_lines = lines
            out.append(g)
        return out

    def _lineup_korean(self, g: Game) -> "list[PlayerLine] | None":
        """이 경기 라인업에서 한국 선수를 찾는다.

        돌려주는 값 셋을 **구분한다** — 이게 이 함수의 요점이다:
          · `None`  라인업을 못 봤다 (아직 안 나옴·조회 실패) = **모른다**
          · `[]`    라인업은 봤는데 없다 = **결장**
          · `[...]` 명단에 있다
        `None`과 `[]`를 뭉개면 "모른다"가 "결장"이 되어 카드가 사라진다.
        """
        gid = g.source_key.rsplit("-", 1)[-1]
        try:
            d = self._get(f"/schedule/games/{gid}/lineup",
                          label=f"naver_football:{self.league.name}:lineup")
        except Exception as e:                               # noqa: BLE001
            self.note("라인업 조회 실패(출전 여부를 말하지 않습니다)",
                      f"{type(e).__name__}")
            return None
        lu = ((d.get("result") or {}).get("lineUpData") or {}).get("lineup")
        if not lu:
            return None                                      # 아직 안 나왔다
        found: list[PlayerLine] = []
        for side in ("home", "away"):
            team_code = g.home.team_code if side == "home" else g.away.team_code
            for pl in self._players(lu.get(side)):
                name = (pl.get("name") or "").strip()
                rec = KOREAN_PLAYERS.get(name)
                if not rec:
                    continue
                self._note_transfer(name, rec, pl, team_code)
                start = str(pl.get("substitute")) == "0"
                found.append(PlayerLine(
                    # **표에 id가 없어도 된다** (v1.17c) — 소스가 주는 값이
                    # 원본이고, 표는 '누가 한국 선수인가'만 안다.
                    player_id=str(pl.get("playerId") or rec.get("id") or ""),
                    name_ko=name, team=TeamRef(self.league, team_code),
                    played=True,
                    # 선발이 아니면 **명단에는 있다**는 뜻이다. 소스는 교체
                    # 투입 여부까지는 이 필드로 말하지 않으므로 지어내지 않는다.
                    dnp_reason=None if start else "교체 명단",
                    soccer={"start": start,
                            "goals": _int(pl.get("goal")),
                            "own_goals": _int(pl.get("ownGoal")),
                            "card": _card_label(pl)}))
        return found

    @staticmethod
    def _players(side: dict | None) -> list:
        """소스의 라인업은 **줄별로 중첩된 배열**이다(포메이션 그대로).

        `{"players": {"lineup": [[...], [...]], "substitutes": [...]}}` 꼴이라
        한 겹만 벗기면 절반을 놓친다. 깊이를 가리지 않고 평탄화한다.
        """
        out: list = []

        def walk(x):
            if isinstance(x, dict):
                if "name" in x and "playerId" in x:
                    out.append(x)
                else:
                    for v in x.values():
                        walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(side or {})
        return out

    def _note_transfer(self, name: str, rec: dict, pl: dict, team_code: str) -> None:
        """표와 실제가 어긋나면 알린다 — **고치는 건 사람이다.**

        선수가 이적하면 표의 `team`이 낡는다. 낡은 표는 조용히 틀린다:
        1차 거름망이 새 팀 경기를 후보에서 빼버려 **그 선수 경기가 통째로
        사라지는데**, 오류도 경고도 없다. 그래서 라인업에서 어긋남을 보는
        순간 소리를 낸다.
        """
        # **표에 적힌 것과 어긋날 때만 말한다.** 표에 없는 칸(id·team)은
        # 어긋날 것도 없다 — v1.17c에서 id를 선택으로 바꿨기 때문이다.
        if rec.get("team") and team_code != rec["team"]:
            self.note("한국 선수 소속팀이 표와 다름 (이적 의심 · 표를 고쳐야 함)",
                      f"{name}: 표 {rec['team']} · 실제 {team_code}")
        pid = str(pl.get("playerId") or "")
        if rec.get("id") and pid and pid != rec["id"]:
            self.note("한국 선수 ID가 표와 다릅니다 (소스가 ID를 바꿨을 수 있습니다)",
                      f"{name}: 표 {rec['id']} · 실제 {pid}")

    def _name(self, raw) -> str:
        """소스 표기 → 우리 표기. **읽을 수 없으면 버린다.**"""
        n = (raw or "").strip()
        n = NAME_FIX.get(n, n)
        if not n or not is_readable_ko(n):
            if n:
                self.note("한국어로 읽을 수 없는 팀 이름", repr(n))
            return ""
        return n

    def _season(self, when: str) -> str:
        """유럽은 8월~5월이라 시즌이 두 해에 걸친다 → `2026-27`."""
        d = datetime.strptime(when[:10], "%Y-%m-%d")
        y0 = d.year if d.month >= 7 else d.year - 1
        return f"{y0}-{str(y0 + 1)[2:]}"

    # ── 경기장 (필요한 것만) ──────────────────────────────────
    # ── 선발 라인업 · 득점자 (v1.17, 2026-09-08) ──────────────────
    #
    # 대표님 지시 둘을 한 재료로 푼다:
    #   · *"경기시작전에 알려줄 출장 라인업이 가능하면 좋아"*  → 라인업 카드
    #   · *"경기결과 시안은 합격, 저것도 적용하자"*             → 종료 속보의 블록
    #
    # **여기서 유럽 골 미수집 결함도 함께 고친다.** v1.15~v1.16에서 유럽 경기의
    # `meta.goals`는 **한 건도 채워지지 않았다**. `naver_game.py`가 흐름을
    # 보강할 때 경기를 **팀 이름으로** 맞추는데, 유럽은 `TEAM_NAMES`가 비어
    # 양쪽이 `None`이 되어 `None vs None`으로 매칭이 언제나 실패했다.
    # 이름으로 맞추는 대신 **우리가 이미 아는 gameId로 직접** 받으면 그 경로가
    # 통째로 필요 없어진다 — 증상을 막는 게 아니라 뿌리를 없애는 쪽이다.

    def fill_lineups(self, games: list[Game], now: datetime, *,
                     limit: int = LINEUP_MAX_PER_TICK) -> int:
        """선발 명단을 채운다 — **볼 값어치가 있는 경기만.**

        조회 대상은 둘뿐이다:
          · 킥오프 `LINEUP_WATCH_SECONDS` 전부터 시작 전까지 (라인업 카드용)
          · 이미 끝났는데 아직 명단이 없는 경기 (종료 속보용)
        이미 채워진 경기는 **다시 보지 않는다** — 선발 명단은 발표되면 안 바뀐다.
        그래서 실제 조회 수는 경기당 몇 번에 그친다.

        **최신 경기부터** 본다(fix54와 같은 이유). 상한이 있는 대기줄을
        오래된 것부터 돌리면 오늘 경기가 영원히 뒤에 선다.
        """
        if not LINEUP_ENABLED:
            return 0
        watch = timedelta(seconds=LINEUP_WATCH_SECONDS)
        todo = [g for g in games
                if not (g.meta and g.meta.lineup)
                and (g.is_terminal
                     or (g.status is Status.LIVE)
                     or (now + watch >= g.start_utc > now))]
        todo.sort(key=lambda g: g.start_utc, reverse=True)
        done = 0
        for g in todo[:limit]:
            lu = self._lineup_full(g)
            if lu is None:
                continue                       # 아직 안 나왔다 — **모른다**
            g.meta.lineup = lu
            done += 1
        return done

    def _lineup_full(self, g: Game) -> "dict | None":
        """양 팀 선발 11명씩. 못 봤으면 `None` — 빈 dict를 돌려주지 않는다.

        **`None`(못 봤다)과 '명단이 비었다'를 뭉개지 않는다.** 뭉개면 발표 전
        경기가 '선발 없음'으로 카드에 실린다.
        """
        gid = g.source_key.rsplit("-", 1)[-1]
        try:
            d = self._get(f"/schedule/games/{gid}/lineup",
                          label=f"naver_football:{self.league.name}:lineup")
        except Exception as e:                               # noqa: BLE001
            self.note("라인업 조회 실패(명단 없이 카드가 나갑니다)",
                      f"{type(e).__name__}")
            return None
        lu = ((d.get("result") or {}).get("lineUpData") or {}).get("lineup")
        if not lu:
            return None

        # ── 한국 선수를 여기서 함께 잡는다 (v1.17c, 2026-09-08) ──────
        #
        # 대표님: *"한국선수 해외리그 출전하는 경기는 꼭 알림이 필요한데"*
        #
        # **조회가 늘지 않는다** — 이미 받은 이 응답을 한 번 더 읽을 뿐이다.
        # 그전까지 한국 선수 표시는 MLS에만 붙었다(`_korean_only` 경로).
        # 유럽은 전 경기를 수집하면서도 `player_lines`를 아무도 안 채워
        # **한국 선수가 뛰어도 카드에 아무 표시가 없었다.**
        #
        # 명단이 반쯤 찼거나 못 읽는 경우에도 이건 해 둔다 — 카드에 명단을
        # 싣지 못하더라도 '누가 나온다'는 사실은 알릴 값어치가 있다.
        lines = self._korean_lines(g, lu)
        if lines:
            g.meta.player_lines = lines

        out: dict = {}
        for side in ("home", "away"):
            rows = self._lineup_rows(lu.get(side))
            if sum(len(r) for r in rows) < 11:
                return None                    # 반쯤 찬 명단은 명단이 아니다
            fm = str((lu.get(side) or {}).get("formation") or "").strip()
            out[side] = {"formation": fm, "rows": self._face_gk_first(rows, fm)}
        if not all(s in out for s in ("home", "away")):
            return None
        # **한국 선수도 여기 담아 함께 저장한다** (v1.17c).
        # 카드는 메모리가 아니라 되읽은 스냅샷으로 그린다(fix49). `player_lines`는
        # 스냅샷에 담지 않는 칸이라(야구 기록 경로가 매 틱 새로 채운다) 여기
        # 실어야 살아남는다 — 축구 선발 명단은 발표되면 안 바뀌므로 저장이 안전하다.
        if lines:
            out["korean"] = [{"name": pl.name_ko, "team": pl.team.team_code,
                              "id": pl.player_id, "dnp": pl.dnp_reason,
                              "soccer": pl.soccer} for pl in lines]
        return out

    def _korean_lines(self, g: Game, lu: dict) -> list:
        """이 라인업 안의 한국 선수. 없으면 빈 목록.

        `_lineup_korean`과 같은 일을 하지만 **이미 받아 둔 응답으로** 한다 —
        그쪽은 MLS를 거르려고 스스로 조회하는 경로이고, 이쪽은 유럽에서
        이미 온 명단을 읽는다. 판정 규칙은 하나로 맞춘다.
        """
        found: list = []
        for side in ("home", "away"):
            team_code = g.home.team_code if side == "home" else g.away.team_code
            for pl in self._players(lu.get(side)):
                name = (pl.get("name") or "").strip()
                rec = KOREAN_PLAYERS.get(name)
                if not rec:
                    continue
                self._note_transfer(name, rec, pl, team_code)
                start = str(pl.get("substitute")) == "0"
                found.append(PlayerLine(
                    player_id=str(pl.get("playerId") or rec.get("id") or ""),
                    name_ko=name, team=TeamRef(self.league, team_code),
                    played=True,
                    dnp_reason=None if start else "교체 명단",
                    soccer={"start": start, "goals": _int(pl.get("goal")),
                            "own_goals": _int(pl.get("ownGoal")),
                            "card": _card_label(pl)}))
        return found

    @staticmethod
    def _lineup_rows(side: "dict | None") -> list:
        """선발만 **줄 구조 그대로** 꺼낸다 (교체 명단은 버린다).

        `_players`는 깊이를 가리지 않고 평탄화하므로 줄이 사라진다. 라인업
        카드는 줄이 곧 포메이션이라, 여기서는 `lineup` 배열만 한 겹으로 읽는다.
        """
        pl = ((side or {}).get("players") or {}).get("lineup") or []
        rows: list = []
        for row in pl:
            if not isinstance(row, list):
                continue
            names = [str(p.get("name") or "").strip()
                     for p in row if isinstance(p, dict)]
            rows.append([n for n in names if n and is_readable_ko(n)])
        return [r for r in rows if r]

    @staticmethod
    def _face_gk_first(rows: list, formation: str) -> list:
        """**줄 순서가 팀마다 반대다** (실측 2026-09-08).

        원정 팀은 공격수가 첫 줄로 오는 경우가 있다. 카드가 매번 다시
        판정하지 않도록, 소스를 본 이 자리에서 한 번만 세워 둔다.

        판정은 추측이 아니라 **포메이션 숫자와 줄 인원을 맞춰서** 한다:
        '4231'이면 GK 다음 줄들이 4·2·3·1이어야 한다. 한쪽 방향만 맞으면
        그쪽이 정답이고, **둘 다 맞거나 둘 다 안 맞으면 손대지 않는다** —
        모를 때 뒤집는 것이 그냥 두는 것보다 나쁘다.
        """
        want = [int(c) for c in formation] if formation.isdigit() else []
        if not want:
            return rows
        counts = [len(r) for r in rows]
        rev = list(reversed(counts))
        fwd_ok = counts[1:1 + len(want)] == want
        rev_ok = rev[1:1 + len(want)] == want
        if rev_ok and not fwd_ok:
            return list(reversed(rows))
        return rows

    def fill_goals(self, games: list[Game], *, limit: int = GOALS_MAX_PER_TICK
                   ) -> int:
        """득점자·시각을 채운다 (`meta.goals`).

        ⚠️ **라인업 안의 `goal` 필드는 쓰지 않는다.** 실측 2026-09-08: 득점이
        난 경기에서도 그 값이 전부 0이었다. 골의 유일한 출처는 경기 상세의
        `game.scorers`다.
        """
        if not LINEUP_ENABLED:
            return 0
        todo = [g for g in games
                if g.is_terminal or g.status is Status.LIVE]
        todo = [g for g in todo if not (g.meta and g.meta.goals)]
        todo.sort(key=lambda g: g.start_utc, reverse=True)   # fix54와 같은 이유
        done = 0
        for g in todo[:limit]:
            gid = g.source_key.rsplit("-", 1)[-1]
            try:
                d = self._get(f"/schedule/games/{gid}",
                              label=f"naver_football:{self.league.name}:game")
            except Exception as e:                           # noqa: BLE001
                self.note("득점자 조회 실패(카드는 점수만 싣습니다)",
                          f"{type(e).__name__}")
                continue
            sc = ((d.get("result") or {}).get("game") or {}).get("scorers") or {}
            goals: list = []
            for side in ("home", "away"):
                for x in (sc.get(side) or []):
                    name = str(x.get("playerName") or "").strip()
                    if not name or not is_readable_ko(name):
                        continue               # 읽을 수 없는 표기는 싣지 않는다
                    goals.append(Goal(minute=_int(x.get("time")), side=side,
                                      name=name,
                                      own_goal=bool(x.get("ownGoal")),
                                      added=_int(x.get("addedTime"))))
            if goals:
                g.meta.goals = tuple(sorted(goals,
                                            key=lambda x: (x.minute, x.added)))
                done += 1
        return done

    def fill_venues(self, games: list[Game], *, limit: int = VENUE_MAX_PER_TICK
                    ) -> int:
        """상세 조회로 경기장을 채운다. **한 틱에 `limit`개까지만.**

        경기장이 없어도 카드는 나간다 — 그래서 이건 '있으면 좋은 것'이고,
        남의 소스에 요청을 몰아칠 이유가 없다. 오늘·내일 경기부터 채운다.
        """
        done = 0
        for g in games:
            if done >= limit or g.venue:
                continue
            gid = g.source_key.rsplit("-", 1)[-1]
            try:
                d = self._get(f"/schedule/games/{gid}",
                              label=f"naver_football:{self.league.name}:game")
            except Exception as e:                           # noqa: BLE001
                self.note("경기장 조회 실패(카드는 그대로 나갑니다)",
                          f"{type(e).__name__}")
                continue
            v = ((d.get("result") or {}).get("game") or {}).get("stadium")
            if v and is_readable_ko(v):
                g.venue = v
                done += 1
        return done
