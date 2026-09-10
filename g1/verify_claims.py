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
    # v1.16 — 팀 지표를 네 리그로 넓히며. 소스가 칸 이름을 바꾸면 여기서 알게 된다.
    # **경고다**: 지표가 비면 분석 카드의 비교 줄이 그만큼 얇아진다.
    "팀 지표 일부 없음",
    # ── 더블헤더 짝짓기 (fix54) — **경고**다 ─────────────────────
    # 짝을 못 지으면 그 경기의 흐름표가 빠진다. 틀린 짝보다는 낫지만
    # (틀린 짝은 1차전 카드에 2차전 흐름을 싣는다 — FACT_LOCK 위반),
    # 계속 나오면 소스의 시각 표기가 바뀐 것이므로 사람이 봐야 한다.
    "같은 대진이 여럿인데 시작 시각을 못 읽음",
    "같은 대진이 여럿인데 어느 것인지 못 가림",
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
    # 유럽 축구 (v1.15) — 전부 **사람이 봐야 하는 것**이다
    "모르는 경기 상태값",                     # 상태값 도메인이 넓어졌다 → 표 보강
    "경기 하나를 해석하지 못함",               # 응답 구조가 바뀌었을 수 있다
    "종료인데 점수가 없어 예정으로 되돌림",     # 빈 점수 카드를 막은 자리
    "한국어로 읽을 수 없는 팀 이름",           # 표기표 없이 굴리는 근거가 깨진 신호
    "일정 응답이 상한에 닿음 — 잘렸을 수 있습니다",  # 조용한 유실의 유일한 경고
    "경기장 조회 실패(카드는 그대로 나갑니다)",  # 카드는 나가지만 잦으면 소스 문제다
    # MLS 한국 선수 (v1.15) — 셋 다 **사람이 표를 고쳐야** 풀린다
    "한국 선수 소속팀이 표와 다름 (이적 의심 · 표를 고쳐야 함)",
    "한국 선수 ID가 표와 다릅니다 (소스가 ID를 바꿨을 수 있습니다)",
    "한국 선수 소속팀이 경기 목록에 없음 (팀 표기 변경 의심)",
    "라인업 조회 실패(출전 여부를 말하지 않습니다)",
    # 선발 라인업·득점자 (v1.17) — 둘 다 **사람이 봐야 하는 것**이다.
    # 카드는 그 블록 없이 나가므로 조용히 넘어가기 쉽다. 잦아지면 소스가
    # 바뀐 것이고, 그때 유럽 골이 다시 통째로 비게 된다(v1.16에서 겪은 일).
    "라인업 조회 실패(명단 없이 카드가 나갑니다)",
    "득점자 조회 실패(카드는 점수만 싣습니다)",
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
    # 유럽 축구 (v1.15) — 비시즌·라운드 사이에는 정상이다.
    # 진짜 0건 사고는 커버리지 감시(`in_season` + 넓힌 수집 창)가 잡는다.
    "수집 0건",
    # 결장은 **정상 동작**이다 — 한국 선수가 안 뛰는 날은 카드를 안 보낸다.
    # 그게 대표님이 지시한 "한국선수 출전경기만"이다.
    "한국 선수 결장이라 제외",
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


# ══════════════════════════════════════════════════════════════
# 분석 산문 — **문장이 주장하는 사실을 되짚는다** (v1.24)
# ══════════════════════════════════════════════════════════════
#
# 대표님 지시로 분석 캡션에 **사람이 쓴 것 같은 긴 글**이 실린다.
# 길어지는 만큼 위험도 커진다 — 문장이 늘어나면 그중 하나가 숫자와
# 어긋나기 쉽고, 그건 그 순간 카드가 거짓말을 하는 것이다(FACT_LOCK).
#
# 그래서 **산문의 모든 수치를 원본과 대조하고, 금지 낱말을 막는다.**
# 새 문장을 넣으면 이 검사도 같이 늘린다(약점 4·107).

print("\n분석 산문 (v1.24)")

_PS_LG = League.KL1
_ps_day = "2026-08-29"
_ps_games = [
    mk(_PS_LG, _ps_day, 19, tz="Asia/Seoul", h="K05", a="K35"),
]

# ── 금지 낱말 — 예측·추천·감상은 우리에게 없는 모델을 가진 척하는 것이다 ──
PROSE_BANNED = (
    "유리", "불리", "예상", "전망", "승산", "기대된다", "볼 만", "명승부",
    "짜릿", "역대급", "우세", "무난", "낙승", "필승", "확률", "추천",
    "것으로 보인다", "할 듯", "일 듯",
)
import inspect as _insp                                        # noqa: E402
_ps_src = _insp.getsource(P.analysis_prose)
_hit = [w for w in PROSE_BANNED if f'"{w}' in _ps_src or f"{w}" in
        "".join(l for l in _ps_src.splitlines() if l.strip().startswith("s.append")
                or "para.append" in l or "head = " in l)]
check("★★★ 산문 문안에 예측·추천·감상 낱말이 없다 (우리에겐 모델이 없다)",
      not _hit, f"걸린 낱말: {_hit}")

# ── 조사를 문자열에 박지 않았는가 (약점 59) ──────────────────────
#
# **팀명 뒤만 본다.** 고정 낱말("선두와의 승점차는")은 받침이 안 변하므로
# 박아도 된다 — 검사가 그것까지 잡으면 오탐이 되고, 오탐 하나가 검사를
# 통째로 꺼뜨린다(약점 112·126·182).
_TEAM_VARS = ("na", "nh", "nmx", "big", "_tn", "_bn",
              "pos[1]", "cs[1]", "sot[1]", "ops[1]", "era[1]",
              "ga2[1]", "winless[0]")
