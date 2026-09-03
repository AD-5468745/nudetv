"""시계 적대적 검증 — 무인 운영에서 깨질 만한 것을 전부 깨본다 (v1.11c).

시계는 사람이 안 보는 동안 24시간 돈다. 여기서 못 잡은 것은 새벽에 채널에서 터진다.
특히 두 가지가 치명적이다:

  1. **같은 카드를 두 번 보내는 것** — 되돌릴 수 없다
  2. **조용히 아무것도 안 하는 것** — 며칠 뒤에야 알아챈다

실행 환경이 매번 새 컨테이너(무상태)라는 점이 이 둘을 다 어렵게 만든다.
상태를 파일로 남기는데, 그 파일이 사라지거나 깨졌을 때 어느 쪽으로 넘어지는지가 중요하다.
**"모르면 안 보낸다"** 가 정답이다 — 안 보낸 것은 다음 틱에 보내면 되지만
두 번 보낸 것은 되돌릴 수 없다.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import contract as C
from contract import (GRACE_SECONDS, ContentType, GameMeta, Game, GateError, KST, League, Score,
                      ScoreUnit, SendState, Status, TeamRef, is_late)
import pipeline as P

ok = fail = 0


def check(n, c, d=""):
    global ok, fail
    if c:
        ok += 1; print(f"  PASS  {n}")
    else:
        fail += 1; print(f"  FAIL  {n}  {d}")


TMP = pathlib.Path(tempfile.mkdtemp(prefix="tickverify-"))
os.environ["NUDETV_STATE"] = str(TMP)
os.environ.setdefault("TELEGRAM_CHAT_ID", "-100test")
import tick as T                                            # noqa: E402
T.ROOT = TMP
T.LEDGER = TMP / "ledger.jsonl"
T.FETCH_LOG = TMP / "fetch.json"
T.SNAP_DIR = TMP / "games"

NOW = datetime(2026, 8, 28, 22, 0, tzinfo=timezone.utc)


def mkgame(lg=League.KBO, h="LG", a="OB", day="2026-08-29", hh=18,
           status=Status.SCHEDULED, score=None, cancel=None):
    st = datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), hh, 30,
                  tzinfo=ZoneInfo("Asia/Seoul"))
    yr, mo = int(day[:4]), int(day[5:7])
    if C.SEASON_FORMAT_BY_LEAGUE[lg] is C.SEASON_SINGLE_YEAR:
        season = f"{yr}"
    else:
        s0 = yr if mo >= 7 else yr - 1
        season = f"{s0}-{str(s0 + 1)[2:]}"
    g = Game(league=lg, season=season, source_key=f"{lg.value}-{day}-{hh}-{h}{a}",
             home=TeamRef(lg, h), away=TeamRef(lg, a),
             start_utc=st.astimezone(timezone.utc), home_tz="Asia/Seoul",
             status=status, score=score, venue="테스트구장",
             meta=GameMeta(gender=C.GENDER_BY_LEAGUE.get(lg), cancel_reason=cancel))
    g.validate()
    return g


print("=" * 62)
print("시계 적대적 검증")
print("=" * 62)

# ── 1. 스냅샷 왕복 ────────────────────────────────────────────
print("\n1. 스냅샷 왕복 — 저장했다 읽으면 그대로인가")
# 손실이 있으면 카드가 틀린다. 카드가 틀리면 사실 오류다.
src = [
    mkgame(status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
    mkgame(h="KT", a="SS", hh=17, status=Status.CANCELED, cancel="우천취소"),
    mkgame(h="HT", a="LT", hh=14),
]
T._save_games("T1", src)
back = T._load_games("T1")
check("건수 보존", len(back) == len(src))
check("game_id 보존", [g.game_id for g in back] == [g.game_id for g in src])
check("상태 보존", [g.status for g in back] == [g.status for g in src])
check("점수 보존", back[0].score is not None
      and (back[0].score.home, back[0].score.away) == (5, 3))
check("점수 단위 보존", back[0].score.unit is ScoreUnit.RUNS)
check("취소 사유 보존", back[1].meta.cancel_reason == "우천취소")
check("sports_day 보존", [g.sports_day for g in back] == [g.sports_day for g in src])
check("시작 시각 보존", all(a.start_utc == b.start_utc for a, b in zip(back, src)))
check("되읽은 것도 계약 통과", all(g.validate() is None for g in back))

# 다전제(BO5)는 e스포츠 카드에 필요하다
lck = Game(league=League.LCK, season="2026", source_key="L1",
           home=TeamRef(League.LCK, "T1"), away=TeamRef(League.LCK, "GEN"),
           start_utc=NOW, home_tz="Asia/Seoul", status=Status.FINAL,
           score=Score(3, 1, ScoreUnit.MAPS), venue=None,
           meta=GameMeta(best_of=5, season_category="LCK 2026"))
lck.validate()
T._save_games("T2", [lck])
check("BO(다전제) 보존", T._load_games("T2")[0].meta.best_of == 5)

# ── 2. 깨진 상태 파일 ─────────────────────────────────────────
print("\n2. 깨진 상태 — 어느 쪽으로 넘어지는가")
(TMP / "games" / "T3.json").write_text("{ 이건 JSON이 아니다", encoding="utf-8")
try:
    T._load_games("T3")
    check("깨진 스냅샷은 예외로 드러난다", False, "조용히 넘어갔다")
except json.JSONDecodeError:
    check("깨진 스냅샷은 예외로 드러난다 (조용히 0건이 되지 않는다)", True)

T.FETCH_LOG.write_text("깨진 파일", encoding="utf-8")
check("깨진 수집 로그는 '수집한 적 없음'과 같게 다룬다 (다음 틱에 다시 수집)",
      T._fetch_log() == {})
T.FETCH_LOG.unlink()

# 원자적 저장 — 중간에 죽어도 반쪽 파일이 남지 않는다
check("저장은 임시파일 → 교체 (반쪽 파일 방지)",
      not list((TMP / "games").glob("*.tmp")))

# ── 3. 큐 — 리그가 서로를 덮지 않는가 ─────────────────────────
print("\n3. 큐 — 여러 리그가 한 큐에 섞일 때")
snaps = {
    "KBO": [mkgame(League.KBO, "LG", "OB", hh=18)],
    "NPB": [mkgame(League.NPB, "YOG", "HAN", hh=18)],
    "KL1": [mkgame(League.KL1, "K01", "K02", hh=19)],
}
items = T.build_all_queues(snaps, NOW, "-100test")
check(f"큐 {len(items)}건 생성", len(items) > 0)
# ── 멱등키 — 2026-09-03 갱신 (콘텐츠 3종 → 7종) ────────────────────
#
# **옛 기대:** "큐의 멱등키는 전부 유일".
# **왜 못 쓰게 됐나:** 나이트 브리핑은 **전 리그 통합 1건**이라 scope가 `ALL:날짜`다.
# `build_queue`는 리그별로 불리므로 KBO·NPB·KL1이 **일부러** 같은 키를 만들고,
# 대장(ledger)이 키로 접어 실제 발송은 1회다. 즉 여기서의 중복은 사고가 아니라 설계다.
#
# **원래 이 검사가 지키려던 것:** ① 같은 카드가 두 번 나가지 않는다
# ② 나가야 할 카드가 다른 카드에 '이미 보냄'으로 먹히지 않는다
# (v1.11c — 리그가 없는 키 때문에 KBO만 나가고 나머지 여덟 리그가 영영 안 나가던 사고).
#
# 기대를 그냥 뒤집으면 그 사고가 되살아난다. 그래서 목적을 네 갈래로 나눠 계속 지킨다.
_nb = [i for i in items if i.content_type is ContentType.NIGHT_BRIEF]
_rest = [i for i in items if i.content_type is not ContentType.NIGHT_BRIEF]
check("나이트 브리핑을 뺀 멱등키는 전부 유일",
      len({i.idem_key for i in _rest}) == len(_rest),
      f"{len(_rest) - len({i.idem_key for i in _rest})}건 중복")

# 리그별 키 집합 — 통합 카드(나이트)와 리그 카드를 따로 본다.
# (build_all_queues는 나이트 브리핑의 league를 None으로 두므로 items만으로는
#  '어느 리그가 만든 키인지'를 알 수 없다. 리그별로 다시 만들어 비교한다.)
_keys_nb: dict[str, set] = {}
_keys_own: dict[str, set] = {}
for _name, _gs in snaps.items():
    _q1 = P.build_queue(_gs, NOW, "-100test", floor_hours=0)
    _keys_nb[_name] = {i.idem_key for i in _q1
                       if i.content_type is ContentType.NIGHT_BRIEF}
    _keys_own[_name] = {i.idem_key for i in _q1
                        if i.content_type is not ContentType.NIGHT_BRIEF}

# **여기가 진짜 위험이다.** 나이트 브리핑 키가 리그마다 조금이라도 다르면
# 대장이 못 접어서 **리그 수만큼** 같은 통합 카드가 채널에 나간다(아홉 리그면 하루 아홉 번).
_nb_sets = list(_keys_nb.values())
check(f"나이트 브리핑은 리그가 달라도 완전히 같은 키 "
      f"({len(_nb)}건 → 키 {len({i.idem_key for i in _nb})}개, 다르면 리그 수만큼 발송된다)",
      bool(_nb_sets) and all(s and s == _nb_sets[0] for s in _nb_sets),
      str({k: sorted(v) for k, v in _keys_nb.items()})[:160])
# 통합 카드를 뺀 나머지는 예전 기대 그대로 — 리그끼리 절대 안 겹친다.
_clash = [(a, b, sorted(_keys_own[a] & _keys_own[b])[:2])
          for a in _keys_own for b in _keys_own
          if a < b and (_keys_own[a] & _keys_own[b])]
check("나이트 브리핑을 뺀 키는 리그 간 충돌 0", not _clash, str(_clash[:2]))

# 같은 리그·같은 날 안에서도 종류가 다르면 키가 달라야 한다. 안 그러면
# 결과 카드가 나간 뒤 순위표가 '이미 보냄'으로 조용히 사라진다
# (콘텐츠가 3종일 때는 한 리그·한 날에 종류가 겹칠 일이 거의 없어 안 보이던 위험이다).
_by_slot: dict = {}
for i in items:
    _by_slot.setdefault((i.league, i.sports_day), {}) \
            .setdefault(i.content_type, set()).add(i.idem_key)
_ct_clash = [(str(lg), day, ca.value, cb.value)
             for (lg, day), m in _by_slot.items()
             for ca in m for cb in m
             if ca.value < cb.value and (m[ca] & m[cb])]
check("같은 리그·같은 날이어도 콘텐츠 종류가 다르면 키가 다르다",
      not _ct_clash, str(_ct_clash[:2]))

morn = [i for i in items if i.content_type is ContentType.MORNING]
check(f"모닝 브리핑이 리그 수만큼 ({len(morn)}건)", len(morn) == 3, str(len(morn)))
check("모닝 키에 리그가 들어있다",
      {i.league for i in morn} == {League.KBO, League.NPB, League.KL1})
check("큐가 시각순 정렬", all(items[i].scheduled_utc <= items[i + 1].scheduled_utc
                          for i in range(len(items) - 1)))

# 한 리그가 깨져도 나머지는 큐에 남는다
class Boom(list):
    """접근하면 터지는 목록 — 한 리그의 손상을 흉내낸다."""
    @property
    def broken(self):
        raise RuntimeError("이 리그는 깨졌다")


snaps_bad = dict(snaps)
snaps_bad["BAD"] = [object()]                # Game이 아닌 것이 들어온 상황
items2 = T.build_all_queues(snaps_bad, NOW, "-100test")
check("한 리그가 깨져도 나머지 큐는 살아남는다", len(items2) >= len(items),
      f"{len(items2)} vs {len(items)}")

# ── 4. 늦은 항목 ──────────────────────────────────────────────
print("\n4. 지각 — 늦으면 스스로 버리는가")
# 무료 스케줄러는 늦는다. "10분 뒤 시작" 카드가 20분 늦게 나가면 거짓말이다.
late_at = NOW - timedelta(seconds=C.GRACE_SECONDS[ContentType.START_ALERT] + 60)
check("유예를 넘긴 시작 알림은 지각",
      is_late(late_at, NOW, ContentType.START_ALERT))
check("유예 안이면 지각 아님",
      not is_late(NOW - timedelta(seconds=60), NOW, ContentType.START_ALERT))
# 결과 카드는 유예가 길다 — 소스가 늦게 채우기 때문
check("결과 카드 유예가 시작 알림보다 길다",
      C.GRACE_SECONDS[ContentType.LEAGUE_RESULT]
      > C.GRACE_SECONDS[ContentType.START_ALERT])

# ── 5. 렌더 ───────────────────────────────────────────────────
print("\n5. 렌더 — 만들 수 없으면 조용히 실패하지 않는가")
kbo_day = [mkgame(League.KBO, "LG", "OB", hh=18, status=Status.FINAL,
                  score=Score(5, 3, ScoreUnit.RUNS)),
           mkgame(League.KBO, "KT", "SS", hh=17, status=Status.FINAL,
                  score=Score(2, 7, ScoreUnit.RUNS))]
q = T.build_all_queues({"KBO": kbo_day}, NOW, "-100test")
res = [i for i in q if i.content_type is ContentType.LEAGUE_RESULT]
if res:
    made = T.render_for(res[0], kbo_day)
    check("결과 카드가 만들어진다", made is not None)
    if made:
        photos, parts = made
        check("사진 1장 + 캡션", len(photos) == 1 and len(parts) >= 1)
        # v1.11i 육안검수: 전에는 `<blockquote expandable>`을 요구했다. 그런데
        # 캡션은 줄 수와 무관하게 **항상** 접혀 나가고 있었고, 2줄짜리에 붙은
        # '펼치기'는 "내용이 더 있다"는 거짓 신호다. 게다가 같은 5경기가 메시지
        # 종류에 따라 접히기도 안 접히기도 했다(시작 알림만 6줄 기준을 썼다).
        # 지켜야 할 것은 '접힘'이 아니라 **캡션이 인용블록을 쓴다**는 구조다.
        check("캡션이 인용블록을 쓴다", "<blockquote" in parts[0], parts[0][:80])
        _long = "\n".join(f"줄 {i}" for i in range(12))
        check("긴 캡션은 접는다", P.QUOTE_EXPANDABLE_THRESHOLD_LINES < 12)
        check("사진이 실제 바이트", len(photos[0][1]) > 5000, f"{len(photos[0][1])}B")
else:
    check("결과 카드 큐 항목", False, "큐에 결과 카드가 없다")

# 종료 경기가 없으면 결과 카드를 만들지 않는다 (빈 카드를 내보내지 않는다)
sched_only = [mkgame(League.KBO, "LG", "OB", hh=18)]
fake = C.QueueItem(idem_key="x", content_type=ContentType.LEAGUE_RESULT,
                   scope="kbo:2026-08-29", scheduled_utc=NOW,
                   league=League.KBO, sports_day="2026-08-29")
check("결과가 아직 없으면 카드를 만들지 않는다 (다음 틱에 다시 본다)",
      T.render_for(fake, sched_only) is None)
# 그날 경기가 아예 없으면
empty = C.QueueItem(idem_key="y", content_type=ContentType.MORNING,
                    scope="kbo:2099-01-01", scheduled_utc=NOW,
                    league=League.KBO, sports_day="2099-01-01")
check("그날 경기가 없으면 None", T.render_for(empty, sched_only) is None)
# sports_day가 없는 항목은 scope를 날짜로 오해하면 안 된다
noday = C.QueueItem(idem_key="z", content_type=ContentType.MORNING,
                    scope="kbo:2026-08-29", scheduled_utc=NOW, league=League.KBO)
check("sports_day가 없으면 scope를 날짜로 쓰지 않는다",
      T.render_for(noday, sched_only) is None)

# ── 6. 대장 ───────────────────────────────────────────────────
print("\n6. 대장 — 같은 것을 두 번 보내지 않는가")
from sender import Ledger, Pacer, Payload, Sender                # noqa: E402


class Fake:
    """호출을 기록만 하는 가짜 전송기. (verify_sender를 임포트하면 그 파일이
    통째로 실행되므로 여기서 따로 둔다.)"""

    def __init__(self):
        self.calls = []
        self._id = 5000

    def call(self, method, payload, files=None):
        self.calls.append((method, payload, sorted((files or {}).keys())))
        if method == "sendMediaGroup":
            out = []
            for _ in payload["media"]:
                self._id += 1
                out.append({"message_id": self._id})
            return out
        self._id += 1
        return {"message_id": self._id}

    @property
    def sent(self):
        return [c for c in self.calls
                if c[0] in ("sendPhoto", "sendMediaGroup", "sendMessage")]

led_path = TMP / "l1.jsonl"
led = Ledger(led_path)
f = Fake()
snd = Sender(f, led, "-100test", pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0),
             now=lambda: NOW)
item = C.QueueItem(idem_key=C.idem_key("-100test", ContentType.MORNING, "kbo:2026-08-29"),
                   content_type=ContentType.MORNING, scope="kbo:2026-08-29",
                   scheduled_utc=NOW, league=League.KBO, sports_day="2026-08-29")
p1 = Payload(photos=[("a.jpg", b"x" * 900, 1080, 1400)], caption="c")
r1 = snd.send(item, p1)
check("첫 발송 성공", r1.state is SendState.SENT)
r2 = snd.send(item, p1)
# 반환 state는 '이번에 보냈나'가 아니라 '이 항목의 최종 상태'다 —
# 이미 보낸 항목은 SENT를 그대로 돌려준다. 진짜 검사는 **API를 다시 쳤는가**이다.
check("두 번째 호출은 API를 치지 않는다", len(f.sent) == 1, f"{len(f.sent)}회")
check("이미 처리됨을 사유로 알린다", "이미 처리" in r2.reason, r2.reason[:50])
check("메시지 id가 새로 생기지 않는다", r2.message_ids == r1.message_ids)
# **로그가 사실을 말해야 한다.** 둘 다 state는 SENT라, 구분하지 않으면 시계가
# 돌 때마다 "발송 성공 2"가 찍혀 채널에 카드가 쌓이는 것처럼 보인다.
# 첫날 밤 로그가 실제로 그랬다(중복 발송은 없었지만 로그로는 알 수 없었다).
check("첫 발송은 '이번에 보냄'으로 표시된다", r1.already is False)
check("두 번째는 '이미 보냄'으로 표시된다 (로그가 거짓말하지 않게)", r2.already is True)

# 새 컨테이너를 흉내낸다 — 대장 파일만 있고 메모리는 비었다
led2 = Ledger(led_path)
f2 = Fake()
snd2 = Sender(f2, led2, "-100test", pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0),
              now=lambda: NOW)
r3 = snd2.send(item, p1)
check("새 컨테이너에서도 API를 치지 않는다 (대장이 유일한 근거)",
      len(f2.sent) == 0, f"{len(f2.sent)}회 — 중복 발송!")
check("새 컨테이너도 이미 처리됨으로 판단", "이미 처리" in r3.reason, r3.reason[:50])

# 대장이 사라지면? — 이것이 이 시스템의 가장 위험한 상태다
led_path.unlink()
led3 = Ledger(led_path)
f3 = Fake()
snd3 = Sender(f3, led3, "-100test", pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0),
              now=lambda: NOW)
r4 = snd3.send(item, p1)
check("대장이 사라지면 다시 보낸다 — 그래서 대장 보존이 최우선이다",
      r4.state is SendState.SENT)
print("        ↳ 운영 규칙: state/ 폴더는 절대 지우지 않는다. "
      "지우면 그날 것이 다시 나간다.")

# ── 7. 수집 실패 격리 ─────────────────────────────────────────
print("\n7. 수집 실패 — 한 리그가 죽어도 나머지가 도는가")
_orig = T._jobs


def _jobs_mixed():
    def boom():
        raise GateError("소스 점검 중")
    return {
        "GOOD": (League.KBO, lambda: [mkgame(League.KBO, "LG", "OB", hh=18)]),
        "BAD": (League.NPB, boom),
    }


T._jobs = _jobs_mixed
counts, errors, soft = T.collect(NOW, force=True)
check("성공한 리그는 저장된다", counts.get("GOOD") == 1, str(counts))
check("실패한 리그는 오류로 보고된다", any("BAD" in e for e in errors), str(errors))
check("실패해도 성공한 리그가 스냅샷에 남는다", len(T._load_games("GOOD")) == 1)
log = T._fetch_log()
check("실패 사유가 로그에 남는다", bool(log.get("BAD", {}).get("error")))
check("실패한 리그는 마지막 성공 시각을 덮어쓰지 않는다",
      log.get("BAD", {}).get("at") is None)
check("소스 구조 오류는 '기다리면 풀릴 것'으로 분류되지 않는다", not soft, str(soft))
check("실패 시각이 기록된다 (다음 시도를 늦추는 근거)",
      bool(log.get("BAD", {}).get("failed_at")))

# ── 7-1. 일시적 실패는 빨간불을 올리지 않는다 ──────────────────
# 첫 배포에서 실제로 이 덫에 걸렸다: Leaguepedia 레이트리밋 하나로 시계 전체가
# 실패 처리되고, 실패한 실행은 캐시를 저장하지 않아 다음 실행도 캐시 없이 출발했다.
print("\n7-1. 일시적 실패 — 다음 틱에 풀릴 것을 사고로 올리지 않는가")
from adapters.lck import RateLimited                          # noqa: E402


def _jobs_ratelimited():
    def limited():
        raise RateLimited("Leaguepedia: ratelimited")
    return {"GOOD": (League.KBO, lambda: [mkgame(League.KBO, "LG", "OB", hh=18)]),
            "LCK": (League.LCK, limited)}


T._jobs = _jobs_ratelimited
counts, errors, soft = T.collect(NOW, force=True)
check("레이트리밋은 '기다리면 풀릴 것'으로 분류된다",
      any("LCK" in s for s in soft), str(soft))
check("레이트리밋은 사람이 볼 실패에 안 들어간다", not errors, str(errors))
check("그래도 조용히 넘기지는 않는다 (로그에 남는다)", len(soft) == 1)
check("다른 리그는 정상 수집된다", counts.get("GOOD") == 1)

# 막힌 소스를 5분마다 다시 두드리지 않는가 (force 없이)
counts2, errors2, soft2 = T.collect(NOW + timedelta(minutes=5))
check("막힌 소스는 15분 안에 다시 두드리지 않는다", not soft2 and not errors2,
      f"soft={soft2} errors={errors2}")
counts3, errors3, soft3 = T.collect(NOW + timedelta(minutes=16))
check("15분이 지나면 다시 시도한다", any("LCK" in s for s in soft3), str(soft3))
T._jobs = _orig

# ── 8. 커버리지 감시 ──────────────────────────────────────────
print("\n8. 커버리지 감시 — 조용한 실패를 잡는가")
import coverage as CV

_CNOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)   # KST 21:00, 야구 시즌 중
_YDAY = "2026-08-27"
_TDAY = "2026-08-28"
_fresh = {"at": _CNOW.isoformat(), "count": 5, "error": None}


def _days(lg, day, n, done=False, **kw):
    """done=True면 종료 상태로 만든다 — 지난 경기를 '예정'으로 두면
    감시가 (정당하게) '결과가 안 들어왔다'로 잡는다."""
    sc = Score(5, 3, ScoreUnit.RUNS) if done else None
    st = Status.FINAL if done else Status.SCHEDULED
    # 같은 시각이 겹치면 source_key가 같아진다 — 팀을 바꿔 구분한다
    # 오늘 경기는 유예(6시간) 안이어야 한다. _CNOW가 KST 21:00이므로 19시 이후로 둔다 —
    # 그러지 않으면 감시가 (정당하게) '결과가 안 들어왔다'로 잡는다.
    pairs = [("LG", "OB"), ("KT", "SS"), ("HT", "LT"), ("NC", "WO"), ("SK", "HH"),
             ("OB", "LG"), ("SS", "KT"), ("LT", "HT"), ("WO", "NC"), ("HH", "SK")]
    return [mkgame(lg, pairs[i % len(pairs)][0], pairs[i % len(pairs)][1],
                   day=day, hh=(14 + i if done else 19), status=st, score=sc, **kw)
            for i in range(n)]


# 정상 — 어제도 오늘도 경기가 있다
r = CV.run({"KBO": _days(League.KBO, _YDAY, 5, done=True)
                   + _days(League.KBO, _TDAY, 5)},
           {"KBO": _fresh}, _CNOW)
check("정상이면 이상 없음", r.ok, str(r.lines()))

# 어제 5경기 → 오늘 0경기: 소스가 오늘 편성을 안 준다
r = CV.run({"KBO": _days(League.KBO, _YDAY, 5, done=True)}, {"KBO": _fresh}, _CNOW)
check("오늘 편성이 사라지면 잡는다",
      any("사라" in x for x in r.lines()), str(r.lines()))

# 반쯤 깨진 경우 — 0건만 보면 놓친다
r = CV.run({"KBO": _days(League.KBO, _YDAY, 10, done=True)
                   + _days(League.KBO, _TDAY, 1)},
           {"KBO": _fresh}, _CNOW)
check("절반 넘게 줄어도 잡는다 (0건만 보면 놓친다)",
      any("급감" in x for x in r.lines()), str(r.lines()))

# 수집이 멈춤 — 스냅샷은 남아 있어서 내용만 보면 정상처럼 보인다
_old = {"at": (_CNOW - timedelta(hours=9)).isoformat(), "count": 5, "error": None}
r = CV.run({"KBO": _days(League.KBO, _YDAY, 5, done=True)
                   + _days(League.KBO, _TDAY, 5)},
           {"KBO": _old}, _CNOW)
check("수집이 멈추면 잡는다 (스냅샷이 남아 있어도)",
      any("멈춤" in x for x in r.lines()), str(r.lines()))

# 한 번도 성공 못 함
r = CV.run({"LCK": []}, {"LCK": {"at": None, "count": 0, "error": "ratelimited"}}, _CNOW)
check("한 번도 수집 못 한 리그를 잡는다",
      any("성공 기록 없음" in x for x in r.lines()), str(r.lines()))
check("시즌 중 리그가 못 들어오면 빨간불 (LCK는 8월이 시즌)", not r.ok, str(r.lines()))

# 비시즌 리그의 수집 실패 — 알리되 빨간불은 아니다.
# 이걸 구분 못 하면 8월마다 농구가 울고, 그 소음에 진짜 사고가 묻힌다.
r = CV.run({"KBL": []}, {"KBL": {"at": None, "count": 0, "error": "timeout"}}, _CNOW)
check("비시즌 리그의 수집 실패도 알리기는 한다", bool(r.findings), str(r.lines()))
check("비시즌 리그의 수집 실패는 빨간불이 아니다", r.ok, str(r.lines()))
check("비시즌임을 문구로 알 수 있다",
      any("비시즌" in x for x in r.lines()), str(r.lines()))

# 비시즌은 조용한 게 정상 — 8월 농구·배구
r = CV.run({"KBL": [], "VLEAGUE_M": []},
           {"KBL": _fresh, "VLEAGUE_M": _fresh}, _CNOW)
check("비시즌 0건은 경보가 아니다 (8월 농구·배구)", r.ok, str(r.lines()))

# 시즌 중 0건은 경보 — 1월 농구
_JAN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
r = CV.run({"KBL": []}, {"KBL": {"at": _JAN.isoformat(), "count": 0, "error": None}}, _JAN)
check("시즌 중 0건은 경보 (1월 농구)",
      any("0건" in x for x in r.lines()), str(r.lines()))

# 결과가 안 들어온 지난 경기
r = CV.run({"KBO": _days(League.KBO, _YDAY, 3) + _days(League.KBO, _TDAY, 3)},
           {"KBO": _fresh}, _CNOW + timedelta(days=2))
check("결과가 안 들어온 지난 경기를 잡는다",
      any("결과가 안 들어온" in x for x in r.lines()), str(r.lines()))

check("전 리그가 시즌 표에 등록됨",
      set(C.SEASON_MONTHS) == set(League),
      str([l.value for l in League if l not in C.SEASON_MONTHS]))

# ── 9. 시작 알림 = 리그 하루 한 통 (v1.11c) ────────────────────
print("\n9. 시작 알림 — 도배가 아니라 하루 한 통인가")
# 사고: 같은 시각(±5분) 경기를 묶어 시각마다 보냈더니 실측 **하루 26건**,
# 그중 17건이 MLB 새벽 1~3시대였다. 새벽에 열일곱 번 울리는 채널은 구독자가 나간다.
from contract import day_schedule_scope, START_ALERT_LEAD_MINUTES

# NOW는 2026-08-29 07:00 KST다. 경기는 그 뒤여야 알림이 미래에 잡힌다
# (과거 예약은 큐가 걸러낸다 — 그것도 정상 동작이다).
_many = ([mkgame(League.MLB, "NYY", "BOS", day="2026-08-29", hh=h)
          for h in (12, 14, 17, 20, 23)]
         + [mkgame(League.MLB, "LAD", "SF", day="2026-08-29", hh=h)
            for h in (12, 17, 23)])
_q = T.build_all_queues({"MLB": _many}, NOW, "-100test")
_sa = [i for i in _q if i.content_type is ContentType.START_ALERT]
check(f"경기 {len(_many)}건 · 시작 알림 {len(_sa)}건 (하루 한 통)", len(_sa) == 1,
      f"{len(_sa)}건 — 도배")
check("scope에 리그와 날짜가 들어간다",
      bool(_sa) and _sa[0].scope == "MLB:2026-08-29", _sa[0].scope if _sa else "")

# 첫 경기 기준으로 리드타임만큼 앞서 예약되는가
_first = min(g.start_utc for g in _many)
if _sa:
    lead = (_first - _sa[0].scheduled_utc).total_seconds() / 60
    check(f"첫 경기 {START_ALERT_LEAD_MINUTES}분 전 예약", abs(lead - START_ALERT_LEAD_MINUTES) < 1,
          f"{lead:.0f}분")

# 한 통에 그날 경기가 다 들어가야 한다 — 빠지면 '하루 한 통'이 정보 손실이 된다
_txt = P.render_start_alert(_many, _first - timedelta(minutes=START_ALERT_LEAD_MINUTES))
check("한 통에 전 경기가 들어간다", _txt.count("vs") == len(_many),
      f"{_txt.count('vs')}/{len(_many)}")
check("시각별로 묶여 있다", _txt.count("◆") == len({g.start_kst.strftime('%H:%M') for g in _many}))
check("텔레그램 텍스트 상한 안", len(_txt) <= 4096, f"{len(_txt)}자")
check("경기가 많으면 접고펼치기", "<blockquote expandable>" in _txt)
check("첫 경기까지 남은 시간을 적는다", "시작" in _txt)

# 두 리그가 같은 날이면 서로 다른 통이어야 한다
_two = T.build_all_queues(
    {"MLB": _many, "KBO": [mkgame(League.KBO, "LG", "OB", day="2026-08-29", hh=18)]},
    NOW, "-100test")
_sa2 = [i for i in _two if i.content_type is ContentType.START_ALERT]
check("두 리그면 두 통 (서로 안 덮음)", len(_sa2) == 2, f"{len(_sa2)}건")
check("멱등키도 다르다", len({i.idem_key for i in _sa2}) == 2)

# 이미 끝난 경기는 시간표에 안 들어간다
_mixed = _many + [mkgame(League.MLB, "CHC", "STL", day="2026-08-29", hh=10,
                         status=Status.FINAL, score=Score(4, 2, ScoreUnit.RUNS))]
_q3 = T.build_all_queues({"MLB": _mixed}, NOW, "-100test")
_sa3 = [i for i in _q3 if i.content_type is ContentType.START_ALERT]
check("종료된 경기는 시작 알림 대상이 아니다", len(_sa3) == 1)

# ── 10. 폰트 게이트 — 두부 카드를 막는가 ──────────────────────
# 개발 컴퓨터에는 한글 폰트가 있고 **서버(ubuntu-latest)에는 없다.**
# 폰트가 없으면 크로미움은 오류도 경고도 없이 두부(□□□)로 그린다 —
# 숫자 검증은 전부 통과하고, 두부 카드가 채널에 나간 뒤에야 알게 된다.
# ('전 리그 카드가 KBO 색'을 숫자검증 215건이 다 통과시키고 눈으로 보고서야
#  잡은 적이 있다. 같은 계열의 사고를 이번엔 기계가 잡는다.)
print("\n10. 폰트 게이트 — 두부 카드를 막는가")


def _msg(fn) -> str:
    try:
        fn()
        return ""
    except Exception as e:                                   # noqa: BLE001
        return str(e)


def _no_raise(fn) -> bool:
    try:
        fn()
        return True
    except Exception:                                        # noqa: BLE001
        return False


def _raises(exc, fn) -> bool:
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:                                        # noqa: BLE001
        return False


check("한글이 제대로 그려지면 통과",
      _no_raise(lambda: P.assert_korean_font(
          {"family": "Noto Sans CJK KR", "ko": 64.0, "tofu": 40.0})))
check("두부면 막는다 (한글폭 = 두부폭)",
      _raises(P.FontMissing, lambda: P.assert_korean_font(
          {"family": "sans-serif", "ko": 42.0, "tofu": 42.0})))
check("폭이 0이면 막는다 (측정 실패도 통과시키지 않는다)",
      _raises(P.FontMissing, lambda: P.assert_korean_font({"family": "x", "ko": 0})))
check("게이트 오류는 GateError 계열 (호출부가 이미 잡는 종류)",
      issubclass(P.FontMissing, GateError))
check("설치 방법을 오류 문구가 알려준다",
      "fonts-noto-cjk" in _msg(lambda: P.assert_korean_font(
          {"family": "s", "ko": 42.0, "tofu": 42.0})))
# 이 검증이 도는 컴퓨터에는 한글 폰트가 있다 — 실제 렌더로 게이트가
# 정상 카드를 막지 않는지도 확인한다(오탐이면 발행이 통째로 멈춘다).
check("실제 렌더도 폰트 게이트를 통과한다",
      _no_raise(lambda: P.render_png(
          P._card("<div class='t' style='font-size:40px'>한글 확인</div>", League.KBO),
          TMP / "fontcheck.png")))

# ── 11. 발송 경로 전체를 실제로 태운다 ────────────────────────
# **왜 이 검증이 따로 필요한가.**
# 시작 알림은 build_all_queues(큐)와 render_start_alert(문구)를 따로따로
# 검사해 전부 통과했다. 그런데 둘을 잇는 render_for가 NameError로 죽고 있었다 —
# 조건부 import 하나 때문에 시작 알림은 **한 번도 렌더된 적이 없었다.**
# 부품을 아무리 잘 시험해도 조립된 길을 안 걸어보면 이런 것이 남는다.
# 그래서 여기서는 큐에 오르는 모든 종류를 render_for로 끝까지 태운다.
#
# **2026-09-03 — 3종에서 7종으로 넓혔다.** 콘텐츠가 늘었는데 그물이 3종에
# 머물면, 새로 켠 4종(순위·리더보드·나이트 브리핑·분석)은 위 사고와 똑같이
# "큐에는 오르는데 한 번도 렌더된 적이 없는" 상태로 며칠을 갈 수 있다.
# `QUEUED_CONTENT_TYPES`를 기준으로 삼아, 콘텐츠를 또 켜면 이 검사가 **먼저** 깨진다.
print("\n11. 발송 경로 — 큐에 오르는 모든 종류가 실제로 만들어지는가")
from contract import QUEUED_CONTENT_TYPES                             # noqa: E402

_day = "2026-08-29"
_full = {
    # 결과 카드 + 모닝 + 시작 알림이 모두 나오도록: 끝난 경기와 앞으로 할 경기를 섞는다
    "KBO": [mkgame(League.KBO, "LG", "OB", day=_day, hh=14, status=Status.FINAL,
                   score=Score(5, 3, ScoreUnit.RUNS)),
            mkgame(League.KBO, "KT", "SS", day=_day, hh=23)],
    "NPB": [mkgame(League.NPB, "YOG", "HAN", day=_day, hh=23)],
}
# 분석 카드의 '최근 5경기' 블록은 지난 경기가 있어야 그려진다. 블록이 2개 미만이면
# 계약이 "'분석'이라 부를 수 없다"며 막는다 — 그건 옳은 게이트이므로 표본을 준다.
for _pd in ("2026-08-26", "2026-08-27"):
    _full["KBO"].append(mkgame(League.KBO, "KT", "HH", day=_pd, hh=18,
                               status=Status.FINAL, score=Score(4, 2, ScoreUnit.RUNS)))
    _full["KBO"].append(mkgame(League.KBO, "SS", "NC", day=_pd, hh=18,
                               status=Status.FINAL, score=Score(1, 6, ScoreUnit.RUNS)))

_items = T.build_all_queues(_full, NOW, "-100test")
_kinds = {i.content_type for i in _items}
check(f"큐에 7종이 다 오른다 ({len(_items)}건)",
      QUEUED_CONTENT_TYPES <= _kinds,
      f"빠진 것: {sorted(c.value for c in (QUEUED_CONTENT_TYPES - _kinds))}")

# ── 렌더에 먹일 기록(RecordBook) ──────────────────────────────
# 순위·리더보드·분석은 기록이 있어야 만들어진다. 네트워크에 기대면 소스가
# 흔들리는 날 검증이 같이 흔들리므로, **계약 게이트를 그대로 통과하는**
# 스냅샷을 여기서 만든다(상대전적 합계 = 순위표, 부문 값 = 순위 방향).
_RB_CODES = ["LG", "OB", "KT", "SS", "HH", "NC", "LT", "HT", "SK", "WO"]


def _mkrecords(asof):
    from contract import LeaderEntry, RecordBook, Standing, StreakKind, WLD
    h2h = {}
    for _i in range(len(_RB_CODES)):
        for _j in range(_i + 1, len(_RB_CODES)):
            h2h[(_RB_CODES[_i], _RB_CODES[_j])] = WLD(9, 6, 1)
            h2h[(_RB_CODES[_j], _RB_CODES[_i])] = WLD(6, 9, 1)
    st = []
    for _i, _code in enumerate(_RB_CODES):
        rows = [w for (a, _), w in h2h.items() if a == _code]
        rec = WLD(sum(w.win for w in rows), sum(w.loss for w in rows),
                  sum(w.draw for w in rows))
        st.append(Standing(
            league=League.KBO, season="2026", team_code=_code, rank=_i + 1,
            games=rec.total, record=rec,
            pct=f"{rec.win / (rec.win + rec.loss):.3f}",
            games_behind=f"{_i * 3.0:.1f}", last10=WLD(6, 4, 0),
            streak_kind=StreakKind.WIN, streak_len=2,
            home=WLD(rec.win // 2, rec.loss // 2, rec.draw // 2),
            away=WLD(rec.win - rec.win // 2, rec.loss - rec.loss // 2,
                     rec.draw - rec.draw // 2)))
    leaders = {}
    for _cat in ("타율", "홈런", "타점", "평균자책점", "도루"):
        _asc = _cat in C.ASCENDING_CATEGORIES
        leaders[_cat] = [LeaderEntry(
            category=_cat, stat_key=_cat, rank=_k + 1, player_id=f"p{_k}{_cat}",
            name=f"선수{_k + 1}", team_code=_RB_CODES[_k],
            value=(f"{2.00 + _k * 0.1:.3f}" if _asc else f"{0.400 - _k * 0.01:.3f}"))
            for _k in range(5)]
    return RecordBook(league=League.KBO, season="2026", collected_utc=asof,
                      source_url="https://example.test/records",
                      standings=st, h2h=h2h, leaders=leaders)


check("시험용 기록 스냅샷이 계약 게이트를 통과한다 (그래야 렌더 실패가 진짜 결함이다)",
      _no_raise(lambda: C.assert_recordbook(_mkrecords(NOW), now_utc=NOW)))

_allg = [g for v in _full.values() for g in v]
# 기록의 기준 시각은 경기 스냅샷과 맞춰야 한다 — 어긋나면 계약이 AsOfMismatch로
# 막는다(순위표와 최근 경기 도트가 서로 다른 날을 말하는 카드를 만들지 않는다).
_ASOF = max(g.start_utc for g in _allg if g.status is Status.FINAL) + timedelta(hours=1)
_records = {"KBO": _mkrecords(_ASOF)}
_saved_now = T._now
T._now = lambda: _ASOF            # 렌더 안의 '지금'도 같은 기준으로 맞춘다

_made, _empty, _broke = [], [], []
for _it in _items:
    # 나이트 브리핑은 리그가 없는 통합 카드다 — 리그별 스냅샷으로는 못 만든다.
    _gs = (_allg if _it.league is None else
           next((g for g in _full.values() if g and g[0].league is _it.league), []))
    try:
        _r = T.render_for(_it, _gs, records=_records, all_games=_allg)
        (_made if _r else _empty).append(_it.content_type.value)
    except Exception as _e:                                  # noqa: BLE001
        _broke.append(f"{_it.content_type.value}/"
                      f"{_it.league.value if _it.league else 'ALL'}: "
                      f"{type(_e).__name__} {_e}")

check("어떤 종류도 예외로 죽지 않는다", not _broke, " | ".join(_broke[:3]))
check("7종이 모두 실제로 만들어진다",
      {c.value for c in QUEUED_CONTENT_TYPES} <= set(_made),
      f"만들어짐={sorted(set(_made))} 비어서건너뜀={sorted(set(_empty))}")

# 시작 알림은 사진 없이 글만 나간다 — 그 형태까지 확인한다
_sa = next(i for i in _items if i.content_type is ContentType.START_ALERT)
_sr = T.render_for(_sa, _full[_sa.league.value])
check("시작 알림은 사진 없이 글만", _sr is not None and _sr[0] == [] and _sr[1])
check("시작 알림 글에 팀 이름이 들어간다",
      bool(_sr) and any("KT" in p or "SS" in p for p in _sr[1]), str(_sr[1])[:120])
# 새 4종은 카드(사진)로 나간다 — 글만 나가면 디자인이 통째로 빠진 것이다
for _ct in (ContentType.STANDINGS, ContentType.LEADERBOARD,
            ContentType.NIGHT_BRIEF, ContentType.ANALYSIS):
    _one = next((i for i in _items if i.content_type is _ct), None)
    _gs = (_allg if _one is not None and _one.league is None
           else _full.get(_one.league.value, []) if _one is not None else [])
    _rr = None if _one is None else T.render_for(_one, _gs, records=_records,
                                                 all_games=_allg)
    check(f"{_ct.value} 카드가 사진 + 캡션으로 나온다",
          bool(_rr) and len(_rr[0]) == 1 and len(_rr[0][0][1]) > 5000 and _rr[1],
          "None" if not _rr else f"사진 {len(_rr[0])}장")

T._now = _saved_now

# ── 11b. 기록이 없는 리그에는 기록 콘텐츠를 올리지 않는다 ─────
# 순위·리더보드·분석은 RecordBook이 있어야 그려진다. 기록 어댑터가 없는 리그에
# 이것들을 큐에 올리면 render_for가 매 틱 None을 돌려주고, 로그에는
# "만들 내용 없음"이 리그 수 × 종류 수만큼 매 틱 쌓인다 — 진짜 결함이 그 속에 묻힌다.
print("\n11b. 기록 콘텐츠 — 기록이 없는 리그에는 올리지 않는가")
_RECORD_ONLY = {ContentType.STANDINGS, ContentType.LEADERBOARD, ContentType.ANALYSIS}
# v1.11k: NPB 기록 어댑터(npb_records)를 추가해 표가 둘로 늘었다.
# **이 검사는 값을 못 박는 것이 목적이 아니라, 표가 늘 때 아래 검사도 함께
# 넓히도록 강제하는 것이 목적이다.** 실제로 NPB를 넣자 이 검사가 먼저 걸렸다.
check("기록 소스 표가 실제 어댑터와 일치한다",
      P.RECORD_SOURCE_LEAGUES == frozenset({League.KBO, League.NPB}),
      str(sorted(l.value for l in P.RECORD_SOURCE_LEAGUES)))
check("기록 소스에 실제 어댑터가 있다",
      set(T._record_jobs()) == {l.value for l in P.RECORD_SOURCE_LEAGUES},
      f"어댑터 {sorted(T._record_jobs())} vs 표 "
      f"{sorted(l.value for l in P.RECORD_SOURCE_LEAGUES)}")

_norec = {
    "KL1": [mkgame(League.KL1, "K01", "K02", day=_day, hh=19),
            mkgame(League.KL1, "K03", "K04", day=_day, hh=14, status=Status.FINAL,
                   score=Score(2, 1, ScoreUnit.GOALS))],
    "KBL": [mkgame(League.KBL, "SK", "LG", day=_day, hh=19)],
}
_nq = T.build_all_queues(_norec, NOW, "-100test")
_leak = sorted({f"{i.league.value if i.league else 'ALL'}/{i.content_type.value}"
                for i in _nq if i.content_type in _RECORD_ONLY})
check("기록이 없는 리그에는 순위·리더보드·분석이 큐에 오르지 않는다", not _leak,
      str(_leak[:4]))
# 반대 방향도 고정한다 — 위 검사는 '아무것도 안 만들면' 저절로 통과하기 때문이다
check("기록이 있는 리그(KBO)에는 세 가지가 실제로 오른다",
      _RECORD_ONLY <= {i.content_type for i in _items},
      str(sorted(c.value for c in (_RECORD_ONLY - {i.content_type for i in _items}))))
# 기록이 없는 리그도 나이트 브리핑에는 참여한다 (통합 카드라 리그를 안 가린다)
check("기록이 없는 리그도 나이트 브리핑은 만든다 (통합 카드)",
      ContentType.NIGHT_BRIEF in {i.content_type for i in _nq})

# ── 11c. 예약 시각 — 약속한 시각에 잡히는가 ────────────────────
# 카드마다 '언제 나간다'가 약속돼 있다. 여기가 틀어지면 아무 오류 없이
# 엉뚱한 시각에 나가고, 창·유예 계산이 전부 다른 이야기를 하게 된다.
print("\n11c. 예약 시각 — 나이트 23:00 · 리더보드 12:00 · 분석 첫 경기 -3h")
# **약속한 숫자를 여기에 직접 적는다.** 파이프라인의 상수(NIGHT_BRIEF_HOUR_KST 등)로
# 검사하면 상수를 바꾸는 순간 검사도 같이 따라가서 아무것도 못 잡는다 —
# 검증이 코드를 되풀이해 읽을 뿐 약속을 지키는지는 안 보게 된다.
_NB_HOUR, _LB_HOUR, _AN_LEAD_H = 23, 12, 3
check("파이프라인 상수가 약속과 같다 (23시 · 12시 · -3시간)",
      (P.NIGHT_BRIEF_HOUR_KST, P.LEADERBOARD_HOUR_KST, P.ANALYSIS_LEAD_HOURS)
      == (_NB_HOUR, _LB_HOUR, _AN_LEAD_H),
      f"{P.NIGHT_BRIEF_HOUR_KST}/{P.LEADERBOARD_HOUR_KST}/{P.ANALYSIS_LEAD_HOURS}")
_nb_at = [i.scheduled_utc.astimezone(KST) for i in _items
          if i.content_type is ContentType.NIGHT_BRIEF]
check(f"나이트 브리핑은 23:00 KST ({len(_nb_at)}건)",
      bool(_nb_at) and all((t.hour, t.minute) == (_NB_HOUR, 0) for t in _nb_at),
      str([f"{t:%H:%M}" for t in _nb_at[:3]]))
_lb_at = [i.scheduled_utc.astimezone(KST) for i in _items
          if i.content_type is ContentType.LEADERBOARD]
check(f"리더보드는 12:00 KST ({len(_lb_at)}건)",
      bool(_lb_at) and all((t.hour, t.minute) == (_LB_HOUR, 0) for t in _lb_at),
      str([f"{t:%H:%M}" for t in _lb_at[:3]]))
_an = [i for i in _items if i.content_type is ContentType.ANALYSIS]
_an_bad = []
for _it in _an:
    _target = P.pick_analysis_game([g for g in _full[_it.league.value]
                                    if g.sports_day == _it.sports_day])
    if (_target is None
            or _it.scheduled_utc != _target.start_utc
            - timedelta(hours=_AN_LEAD_H)):
        _an_bad.append(f"{_it.sports_day} {_it.scheduled_utc:%H:%M}")
check(f"분석 카드는 그날 주목 경기 시작 -{_AN_LEAD_H}시간 ({len(_an)}건)",
      bool(_an) and not _an_bad, str(_an_bad[:3]))
# 분석은 경기가 시작된 뒤에 나가면 '분석'이 아니라 뒷북이다 — 예약이 늘 경기 앞이다
check("분석 카드 예약은 반드시 경기 시작 전",
      all(i.scheduled_utc < min(g.start_utc for g in _full[i.league.value]
                                if g.sports_day == i.sports_day
                                and g.status is Status.SCHEDULED)
          for i in _an))

# ── 12. 카드가 하는 말과 시스템이 하는 일이 같은가 ────────────
# 카드 아래에 "경기 시작 10분 전 알림"이 문자열로 박혀 있었다. 리드타임을
# 2시간으로 바꾼 뒤에도 카드는 계속 10분이라고 말했다 — **카드가 거짓말을
# 하고 있었고, 숫자 검증 347건이 전부 통과했다.** 눈으로 카드를 보고서야 알았다.
# 안 쓰는 기능("예측 투표는 경기 3시간 전")도 안내하고 있었다.
print("\n12. 카드 문구 — 시스템이 하는 일과 같은 말을 하는가")
from contract import start_alert_lead_text, venue_name                # noqa: E402

_txt = start_alert_lead_text()
check(f"리드타임 문구가 설정을 따라간다 ({_txt})",
      str(C.START_ALERT_LEAD_MINUTES // 60) in _txt
      or str(C.START_ALERT_LEAD_MINUTES) in _txt, _txt)

_mhtml = P.render_morning(
    [mkgame(League.NPB, "YOG", "HAN", day="2026-08-29", hh=18)], "2026-08-29")
check("모닝 카드가 실제 리드타임을 적는다", _txt in _mhtml, _txt)
check("옛 문구('10분 전')가 카드에 남아 있지 않다", "10분 전 알림" not in _mhtml)
check("없는 기능을 안내하지 않는다 (예측 투표)", "예측 투표" not in _mhtml)

# 경기장 이름 — 일본어가 그대로 나가면 한국 시청자는 못 읽는다
check("일본 구장이 한국어로 바뀐다", venue_name("京セラD大阪") == "교세라돔")
check("전각·사이 공백이 섞여도 맞춘다", venue_name("横 浜") == "요코하마")
# 이 검사가 한 번 깨졌었다 — 예시로 쓰던 'Yankee Stadium'이 표에 등록되면서다.
# 표에 정말 없는 이름으로 확인한다(중립 개최·신설 구장이 이렇게 들어온다).
check("모르는 구장은 원문을 그대로 (빈칸으로 만들지 않는다)",
      venue_name("Some New Ballpark") == "Some New Ballpark")
check("MLB 구장도 한글로 바뀐다", venue_name("Yankee Stadium") == "양키 스타디움")
check("잘려서 안 읽히던 긴 구장명도 한글로",
      venue_name("American Family Field") == "아메리칸 패밀리 필드")
check("경기장이 없으면 빈 문자열", venue_name(None) == "")

# ── 13. 홈/원정 게이트 ────────────────────────────────────────
# NPB에서 실제로 287경기 중 281경기가 뒤집혀 있었다. 팀 이름 두 개가 자리만
# 바꾼 것이라 다른 검증은 전부 통과했고, **점수까지 함께 뒤집혀** 결과 카드가
# 승패를 반대로 내보낼 뻔했다. 기계가 볼 수 있는 근거는 경기장뿐이다.
print("\n13. 홈/원정 — 뒤집힘을 경기장으로 잡는가")
import dataclasses as _dc                                             # noqa: E402
from contract import (HOME_VENUES, assert_home_away,                  # noqa: E402
                      home_venue_mismatches)

_ok_games = [mkgame(League.KBO, h, a, day="2026-08-29", hh=14 + i)
             for i, (h, a) in enumerate([("LG", "OB"), ("KT", "SS"),
                                         ("HT", "LT"), ("WO", "NC"),
                                         ("SS", "KT"), ("LT", "HT")])]
for g in _ok_games:                       # 홈팀의 홈구장을 넣어준다
    g.venue = sorted(HOME_VENUES[League.KBO][g.home.team_code])[0]
check("정상이면 어긋남 0", not home_venue_mismatches(_ok_games))
check("정상이면 게이트 통과", _no_raise(lambda: assert_home_away(_ok_games)))

# 홈과 원정을 통째로 바꾼다 — 경기장은 그대로 두어 '뒤집힘'을 흉내낸다
_flipped = []
for g in _ok_games:
    f = _dc.replace(g, home=g.away, away=g.home)
    f.venue = g.venue
    _flipped.append(f)
check("통째로 뒤집으면 어긋남이 대량 잡힌다",
      len(home_venue_mismatches(_flipped)) >= 5,
      str(len(home_venue_mismatches(_flipped))))
check("통째로 뒤집으면 게이트가 막는다",
      _raises(GateError, lambda: assert_home_away(_flipped)))
check("오류 문구가 근거(경기장·어느 팀 홈)를 밝힌다",
      "홈구장" in _msg(lambda: assert_home_away(_flipped)))

# 한두 건은 대체 개최일 수 있다 — 막지 않는다(오탐이면 발행이 통째로 멈춘다).
_one_off = list(_ok_games)
_one_off[0] = _dc.replace(_ok_games[0], home=_ok_games[0].away,
                          away=_ok_games[0].home)
_one_off[0].venue = _ok_games[0].venue
check("한 건 어긋남은 통과 (8월 고시엔처럼 대체 개최가 있다)",
      _no_raise(lambda: assert_home_away(_one_off)))

# 표본이 적으면 판정하지 않는다
check("경기가 3건 이하면 판정하지 않는다",
      _no_raise(lambda: assert_home_away(_flipped[:3])))

# 표에 없는 리그·구장은 통과 (모르는 것으로 막지 않는다)
_lck = [mkgame(League.LCK, "T1", "GEN", day="2026-08-29", hh=17 + i) for i in range(5)]
check("홈구장 표가 없는 리그는 통과 (LCK)",
      _no_raise(lambda: assert_home_away(_lck)))

check("주요 리그에 홈구장 표가 있다 (KBO·NPB·MLB·K리그·V리그)",
      {League.KBO, League.NPB, League.MLB, League.KL1,
       League.VLEAGUE_M, League.VLEAGUE_W} <= set(HOME_VENUES),
      str(sorted(l.value for l in HOME_VENUES)))

# ── 14. 뜸한 시계에서도 발행이 살아남는가 ─────────────────────
# **이번 사고 그 자체를 재현한다.**
# 깃허브에 5분(*/5)을 걸었는데 실측 간격은 약 100분이었다. 처리 창이 6분이라
# 예약 시각이 그 창에 안 들어왔고, 모닝 브리핑 4건과 시작 알림 4건이 하루 종일
# 한 건도 못 나갔다. 오류는 없었다 — 로그는 "큐 14 · 지금 처리 0"으로 평온했다.
# 그래서 여기서는 100분 간격 시계를 실제로 돌려보고 전부 걸리는지 확인한다.
print("\n14. 뜸한 시계 — 100분마다 깨어나도 발행이 살아남는가")
from contract import (assert_send_windows, lookahead_for,                # noqa: E402
                      send_window_seconds, QUEUED_CONTENT_TYPES)

check("시계 간격 상수가 실측값을 담는다 (설정값이 아니라)",
      T.TICK_INTERVAL_SECONDS >= 60 * 60, f"{T.TICK_INTERVAL_SECONDS}초")
check("현재 설정이 게이트를 통과한다",
      _no_raise(lambda: assert_send_windows(T.TICK_INTERVAL_SECONDS,
                                            T.LOOKAHEAD_SECONDS)))
check("모닝 브리핑은 일찍 나가지 않는다 (앞창 0)",
      lookahead_for(ContentType.MORNING, 90 * 60) == 0,
      str(lookahead_for(ContentType.MORNING, 90 * 60)))
# v1.11i: 앞창 값을 상수로 못 박던 검사였다(2시간). 전수조사에서 실측 최악
# 시계 간격이 240분인데 시작 알림 창이 235분이라 1.67%가 조용히 사라지는 것을
# 발견해 앞창을 2.5시간으로 넓혔다. **지켜야 할 것은 특정 숫자가 아니라**
# ① 일찍부터 잡는다 ② 창이 실측 최악 간격을 덮는다 — 두 가지다.
check("시작 알림은 일찍부터 잡는다 (문구가 실시간이라 안전)",
      lookahead_for(ContentType.START_ALERT, 6 * 60) >= 2 * 3600,
      str(lookahead_for(ContentType.START_ALERT, 6 * 60)))
_WORST_OBSERVED_TICK_SECONDS = 240 * 60      # 실측 최악(깃허브 자동 시계)
check("시작 알림 창이 실측 최악 간격을 덮는다",
      send_window_seconds(ContentType.START_ALERT,
                          T.LOOKAHEAD_SECONDS) >= _WORST_OBSERVED_TICK_SECONDS,
      f"{send_window_seconds(ContentType.START_ALERT, T.LOOKAHEAD_SECONDS) // 60}분")
check("모닝 브리핑 창도 실측 최악 간격을 덮는다 (전 리그가 같은 창이라 함께 사라진다)",
      send_window_seconds(ContentType.MORNING,
                          T.LOOKAHEAD_SECONDS) >= _WORST_OBSERVED_TICK_SECONDS,
      f"{send_window_seconds(ContentType.MORNING, T.LOOKAHEAD_SECONDS) // 60}분")
# 2026-09-03 — 새로 켠 4종에도 같은 잣대를 댄다.
# 이 잣대가 없던 동안, 아직 안 켠 콘텐츠들이 240분 시계에서 25~62% 조용히
# 사라진다는 사실이 어디에도 안 나타났다(그래서 v1.11k에서 유예·앞창을 넓혔다).
# 켠 다음에 다시 좁아지면 로그는 평온한데 카드만 사라진다 — 여기서 못 박는다.
for _ct in (ContentType.STANDINGS, ContentType.LEADERBOARD,
            ContentType.NIGHT_BRIEF, ContentType.ANALYSIS):
    _w = send_window_seconds(_ct, T.LOOKAHEAD_SECONDS)
    check(f"{_ct.value} 창이 실측 최악 간격(240분)을 덮는다 ({_w // 60}분)",
          _w >= _WORST_OBSERVED_TICK_SECONDS, f"{_w // 60}분")
# 큐에 오르는 것 전체로도 한 번 — 종류가 또 늘면 위 목록보다 이쪽이 먼저 깨진다
_narrow = [f"{ct.value} {send_window_seconds(ct, T.LOOKAHEAD_SECONDS) // 60}분"
           for ct in QUEUED_CONTENT_TYPES
           if send_window_seconds(ct, T.LOOKAHEAD_SECONDS)
           < _WORST_OBSERVED_TICK_SECONDS]
check("큐에 오르는 7종 전부가 실측 최악 간격을 덮는다", not _narrow, str(_narrow))
# **결과 카드를 일찍 보내면 경기가 빠진다.** 예약 시각은 '마감'이고, 렌더는
# "한 경기라도 끝났으면" 카드를 만든다. 앞창을 열면 5경기 중 1경기만 끝난
# 시점에 카드가 나가고 나머지는 영영 빠진다(멱등키가 재발송을 막으므로).
check("결과 카드는 마감보다 일찍 나가지 않는다 (앞창 0)",
      lookahead_for(ContentType.LEAGUE_RESULT, 90 * 60) == 0,
      str(lookahead_for(ContentType.LEAGUE_RESULT, 90 * 60)))
check("순위 카드도 마찬가지",
      lookahead_for(ContentType.STANDINGS, 90 * 60) == 0)
# 기본 앞창을 아무리 넓혀도 잠긴 것은 열리지 않아야 한다
check("기본 앞창을 크게 줘도 잠금이 이긴다",
      all(lookahead_for(ct, 999 * 60) == 0
          for ct in (ContentType.MORNING, ContentType.LEAGUE_RESULT,
                     ContentType.STANDINGS, ContentType.NIGHT_BRIEF)))

# 사고 당시 설정을 되돌려 게이트가 그것을 잡는지 본다
_saved = C.GRACE_SECONDS[ContentType.MORNING]
C.GRACE_SECONDS[ContentType.MORNING] = 3600            # 사고 당시 값
check("사고 당시 설정(모닝 유예 1시간 · 100분 시계)을 게이트가 막는다",
      _raises(GateError, lambda: assert_send_windows(100 * 60, 6 * 60)))
check("막는 이유를 문구가 설명한다 (조용히 사라진다)",
      "사라집니다" in _msg(lambda: assert_send_windows(100 * 60, 6 * 60)))
C.GRACE_SECONDS[ContentType.MORNING] = _saved

# **실제 시뮬레이션** — 100분마다 깨어나는 시계로 하루를 돌려본다.
# 예약된 항목이 어느 틱에서든 한 번은 처리 대상(due)에 들어와야 한다.
_sim_day = "2026-08-31"
_sim = {"KBO": [mkgame(League.KBO, "LG", "OB", day=_sim_day, hh=18),
                mkgame(League.KBO, "KT", "SS", day=_sim_day, hh=18)]}
_base = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)   # KST 05:00
_seen: set = set()
for _step in range(20):                                     # 100분 x 20 = 33시간
    _t = _base + timedelta(minutes=100 * _step)
    for _it in T.build_all_queues(_sim, _t, "-100test"):
        _win = lookahead_for(_it.content_type, T.LOOKAHEAD_SECONDS)
        if (_it.scheduled_utc <= _t + timedelta(seconds=_win)
                and not is_late(_it.scheduled_utc, _t, _it.content_type)):
            _seen.add(_it.content_type)

check(f"모닝 브리핑이 100분 시계에 걸린다", ContentType.MORNING in _seen,
      str(sorted(c.value for c in _seen)))
check("시작 알림이 100분 시계에 걸린다", ContentType.START_ALERT in _seen,
      str(sorted(c.value for c in _seen)))
check("결과 카드가 100분 시계에 걸린다", ContentType.LEAGUE_RESULT in _seen,
      str(sorted(c.value for c in _seen)))
check("큐에 오르는 모든 종류가 빠짐없이 걸린다",
      QUEUED_CONTENT_TYPES <= _seen,
      f"놓친 것: {sorted(c.value for c in (QUEUED_CONTENT_TYPES - _seen))}")

# ── 14b. 정확도를 위해 한 틱 미루기 (2026-09-03 신설) ─────────
# 새 4종을 켜면서 앞창을 크게 열었다(분석 6시간). 앞창만 넓히면 카드가
# 목표 시각보다 몇 시간 일찍 나가므로, `defer_for_precision`이 발송 순간에
# "목표에 더 가까운 틱이 곧 온다면 이번엔 보내지 않는다"를 판정해 정확도를 되찾는다.
#
# **이 규칙은 두 방향 모두 틀리면 위험하다.**
#   · 너무 잘 미루면 → 뜸한 시계에서 영영 안 나간다(유실). 그래서 '시계를 모르면
#     안 미룬다', '목표를 지났으면 안 미룬다'가 반드시 성립해야 한다.
#   · 전혀 안 미루면 → 앞창을 넓힌 대가만 치르고 정확도는 못 얻는다.
print("\n14b. 정확도 미루기 — 시계가 좋아지면 저절로 정확해지는가")
from contract import defer_for_precision                             # noqa: E402

_D_NOW = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)
_D_AT = _D_NOW + timedelta(minutes=60)          # 목표까지 60분 남음
check("시계가 촘촘하면 미룬다 (5분 시계 · 목표 60분 뒤)",
      defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS, 5 * 60, 10 * 60))
check("시계가 뜸하면 안 미룬다 (240분 시계 — 미루면 그대로 유실이다)",
      not defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS, 240 * 60, 240 * 60))
check("목표를 이미 지났으면 안 미룬다 (더 미루면 늦어질 뿐이다)",
      not defer_for_precision(_D_NOW - timedelta(minutes=1), _D_NOW,
                              ContentType.ANALYSIS, 5 * 60, 10 * 60))
check("시계를 모르면(0) 안 미룬다 — 모를 때는 보내는 쪽이 안전하다",
      not defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS, 0, 0))
check("리더보드도 미루기 대상 (앞창이 열려 있는 콘텐츠)",
      defer_for_precision(_D_AT, _D_NOW, ContentType.LEADERBOARD, 5 * 60, 10 * 60))
# 앞창이 0인 콘텐츠는 애초에 일찍 나가지 않으므로 미룰 것도 없다.
# 여기서 미루면 유예만 깎아먹는다.
check("앞창이 잠긴 콘텐츠(모닝·결과·순위·나이트)는 미루지 않는다",
      not any(defer_for_precision(_D_AT, _D_NOW, ct, 5 * 60, 10 * 60)
              for ct in (ContentType.MORNING, ContentType.LEAGUE_RESULT,
                         ContentType.STANDINGS, ContentType.NIGHT_BRIEF)))

# ── Codex 검수(2026-09-04)가 잡은 결함 — 미루기가 마감을 봐야 한다 ────────
# 처음 구현은 "다음 틱이 곧 온다"는 예상만 보고 미뤘다. 그런데 연속 운전이 끝나는
# 순간이나 러너 배정이 밀리는 순간에 미루면, 다음 틱이 유예를 넘겨 도착해
# **원래 보낼 수 있던 발행이 사라진다.** 정확도를 얻으려다 발행을 잃는 것은
# 우리가 고치려던 바로 그 병이다. 그래서 "최악의 간격으로 와도 마감 전"일 때만 미룬다.
check("최악 간격이 마감을 넘기면 미루지 않는다 (Codex 검수)",
      not defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS,
                              5 * 60, 10 * 3600),
      "예상은 5분이어도 최악이 10시간이면 미루면 안 된다")
check("최악 간격을 모르면 미루지 않는다",
      not defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS, 5 * 60, 0))
check("최악 간격이 넉넉하면 미룬다",
      defer_for_precision(_D_AT, _D_NOW, ContentType.ANALYSIS, 5 * 60, 10 * 60))
# **연속 운전이 끝나는 순간**이 가장 위험하다 — Codex가 지목한 구간이다.
# 그동안 5분 간격만 봐 왔으니 예상(중앙값)은 5분인데, 실제로는 크론이 다시 걸릴 때까지
# 몇 시간이 빈다. 최악 간격에 그 공백이 한 번이라도 잡혀 있으면 미루지 않아야 한다.
_D_FAR = _D_NOW + timedelta(minutes=200)      # 목표가 멀다 = 미룰 값어치는 있다
check("연속 운전 종료 직후 구간에서는 미루지 않는다 (Codex 검수)",
      not defer_for_precision(_D_FAR, _D_NOW, ContentType.ANALYSIS,
                              5 * 60, 5 * 3600),
      "예상 5분·최악 5시간 — 마감(유예 3h) 안에 못 들어온다")
check("같은 상황에서 최악이 작으면 미룬다",
      defer_for_precision(_D_FAR, _D_NOW, ContentType.ANALYSIS, 5 * 60, 20 * 60))

# ── 미루기의 "다음 틱 보장"은 통계가 아니라 사실이어야 한다 ──────────
# Codex 검수 2026-09-04: "최근 관측 최대값은 미래 공백의 상한이 아니다."
# 그래서 연속 운전이 알려주는 결정론적 값만 쓴다.
_env_bak = {k: os.environ.get(k) for k in ("TICK_LOOPS_LEFT", "TICK_LOOP_INTERVAL_SECONDS")}
for _k in _env_bak:
    os.environ.pop(_k, None)
check("단발 실행에서는 미루지 않는다 (다음 틱 보장 없음)",
      T._next_tick_estimate(NOW) == (0, 0), str(T._next_tick_estimate(NOW)))
os.environ["TICK_LOOPS_LEFT"] = "0"
check("연속 운전 마지막 회차에서는 미루지 않는다",
      T._next_tick_estimate(NOW) == (0, 0), str(T._next_tick_estimate(NOW)))
os.environ["TICK_LOOPS_LEFT"] = "59"
os.environ["TICK_LOOP_INTERVAL_SECONDS"] = "300"
_nt, _wt = T._next_tick_estimate(NOW)
check("연속 운전 중에는 결정론적 값을 쓴다", _nt == 300 and _wt >= 300, f"{_nt},{_wt}")
check("최악 가정이 예상보다 넉넉하다", _wt > _nt, f"{_nt},{_wt}")
os.environ["TICK_LOOPS_LEFT"] = "abc"
check("값이 이상하면 미루지 않는다", T._next_tick_estimate(NOW) == (0, 0))
for _k, _v in _env_bak.items():
    os.environ.pop(_k, None)
    if _v is not None:
        os.environ[_k] = _v
# 미루기 대상은 전부 앞창이 열려 있어야 한다 — 이 관계가 깨지면
# '일찍 나가지도 않는데 미루기까지 하는' 콘텐츠가 생겨 조용히 사라진다.
check("미루기 대상은 전부 앞창이 열려 있다",
      all(lookahead_for(ct, T.LOOKAHEAD_SECONDS) > 0
          for ct in C.DEFER_FOR_PRECISION),
      str(sorted(ct.value for ct in C.DEFER_FOR_PRECISION
                 if lookahead_for(ct, T.LOOKAHEAD_SECONDS) == 0)))

# ── 15. 결과 카드는 마지막 경기가 끝나면 곧바로 ────────────────
# 대표님 지시: "같은 리그 마지막 경기가 종료되고 1시간 이내에 발송".
# 마감(deadline)은 "이때까지는 소스가 결과를 채웠을 것"이라는 상한일 뿐,
# 보내야 할 시각이 아니다. 일찍 끝난 날 그 시각까지 기다리면 몇 시간씩 늦는다.
print("\n15. 결과 카드 — 마지막 경기가 끝나면 곧바로 나가는가")

_RD = "2026-08-29"


def _kbo(h, a, hh, st=Status.SCHEDULED, cancel=None):
    return mkgame(League.KBO, h, a, day=_RD, hh=hh, status=st,
                  score=(Score(5, 3, ScoreUnit.RUNS) if st is Status.FINAL else None),
                  cancel=cancel)


def _result_item(games):
    q = [i for i in P.build_queue(games, NOW, "-100test", floor_hours=0)
         if i.content_type is ContentType.LEAGUE_RESULT]
    return q[0] if q else None


# 전부 종결 → 지금 보낸다
_all_done = [_kbo("LG", "OB", 14, Status.FINAL), _kbo("KT", "SS", 15, Status.FINAL)]
_it = _result_item(_all_done)
check("전부 끝난 날은 예약이 '지금'이다", _it is not None
      and abs((_it.scheduled_utc - NOW).total_seconds()) < 60,
      str(_it.scheduled_utc if _it else None))

# 한 경기가 진행 중 → 마감까지 기다린다
_live = [_kbo("LG", "OB", 14, Status.FINAL), _kbo("KT", "SS", 18, Status.LIVE)]
_it2 = _result_item(_live)
check("아직 진행 중이면 마감까지 기다린다 (일찍 보내면 경기가 빠진다)",
      _it2 is not None and (_it2.scheduled_utc - NOW).total_seconds() > 3600,
      str((_it2.scheduled_utc - NOW).total_seconds() / 60 if _it2 else None))

# 예정만 남았어도 기다린다
_sched = [_kbo("LG", "OB", 18), _kbo("KT", "SS", 18)]
_it3 = _result_item(_sched)
check("예정만 있으면 마감까지 기다린다",
      _it3 is not None and (_it3.scheduled_utc - NOW).total_seconds() > 3600)

# 취소도 '종결'이다 — 열리지 않은 경기를 기다릴 이유가 없다
_mixed = [_kbo("LG", "OB", 14, Status.FINAL),
          _kbo("KT", "SS", 15, Status.CANCELED, cancel="우천취소")]
_it4 = _result_item(_mixed)
check("취소 경기는 기다리지 않는다 (취소도 종결)",
      _it4 is not None and abs((_it4.scheduled_utc - NOW).total_seconds()) < 60)

# 시각이 바뀌어도 중복 방지 키는 그대로여야 한다
check("예약 시각이 바뀌어도 멱등키는 같다 (중복 발송 없음)",
      _it.idem_key == _it2.idem_key == _it3.idem_key,
      f"{_it.idem_key} / {_it2.idem_key}")

# **전부 취소된 날도 알린다 (v1.11h에서 바뀜).**
# 전에는 FINAL이 0건이면 카드를 안 만들었다. 그래서 여름 KBO 우천·폭염
# 종일 취소(실측 5일)에 구독자는 취소 사실을 채널에서 못 봤다 —
# 모닝은 07:30에 나가고 취소는 그 뒤에 발표되기 때문이다.
# 이제 "전 경기 취소" 카드를 낸다.
_off = [_kbo("LG", "OB", 14, Status.CANCELED, cancel="우천취소"),
        _kbo("KT", "SS", 15, Status.CANCELED, cancel="우천취소")]
_it5 = _result_item(_off)
check("전부 취소된 날도 결과 카드를 만든다", _it5 is not None)
_r5 = T.render_for(_it5, _off) if _it5 else None
check("그 카드는 '전 경기 취소'라고 말한다",
      bool(_r5) and "전 경기 취소" in "".join(_r5[1]),
      str(_r5[1])[:120] if _r5 else "None")
check("취소 사유가 실린다", bool(_r5) and "우천취소" in "".join(_r5[1]))

# 아무것도 끝나지 않은 날(전부 예정·진행 중)은 여전히 카드를 안 만든다
_pending = [_kbo("LG", "OB", 14, Status.SCHEDULED),
            _kbo("KT", "SS", 15, Status.SCHEDULED)]
_it6 = _result_item(_pending)
check("아무것도 종결 안 됐으면 결과 카드를 안 만든다",
      _it6 is None or T.render_for(_it6, _pending) is None)

# 마지막 경기 종료를 얼마나 빨리 알아차리는가 — 대표님 기준은 1시간
_lag = (T.FETCH_EVERY_LIVE_SECONDS + 5 * 60) / 60      # 수집 주기 + 5분 시계
check(f"연속 운전 시 종료 인지~발송이 1시간 안 ({_lag:.0f}분)", _lag <= 60,
      f"{_lag}분")

# ── 16. 팀명 — 시청자가 쓰는 이름인가 ─────────────────────────
# 대표님이 네이버 스포츠 화면을 붙여주고서야 알았다: MLB 국내 표준은 애칭
# (양키스·레드삭스)이 아니라 **연고지**(보스턴·디트로이트)다. 게다가 내 표에는
# '화삭스'·'D-백스' 같은 커뮤니티 축약어까지 섞여 있었다.
# 카드는 시청자가 읽는 것이므로 내가 아는 이름이 아니라 시청자가 쓰는 이름을 쓴다.
print("\n16. 팀명 — 표기와 커버리지")
from contract import (TEAM_NAMES, assert_team_names_cover,                # noqa: E402
                      unknown_team_codes)

_mlb = TEAM_NAMES[League.MLB]
check("MLB는 연고지 기준 (네이버 스포츠 표기)",
      _mlb["BOS"] == "보스턴" and _mlb["DET"] == "디트로이트"
      and _mlb["STL"] == "세인트루이스", str(_mlb.get("BOS")))
check("같은 도시 두 팀만 구분자를 붙인다",
      _mlb["NYY"] == "뉴욕양키스" and _mlb["NYM"] == "뉴욕메츠"
      and _mlb["CHC"] == "시카고컵스" and _mlb["CWS"] == "화이트삭스"
      and _mlb["LAD"] == "LA다저스" and _mlb["LAA"] == "LA에인절스")
check("커뮤니티 축약어를 쓰지 않는다",
      not ({"화삭스", "D-백스", "파이리츠"} & set(_mlb.values())),
      str(sorted(set(_mlb.values()) & {"화삭스", "D-백스", "파이리츠"})))
check("MLB 30팀이 전부 등록", len(_mlb) == 30, str(len(_mlb)))

# 다른 리그는 이미 국내 표기와 같다 — 바꾸지 않았음을 고정한다
check("KBO는 구단 통칭 그대로",
      TEAM_NAMES[League.KBO]["OB"] == "두산"
      and TEAM_NAMES[League.KBO]["HT"] == "KIA")
check("K리그1은 연고 도시", TEAM_NAMES[League.KL1]["K09"] == "서울")
check("V리그는 기업명", TEAM_NAMES[League.VLEAGUE_M]["KAL"] == "대한항공")
check("인수로 바뀐 팀도 반영 (페퍼저축은행 -> SOOP)",
      TEAM_NAMES[League.VLEAGUE_W]["SOOP"] == "SOOP"
      and "PEPPER" in TEAM_NAMES[League.VLEAGUE_W],
      "옛 코드도 남겨야 과거 경기에 코드가 안 찍힌다")

# **커버리지 게이트** — 표에 없는 코드는 카드에 코드가 그대로 찍힌다
_known = [mkgame(League.KBO, "LG", "OB", day="2026-08-29", hh=18)]
check("표에 있는 팀만 있으면 통과", _no_raise(lambda: assert_team_names_cover(_known)))

_ghost = mkgame(League.KBO, "LG", "OB", day="2026-08-29", hh=19)
_ghost.away = C.TeamRef(League.KBO, "ZZZ")           # 소스가 새 코드를 보냈다
check("표에 없는 코드를 잡는다", _raises(GateError,
      lambda: assert_team_names_cover([_ghost])))
check("어느 리그의 어떤 코드인지 알려준다",
      "KBO:ZZZ" in _msg(lambda: assert_team_names_cover([_ghost])),
      _msg(lambda: assert_team_names_cover([_ghost]))[:90])
check("팀이 바뀌었을 수 있다고 안내한다",
      "바뀌었을" in _msg(lambda: assert_team_names_cover([_ghost])))
check("unknown_team_codes가 목록을 돌려준다",
      unknown_team_codes([_ghost]) == [(League.KBO, "ZZZ")])

# 표가 아예 없는 리그는 판정하지 않는다 (유럽 축구는 아직 표가 없다).
# 표가 없는 것과 '표에 없는 코드'는 다르다 — 전자는 통과, 후자는 차단이다.
_eu = mkgame(League.EPL, "AAA", "BBB", day="2026-08-29", hh=20)
check("표가 아예 없는 리그는 통과 (유럽 축구)",
      _no_raise(lambda: assert_team_names_cover([_eu])))
check("표가 있는 리그의 모르는 코드는 차단 (국제 LoL)",
      _raises(GateError, lambda: assert_team_names_cover(
          [mkgame(League.INTL_LOL, "AAA", "BBB", day="2026-08-29", hh=20)])))

# ── 17. 긴 팀명이 카드에서 접히지 않는가 ──────────────────────
# 국내 표기로 바꾸자 '세인트루이스'(6자)·'샌프란시스코'(7자)가 결과 카드에서
# 두 줄로 접혔다. 글자 수 상한(8자)은 통과했다 — 상한은 글자 수를 세지 실제로
# 그려진 폭을 보지 않기 때문이다. 카드를 눈으로 보고서야 알았다.
print("\n17. 긴 팀명 — 카드에서 두 줄로 접히지 않는가")
check("6자 팀명에 축소 클래스가 붙는다", P._name_cls("세인트루이스") == " n6")
check("6자 팀명은 n6 (MLB 최장이 6자: 샌프란시스코·세인트루이스)",
      P._name_cls("샌프란시스코") == " n6" and len("샌프란시스코") == 6)
check("7자 팀명에 더 작은 클래스 (IBK기업은행·디플러스 기아)",
      P._name_cls("IBK기업은행") == " n7" and P._name_cls("디플러스 기아") == " n7")
check("짧은 이름은 그대로", P._name_cls("보스턴") == "")
check("접힘 감지가 한 줄은 통과",
      _no_raise(lambda: P.assert_no_wrapped_names(
          [{"t": "세인트루이스", "cls": "n1 n6", "lines": 1, "fs": 39}])))
check("접힘 감지가 두 줄을 막는다",
      _raises(P.NameWrapped, lambda: P.assert_no_wrapped_names(
          [{"t": "세인트루이스", "cls": "n1", "lines": 2, "fs": 45}])))
check("무엇이 접혔는지 알려준다",
      "세인트루이스" in _msg(lambda: P.assert_no_wrapped_names(
          [{"t": "세인트루이스", "cls": "n1", "lines": 2, "fs": 45}])))
check("한 줄이 아니어도 되는 칸은 검사하지 않는다 (캡션 등)",
      _no_raise(lambda: P.assert_no_wrapped_names(
          [{"t": "긴 안내 문구", "cls": "sub", "lines": 3, "fs": 30}])))

# 실제로 가장 긴 이름들로 결과 카드를 그려 게이트까지 통과하는지 본다
_long = [mkgame(League.MLB, "SF", "ARI", day="2026-08-29", hh=14,
                status=Status.FINAL, score=Score(7, 1, ScoreUnit.RUNS)),
         mkgame(League.MLB, "STL", "PIT", day="2026-08-29", hh=15,
                status=Status.FINAL, score=Score(2, 6, ScoreUnit.RUNS))]
check("가장 긴 이름으로 실제 렌더해도 통과",
      _no_raise(lambda: P.render_png(
          P.render_result(_long, "2026-08-29"), TMP / "longnames.png")))

print(f"\n결과: {ok} PASS / {fail} FAIL")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
