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

# ─────────────────────────────────────────────────────────────
# 11. 경보가 소음이 되지 않는가 (v1.11p)
# ─────────────────────────────────────────────────────────────
# 대표님이 보내주신 실운영 알림 로그에 이런 줄이 **매 틱** 올라왔다:
#   "KBO: 시리즈별 수집 정규시즌 238 · 와일드카드 0 · 플레이오프 0"
#   "VLEAGUE_M: 선택한 시즌 023 (126경기)"
#   "LCK: 대진 미확정(TBD)이라 건너뜀 3건"
# 셋 다 정상 상태다. 진짜 사고가 이 사이에 묻힌다(27번 약점의 재발).
print("\n11. 경보가 소음이 되지 않는가")

from adapters._notices import NoticeMixin                     # noqa: E402


class _Probe(NoticeMixin):
    pass


_pr = _Probe()
_pr.note_info("선택한 시즌", "023 (126경기)")
_pr.note_info("대진 미확정(TBD)이라 건너뜀", "T1 vs TBD")
_pr.note("미등록 팀·상태로 건너뜀", "미지 상태값 'Delayed'")
_pr.note_cache_age(48 * 3600)

_all = _pr.skipped_report()
_alert = _pr.alert_report()
check("정상 상태도 로그(전체 보고)에는 남는다",
      "선택한 시즌" in _all and "대진 미확정(TBD)이라 건너뜀" in _all, str(_all))
check("정상 상태는 알림에 안 실린다",
      "선택한 시즌" not in _alert
      and "대진 미확정(TBD)이라 건너뜀" not in _alert, str(_alert))
check("정상 상태의 '예시' 항목도 함께 빠진다",
      not any(k.endswith("예시") and "선택한 시즌" in k for k in _alert), str(_alert))
check("진짜 경고는 알림에 남는다", "미등록 팀·상태로 건너뜀" in _alert, str(_alert))
check("묵은 캐시는 정상이 아니다 — 알림에 남는다",
      any("캐시" in k for k in _alert), str(_alert))
check("등급을 안 정하면 경고 쪽이다 (조용히 사라지지 않게)",
      "미등록 팀·상태로 건너뜀" in _alert)
_pr.reset_notices()
check("수집을 새로 시작하면 등급도 초기화된다", _pr.alert_report() == {})

# 실제 어댑터가 정상 상태를 정보로 표시하고 있는가 (소스 확인)
_src_pairs = [
    ("g1/adapters/kbo.py", "시리즈별 수집"),
    ("g1/adapters/kovo.py", "선택한 시즌"),
    ("g1/adapters/lck.py", "대진 미확정(TBD)이라 건너뜀"),
    ("g1/adapters/mlb.py", "발행 대상 아닌 경기 종류로 건너뜀"),
]
_root = pathlib.Path(__file__).resolve().parents[1]
for _f, _label in _src_pairs:
    _t = (_root / _f).read_text(encoding="utf-8")
    _line = next((ln for ln in _t.splitlines() if _label in ln and "note" in ln), "")
    check(f"{_f.split('/')[-1]}: '{_label}'은 정보로 표시한다",
          "note_info" in _line or "note_text_info" in _line, _line.strip()[:90])

# ── 11-b. **거른 것을 읽는 쪽까지 조용한가 (fix44)** ──────────
#
# 등급을 매기는 것만으로는 부족했다. 알림을 **읽는** tick의 `_adapter_health()`가
# `alert_report()`가 빈 dict를 주면 "못 읽었다"로 오해하고 다음 이름으로 넘어가
# 끝내 `skipped_report()`(정보 포함)를 읽었다. 그래서 **정상 상태만 있는 어댑터가
# 오히려 제일 시끄러웠다** — 대표님 채널에 "KBO: 시리즈별 수집 …"이 그대로 갔다.
# 거른 결과가 비었다고 거르기 전 것으로 되돌아가는 구조는 필터가 아니다.
import tick as _T                                             # noqa: E402