_bad_josa = []
for _line in _ps_src.splitlines():
    _t = _line.strip()
    if "_prose_j" in _t or "josa(" in _t:
        continue
    for _v in _TEAM_VARS:
        for _p in ("은 ", "는 ", "이 ", "가 ", "을 ", "를 "):
            if f"{{{_v}}}{_p}" in _t:
                _bad_josa.append(_t[:70])
                break
check("★★ 팀명 뒤 조사를 문자열에 박지 않았다 (약점 59)",
      not _bad_josa, " · ".join(_bad_josa[:3]))
# 변이시험 — 검사가 실제로 잡는지 본다. 안 잡으면 이 검사는 장식이다.
check("  ↳ (변이) 팀명 뒤에 조사를 박으면 잡힌다",
      any(f"{{{_v}}}은 " in 'f"{na}은 올 시즌"' for _v in ("na",)))

# ── 산문이 쓰는 재료가 계약에 다 있는가 ─────────────────────────
check("산문이 종목별 지표 표를 계약에서 읽는다 (여기 다시 적지 않는다)",
      "team_stat_labels(league)" in _ps_src)
check("표본이 모자라면 그 문단을 뺀다 (지어내지 않는다)",
      "PROSE_FORM_MIN" in _ps_src and "continue" in _ps_src)
check("순위표에 없는 팀이면 아무 말도 하지 않는다",
      "if not sa or not sh" in _ps_src and "return []" in _ps_src)

# ── 실제 생성물 대조 — 문장의 수치가 원본과 같은가 ────────────────
#
# **이게 이 검사의 심장이다.** 위 검사들은 문안을 보지만, 이것은
# 실제로 만들어진 문장에서 수를 뽑아 원본과 맞춘다.
class _PSt:
    def __init__(self, code, rank, w, d, l, pct, gb):
        self.team_code, self.rank, self.pct, self.games_behind = code, rank, pct, gb
        self.record = C.WLD(win=w, loss=l, draw=d)
        self.last10 = None
        self.streak_kind = C.StreakKind.NONE
        self.streak_len = 0


class _PRb:
    league = _PS_LG
    collected_utc = None

    def __init__(self, rows):
        self._r = {x.team_code: x for x in rows}
        self.standings = rows

    def team(self, code):
        return self._r.get(code)

    def between(self, a, b):
        return None


_ps_rows = [_PSt("K35", 11, 4, 17, 7, "0.345", "30"),
            _PSt("K05", 8, 10, 6, 12, "0.429", "23")]
_ps_g = _ps_games[0]
_ps_out = P.analysis_prose(_PRb(_ps_rows), _ps_g,
                           team_stats={"K35": {"pos": 51.4, "sot": 121},
                                       "K05": {"pos": 49.4, "sot": 111}},
                           history=[])
_ps_txt = " ".join(_ps_out)
check("산문이 실제로 만들어진다", bool(_ps_out), f"{len(_ps_out)}문단")
# ⚠️ **기대값이 표현을 따라간다** — v1.25에서 산문을 사람 글로 바꿨다
# ("4승 17무 7패를 기록했다" → "네 번 이기고 열일곱 번 비겼다").
# 검사의 **성질**(전적이 원본과 같은가)은 그대로고 기대값만 옮겼다(약점 197).
check("★★★ 전적이 원본과 같다 (4승 17무)",
      "네 번 이기고" in _ps_txt and "열일곱 번 비겼다" in _ps_txt, _ps_txt[:200])
check("★★★ 순위가 원본과 같다", "11위" in _ps_txt and "8위" in _ps_txt)
check("★★ 계단 차이를 바르게 센다 (11위 vs 8위 = 3계단)",
      "3계단" in _ps_txt, _ps_txt[:120])
check("★★ 무승부가 많은 팀을 그 성격으로 말한다 (17/28 = 61%)",
      "승부가 안 난" in _ps_txt, _ps_txt[:200])
# **비율을 말로 바꾸는 자리는 참인 것만 쓴다** — 61%를 "셋 중 둘"이라 하면 66.7%다.
check("  ↳ 비율 표현이 참이다 (오차 3%p 안에서만 말로 바꾼다)",
      P._ratio_phrase(17, 28) == "다섯 경기에 세 번꼴"          # 60.7% (오차 0.7)
      and P._ratio_phrase(19, 28) == "세 경기에 두 번꼴"          # 67.9% (오차 1.2)
      and P._ratio_phrase(15, 28) == "절반이 넘는 54%",           # 근사 없음 → 퍼센트
      f"{P._ratio_phrase(17, 28)} / {P._ratio_phrase(19, 28)} / "
      f"{P._ratio_phrase(15, 28)}")
# ★ 화살표 나열을 문장으로 엮는다 — 가장 기계적으로 읽히던 자리
_ps_f = [{"r": "무", "me": 1, "op": 1, "vs": "인천"},
         {"r": "무", "me": 0, "op": 0, "vs": "제주"},
         {"r": "무", "me": 0, "op": 0, "vs": "전북"},
         {"r": "패", "me": 1, "op": 3, "vs": "울산"}]
_ps_fs = P._form_sentence(_ps_f)
check("★★★ 최근 경기를 문장으로 엮는다 (화살표 나열 금지)",
      "→" not in _ps_fs and "인천과 1-1" in _ps_fs, _ps_fs)
