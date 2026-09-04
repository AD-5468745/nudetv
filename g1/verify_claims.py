"""**카드가 하는 말이 참인가** — 문구 전수 게이트 (v1.11p).

대표님이 채널을 눈으로 보고 결함을 계속 잡아내셨다. 숫자 검증 880건이 전부 통과한
상태에서다. 잡힌 것들은 전부 같은 계열이었다:

    "경기 시작 2시간 전 알림"  →  실제 5시간 10분 전
    "나머지 12경기는 아래 글에" →  글에 0줄
    "3개 리그 공식 결과"        →  행이 0줄인 리그를 세고, 그중 하나는 팬 위키
    "오늘 최다 득점"            →  농구 점수가 야구를 이겨 매번 농구만 뽑힘
    "0경기 종료"                →  전 경기 취소된 날
    "편성 1경기 (총 3경기)"     →  한 낱말이 두 수를 가리킴

**공통 원인은 하나다: 문장이 주장하는 사실을 아무도 검사하지 않았다.**
숫자·구조 검증은 "카드가 만들어지는가"를 보지 "카드가 참말을 하는가"를 안 본다.

이 파일이 그 자리를 맡는다. 각 검사는 **문장 하나를 골라 그 주장이 성립하는 조건을
직접 확인**한다. 새 문구를 넣을 때 여기에 검사를 하나 추가하는 것이 규칙이다.

돌리는 법:  python3 g1/verify_claims.py
"""
from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import contract as C                                          # noqa: E402
import pipeline as P                                          # noqa: E402
from contract import (KST, ContentType, Game, GameMeta, League,  # noqa: E402
                      Score, ScoreUnit, Status, TeamRef)

PASS = 0
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name} — {detail}")