class _QuietAdapter(NoticeMixin):
    pass


_qa = _QuietAdapter()
_qa.note_info("시리즈별 수집", "정규시즌 238 · 와일드카드 0")
_qa.note_text_info("선택한 시즌", "023 (126경기)")
_T._ADAPTERS["_QUIET_PROBE"] = _qa
_qn, _ = _T._adapter_health("_QUIET_PROBE")
check("정상 상태만 있는 어댑터는 알림 줄이 0줄이다 (거른 뒤 되돌아가지 않는다)",
      _qn == [], " · ".join(_qn))

_qa.note("미등록 팀·상태로 건너뜀", "Delayed Start")
_wn, _ = _T._adapter_health("_QUIET_PROBE")
check("진짜 경고가 섞이면 그것만 올라온다 (정상 상태는 여전히 빠진다)",
      bool(_wn) and all("미등록" in n for n in _wn), " · ".join(_wn))
_T._ADAPTERS.pop("_QUIET_PROBE", None)

# ─────────────────────────────────────────────────────────────
# 12. 처음 보는 상태값에 경기를 잃지 않는가 (v1.11p)
# ─────────────────────────────────────────────────────────────
# 실운영에서 'Delayed Start' · 'Delayed' · 'Player challenge'가 사흘에 걸쳐 떴고
# 그때마다 그 경기가 통째로 빠졌다. detailedState는 MLB가 계속 늘리는 값이다.
print("\n12. 처음 보는 상태값 — 경기를 잃지 않는가")
from adapters.mlb import MlbAdapter                           # noqa: E402

_ad = MlbAdapter.__new__(MlbAdapter)


def _st(ab, ds):
    """**검사가 예외로 죽으면 통과도 실패도 아니다.** 안전하게 감싼다 —
    폴백을 없애는 변이에서 이 파일이 통째로 죽어 결과 줄조차 안 찍혔다."""
    try:
        return _ad._status_of({"abstractGameState": ab}, ds)
    except Exception as e:                                   # noqa: BLE001
        return e


for _ds, _ab, _want in [("Delayed Start", "Preview", Status.SCHEDULED),
                        ("Delayed", "Live", Status.LIVE),
                        ("Player challenge", "Live", Status.LIVE)]:
    _got = _st(_ab, _ds)
    check(f"'{_ds}'를 잃지 않는다 → {_want.value}", _got is _want,
          f"{type(_got).__name__ if isinstance(_got, Exception) else _got}")
check("처음 보는 값은 조용히 넘기지 않는다 (알림에 남는다)",
      any("처음 보는 상태값" in k for k in _ad.skipped_report()),
      str(_ad.skipped_report()))
# Final 계열은 폴백하지 않는다 — 연기 경기도 abstract가 Final이라 위험하다
check("Final 계열은 지어내지 않고 건너뛴다 (연기도 abstract가 Final이다)",
      isinstance(_st("Final", "Some New Final-ish State"), Exception))
check("아는 값은 그대로 정확히 (연기가 종료로 바뀌지 않는다)",
      _st("Final", "Postponed") is Status.POSTPONED)

# ─────────────────────────────────────────────────────────────
# 13. 새 알림은 반드시 등급을 정하고 들어온다 (v1.11p)
# ─────────────────────────────────────────────────────────────
# 소음을 한 번 걷어내도 새 `note()`가 들어오면 다시 시끄러워진다.
# 실제로 fix35에서 넣은 알림이 사흘 만에 소음이 됐고, NPB 시차 알림은
# 주석에 "정상적인 상태"라 써 놓고 경고로 올리고 있었다.
#
# **이 검사는 소스를 읽어 알림 라벨을 전부 모으고, 등록되지 않은 라벨을 막는다.**
# 새 알림을 넣으면 여기 목록에 한 줄 더해야 한다 — 그때 '경고인가 정보인가'를
# 반드시 한 번 생각하게 된다. 그게 이 검사의 목적이다.
print("\n13. 새 알림은 등급을 정하고 들어오는가")