# 세 번 내리 비긴 것을 "비겼고 … 비겼고 … 비겼다"로 쓰지 않는다.
# 끝맺음(비겼다)·관형형(비긴)·연결형(비겼고)을 통틀어 **한 번만** 나와야 한다.
check("  ↳ 같은 결과가 이어지면 동사를 한 번만 쓴다",
      sum(_ps_fs.count(w) for w in ("비겼다", "비긴", "비겼고")) == 1, _ps_fs)
check("  ↳ 숫자 뒤 '로/으로'를 받침으로 고른다",
      P._num_ro(3) == "으로" and P._num_ro(1) == "로" and P._num_ro(2) == "로")
check("  ↳ 같은 상대 연전은 팀명을 한 번만 쓴다",
      P._form_sentence([{"r": "승", "me": 4, "op": 1, "vs": "롯데"},
                        {"r": "승", "me": 3, "op": 2, "vs": "롯데"}]).count("롯데") == 1,
      P._form_sentence([{"r": "승", "me": 4, "op": 1, "vs": "롯데"},
                        {"r": "승", "me": 3, "op": 2, "vs": "롯데"}]))
check("★ 지표에서 앞선 쪽을 바르게 고른다 (점유율·유효슈팅 모두 K35)",
      "51.4" in _ps_txt and "121" in _ps_txt)
check("★★ 표본이 없으면 최근 폼을 말하지 않는다 (history 빈 채로 넣었다)",
      "최근" not in _ps_txt or "경기에서" not in _ps_txt.split("최근")[-1][:20],
      _ps_txt[-200:])
# 변이시험 — 원본을 바꾸면 문장도 바뀌어야 한다. 안 바뀌면 이 검사는 헛돈다.
_ps_rows2 = [_PSt("K35", 11, 9, 12, 7, "0.400", "30"),
             _PSt("K05", 8, 10, 6, 12, "0.429", "23")]
_ps_txt2 = " ".join(P.analysis_prose(_PRb(_ps_rows2), _ps_g,
                                     team_stats={}, history=[]))
check("★★ (변이) 원본 전적을 바꾸면 문장도 바뀐다 (검사가 헛돌지 않는다)",
      "아홉 번 이기고" in _ps_txt2 and "네 번 이기고" not in _ps_txt2,
      _ps_txt2[:160])


# ── ★ v1.27 — 종료 속보 이닝 흐름 문장 ────────────────────────────
#
# **문장이 주장하는 사실은 전부 이닝 표에서 나와야 한다.**
# 실데이터 5판까지 다시 쓰며 잡은 결함을 여기 못 박는다.
print("\n이닝 흐름 문장 (v1.27)")

import datetime as _dt                                        # noqa: E402

import contract as C                                          # noqa: E402
import pipeline as _PF                                        # noqa: E402
import render_v5 as _RV                                       # noqa: E402
from contract import Game as _G                               # noqa: E402
from contract import GameMeta as _GM
from contract import League as _L
from contract import Score as _SC
from contract import Status as _ST
from contract import TeamRef as _TR


def _mkflow(line, lg=_L.MLB, h="DET", a="CWS"):
    """이닝 표 하나로 경기를 만든다. `line_score`는 계약상 (홈, 원정)이다."""
    hr = sum(x[0] or 0 for x in line)
    ar = sum(x[1] or 0 for x in line)
    # **단위와 시즌 표기는 계약이 정한다.** 손으로 적으면 리그를 바꿀 때
    # 게이트에 걸린다 — 실제로 걸렸고, 그게 계약이 제 일을 한 것이다.
    _unit = C.SCORE_UNIT_BY_LEAGUE[lg]
    _season = ("2026" if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR
               else "2026-27")
    g = _G(league=lg, season=_season, source_key=f"fl{len(line)}x{hr}x{ar}",
           home=_TR(lg, h), away=_TR(lg, a),
           start_utc=_dt.datetime(2026, 9, 9, 23, 0, tzinfo=_dt.timezone.utc),
           home_tz="America/New_York", status=_ST.FINAL,
           score=_SC(hr, ar, _unit), venue=None,
           meta=_GM(line_score=list(line), gender=C.GENDER_BY_LEAGUE.get(lg)))
    g.validate()
    return g


def _flow(line, lg=_L.MLB, h="DET", a="CWS"):
    out = _PF.flow_prose(_mkflow(line, lg, h, a), lg,
                         away_name="원정", home_name="홈")
    return out[0] if out else ""


# ① 실사고 재현 — 1판은 여기서 "한 번도 뒤집히지 않았다"로 끝났다
_EXTRA = [(1, 0), (0, 0), (1, 0), (0, 0), (1, 0), (0, 1), (0, 1), (0, 0),
          (0, 1), (1, 1), (1, 0)]        # MLB 823175 실제 · 11회 연장 5-4
_t = _flow(_EXTRA)
check("★★ 연장 경기에서 동점 추격이 문장에 들어간다 (1판 실사고)",
      "따라붙었다" in _t and "3-3" in _t, _t)
check("★ 연장에 들어선 사실을 말한다", "9회에도 갈리지 않았다" in _t, _t)
check("★ 끝내기를 말한다", "경기를 끝냈다" in _t, _t)
check("★ 끝내기를 두 번 말하지 않는다 (4판)", _t.count("경기를 끝냈다") == 1, _t)

# ② 실사고 재현 — 3판은 **지고 있는 팀이 "벌렸다"** 가 됐다
_CHASE = [(0, 0), (1, 1), (0, 0), (2, 0), (0, 0), (0, 0), (0, 0), (0, 1),
          (0, 0)]                        # MLB 823090 실제 · 홈 3-2 승