def txt(html: str) -> str:
    """태그를 걷어낸 사람이 읽는 글. **공백을 하나로 모은다** —
    `<em>1경기</em> 열림`이 "1경기  열림"이 되어 검사가 헛돌았다."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def mk(lg, day, hh, mm=0, *, tz, h, a, status=Status.SCHEDULED, score=None,
       venue=None) -> Game:
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, mm,
                  tzinfo=ZoneInfo(tz))
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season, source_key=f"{lg.value}-{day}-{hh}{mm}-{h}{a}",
             home=TeamRef(lg, h), away=TeamRef(lg, a),
             start_utc=st.astimezone(timezone.utc), home_tz=tz,
             status=status, score=score, venue=venue,
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.validate()
    return g


print("=" * 62)
print("카드가 하는 말이 참인가 — 문구 전수 게이트")
print("=" * 62)

DAY = "2026-09-04"
NOW = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)

# ─────────────────────────────────────────────────────────────
# 1. "나머지 N경기는 아래 글에" — 정말 아래 글에 있는가
# ─────────────────────────────────────────────────────────────
# 카드가 자리 부족으로 못 실은 경기를 캡션이 싣는다는 약속이다.
# 나이트 브리핑 캡션은 **예정 경기를 통째로 뺀다**. 그래서 예정을 세어 약속하면
# 그 수만큼 글에 없다. MLB는 한국시각 밤~새벽에 열려 이 일이 매일 났다.
print("\n1. '아래 글에' — 약속한 것이 정말 글에 있는가")

_night = [mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
             status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
          mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
             status=Status.FINAL, score=Score(2, 4, ScoreUnit.RUNS))]
# 한국시각 익일 새벽에 열리는 MLB 경기 여러 건 — 카드에도 캡션에도 안 실린다
_night += [mk(League.MLB, DAY, 19, 10 + i, tz="America/New_York",
              h="NYM", a="SF") for i in range(6)]

_card = txt(P.render_night_brief(_night, DAY))
_cap = P.caption_night_brief(_night, DAY)
_m = re.search(r"나머지 (\d+)경기는 아래 글에", _card)
_promised = int(_m.group(1)) if _m else 0
_in_caption = sum(1 for ln in _cap.splitlines() if ln.startswith("  "))
_shown_card = len(re.findall(r"\d+:\d+", _card))
check("나이트 브리핑: 카드가 약속한 '아래 글'의 경기가 실제로 글에 있다",
      _promised == 0 or _in_caption >= _shown_card + _promised,
      f"약속 {_promised} · 카드 {_shown_card} · 글 {_in_caption}")

# ─────────────────────────────────────────────────────────────
# 2. "N개 리그" — 카드에 그 리그의 행이 정말 있는가
# ─────────────────────────────────────────────────────────────
print("\n2. 'N개 리그' — 센 리그가 카드에 실제로 실렸는가")
_only_sched = [mk(League.MLB, DAY, 19, 10, tz="America/New_York", h="NYM", a="SF")]
_only_sched += _night[:2]                       # KBO 결과 2건 + MLB 예정 1건
_c2 = txt(P.render_night_brief(_only_sched, DAY))
_m2 = re.search(r"(\d+)개 리그(?! 중)", _c2)
_claimed = int(_m2.group(1)) if _m2 else 0
# 카드에 실제로 이름이 찍힌 리그를 센다
_named = sum(1 for lg in (League.KBO, League.MLB)
             if P.LEAGUE_LABEL.get(lg, lg.value) in _c2)
check("나이트 브리핑: 푸터가 센 리그 수가 카드에 실린 리그 수와 같다",
      _claimed <= _named, f"주장 {_claimed} · 실제 {_named}")
check("결과가 한 줄도 없는 리그는 카드에 판을 그리지 않는다",
      "MLB" not in _c2 or ":" in _c2.split("MLB")[1][:120],
      _c2[:200])

# ─────────────────────────────────────────────────────────────
# 3. "공식" — 정말 공식 소스인가
# ─────────────────────────────────────────────────────────────
# LCK·LoL 국제대회는 라이엇 공식 API 키를 못 구해 Leaguepedia(팬 위키)를 쓴다.
print("\n3. '공식' — 공식 소스에서 온 것에만 쓰는가")
check("LCK는 공식 소스가 아니다", not P._is_official(League.LCK))
check("LoL 국제대회도 아니다", not P._is_official(League.INTL_LOL))
check("KBO·MLB·NPB는 공식이다",
      all(P._is_official(x) for x in (League.KBO, League.MLB, League.NPB)))

_lck = [mk(League.LCK, DAY, 17, 0, tz="Asia/Seoul", h="T1", a="GEN",
           status=Status.FINAL, score=Score(2, 0, ScoreUnit.MAPS))]
_lck_card = txt(P.render_result(_lck, DAY))
check("LCK 결과 카드가 '공식'이라 주장하지 않는다",
      "공식" not in _lck_card, _lck_card[-160:])
check("대신 출처를 밝힌다", "Leaguepedia" in _lck_card, _lck_card[-160:])

_mixed = _night[:2] + _lck
_mx_card = txt(P.render_night_brief(_mixed, DAY))
check("팬 위키가 섞인 나이트 브리핑도 '공식'이라 하지 않는다",
      "공식" not in _mx_card, _mx_card[-200:])
check("팬 위키가 섞이면 캡션도 '공식'이라 하지 않는다",
      "공식" not in P.caption_night_brief(_mixed, DAY)[-120:],
      P.caption_night_brief(_mixed, DAY)[-120:])

# 점수 단위가 섞이면 그 사실을 밝힌다 (맵 2:0과 득점 5:3이 한 장에 있다)
check("단위가 섞인 카드는 그 사실을 밝힌다",
      "혼재" in _mx_card or "맵" in _mx_card, _mx_card[-200:])

# ─────────────────────────────────────────────────────────────
# 4. "최다 득점" — 비교할 수 있는 것끼리 비교했는가
# ─────────────────────────────────────────────────────────────
# 점수 단위가 리그마다 다르다. 농구(합계 150~200)는 야구(5~15)를 **항상** 이긴다.
print("\n4. '최다 득점' — 같은 단위끼리 비교했는가")
_bball = [mk(League.KBL, DAY, 19, 0, tz="Asia/Seoul", h="LG", a="KT",
             status=Status.FINAL, score=Score(88, 95, ScoreUnit.POINTS))]
_both = _night[:2] + _bball
_rec = P._night_record_line(P._night_counts(_both)) if hasattr(P, "_night_counts") else None
if _rec is None:
    # _night_counts 이름이 다르면 렌더 결과로 확인한다
    _bc = txt(P.render_night_brief(_both, DAY))
    check("리그가 섞인 날 '최다 득점'을 단정하지 않는다",
          "최다 득점" not in _bc, _bc[:300])
else:
    check("리그가 섞인 날 '최다 득점'을 단정하지 않는다", _rec is None, str(_rec))

# 같은 리그만 있으면 말해도 된다 — 다만 리그 이름을 밝힌다
_kbo_hi = [mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
              status=Status.FINAL, score=Score(11, 13, ScoreUnit.RUNS)),
           mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
              status=Status.FINAL, score=Score(1, 2, ScoreUnit.RUNS))]
_kc = txt(P.render_night_brief(_kbo_hi, DAY))
check("한 리그만 있으면 최다 득점을 말하되 리그를 밝힌다",
      "최다 득점" not in _kc or "KBO" in _kc, _kc[:300])

# 동률이면 단정하지 않는다
_tie = [mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
           status=Status.FINAL, score=Score(6, 6, ScoreUnit.RUNS)),
        mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
           status=Status.FINAL, score=Score(5, 7, ScoreUnit.RUNS))]
_tc = txt(P.render_night_brief(_tie, DAY))
check("합계가 동률이면 '가장 많았다'고 단정하지 않는다",
      "가장 많았습니다" not in _tc, _tc[:300])

# ─────────────────────────────────────────────────────────────
# 5. 수를 세는 낱말이 한 메시지 안에서 한 뜻인가
# ─────────────────────────────────────────────────────────────
print("\n5. 카드와 캡션이 같은 낱말로 같은 수를 말하는가")
_cx = [mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB"),
       mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
          status=Status.CANCELED),
       mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="HH", a="NC",
          status=Status.CANCELED)]
_mn = datetime(2026, 9, 4, 7, 30, tzinfo=KST)
_mcard = txt(P.render_morning(_cx, DAY, now=_mn))
_mcap = P.caption_morning(_cx, DAY, now=_mn)
check("모닝: 카드와 캡션이 열리는 수를 같은 낱말로 말한다",
      "1경기 열림" in _mcard and "1경기 열림" in _mcap,
      f"{_mcard[:80]} || {_mcap.splitlines()[0]}")
check("모닝: 한 낱말이 두 수를 가리키지 않는다",
      not ("편성 1경기" in _mcap and "총 3경기" in _mcap),
      _mcap.splitlines()[0])

# 나이트 브리핑 캡션은 '0경기 종료'라 말하지 않는다
_allcx = [mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB",
             status=Status.CANCELED),
          mk(League.KBO, DAY, 17, 0, tz="Asia/Seoul", h="SS", a="KT",
             status=Status.CANCELED)]
_ac = P.caption_night_brief(_allcx, DAY)
check("나이트 브리핑 캡션이 '0경기 종료'라 하지 않는다",
      "0경기 종료" not in _ac, _ac.splitlines()[0])

# ─────────────────────────────────────────────────────────────
# 6. 시작 알림이 그날 편성을 모닝 카드와 다르게 말하지 않는가
# ─────────────────────────────────────────────────────────────
print("\n6. 시작 알림 — 취소를 숨기지 않는가")
_sa = txt(P.render_start_alert([g for g in _cx if g.status is Status.SCHEDULED],
                               NOW, all_games=_cx))
check("시작 알림 분모에 취소를 넣지 않는다", "3경기 중" not in _sa, _sa[:160])
check("취소가 있으면 시작 알림도 그 사실을 밝힌다",
      "취소" in _sa, _sa[:200])

# ─────────────────────────────────────────────────────────────
# 7. 지킬 수 없는 약속을 하지 않는가
# ─────────────────────────────────────────────────────────────
# 시작 알림의 실제 발송 구간 = 예약 −앞창 ~ 예약 +유예. 지금 4시간이 넘는다.
# 그 구간 안 어디서든 나가므로 시각을 적으면 거짓이 된다.
print("\n7. 지킬 수 없는 약속을 하지 않는가")
_win = (C.lookahead_for(ContentType.START_ALERT, 3600)
        + C.GRACE_SECONDS[ContentType.START_ALERT])
check("시작 알림 발송 구간이 넓다는 사실을 확인 (이 검사의 전제)",
      _win >= 3600, f"{_win}초")
_notice = C.start_alert_notice(_cx, NOW)
check("그래서 꼬리말이 시각을 약속하지 않는다",
      not re.search(r"\d{1,2}:\d{2}", _notice) and "시간 전" not in _notice,
      _notice)
check("그래도 무엇을 하는지는 말한다", "시간표" in _notice, _notice)
check("보낼 것이 없으면 아무 약속도 하지 않는다",
      C.start_alert_notice([], NOW) == "")

# ─────────────────────────────────────────────────────────────
# 8. 발송 시점에 따라 참·거짓이 갈리는 낱말
# ─────────────────────────────────────────────────────────────
# 나이트 브리핑은 23:00 예약에 유예 6시간이라 익일 05:00까지 나갈 수 있다.
# 그때 '오늘'은 어제를 가리킨다 — 바로 아래 푸터가 날짜로 스스로 반박한다.
print("\n8. '오늘' — 언제 나가도 참인가")
check("나이트 브리핑 헤드라인이 '오늘'이라 단정하지 않는다",
      "오늘" not in txt(P.render_night_brief(_night[:2], DAY)).split("종료")[0],
      txt(P.render_night_brief(_night[:2], DAY))[:200])

# ─────────────────────────────────────────────────────────────
# 9. 확인하지 않은 것을 확인한 것처럼 말하지 않는가
# ─────────────────────────────────────────────────────────────
print("\n9. 근거 없는 단정을 하지 않는가")
_src = pathlib.Path(P.__file__).read_text(encoding="utf-8")
check("'공식 소스에 없어'처럼 확인 안 된 이유를 단정하지 않는다",
      "공식 소스에 없어" not in _src)

# ─────────────────────────────────────────────────────────────
# 10. 표본 수를 제목이 정확히 말하는가
# ─────────────────────────────────────────────────────────────
print("\n10. '최근 N경기' — 실제 표본과 같은가")
_hist = [mk(League.KBO, f"2026-08-{20 + i:02d}", 18, 30, tz="Asia/Seoul",
            h="LG", a="OB", status=Status.FINAL,
            score=Score(3, 5, ScoreUnit.RUNS)) for i in range(3)]
_game = mk(League.KBO, DAY, 18, 30, tz="Asia/Seoul", h="LG", a="OB")
_form = P.recent_form(_hist, "LG", _game.start_utc, 5)
check("표본이 3경기인 상황을 만들었다 (이 검사의 전제)", len(_form) == 3,
      str(len(_form)))
_fb = P._form_block(type("RB", (), {"league": League.KBO})(), _game, _hist)
if _fb:
    _ft = txt(_fb[0])
    check("표본이 3경기면 제목도 5경기라 하지 않는다",
          "최근 5경기" not in _ft, _ft[:120])

print()
print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
for line in FAIL:
    print(f"  ✗ {line}")
sys.exit(1 if FAIL else 0)