# 사람이 보고 **할 일이 있는** 것만 경고다. 그 외는 note_info로 내린다.
KNOWN_WARN = {
    # ── 흐름 보강기 (v1.12) — 전부 **경고**다 ────────────────────
    # 흐름표는 결과 카드의 얼굴이라 여기서 어긋나면 카드가 거짓말을 한다.
    # "소스가 안 준다"와 "소스가 우리와 다른 말을 한다"는 둘 다 사람이 봐야 한다.
    # 정상 동작으로 나오는 것은 이 목록에 없다 — 보강이 되면 조용하다(정보 한 줄뿐).
    "이닝 점수 없음", "이닝 합이 최종 점수와 다름",
    "쿼터 점수 없음", "쿼터 합이 최종 점수와 다름",
    "세트 점수 없음", "이긴 세트 수가 세트 스코어와 다름",
    "득점자 수가 점수와 다름",
    "소스에서 같은 경기를 못 찾음",          # 팀 이름이 바뀌면 조용히 사라진다(약점 16)
    "최종 점수가 우리와 다름 — 보강하지 않음",  # 이 소스가 틀린 점수를 준 실측 사례가 있다
    "보강 실패",
    # 소스가 우리가 모르는 것을 줬다 — 표를 고쳐야 한다
    "미등록 값이라 건너뜀", "홈팀 시간대를 몰라 건너뜀", "처음 보는 취소 사유",
    "처음 보는 gameType", "미등록 팀·상태로 건너뜀", "처음 보는 상태값 — 상위 분류로 처리",
    "처음 보는 외부 상태 코드", "외부 팀 표기를 우리 코드로 매핑 못 함",
    "네이버 매핑표에 우리 계약에 없는 팀 코드가 있음",
    "네이버 팀 코드를 우리 코드로 매핑 못 함(보강 안 함)",
    # 결과·점수가 어긋난다 — 사실 오류로 이어질 수 있다
    "두 소스의 점수가 달라 결과를 보류함(추측 금지)",
    "속보의 홈·원정이 일정 페이지와 다름 — 보강 안 함",
    "네이버가 종료라는데 점수가 없어 보강 안 함",
    "네이버는 종료라는데 npb.jp 속보를 못 읽어 보류",
    "네이버 일정에 그 경기가 없어 보강 못 함",
    "같은 날 같은 두 팀이 여러 경기 — 짝을 특정 못 해 보강 안 함",
    "같은 키의 행이 둘 이상 — 확정된 쪽만 남김",
    "승부차기인데 PK 점수를 안 줘서 연장(AET)으로 표기",
    # 수집이 불완전하다
    "소스가 결과를 안 주는 지난 경기라 격리",
    "결과 보강 실패(수집은 계속)", "네이버 보강 못 함(외부 소스)",
    "결과 없는 날짜가 상한(2일)보다 많아 오래된 날은 건너뜀",
    "네이버 보강 예산(12초) 초과로 건너뜀", "보강 예산 초과로 속보 확인 중단",
    "속보 링크를 못 구해 2차 확인 불가(스트립은 npb.jp의 '오늘'만 싣는다)",
    "2차전 합산 확인 실패(시즌 일정을 못 받음)",
    "대조 예산 초과로 건너뜀", "외부 소스 실패(차단 아님)", "대조기 예외(차단 아님)",
    "대조 불가",
    "속보 링크가 없어 키를 못 맞춤 — 보강 안 함(정정 폭풍 방지)",
    "속보 스트립",
    # 소스가 우리 표와 어긋난다 — 사실 오류로 이어질 수 있다
    "네이버와 홈·원정 방향이 다름 — 보강도 대조도 안 함",
    "처음 보는 네이버 상태 코드 — 보강 안 함",
    "보강 값이 계약 검증에 걸려 되돌림",
    "state 칸에 새 값이 보임(진행 신호가 생겼을 수 있음)",
    "점수는 있는데 결과투수 표기가 없어 '종료'로 안 봄",
    # 묵은 데이터로 버티고 있다 — 정상이 아니다
    "캐시로 버팀(묵은 데이터)", "네이버 보강", "시즌 되돌림", "정규리그 0건",
    "다음 시즌 미게시",
    # 네이버 통계 (v1.12) — 표를 고쳐야 하는 것들
    "부문 선수의 팀을 매핑 못 해 건너뜀",
    "선수 이름이나 ID가 비어 건너뜀",
    "팀 수가 예상과 다름",
}
KNOWN_INFO = {
    "흐름 보강",                              # 정상 동작 — 매 틱 나온다
    "묵은 경기 캐시 정리",                     # 정상 동작 — 7일 지난 원본을 지운다
    "선택한 시즌", "시리즈별 수집", "대진 미확정(TBD)이라 건너뜀",
    "대진 미확정·가상팀이라 건너뜀", "발행 대상 아닌 경기 종류로 건너뜀",
    "발행 대상 아닌 시즌 구분이라 제외", "결과 보강",
    "네이버는 종료·npb.jp 속보는 아직 — 다음 틱에 다시 본다",
    "D리그(2군)라 제외", "외국 구단·가상팀이 나오는 구간이라 제외(EASL·올스타)",
    "우리 대회가 아니라 제외(지역 선발전)", "팀 목록 캐시 사용",
    "대조 소스가 없는 리그", "대조 요약",
    "중립 구장 리그의 표시 순서가 반대 — 점수를 맞춰 읽음",
    "순위 표 구분 보정", "표 구분 보정",
    "K리그1 정규리그가 아니어서 제외",
    "같은 경기가 두 행으로 와서 하나로 합침(연기·서스펜디드)",
    "결과 보강(1차 npb.jp 일정이 아직 미게시)",
    "네이버가 취소·중단으로 표기 — 보강 안 함",
    # 네이버 통계 (v1.12) — 소스에 원래 없는 것은 정상이다(NPB에는 QS가 없다)
    "이 소스에 없는 부문이라 제외",
    "HITTER 표본", "PITCHER 표본",
}