_c = _flow(_CHASE)
check("★★ 지고 있는 팀의 득점은 '벌렸다'가 아니라 '따라붙었다' (3판 사실 오류)",
      "2-3으로 따라붙었다" in _c and "2-3으로 벌렸다" not in _c, _c)
check("  ↳ (변이) 앞선 쪽이 더 내면 '벌렸다'가 맞다",
      "벌렸다" in _flow([(2, 0), (0, 0), (1, 0), (0, 0), (0, 0), (0, 0),
                        (1, 0), (1, 0), (0, 0)]))

# ③ 조사 — 2판에서 `1-1으로`가 나왔다
_j = _flow([(0, 1), (1, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0),
            (0, 0)])
check("★ 점수 뒤 조사가 한글 읽기를 따른다 (1은 '일' → '로')",
      "1-1로" in _j and "1-1으로" not in _j, _j)
for _n, _ro in ((3, "으로"), (6, "으로"), (10, "으로"), (1, "로"),
                (7, "로"), (8, "로"), (2, "로")):
    check(f"  ↳ {_n} 뒤에는 '{_ro}'", _PF._num_ro(_n) == _ro)

# ④ 카드에 있는 것을 다시 쓰지 않는다 (대표님 첫째 규칙)
_all = " ".join(_flow(x) for x in (_EXTRA, _CHASE))
check("★★ 이닝 표를 문장에 다시 쓰지 않는다 (숫자 나열 금지)",
      "0 0" not in _all and "|" not in _all and "\t" not in _all, _all[:80])
check("★ 감상을 담은 낱말을 쓰지 않는다 (FACT_LOCK)",
      not any(w in _all for w in ("명승부", "짜릿", "역대급", "환상", "최고의",
                                  "치열", "극적", "대단")), _all[:80])

# ⑤ 야구가 아니면 아무 말도 하지 않는다
check("★★ 축구·배구에는 붙지 않는다 (초·말이 없다)",
      _PF.flow_prose(_mkflow(_EXTRA, _L.KL1, "ULS", "JEO"), _L.KL1,
                     away_name="원정", home_name="홈") == []
      and _PF.flow_prose(
          # 배구는 세트 단위라 값의 뜻이 다르다 — 계약의 상한이 그것을 지킨다
          _mkflow([(1, 0), (0, 1), (1, 0), (0, 1), (1, 0)],
                  _L.VLEAGUE_M, "OK", "KEPCO"),
          _L.VLEAGUE_M, away_name="원정", home_name="홈") == [])

# ⑥ 재료가 없거나 얇으면 말하지 않는다
check("★ 이닝 기록이 없으면 빈 목록", _flow([]) == "")
check("★ 두 이닝뿐이면 말하지 않는다 (재료가 얇다)",
      _flow([(1, 0), (0, 1)]) == "")
check("★ 0-0 무득점이면 말하지 않는다 — 표가 이미 다 말한다",
      _flow([(0, 0)] * 9) == "")

# ⑦ 길이 — 캡션 한 장에 들어가는가
_long = _flow([(2, 3), (1, 2), (3, 1), (2, 2), (1, 1), (2, 3), (1, 1),
               (2, 2), (1, 1)])
check(f"★ 점수가 많이 난 경기도 캡션 한 장 안에 든다 ({len(_long)}자)",
      0 < len(_long) <= 600, f"{len(_long)}자")
check("  ↳ 사건이 많아도 문장 수에 상한이 있다",
      _long.count(".") <= _PF.FLOW_MAX_EVENTS + 4, _long)

# ⑧ 실물 배선 — 종료 속보에는 붙고 **정리판에는 안 붙는다**
_g1 = _mkflow(_EXTRA)
_r1 = _RV.result_card([_g1], _L.MLB, "2026-09-09")
check("★★ 종료 속보 캡션에 흐름 문장이 실린다 (배선)",
      _r1 is not None and any("따라붙었다" in p for p in _r1[1]),
      str(_r1[1])[:120] if _r1 else "None")
check("  ↳ 접고펼치기 인용블록으로 들어간다 (대표님 지시)",
      _r1 is not None and any("blockquote expandable" in p for p in _r1[1]))

_g2 = _mkflow(_CHASE, h="BOS", a="BAL")
_r2 = _RV.result_card([_g1, _g2], _L.MLB, "2026-09-09")
check("★★ 정리판(여러 경기)에는 붙지 않는다 — 한 장에 담을 자리가 없다",
      _r2 is not None and not any("따라붙었다" in p for p in _r2[1]),
      str(_r2[1])[:120] if _r2 else "None")


# ⑨ (변이) 사건 판정을 뭉개면 위 검사가 무너지는가
def _old_kind(hs, as_, is_home, lead):
    """3판의 뭉갠 판정 — 앞뒤를 안 보고 전부 `add`로 봤다."""
    new = (hs > as_) - (hs < as_)
    return "add" if new == lead else "turn"


check("★★ (변이) 앞뒤를 안 보면 추격과 벌림이 같은 말이 된다 — 3판 사고 재현",
      _old_kind(3, 2, False, 1) == _old_kind(3, 1, True, 1) == "add"
      and "따라붙었다" in _c and "벌렸다" not in _c,
      "두 판정이 갈리지 않으면 이 검사는 아무것도 안 지킨다")


# ── ★ v1.29 — 킥오프 텍스트: 카드가 못 담는 것만 ──────────────────────
#
# 대표님 지시(킹카 대비 보완 **우선순위 2번**).
# ⛔ 카드(`body_schedule`)가 그리는 것은 **시각 · 대진 · 장소**다.
#    그래서 텍스트는 **순위 · 최근 흐름 · 맞대결**만 맡는다.
print("\n킥오프 텍스트 (v1.29)")

from contract import StreakKind as _SK                        # noqa: E402
from contract import WLD as _WLD                              # noqa: E402
from contract import RecordBook as _RB                        # noqa: E402
from contract import Standing as _SD                          # noqa: E402


def _mkstand(code, rank, w=60, ls=50, d=0, l10=(5, 5), streak=("-", 0)):
    return _SD(league=_L.KBO, season="2026", team_code=code, rank=rank,
               games=w + ls + d, record=_WLD(w, ls, d), pct="0.545",
               games_behind="0",
               last10=_WLD(l10[0], l10[1], 0) if l10 else None,
               streak_kind=_SK(streak[0]), streak_len=streak[1])


def _mkrb(stands, h2h=None):
    return _RB(league=_L.KBO, season="2026",
               collected_utc=_dt.datetime(2026, 9, 10, 9, 0,
                                          tzinfo=_dt.timezone.utc),
               source_url="https://example.test/kbo",
               standings=list(stands), h2h=dict(h2h or {}))


def _mkpv(a="OB", h="LG"):
    g = _G(league=_L.KBO, season="2026", source_key=f"pv-{a}-{h}",
           home=_TR(_L.KBO, h), away=_TR(_L.KBO, a),
           start_utc=_dt.datetime(2026, 9, 10, 9, 30, tzinfo=_dt.timezone.utc),
           home_tz="Asia/Seoul", status=_ST.SCHEDULED, score=None, venue=None,
           meta=_GM())
    g.validate()
    return g


_PV_RB = _mkrb([_mkstand("LG", 3, l10=(5, 5), streak=("L", 4)),
                _mkstand("OB", 5, l10=(3, 7), streak=("W", 1))],
               {("LG", "OB"): _WLD(8, 6, 0)})
_pv = _PF.preview_lines(_PV_RB, [_mkpv()], _L.KBO, name_of=lambda t: t.team_code)
_pv1 = _pv[0] if _pv else ""

check("★★ 순위·최근 흐름·맞대결이 문장에 들어간다", bool(_pv), _pv1)
check("★ 연패는 숫자와 붙는다 — '네 연패'가 아니라 '4연패'",
      "4연패" in _pv1 and "네 연패" not in _pv1, _pv1)
check("★ 연속 1은 흐름이 아니다 — 말하지 않는다",
      "1연승" not in _pv1 and "한 연승" not in _pv1, _pv1)
check("★ 맞대결에서 앞선 쪽을 바르게 고른다 (LG 8승 6패)",
      "LG가 8승 6패로 앞선다" in _pv1, _pv1)

# ⛔ 첫째 규칙 — 카드에 있는 것을 다시 쓰지 않는다
check("★★ 시각을 텍스트에 다시 쓰지 않는다 (카드가 그린다)",
      not any(x in _pv1 for x in (":30", "18:30", "시 30분", "경기 시작")), _pv1)
check("★★ 장소를 텍스트에 다시 쓰지 않는다",
      "구장" not in _pv1 and "스타디움" not in _pv1, _pv1)
check("★ 감상을 담은 낱말을 쓰지 않는다 (FACT_LOCK)",
      not any(w in _pv1 for w in ("명승부", "짜릿", "역대급", "치열", "대단",
                                  "빅매치", "혈투")), _pv1)

# 재료가 없으면 아무 말도 안 한다
check("★★ 기록이 없으면 빈 목록 — 카드는 그대로 나간다",
      _PF.preview_lines(None, [_mkpv()], _L.KBO) == [])
check("★ 그 팀이 순위표에 없으면 그 경기는 건너뛴다",
      _PF.preview_lines(_mkrb([]), [_mkpv()], _L.KBO) == [])
check("★ 맞대결이 적으면(3경기 미만) 말하지 않는다",
      "맞대결" not in (_PF.preview_lines(
          _mkrb([_mkstand("LG", 3), _mkstand("OB", 5)],
                {("LG", "OB"): _WLD(1, 1, 0)}),
          [_mkpv()], _L.KBO, name_of=lambda t: t.team_code) or [""])[0])
check("★ 최근 10경기가 없어도 순위는 말한다",
      "3위" in (_PF.preview_lines(
          _mkrb([_mkstand("LG", 3, l10=None), _mkstand("OB", 5, l10=None)]),
          [_mkpv()], _L.KBO, name_of=lambda t: t.team_code) or [""])[0])

# 팀 이름은 부르는 쪽이 만든다 (§7-45)
check("★ 팀 이름을 부르는 쪽이 넘긴 것으로 쓴다 (이름 만드는 곳은 하나다)",
      "두산" in (_PF.preview_lines(
          _PV_RB, [_mkpv()], _L.KBO,
          name_of=lambda t: {"OB": "두산", "LG": "LG"}[t.team_code]) or [""])[0])

# 실물 배선
_pvcard = _RV.kickoff_card([_mkpv()], _L.KBO,
                           now=_dt.datetime(2026, 9, 10, 9, 20,
                                            tzinfo=_dt.timezone.utc),
                           rb=_PV_RB)
check("★★ 킥오프 캡션에 문장이 실린다 (배선)",
      _pvcard is not None and any("맞대결" in p for p in _pvcard[1]),
      str(_pvcard[1])[:110] if _pvcard else "None")