_lit = re.compile(r'self\.(note|note_text|note_info|note_text_info)\(\s*"([^"]+)"')
_found: dict[str, str] = {}
for _f in sorted((pathlib.Path(P.__file__).parent / "adapters").glob("*.py")):
    if _f.name == "_notices.py":
        continue
    for _m in _lit.finditer(_f.read_text(encoding="utf-8")):
        _kind, _label = _m.group(1), _m.group(2)
        _found[_label] = "info" if _kind.endswith("_info") else "warn"

_unknown = sorted(l for l in _found if l not in KNOWN_WARN and l not in KNOWN_INFO)
check("등록되지 않은 새 알림이 없다 (있으면 경고/정보를 정하고 목록에 넣을 것)",
      not _unknown, " · ".join(_unknown[:5]))
_miscat = sorted(l for l, k in _found.items()
                 if (k == "warn" and l in KNOWN_INFO) or (k == "info" and l in KNOWN_WARN))
check("등급이 목록과 어긋난 알림이 없다", not _miscat, " · ".join(_miscat[:5]))
check("알림 라벨을 실제로 찾았다 (이 검사가 헛돌지 않게)", len(_found) >= 30,
      f"{len(_found)}개")
print(f"  (알림 라벨 {len(_found)}개 · 경고 "
      f"{sum(1 for v in _found.values() if v == 'warn')} · "
      f"정보 {sum(1 for v in _found.values() if v == 'info')})")

print()
print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
for line in FAIL:
    print(f"  ✗ {line}")
sys.exit(1 if FAIL else 0)