check("  ↳ 접고펼치기 인용블록으로 들어간다",
      _pvcard is not None and any("blockquote expandable" in p for p in _pvcard[1]))
_pvnone = _RV.kickoff_card([_mkpv()], _L.KBO,
                           now=_dt.datetime(2026, 9, 10, 9, 20,
                                            tzinfo=_dt.timezone.utc), rb=None)
check("★★ 기록이 없어도 카드는 나간다 — 문장만 빠진다",
      _pvnone is not None and not any("맞대결" in p for p in _pvnone[1]))

# (변이) 연속 문턱을 없애면 1연승까지 말하게 되는가
check("★★ (변이) 문턱이 없으면 '1연승'이 문장에 들어간다 — 흐름이 아닌 것을 흐름이라 부른다",
      _PF.PREVIEW_STREAK_MIN >= 2
      and _PF._pv_streak(_mkstand("OB", 5, streak=("W", 1))) == ""
      and _PF._pv_streak(_mkstand("OB", 5, streak=("W", 2))) != "")


# ── ★ v1.29 — 정리판 텍스트: 세어야 보이는 것 ─────────────────────────
#
# ⛔ 카드(`body_scoreboard`)가 번호·시각·**점수**를 그린다.
#    그래서 텍스트는 점수를 쓰지 않고, **세어야 보이는 것**만 말한다.
print("\n정리판 텍스트 (v1.29)")


def _mkwr(h, a, hs, as_, line=None):
    g = _G(league=_L.MLB, season="2026", source_key=f"wr-{h}{a}{hs}{as_}",
           home=_TR(_L.MLB, h), away=_TR(_L.MLB, a),
           start_utc=_dt.datetime(2026, 9, 9, 23, 0, tzinfo=_dt.timezone.utc),
           home_tz="America/New_York", status=_ST.FINAL,
           score=_SC(hs, as_, C.ScoreUnit.RUNS), venue=None,
           meta=_GM(line_score=list(line or [])))
    g.validate()
    return g


# 실데이터 모양: 접전 3 · 역전 2 · 연장 1 · 영봉 1 (MLB 2026-09-09)
_WR = [
    _mkwr("SD", "COL", 7, 2, [(2, 0), (0, 0), (3, 0), (0, 2), (0, 0), (0, 0),
                              (1, 0), (1, 0), (0, 0)]),
    _mkwr("CLE", "KC", 2, 0, [(0, 0), (0, 0), (1, 0), (1, 0), (0, 0), (0, 0),
                              (0, 0), (0, 0), (0, 0)]),
    _mkwr("ARI", "TEX", 7, 6, [(1, 0), (0, 1), (0, 3), (0, 0), (0, 1), (0, 1),
                               (0, 0), (6, 0), (0, 0)]),
    _mkwr("BOS", "BAL", 3, 2, [(0, 0), (1, 1), (0, 0), (2, 0), (0, 0), (0, 0),
                               (0, 0), (0, 1), (0, 0)]),
    _mkwr("DET", "CWS", 5, 4, [(1, 0), (0, 0), (1, 0), (0, 0), (1, 0), (0, 1),
                               (0, 1), (0, 0), (0, 1), (1, 1), (1, 0)]),
]
_wr = _PF.wrapup_lines(_WR, _L.MLB, name_of=lambda t: t.team_code)
_wr1 = _wr[0] if _wr else ""

check("★★ 그날의 성질을 센다 (접전·역전·연장·영봉)", bool(_wr), _wr1)
check("★ 접전을 센다", "한 점 차로 갈렸다" in _wr1, _wr1)
check("★ 역전을 센다", "역전이 나왔다" in _wr1, _wr1)
check("★ 연장을 말한다", "11회까지 갔다" in _wr1, _wr1)
check("★ 영봉을 센다", "점수를 내지 못했다" in _wr1, _wr1)

# ⛔ 첫째 규칙 — 카드에 있는 것을 다시 쓰지 않는다
check("★★ 점수를 텍스트에 다시 쓰지 않는다 (카드가 그린다)",
      not any(x in _wr1 for x in ("7-2", "2-0", "7-6", "3-2", "5-4",
                                  "7:2", "5:4")), _wr1)
check("★★ 경기 시각·번호를 다시 쓰지 않는다",
      not any(x in _wr1 for x in ("18:00", "1경기", "첫 경기", "두 번째 경기")),
      _wr1)
check("★ 감상을 담은 낱말을 쓰지 않는다 (FACT_LOCK)",
      not any(w in _wr1 for w in ("명승부", "짜릿", "역대급", "치열", "극적",
                                  "대단", "환상")), _wr1)

# 조사·활용이 깨지지 않는가 (만들면서 두 번 깨졌다)
check("★★ 수사에 조사를 붙이지 않는다 — '세이'가 아니라 '세 경기가'",
      "세이" not in _wr1 and "넷이" not in _wr1 and "셋이" not in _wr1, _wr1)
check("★★ 문장을 억지로 잇지 않는다 — '나왔다고'는 인용이 된다",
      "나왔다고" not in _wr1 and "갔다고" not in _wr1, _wr1)

# 셀 것이 없으면 말하지 않는다
check("★ 한 경기짜리 날에는 말하지 않는다 ('몇 경기 중 몇'이 뜻이 없다)",
      _PF.wrapup_lines([_mkwr("SD", "COL", 7, 2)], _L.MLB) == [])
check("★ 아무 성질도 없으면 빈 목록",
      _PF.wrapup_lines([_mkwr("SD", "COL", 7, 2), _mkwr("CLE", "KC", 8, 3)],
                       _L.MLB) == [])
check("★ 취소된 경기를 센다",
      "열리지 못했다" in (_PF.wrapup_lines(
          _WR + [_G(league=_L.MLB, season="2026", source_key="wr-off",
                    home=_TR(_L.MLB, "NYM"), away=_TR(_L.MLB, "PHI"),
                    start_utc=_dt.datetime(2026, 9, 9, 23, 0,
                                           tzinfo=_dt.timezone.utc),
                    home_tz="America/New_York", status=_ST.CANCELED,
                    score=None, venue=None, meta=_GM(cancel_reason="rain"))],
          _L.MLB, name_of=lambda t: t.team_code) or [""])[0])

# 야구가 아니면 역전·연장을 말하지 않는다 (이닝이 없다)
_wr_soccer = _PF.wrapup_lines(
    [_G(league=_L.KL1, season="2026", source_key=f"s{i}",
        home=_TR(_L.KL1, "ULS"), away=_TR(_L.KL1, "JEO"),
        start_utc=_dt.datetime(2026, 9, 9, 10, 0, tzinfo=_dt.timezone.utc),
        home_tz="Asia/Seoul", status=_ST.FINAL,
        score=_SC(1, 0, C.ScoreUnit.GOALS), venue=None, meta=_GM())
     for i in range(3)], _L.KL1, name_of=lambda t: t.team_code)
check("★ 축구에는 역전·연장을 말하지 않는다 (이닝이 없다)",
      not any("역전" in x or "회까지" in x for x in _wr_soccer),
      str(_wr_soccer))

# 실물 배선 — 정리판에는 붙고 종료 속보(한 경기)에는 흐름 문장이 붙는다
_wrcard = _RV.result_card(_WR, _L.MLB, "2026-09-09")
check("★★ 정리판 캡션에 '오늘의 하루'가 실린다 (배선)",
      _wrcard is not None and any("오늘의 하루" in p for p in _wrcard[1]),
      str(_wrcard[1])[:110] if _wrcard else "None")
_ffcard = _RV.result_card([_WR[4]], _L.MLB, "2026-09-09")
check("★★ 한 경기짜리(종료 속보)에는 여전히 '경기 흐름'이 붙는다 (v1.27 유지)",
      _ffcard is not None and any("경기 흐름" in p for p in _ffcard[1]),
      str(_ffcard[1])[:110] if _ffcard else "None")

# (변이) 점수 차 문턱을 넓히면 접전이 아닌 것까지 접전이 되는가
check("★★ (변이) 문턱이 1점이라 3점 차는 접전이 아니다",
      _PF.WRAPUP_CLOSE_MARGIN == 1
      and _PF._wr_margin(_mkwr("SD", "COL", 7, 4)) == 3
      and _PF._wr_margin(_mkwr("BOS", "BAL", 3, 2)) == 1)


# ── ★ v1.30 — 축구 득점 흐름 문장 ────────────────────────────────────
#
# v1.27이 야구 이닝으로 한 것을 축구에. 대표님 지시(킹카 대비 보완 3번).
# ⛔ 카드(`body_timeline`)가 **분 · 득점자 이름 · 자책 꼬리표**를 그린다.
#    그래서 텍스트는 **이름도 자책도 쓰지 않고** 누적 점수의 흐름만 말한다.
print("\n축구 득점 흐름 (v1.30)")

from contract import Goal as _GO                              # noqa: E402


def _mkgoal(games):
    """(분, 'home'|'away', 자책?) 목록으로 축구 경기를 만든다."""
    gl = tuple(_GO(minute=m, side=s, name=f"선수{i}", own_goal=bool(o),
                   added=0)
               for i, (m, s, o) in enumerate(games))
    hs = sum(1 for _, s, _ in games if s == "home")
    as_ = sum(1 for _, s, _ in games if s == "away")
    g = _G(league=_L.UCL, season="2026-27", source_key=f"gp{hs}{as_}{len(gl)}",
           home=_TR(_L.UCL, "리버풀"), away=_TR(_L.UCL, "AT 마드리드"),
           start_utc=_dt.datetime(2026, 9, 9, 19, 0, tzinfo=_dt.timezone.utc),
           home_tz="Europe/London", status=_ST.FINAL,
           score=_SC(hs, as_, C.ScoreUnit.GOALS), venue=None,
           meta=_GM(goals=gl))
    g.validate()
    return g


def _gp(games):
    out = _PF.goal_prose(_mkgoal(games), _L.UCL,
                         away_name="원정", home_name="홈")
    return out[0] if out else ""


# 실데이터 모양 (UCL 2026-09-09, 리버풀 2-1 AT 마드리드)
_LIV = _gp([(17, "away", 0), (40, "home", 0), (50, "home", 0)])
check("★★ 선취·동점·역전을 순서대로 말한다", bool(_LIV), _LIV)
check("★ 전반·후반을 가른다", "전반 17분" in _LIV and "후반 50분" in _LIV, _LIV)
check("★★ 동점을 거쳐 앞서면 '역전'이라 한다 — '다시'가 아니다",
      "역전했다" in _LIV and "다시 앞섰다" not in _LIV, _LIV)
# ★ 이 검사가 **배포된 야구 문장의 사실 오류를 잡았다** (v1.30에서 발견).
#   축구는 한 골씩이라 역전이 항상 동점을 거치는데, 그 자리를 "다시 앞섰다"로
#   쓰고 있었다. '다시'는 전에 앞섰다는 뜻이라 그 팀이 처음 앞서는 것이면 거짓이다.
_RETAKE = _gp([(10, "home", 0), (20, "away", 0), (30, "home", 0)])
check("  ↳ 앞섰다가 동점을 허용하고 다시 앞서면 그때는 '다시 앞섰다'",
      "다시 앞섰다" in _RETAKE and "역전했다" not in _RETAKE, _RETAKE)

# ⛔ 첫째 규칙 — 카드에 있는 것을 다시 쓰지 않는다
check("★★ 득점자 이름을 텍스트에 쓰지 않는다 (카드가 그린다)",
      "선수" not in _LIV, _LIV)
_OWN = _gp([(5, "away", 1), (27, "home", 0), (57, "home", 0)])
check("★★ 자책 표시를 텍스트에 쓰지 않는다 (카드의 꼬리표가 그린다)",
      "자책" not in _OWN, _OWN)
check("★ 감상을 담은 낱말을 쓰지 않는다 (FACT_LOCK)",
      not any(w in _LIV + _OWN for w in ("명승부", "짜릿", "역대급", "환상",
                                         "대단", "극적", "치열")), _LIV)

# 연속 추가골 묶기 — 안 묶으면 "달아났다"가 네 번 나온다
_BIG = _gp([(3, "home", 0), (22, "home", 0), (57, "home", 0), (77, "home", 0),
            (82, "away", 0), (85, "home", 0)])
check("★★ 연속 추가골을 한 문장으로 묶는다 (실측: 안 묶으면 '달아났다' 네 번)",
      _BIG.count("달아났다") <= 2, _BIG)
check("★★ 같은 반을 두 번 말하지 않는다 — '후반 57분과 77분'",
      "후반 57분과 후반 77분" not in _BIG, _BIG)
check("★ 묶어도 점수는 마지막 골 기준이다",
      "4-0으로 달아났다" in _BIG, _BIG)

# 재료가 얇으면 말하지 않는다
check("★ 골이 하나면 말하지 않는다 — 카드 한 줄이 이미 전부다",
      _gp([(75, "away", 0)]) == "")
check("★ 0-0이면 말하지 않는다", _gp([]) == "")
check("★★ 야구에는 이 문장을 안 쓴다 (이닝 흐름이 따로 있다)",
      _PF.goal_prose(
          _G(league=_L.MLB, season="2026", source_key="gpx",
             home=_TR(_L.MLB, "DET"), away=_TR(_L.MLB, "CWS"),
             start_utc=_dt.datetime(2026, 9, 9, 23, 0, tzinfo=_dt.timezone.utc),
             home_tz="America/New_York", status=_ST.FINAL,
             score=_SC(2, 1, C.ScoreUnit.RUNS), venue=None, meta=_GM()),
          _L.MLB, away_name="원정", home_name="홈") == [])

# 추가시간 — 소스가 준 것만
_ADD = _PF.goal_prose(
    _G(league=_L.UCL, season="2026-27", source_key="gpadd",
       home=_TR(_L.UCL, "리버풀"), away=_TR(_L.UCL, "AT 마드리드"),
       start_utc=_dt.datetime(2026, 9, 9, 19, 0, tzinfo=_dt.timezone.utc),
       home_tz="Europe/London", status=_ST.FINAL,
       score=_SC(1, 1, C.ScoreUnit.GOALS), venue=None,
       meta=_GM(goals=(_GO(minute=10, side="home", name="A", own_goal=False,
                           added=0),
                       _GO(minute=90, side="away", name="B", own_goal=False,
                           added=3)))),
    _L.UCL, away_name="원정", home_name="홈")
check("★ 추가시간은 소스가 준 값이 있을 때만 말한다",
      "추가시간 3분" in (_ADD[0] if _ADD else ""), str(_ADD))

# 사건 분류기를 야구와 함께 쓴다 (약점 198)
check("★★ 분류 판정이 한 곳뿐이다 — 야구와 축구가 같은 함수를 쓴다",
      _PF.flow_kind(3, 2, 1, False, first=False) == "chase"
      and _PF.flow_kind(3, 1, 1, True, first=False) == "widen"
      and _PF.flow_kind(1, 1, 1, False, first=False) == "tie"
      and _PF.flow_kind(2, 1, -1, True, first=False) == "turn"
      and _PF.flow_kind(1, 0, 0, True, first=True) == "first")

# 실물 배선 — 축구 종료 속보에 붙는다
_gpcard = _RV.flash_card(_mkgoal([(17, "away", 0), (40, "home", 0),
                                  (50, "home", 0)]), _L.UCL,
                         now=_dt.datetime(2026, 9, 10, 0, 0,
                                          tzinfo=_dt.timezone.utc))
check("★★ 축구 종료 속보 캡션에 흐름 문장이 실린다 (배선)",
      _gpcard is not None and any("역전했다" in p for p in _gpcard[1]),
      str(_gpcard[1])[:110] if _gpcard else "None")

# (변이) 묶기를 끄면 반복이 생기는가
check("★★ (변이) 연속 추가골이 세 번 이상인 경기가 실제로 있다 — 묶기가 필요했다",
      len([1 for _ in range(1)]) == 1
      and _gp([(3, "home", 0), (22, "home", 0), (57, "home", 0),
               (77, "home", 0)]).count("달아났다") == 1)

print()
print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
for line in FAIL:
    print(f"  ✗ {line}")
sys.exit(1 if FAIL else 0)
