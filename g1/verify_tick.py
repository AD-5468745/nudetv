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

# ⚠️ **나이트 브리핑은 2026-09-07에 껐다.** 리그별로 나누라는 지시를 받고
# 나눠 봤더니 리그 결과 정리판과 같은 자리에 섰다 — 같은 리그, 같은 날,
# 같은 경기 목록. 대표님이 그린 흐름에도 리그 단위 카드는 정리판 하나다.
# 그래서 여기서 보는 것은 "**정말 꺼졌는가**"다. 반쯤 꺼진 것이 제일 나쁘다:
# 큐에는 없는데 게이트는 찾으면 매일 유령 경고가 뜬다(약점 112·113).
# ⚠️ **콘텐츠 하나씩 검사하면 다음에 또 빠뜨린다.**
# 실제로 그랬다(2026-09-07): 나이트는 큐에서 빼면서 검사도 넣었는데,
# 같은 날 시작 알림을 끄면서는 **계약에서만 빼고 큐 생성부를 안 고쳐** 계속
# 만들어지고 있었다. 나이트 전용 검사는 그것을 볼 이유가 없었다.
# 그래서 **끈 콘텐츠 전부**를 한 번에 본다 — 목록이 늘어도 검사는 그대로다.
_all_items = T.build_all_queues(snaps, NOW, "-100test")
_leaked = sorted({i.content_type.value for i in _all_items
                  if i.content_type in C.DISABLED_CONTENT_TYPES})
check(f"★★ 끈 콘텐츠는 큐에 하나도 안 오른다 "
      f"({len(C.DISABLED_CONTENT_TYPES)}종: "
      f"{', '.join(sorted(c.value for c in C.DISABLED_CONTENT_TYPES))})",
      not _leaked, f"새어 나온 것: {_leaked}")
check("  ↳ 계약의 두 목록이 서로 어긋나지 않는다 (큐 목록 ∩ 끈 목록 = 0)",
      not (C.QUEUED_CONTENT_TYPES & C.DISABLED_CONTENT_TYPES),
      str(sorted(c.value for c in
                 (C.QUEUED_CONTENT_TYPES & C.DISABLED_CONTENT_TYPES))))
# 변이시험 — 끈 목록에서 하나를 빼면 이 검사가 정말 잡는가.
_saved_dis = C.DISABLED_CONTENT_TYPES
try:
    C.DISABLED_CONTENT_TYPES = frozenset({ContentType.MORNING})   # 실제로 나가는 것
    _mut = sorted({i.content_type.value for i in _all_items
                   if i.content_type in C.DISABLED_CONTENT_TYPES})
    check("  ↳ 변이시험 — 나가고 있는 콘텐츠를 '껐다'고 하면 잡는다", bool(_mut))
finally:
    C.DISABLED_CONTENT_TYPES = _saved_dis
check("  ↳ 변이시험 뒤 목록이 원래대로 돌아왔다",
      C.DISABLED_CONTENT_TYPES == _saved_dis)
import render_v5 as _R5NB                                    # noqa: E402
check("  ↳ 되돌리는 길이 한 줄로 남아 있다 (렌더·계약 코드는 지우지 않았다)",
      hasattr(_R5NB, "night_card") and ContentType.NIGHT_BRIEF in C.GRACE_SECONDS)
# 나머지도 같은 기대다 — 리그끼리 절대 안 겹친다.
_clash = [(a, b, sorted(_keys_own[a] & _keys_own[b])[:2])
          for a in _keys_own for b in _keys_own
          if a < b and (_keys_own[a] & _keys_own[b])]
check("나이트 브리핑을 뺀 키도 리그 간 충돌 0", not _clash, str(_clash[:2]))

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
        # **v1.12: 조건이 바뀌었다.** 인용블록의 목적은 긴 텍스트를 접는 것인데,
        # v5 카드는 **긴 텍스트 자체를 없앴다** — 카드에 다 들어가면 본문을 안 붙인다
        # (대표님 지적: "굳이 텍스트를 넣지 않아도 되는 카드들도 있는 것 같아").
        # 그래서 검사도 조건부로 바꾼다: **덧붙일 내용이 있을 때만** 인용블록을 쓴다.
        # 조건을 지우지 않고 좁힌다 — 지우면 긴 텍스트가 안 접혀도 아무도 모른다.
        _cap = parts[0]
        _has_extra = "\n\n" in _cap or len(_cap.splitlines()) > 2
        check("덧붙인 내용이 있으면 인용블록을 쓴다",
              (not _has_extra) or "<blockquote" in _cap, _cap[:80])
        check("캡션 첫 줄이 무엇/언제를 말한다",
              _cap.startswith(("✅", "📋", "⏰", "📊", "🏅", "⚖️", "🌙"))
              and "<b>" in _cap.splitlines()[0], _cap[:60])
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
import sender as S                                              # noqa: E402
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
# v1.11k: **레이트리밋은 백오프가 다르다.** 실측에서 LCK가 104시간(4.3일)
# 동안 리밋에서 못 벗어났고, 원인은 15분마다 계속 두드린 것이었다.
# 시간당 쿼터를 쓰는 상대에게 그건 회복할 틈을 주지 않는다.
# 지켜야 할 것은 "15분"이라는 숫자가 아니라 **막힌 상대를 쉬게 둔다**이다.
counts3, errors3, soft3 = T.collect(NOW + timedelta(minutes=16))
check("레이트리밋은 16분 뒤에도 다시 두드리지 않는다", not soft3 and not errors3,
      f"soft={soft3} errors={errors3}")
counts4, errors4, soft4 = T.collect(NOW + timedelta(hours=7))
check("레이트리밋도 충분히 쉬면 다시 시도한다", any("LCK" in s for s in soft4), str(soft4))
check("레이트리밋 백오프가 일반 실패보다 길다",
      T.RETRY_AFTER_RATELIMIT_SECONDS > T.RETRY_AFTER_FAIL_SECONDS,
      f"{T.RETRY_AFTER_RATELIMIT_SECONDS} vs {T.RETRY_AFTER_FAIL_SECONDS}")
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
#
# ⚠️ **표본을 LCK에서 NPB로 바꿨다 (v1.23).** LCK는 `DISABLED_LEAGUES`가 되어
# 이제 참고(soft)로 내려간다 — 그건 의도한 동작이고, **검사의 표본이 낡은 것**이다
# (약점 157: 동작을 바꾸면 그 동작을 세던 검사도 같이 낡는다).
# 지키려는 성질은 그대로다: **발행하는 리그가 시즌 중에 못 들어오면 빨간불.**
r = CV.run({"NPB": []}, {"NPB": {"at": None, "count": 0, "error": "ratelimited"}}, _CNOW)
check("한 번도 수집 못 한 리그를 잡는다",
      any("성공 기록 없음" in x for x in r.lines()), str(r.lines()))
check("시즌 중 리그가 못 들어오면 빨간불 (NPB는 9월이 시즌)", not r.ok, str(r.lines()))
# ★ 그리고 그 반대쪽도 못 박는다 — 발행하지 않는 리그는 빨간불이 아니다.
_rd = CV.run({"LCK": []},
             {"LCK": {"at": None, "count": 0, "error": "ratelimited"}}, _CNOW)
check("★★ 발행 제외 리그(LCK)의 수집 실패는 빨간불이 아니다 (고칠 것이 없는 경보)",
      _rd.ok and any("발행 제외 리그" in x for x in _rd.lines()), str(_rd.lines()))
check("  ↳ 그래도 기록은 남는다 (나중에 그 리그를 다시 켤 때 필요하다)",
      len(_rd.lines()) == 1, str(_rd.lines()))
check("  ↳ 알림에는 개수만 실린다 (본문은 health.json에)",
      not any("LCK" in x for x in _rd.alert_lines())
      and any("참고 1건" in x for x in _rd.alert_lines()),
      str(_rd.alert_lines()))

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

# ── 9. 경기 예고 = 리그 하루 한 장 (v1.11c → v1.15) ─────────────
print("\n9. 경기 예고 — 도배가 아니라 하루 한 장인가")
# 사고: 같은 시각(±5분) 경기를 묶어 시각마다 보냈더니 실측 **하루 26건**,
# 그중 17건이 MLB 새벽 1~3시대였다. 새벽에 열일곱 번 울리는 채널은 구독자가 나간다.
# 그 규칙("리그 하루 한 장")은 시작 알림이 지키던 것이고, 2026-09-07에
# **경기 예고**가 그대로 이어받았다 — 시작 알림은 껐다(같은 목록을 두 번 말했다).
from contract import day_schedule_scope, START_ALERT_LEAD_MINUTES

# NOW는 2026-08-29 07:00 KST다. 경기는 그 뒤여야 예고가 미래에 잡힌다
# (과거 예약은 큐가 걸러낸다 — 그것도 정상 동작이다).
_many = ([mkgame(League.MLB, "NYY", "BOS", day="2026-08-29", hh=h)
          for h in (12, 14, 17, 20, 23)]
         + [mkgame(League.MLB, "LAD", "SF", day="2026-08-29", hh=h)
            for h in (12, 17, 23)])
_q = T.build_all_queues({"MLB": _many}, NOW, "-100test")
_sa = [i for i in _q if i.content_type is ContentType.MORNING]
# ⚠️ **2026-09-07: '하루 한 장'에서 '시간대 덩어리마다 한 장'으로 바뀌었다.**
# 유럽 5대리그가 하루 19~22시간에 걸쳐 열려, 한 장으로 예고하면 "2시간 전"이
# 마지막 경기에는 22시간 전이 된다. 대표님이 *"시간대별로 쪼갠다"*로 정했다.
# 시험 데이터는 12·14·17·20·23시라 3시간 규칙으로 갈린다.
_want = len(C.preview_buckets(_many))
check(f"경기 {len(_many)}건 · 경기 예고 {len(_sa)}건 (시간대 덩어리 {_want}개)",
      len(_sa) == _want, f"{len(_sa)}건 vs 덩어리 {_want}개")
# **도배 방지는 여전히 살아 있어야 한다** — 옛 시작 알림은 시각마다 보내
# 하루 26건을 냈고 그래서 접었다. 덩어리는 경기 수보다 반드시 적다.
check("★ 예고 장수가 경기 수보다 적다 (경기마다 한 장이면 도배로 되돌아간 것)",
      len(_sa) < len(_many), f"예고 {len(_sa)} vs 경기 {len(_many)}")
check("scope에 리그·날짜·묶음이 들어간다",
      bool(_sa) and all(i.scope.startswith("MLB:2026-08-29#") for i in _sa),
      str([i.scope for i in _sa])[:120])
check("  ↳ 묶음마다 scope가 다르다 (같으면 둘째 예고가 '이미 보냄'으로 사라진다)",
      len({i.scope for i in _sa}) == len(_sa))
# 3시간 안에 붙어 있으면 한 덩어리다 — 쪼개기가 지나치지 않은지 본다.
_tight = [mkgame(League.MLB, "NYY", "BOS", day="2026-08-29", hh=h)
          for h in (12, 13, 14)]
check("★ 3시간 안에 붙은 경기는 한 장으로 묶인다 (지나치게 쪼개지 않는다)",
      len(C.preview_buckets(_tight)) == 1,
      str([k for k, _ in C.preview_buckets(_tight)]))

# ── 심야 회피가 만든 겹침을 병합하는가 (2026-09-07) ────────────
#
# 새벽 경기의 예고는 전날 밤으로 앞당겨진다. 그러다 보면 **앞 덩어리의
# 예고와 몇 분 차로 겹친다.** 실측 리그1 2026-09-13:
#   00:15 경기 → 예고 22:15   ·   03:45 경기 → 예고 22:00(심야 회피)
# 15분 차에 두 장이면 쪼갠 뜻이 없고 도배만 된다.
# `mkgame(hh=N)`은 **한국시각 N:30**을 만든다.
#   00:30 경기 → 예고 22:30 (전날, 심야 아님)
#   03:30 경기 → 예고 01:30 → 심야(00~06)라 **전날 22:00**으로 회피
# 두 예고가 30분 차라 병합 대상이다. 시작 시각은 3시간 차라 원래 다른 덩어리다.
_l1 = ([mkgame(League.MLB, "AAA", "BBB", day="2026-09-13", hh=0)]
       + [mkgame(League.MLB, "CCC", "DDD", day="2026-09-13", hh=3)]
       + [mkgame(League.MLB, "EEE", "FFF", day="2026-09-13", hh=20)])
_raw = C.preview_buckets(_l1)
_lead = P.PREVIEW_BEFORE_FIRST_SECONDS
_mrg = C.preview_buckets(_l1, lead_seconds=_lead)
check("★★ 예약 시각이 붙은 덩어리는 하나로 합친다",
      len(_mrg) <= len(_raw), f"시작기준 {len(_raw)}개 → 예약기준 {len(_mrg)}개")
_ats = sorted(C.preview_at(min(x.start_utc for x in b), _lead) for _k, b in _mrg)
_gaps = [(_ats[i + 1] - _ats[i]).total_seconds() for i in range(len(_ats) - 1)]
check("★★ 남은 예고끼리는 30분 넘게 떨어져 있다 (연달아 두 장이 안 나간다)",
      all(g > C.PREVIEW_MERGE_WITHIN_SECONDS for g in _gaps),
      str([f"{g / 60:.0f}분" for g in _gaps]))
check("  ↳ 병합해도 경기가 사라지지 않는다 (합친 뒤 총 경기 수가 같다)",
      sum(len(b) for _k, b in _mrg) == len(_l1),
      f"{sum(len(b) for _k, b in _mrg)} vs {len(_l1)}")
# 변이시험 — 병합을 끄면(예약 시각을 안 보면) 겹침이 실제로 남는가.
_ats_raw = sorted(C.preview_at(min(x.start_utc for x in b), _lead)
                  for _k, b in _raw)
_gaps_raw = [(_ats_raw[i + 1] - _ats_raw[i]).total_seconds()
             for i in range(len(_ats_raw) - 1)]
check("  ↳ 변이시험 — 병합을 안 하면 30분 안에 겹치는 쌍이 실제로 생긴다",
      any(g <= C.PREVIEW_MERGE_WITHIN_SECONDS for g in _gaps_raw),
      str([f"{g / 60:.0f}분" for g in _gaps_raw]))

# ★★ **첫 경기 30분 전** — 종료 흐름(마지막 속보 + 30분)의 거울상이다.
_first = min(g.start_utc for g in _many)
if _sa:
    lead = (_first - _sa[0].scheduled_utc).total_seconds() / 60
    # **최소 리드타임**을 본다 — 심야 회피가 걸리면 더 **일찍** 나갈 수 있다.
    # (MLB 실측: 첫 경기 05:10 KST → 전날 22:00, 7.2시간 전)
    check(f"★★ 첫 경기보다 적어도 {P.PREVIEW_BEFORE_FIRST_SECONDS // 60}분 앞선다",
          lead >= P.PREVIEW_BEFORE_FIRST_SECONDS / 60 - 1, f"{lead:.0f}분")
    # ⚠️ **앞뒤 간격이 같으면 안 된다 (2026-09-07 대표님 지적).**
    # 처음에 둘 다 30분으로 뒀다가 바로잡혔다:
    #   *"경기시작전에는 빨리 정보를 확인하고싶고 경기종료후에는 빨리 결과를
    #     확인하고 싶어하니까"* — 같아야 하는 것은 **구조**이지 숫자가 아니다.
    check("★ 예고는 정리판보다 훨씬 일찍 나간다 (앞뒤는 보는 마음이 반대다)",
          P.PREVIEW_BEFORE_FIRST_SECONDS > P.RESULT_AFTER_LAST_FLASH_SECONDS,
          f"예고 {P.PREVIEW_BEFORE_FIRST_SECONDS}초 vs "
          f"정리판 {P.RESULT_AFTER_LAST_FLASH_SECONDS}초")
    check("  ↳ 첫 경기보다 **앞**이다 (예고가 시작 뒤에 나가면 예고가 아니다)",
          _sa[0].scheduled_utc < _first)

# 두 리그가 같은 날이면 서로 다른 장이어야 한다
_two = T.build_all_queues(
    {"MLB": _many, "KBO": [mkgame(League.KBO, "LG", "OB", day="2026-08-29", hh=18)]},
    NOW, "-100test")
_sa2 = [i for i in _two if i.content_type is ContentType.MORNING]
_kbo_pv = [i for i in _sa2 if i.league is League.KBO]
check("두 리그가 섞여도 리그마다 자기 예고를 갖는다",
      len(_kbo_pv) == 1 and len(_sa2) == len(_sa) + 1,
      f"KBO {len(_kbo_pv)} · 전체 {len(_sa2)}")
check("멱등키가 전부 다르다 (하나라도 겹치면 그 장이 사라진다)",
      len({i.idem_key for i in _sa2}) == len(_sa2))

# 끈 시작 알림이 되살아나지 않았는지 — 같은 데이터로 확인한다.
check("시작 알림은 만들어지지 않는다 (껐다)",
      not [i for i in _q if i.content_type is ContentType.START_ALERT])

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

# 경기별 속보는 **종료를 알아챈 시각**이 찍힌 경기만 큐에 오른다.
# 표본에 안 주면 그 종류가 통째로 안 잡히고, 아래 '큐에 다 오른다' 검사가
# 그것을 잡는다 — 실제로 v1.14 배선 때 그렇게 걸렸다.
for _g in _full["KBO"]:
    if _g.status is Status.FINAL:
        _g.meta.first_final_at = T._iso(NOW - timedelta(minutes=3))

# **선발 라인업(v1.17)도 같은 이유로 표본이 필요하다.** 예약 시각이 시계가
# 아니라 '명단을 처음 본 시각'이라, `lineup_seen_at`이 찍힌 경기가 표본에
# 없으면 그 종류가 통째로 안 잡힌다 — 위 속보와 판박이다.
# 축구 리그로 넣는다: 카드가 포메이션과 22명을 실제로 그려야 통과한다.
_EPL_DAY = "2026-08-29"
_lug = mkgame(League.EPL, "ARS", "CHE", day=_EPL_DAY, hh=23)
_lug.meta.lineup = {
    side: {"formation": "4231",
           "rows": [[f"{side[0].upper()}선수{_i}"] if _i == 0 else
                    [f"{side[0].upper()}선수{_i}-{_j}" for _j in range(_n)]
                    for _i, _n in enumerate((1, 4, 2, 3, 1))]}
    for side in ("home", "away")}
# 킥오프보다 넉넉히 앞서 관측된 것으로 둔다 — 큐가 '킥오프 15분 전까지
# 관측된 것'만 만들기 때문이다(그 경계 자체는 아래 12-B에서 따로 친다).
_lug.meta.lineup_seen_at = T._iso(NOW - timedelta(minutes=3))
_full["EPL"] = [_lug]
_items = T.build_all_queues(_full, NOW, "-100test")
_kinds = {i.content_type for i in _items}
check(f"큐에 오르는 {len(QUEUED_CONTENT_TYPES)}종이 다 오른다 ({len(_items)}건)",
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
check(f"{len(QUEUED_CONTENT_TYPES)}종이 모두 실제로 만들어진다",
      {c.value for c in QUEUED_CONTENT_TYPES} <= set(_made),
      f"만들어짐={sorted(set(_made))} 비어서건너뜀={sorted(set(_empty))}")

# **경기 예고는 카드(사진)로 나간다.** 예전 시작 알림은 텍스트였고, 껐다 —
# 같은 목록을 이미지와 텍스트로 두 번 말하고 있었기 때문이다.
_pv = next((i for i in _items if i.content_type is ContentType.MORNING), None)
check("경기 예고가 큐에 있다", _pv is not None)
if _pv is not None:
    _pr = T.render_for(_pv, _full[_pv.league.value])
    check("★ 경기 예고는 사진으로 나간다 (텍스트 목록으로 되돌아가지 않는다)",
          _pr is not None and bool(_pr[0]), str(_pr)[:100] if _pr else "None")
    check("  ↳ 캡션도 함께 나간다 (비면 푸시에 '사진'만 뜬다)",
          bool(_pr) and bool(_pr[1]) and bool(str(_pr[1][0]).strip()))
# 나머지 카드도 사진으로 나간다 — 글만 나가면 디자인이 통째로 빠진 것이다
for _ct in (ContentType.STANDINGS, ContentType.LEADERBOARD,
            ContentType.ANALYSIS):
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
check("순위표·리더보드 카드가 나가는 리그 표",
      P.RECORD_SOURCE_LEAGUES == frozenset({League.KBO, League.NPB}),
      str(sorted(l.value for l in P.RECORD_SOURCE_LEAGUES)))
# ── v1.16: **기록을 받는 리그**와 **순위표 카드가 나가는 리그**가 갈렸다 ──
# 분석은 순위+팀지표만 있으면 되지만, 순위표 카드는 MLB 지구 6개·K리그 부문
# 없음 같은 사정이 걸린다. 그래서 표를 둘로 나눴다.
check("★★ 분석 리그가 전부 기록 어댑터를 갖는다 (없으면 큐만 쌓이고 카드는 안 나온다)",
      {l.value for l in P.ANALYSIS_LEAGUES} <= set(T._record_jobs()),
      f"어댑터 {sorted(T._record_jobs())} vs 분석 "
      f"{sorted(l.value for l in P.ANALYSIS_LEAGUES)}")
check("★ 순위표 리그는 분석 리그의 부분집합이다 (순위표만 있고 분석이 없는 리그는 없다)",
      P.RECORD_SOURCE_LEAGUES <= P.ANALYSIS_LEAGUES,
      str(sorted(l.value for l in (P.RECORD_SOURCE_LEAGUES - P.ANALYSIS_LEAGUES))))
check("★ 기록 어댑터에 분석 대상이 아닌 리그가 없다 (죽은 수집 금지 — 약점 53)",
      set(T._record_jobs()) <= {l.value for l in P.ANALYSIS_LEAGUES},
      str(sorted(set(T._record_jobs()) - {l.value for l in P.ANALYSIS_LEAGUES})))

# **기록이 정말 없는 리그로 시험한다.** K리그는 v1.16에서 기록이 생겼으므로
# 더 이상 이 표본이 아니다 — 낡은 표본을 두면 검사가 헛돈다(약점 106).
_norec = {
    "KBL": [mkgame(League.KBL, "SK", "LG", day=_day, hh=19),
            mkgame(League.KBL, "DB", "KC", day=_day, hh=14, status=Status.FINAL,
                   score=Score(88, 80, ScoreUnit.POINTS))],
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
# 기록이 없는 리그도 **결과 정리판**은 만든다 — 정리판은 순위·기록이 아니라
# 그날 경기만 있으면 그릴 수 있다. (나이트는 껐다 — 위 참고.)
check("기록이 없는 리그도 결과 정리판은 만든다",
      ContentType.LEAGUE_RESULT in {i.content_type for i in _nq})

# ── 11c. 예약 시각 — 약속한 시각에 잡히는가 ────────────────────
# 카드마다 '언제 나간다'가 약속돼 있다. 여기가 틀어지면 아무 오류 없이
# 엉뚱한 시각에 나가고, 창·유예 계산이 전부 다른 이야기를 하게 된다.
print("\n11c. 예약 시각 — 나이트 23:00 · 리더보드 12:00 · 분석 첫 경기 -3h")
# **약속한 숫자를 여기에 직접 적는다.** 파이프라인의 상수(NIGHT_BRIEF_HOUR_KST 등)로
# 검사하면 상수를 바꾸는 순간 검사도 같이 따라가서 아무것도 못 잡는다 —
# 검증이 코드를 되풀이해 읽을 뿐 약속을 지키는지는 안 보게 된다.
_NB_HOUR, _LB_HOUR, _AN_LEAD_H = 23, 12, 3
check("파이프라인 상수가 약속과 같다 (12시 · -3시간)",
      (P.LEADERBOARD_HOUR_KST, P.ANALYSIS_LEAD_HOURS) == (_LB_HOUR, _AN_LEAD_H),
      f"{P.LEADERBOARD_HOUR_KST}/{P.ANALYSIS_LEAD_HOURS}")
# 나이트 브리핑(23:00)은 껐다 — 그 자리를 결과 정리판이 맡는다.
# **정리판은 고정 시각이 아니다**: 그 리그 마지막 경기 속보 + 30분이라
# 리그마다·날마다 다르다. 그래서 여기서는 시각이 아니라 **늦춤의 규칙**을 본다.
check(f"정리판 늦춤이 약속과 같다 ({P.RESULT_AFTER_LAST_FLASH_SECONDS // 60}분)",
      P.RESULT_AFTER_LAST_FLASH_SECONDS == 30 * 60,
      str(P.RESULT_AFTER_LAST_FLASH_SECONDS))
check("나이트 브리핑은 큐에 오르지 않는다 (껐다)",
      not [i for i in _items if i.content_type is ContentType.NIGHT_BRIEF])
_lb_at = [i.scheduled_utc.astimezone(KST) for i in _items
          if i.content_type is ContentType.LEADERBOARD]
check(f"리더보드는 12:00 KST ({len(_lb_at)}건)",
      bool(_lb_at) and all((t.hour, t.minute) == (_LB_HOUR, 0) for t in _lb_at),
      str([f"{t:%H:%M}" for t in _lb_at[:3]]))
_an = [i for i in _items if i.content_type is ContentType.ANALYSIS]
# ── 분석은 **그날 전 경기를 묶음마다 한 장**이다 (v1.15f) ──────────
# 전에는 `pick_analysis_game`(그날 첫 경기) 하나만 봤다. 야구는 대부분
# 동시 시작이라 그 검사는 새 동작에서도 우연히 통과한다 — 그래서 여기를
# **묶음 기준으로 다시 쓴다**(약점 98: 낡은 검증이 버그를 정상이라 보증한다).
_an_bad = []
for _it in _an:
    _bi = int(_it.scope.rsplit("#", 1)[1]) if "#" in _it.scope else -1
    _bt = P.analysis_batches([g for g in _full[_it.league.value]
                              if g.sports_day == _it.sports_day])
    if _bi < 0 or _bi >= len(_bt):
        _an_bad.append(f"{_it.scope} 묶음번호 없음/범위밖"); continue
    if _it.scheduled_utc != _bt[_bi][0].start_utc - timedelta(hours=_AN_LEAD_H):
        _an_bad.append(f"{_it.scope} {_it.scheduled_utc:%H:%M}")
check(f"분석 카드는 **그 묶음 첫 경기** 시작 -{_AN_LEAD_H}시간 ({len(_an)}건)",
      bool(_an) and not _an_bad, str(_an_bad[:3]))
check("★★ scope에 묶음 번호가 있다 (없으면 여러 장이 서로를 덮어쓴다)",
      bool(_an) and all("#" in i.scope for i in _an),
      str([i.scope for i in _an[:3]]))
check("★ 멱등키가 묶음마다 다르다",
      len({i.idem_key for i in _an}) == len(_an))
# ★★ 그날 전 경기가 빠짐없이 어느 한 묶음에 들어간다
_cov_bad = []
for _lgv, _gs in _full.items():
    for _d in {g.sports_day for g in _gs}:
        _dd = [g for g in _gs if g.sports_day == _d and g.status is Status.SCHEDULED]
        if not _dd:
            continue
        _flat = [g.game_id for b in P.analysis_batches(_dd) for g in b]
        if sorted(_flat) != sorted(g.game_id for g in _dd) or len(set(_flat)) != len(_flat):
            _cov_bad.append(f"{_lgv} {_d}")
check("★★ 묶음이 그날 예정 경기를 빠짐없이·중복 없이 덮는다", not _cov_bad,
      str(_cov_bad[:3]))
check(f"★ 한 장에 {P.ANALYSIS_PER_CARD}경기를 넘지 않는다 (우겨넣지 않는다)",
      all(len(b) <= P.ANALYSIS_PER_CARD
          for _lgv, _gs in _full.items()
          for _d in {g.sports_day for g in _gs}
          for b in P.analysis_batches([g for g in _gs if g.sports_day == _d])))
# ★★ 변이시험 — **표본을 직접 만들어 무조건 돌린다.**
# 실데이터에 기대면 그날 경기가 적을 때 이 검사가 조용히 건너뛴다(약점 106:
# 표본이 그 검사를 의미 있게 만드는지 먼저 확인한다).
class _AnG:
    def __init__(self, i):
        self.status = Status.SCHEDULED
        self.game_id = f"g{i:02d}"
        self.start_utc = datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc)


_big = [_AnG(i) for i in range(7)]
_bt7 = P.analysis_batches(_big)
check("★★ 7경기는 여러 장으로 나뉜다 (한 장에 몰아넣지 않는다)",
      len(_bt7) == 3 and [len(b) for b in _bt7] == [3, 3, 1],
      str([len(b) for b in _bt7]))
check("★★ 변이시험 — 안 나누면 한 장에 7경기가 다 들어간다 (지금은 안 그렇다)",
      len(_big) > P.ANALYSIS_PER_CARD
      and max(len(b) for b in _bt7) <= P.ANALYSIS_PER_CARD)
check("  ↳ 나눠도 순서가 유지된다 (번호가 그날 순서와 어긋나면 카드가 거짓말한다)",
      [g.game_id for b in _bt7 for g in b] == [g.game_id for g in _big])
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
from contract import start_alert_at, start_alert_notice, venue_name   # noqa: E402

# **'몇 시간 전'은 못 지키는 약속이라 시각으로 바꿨다 (v1.11n).**
# 심야 회피가 걸리면 실제 발송은 리드타임 그대로가 아니다 —
# 실측 MLB 2026-09-04: 첫 경기 03:10인데 알림 예약은 전날 22:00(5시간 10분 전).
# 그런데 카드는 "경기 시작 2시간 전 알림"이라고 적고 있었다(대표님 지적).
_gs = [mkgame(League.NPB, "YOG", "HAN", day="2026-08-29", hh=18)]
_now = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)
_txt = start_alert_notice(_gs, _now)
# **시각조차 약속하지 않는다 (v1.11p).** 예약 시각을 적었더니 그것도 거짓이었다 —
# 실제 발송은 앞창 2.5시간·유예 1시간 55분 안 어디서든 일어난다.
check(f"꼬리말이 시각도 '몇 시간 전'도 약속하지 않는다 ({_txt})",
      ":" not in _txt and "전 알림" not in _txt and "시간 전" not in _txt, _txt)
check("그래도 무엇을 하는지는 말한다", "시간표" in _txt, _txt)
check("보낼 것이 없으면 아무 약속도 하지 않는다", start_alert_notice([], _now) == "")

# 예약 계산 자체는 큐와 공유한다 — 카드가 시각을 안 적을 뿐, 계산은 한 곳이다.
_night = [mkgame(League.MLB, "NYY", "BOS", day="2026-08-29", hh=3)]
_nat = start_alert_at(_night)
check("심야 회피가 실제로 걸렸다 (계산이 살아 있다)",
      _nat.astimezone(KST).hour == C.START_ALERT_EVENING_HOUR,
      str(_nat.astimezone(KST)))

_mhtml = P.render_morning(_gs, "2026-08-29", now=_now)
check("모닝 카드가 그 문구를 적는다", _txt in _mhtml, _txt)
check("옛 문구('N시간 전 알림')가 카드에 남아 있지 않다",
      "전 알림" not in _mhtml)
check("예약 시각도 카드에 적지 않는다 (지킬 수 없는 정밀도)",
      start_alert_at(_gs).astimezone(KST).strftime("%H:%M") not in _mhtml)
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
# **게이트는 실측 간격으로 돈다** — 여기서도 실측 범위로 본다.
# 설정 상수(TICK_INTERVAL_SECONDS)는 옛 크론 시절 값(1시간)이라, 연속 운전이
# 도는 지금의 현실이 아니다. 실측 중앙 5.4분 · 최대 12.2분(2026-09-05, 24시간).
check("실측 정상 범위(9분)에서 게이트가 조용하다",
      _no_raise(lambda: assert_send_windows(9 * 60, T.LOOKAHEAD_SECONDS)))
# **v1.18에서 이 검사의 전제가 바뀌었다 (2026-09-08).**
# 킥오프 창을 9분 → 29분으로 넓혔으므로 13분 공백에는 이제 경고가 뜨지 않는다.
# **그게 옳다** — 대표님 지시("누락되는 알림이 절대 발생되면 안되")에 따라
# 흔한 공백을 창으로 덮었기 때문이다. 검사는 **덮은 만큼 조용하고 그 밖에서
# 시끄러운지**를 본다.
check("★ 실측 최대 공백(13분)에서 이제 게이트가 조용하다 (창을 넓혀 덮었다)",
      _no_raise(lambda: assert_send_windows(13 * 60, T.LOOKAHEAD_SECONDS)))
check("★ 그보다 뜸해지면(31분) 여전히 경고한다 — 창 밖은 사실이므로 경고가 맞다",
      not _no_raise(lambda: assert_send_windows(31 * 60, T.LOOKAHEAD_SECONDS)))
# ── ★ 누락 절대 금지 — 대표님 지시 (2026-09-08) ──────────────
#
# *"누락되는 알림이 절대 발생되면 안되"*
# 이 지시를 계약이 어떻게 지키는지를 여기서 못 박는다.
from contract import (MUST_ALERT_ON_MISS, KICKOFF_LEAD_SECONDS,       # noqa: E402
                      GRACE_SECONDS as _GS, NARROW_BY_DESIGN as _NBD,
                      SAFETY_NET_FOR as _SNF)
check("★★★ 킥오프 창이 실측 최대 공백(12.2분)을 덮는다 (전에는 9분이라 뚫렸다)",
      send_window_seconds(ContentType.KICKOFF, T.LOOKAHEAD_SECONDS) >= 13 * 60,
      f"{send_window_seconds(ContentType.KICKOFF, T.LOOKAHEAD_SECONDS) // 60}분")
check("  ↳ 그래도 경기 시작 이후 발송은 여전히 불가능하다 (유예 < 리드)",
      _GS[ContentType.KICKOFF] < KICKOFF_LEAD_SECONDS,
      f"유예 {_GS[ContentType.KICKOFF]}초 · 리드 {KICKOFF_LEAD_SECONDS}초")
check("★★★ 창이 좁은 콘텐츠는 안전망을 갖거나 놓쳤을 때 반드시 알린다",
      _NBD <= (frozenset(_SNF) | MUST_ALERT_ON_MISS),
      str(sorted(c.value for c in _NBD - (frozenset(_SNF) | MUST_ALERT_ON_MISS))))
check("  ↳ 킥오프가 그 목록에 있다 (안전망이 없으므로 알림이 유일한 방어다)",
      ContentType.KICKOFF in MUST_ALERT_ON_MISS)

# ── ★★★ 정보 체인 — 시계가 4시간 죽어도 그 경기가 사라지지 않는가 (v1.18b) ──
#
# 대표님: *"한계는 항상 해결 가능해. 방법 찾아서 처리해"*
# 앞서 "240분 공백은 어떤 창으로도 못 덮는다"고 적었는데 **범위가 틀렸다.**
# 못 덮는 것은 **임박 알림 한 장**뿐이고, 그 경기의 존재·결과는 덮을 수 있다.
_WORST = 240 * 60
for _ct in (ContentType.MORNING, ContentType.ANALYSIS,
            ContentType.FINAL_FLASH, ContentType.LEAGUE_RESULT):
    _w = send_window_seconds(_ct, T.LOOKAHEAD_SECONDS)
    check(f"★★★ {_ct.value}가 최악 공백(240분)을 덮는다 "
          f"— 시계가 4시간 죽어도 이 카드는 나간다",
          _w >= _WORST, f"{_w // 60}분")
check("★ 임박 알림만 못 덮는다는 사실이 계약에 적혀 있다 "
      "(원리적 한계이므로 숨기지 않고 알림으로 방어한다)",
      send_window_seconds(ContentType.KICKOFF, T.LOOKAHEAD_SECONDS) < _WORST
      and ContentType.KICKOFF in MUST_ALERT_ON_MISS)
# **변이시험** — 종료 속보 창을 되돌리면 이 검사가 잡는가
_orig_grace = _GS[ContentType.FINAL_FLASH]
try:
    _GS[ContentType.FINAL_FLASH] = 3600
    _broke = send_window_seconds(ContentType.FINAL_FLASH, T.LOOKAHEAD_SECONDS) >= _WORST
finally:
    _GS[ContentType.FINAL_FLASH] = _orig_grace
check("  ↳ 변이: 종료 속보 창을 옛 값(60분)으로 되돌리면 잡힌다",
      _broke is False, "깨뜨렸는데도 통과했다")

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
# 큐에 오르는 것 전체로도 한 번 — 종류가 또 늘면 위 목록보다 이쪽이 먼저 깨진다.
#
# **경기별 2종은 여기서 뺀다.** 창을 넓힐 수 없는 콘텐츠라서다(계약 주석 참조).
# 그냥 빼면 구멍이 되므로, **짝이 되는 안전망이 최악 간격을 견디는지**를
# 대신 확인한다 — 안전망 없이 좁은 창을 두면 그건 대가가 아니라 누락이다.
from contract import (NARROW_BY_DESIGN, SAFETY_NET_FOR,               # noqa: E402
                      DISABLED_CONTENT_TYPES)
_narrow = [f"{ct.value} {send_window_seconds(ct, T.LOOKAHEAD_SECONDS) // 60}분"
           for ct in QUEUED_CONTENT_TYPES - NARROW_BY_DESIGN
           if send_window_seconds(ct, T.LOOKAHEAD_SECONDS)
           < _WORST_OBSERVED_TICK_SECONDS]
check("큐에 오르는 종류 전부가 실측 최악 간격을 덮는다 (설계상 좁은 것 제외)",
      not _narrow, str(_narrow))
for _nc, _net in sorted(SAFETY_NET_FOR.items(), key=lambda kv: kv[0].value):
    _nw = send_window_seconds(_net, T.LOOKAHEAD_SECONDS)
    check(f"★★ {_nc.value}가 사라져도 {_net.value}가 담는다 "
          f"(안전망 창 {_nw // 60}분 ≥ 최악 {_WORST_OBSERVED_TICK_SECONDS // 60}분)",
          _nw >= _WORST_OBSERVED_TICK_SECONDS, f"{_nw // 60}분")
    check(f"  ↳ {_net.value}는 설계상 좁은 목록에 없다 (안전망이 안전망을 못 가진다)",
          _net not in NARROW_BY_DESIGN)
    # ★★★ **안전망이 실제로 발행 중인가** (2026-09-08 신설, 코덱스 협업에서 확정).
    #
    # 이 검사가 없어서 오늘 실제 사고가 났다. 계약은
    # `SAFETY_NET_FOR[KICKOFF] = START_ALERT`라고 적어 두었는데 START_ALERT는
    # 2026-09-07에 **꺼졌다**(`DISABLED_CONTENT_TYPES`). 그래서 킥오프를 놓친
    # 경기는 아무 데도 실리지 않는데, 검사는 창 넓이만 보고 통과시켰다.
    # 2026-09-08 KBO 5경기(18:30)의 시작 알림이 한 장도 안 나갔고 경고도 없었다.
    #
    # **꺼진 콘텐츠는 안전망이 아니다.** 창이 아무리 넓어도 안 나가기 때문이다.
    check(f"  ↳ ★★★ {_net.value}가 실제로 발행 중이다 (꺼진 콘텐츠는 안전망이 아니다)",
          _net not in DISABLED_CONTENT_TYPES and _net in QUEUED_CONTENT_TYPES,
          f"{_net.value}: 꺼짐={_net in DISABLED_CONTENT_TYPES} "
          f"큐에있음={_net in QUEUED_CONTENT_TYPES}")
# **결과 카드를 일찍 보내면 경기가 빠진다.** 예약 시각은 '마감'이고, 렌더는
# "한 경기라도 끝났으면" 카드를 만든다. 앞창을 열면 5경기 중 1경기만 끝난
# 시점에 카드가 나가고 나머지는 영영 빠진다(멱등키가 재발송을 막으므로).
check("결과 카드는 마감보다 일찍 나가지 않는다 (앞창 0)",
      lookahead_for(ContentType.LEAGUE_RESULT, 90 * 60) == 0,
      str(lookahead_for(ContentType.LEAGUE_RESULT, 90 * 60)))
# **순위표는 0이 아니라 '오프셋만큼'이다 (v1.11n에서 바뀜).**
# 전에는 결과 카드와 똑같이 0으로 잠가 두었는데, 순위표의 예약은
# `결과 카드 예약 + 10분`이고 결과 카드 예약은 그날이 끝났으면 '지금'이다.
# 그래서 순위표는 매 틱 '지금+10분'으로 다시 계산되며 앞으로 도망갔고,
# 앞창 0으로는 영원히 따라잡지 못해 **한 장도 못 나갔다**(실측 2026-09-04).
# 잠금의 원래 목적(기본 앞창을 넓혀도 일찍 열리지 않는다)은 그대로 지킨다 —
# 값이 오프셋에 고정되어 기본 앞창을 따라 늘어나지 않는다.
check("순위 카드 앞창은 '결과 뒤 오프셋'에 고정된다",
      lookahead_for(ContentType.STANDINGS, 90 * 60) == C.STANDINGS_AFTER_RESULT_SECONDS,
      str(lookahead_for(ContentType.STANDINGS, 90 * 60)))
check("순위 카드 앞창이 오프셋보다 좁으면 영원히 안 나간다 — 그 아래로는 못 내려간다",
      lookahead_for(ContentType.STANDINGS, 0) >= C.STANDINGS_AFTER_RESULT_SECONDS)
# 기본 앞창을 아무리 넓혀도 잠긴 것은 열리지 않아야 한다
check("기본 앞창을 크게 줘도 잠금이 이긴다",
      all(lookahead_for(ct, 999 * 60) == 0
          for ct in (ContentType.MORNING, ContentType.LEAGUE_RESULT,
                     ContentType.NIGHT_BRIEF)))
check("기본 앞창을 크게 줘도 순위 카드는 오프셋에서 안 늘어난다",
      lookahead_for(ContentType.STANDINGS, 999 * 60)
      == C.STANDINGS_AFTER_RESULT_SECONDS)

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
# **선발 라인업(v1.17)을 여기 넣는 것이 요점이다.** 계약 주석은 "라인업은
# 안전망 없이 자기 창(4시간)만으로 뜸한 시계를 견딘다"고 주장한다 —
# 그 주장을 실제로 치는 자리가 여기다. 넣지 않으면 주장이 검사되지 않은 채
# 남는다(약점 50: 검사 목록에 없는 것은 게이트 밖이다).
_sim_lu = mkgame(League.EPL, "ARS", "CHE", day=_sim_day, hh=23)
_sim_lu.meta.lineup = {s: {"formation": "433",
                           "rows": [["GK"], ["D1", "D2", "D3", "D4"],
                                    ["M1", "M2", "M3"], ["F1", "F2", "F3"]]}
                       for s in ("home", "away")}
# 킥오프 80분 전에 관측된 것으로 둔다 — 실측한 발표 시점(약 1시간 전)에 맞춘 값이고,
# 큐 조건('킥오프 15분 전까지')도 넉넉히 통과한다.
_sim_lu.meta.lineup_seen_at = T._iso(_sim_lu.start_utc - timedelta(minutes=80))
_sim["EPL"] = [_sim_lu]
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
# 시작 알림은 껐다 — 그 자리를 **경기 예고**가 맡는다(리그 하루 한 장).
check("경기 예고가 100분 시계에 걸린다", ContentType.MORNING in _seen,
      str(sorted(c.value for c in _seen)))
check("결과 카드가 100분 시계에 걸린다", ContentType.LEAGUE_RESULT in _seen,
      str(sorted(c.value for c in _seen)))
# 경기별 2종은 100분 시계에서 구조적으로 못 걸린다(창 9분·60분) —
# 그래서 안전망을 뒀다. 여기서는 나머지가 전부 걸리는지를 본다.
_expect = QUEUED_CONTENT_TYPES - NARROW_BY_DESIGN
check("큐에 오르는 모든 종류가 빠짐없이 걸린다 (설계상 좁은 것 제외)",
      _expect <= _seen,
      f"놓친 것: {sorted(c.value for c in (_expect - _seen))}")

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
# **글자가 아니라 뜻을 검사한다.** 옛 카드는 "전 경기 취소 · 우천취소"처럼
# 둘로 나눠 적었고, v5는 "전 경기 우천취소"로 합쳐 적는다 — 같은 말이다.
# 지켜야 할 것은 낱말의 배열이 아니라 **'오늘 볼 경기가 없다'가 캡션에 있다**는 것이다.
_cap5 = "".join(_r5[1]) if _r5 else ""
check("그 카드는 '전 경기가 취소됐다'고 말한다",
      "전 경기" in _cap5 and ("취소" in _cap5 or "연기" in _cap5),
      _cap5[:120] or "None")
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

# ── 캐시로 버틴 것을 '갓 수집'으로 읽지 않는가 (fix46) ─────────
#
# 대표님 채널에 온 알림이 출발점이다:
#   LCK: 캐시로 버팀(묵은 데이터) 2.4시간 전 스냅샷
#   INTL_LOL: 캐시로 버팀(묵은 데이터) 1.1시간 전 스냅샷
# 리밋에 걸려 캐시를 돌려준 어댑터도 fn()은 정상으로 끝난다. 그래서
# ① fetch.json의 at이 '지금'으로 찍혀 24시간 발송 보류가 영원히 안 걸리고
# ② 성공으로 찍히니 레이트리밋 백오프가 안 걸려 30분마다 다시 두드렸다.
print("\n캐시로 버틴 수집 — 갓 수집한 것으로 읽지 않는가")
import tick as T                                                     # noqa: E402

_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _age(rec, now=_NOW):
    return T.snapshot_age_seconds("LCK", {"LCK": rec}, now)


check("갓 수집한 스냅샷은 나이가 0에 가깝다",
      _age({"at": T._iso(_NOW)}) < 60, str(_age({"at": T._iso(_NOW)})))
# 캐시로 버틴 그 순간 — 데이터는 이미 2.4시간 묵어 있었다
_c = {"at": T._iso(_NOW), "cache_age": 2.4 * 3600}
check("캐시로 버틴 틱은 캐시 나이만큼 묵은 것으로 센다",
      abs(_age(_c) - 2.4 * 3600) < 60, f"{_age(_c) / 3600:.2f}시간")
# **제동에 걸린 다음 틱들** — 어댑터가 안 돌아 인스턴스 값은 0이다.
# 기록에 안 남기면 여기서 '갓 수집'으로 되돌아간다(fix44와 같은 부류).
check("제동에 걸린 뒤에도 진짜 나이를 안다 (캐시 나이 + 그 뒤 흐른 시간)",
      abs(_age(_c, _NOW + timedelta(hours=3)) - 5.4 * 3600) < 60,
      f"{_age(_c, _NOW + timedelta(hours=3)) / 3600:.2f}시간")
# 24시간을 넘기면 그 리그는 발송을 보류해야 한다 — 그것이 이 값의 존재 이유다
check("캐시로 오래 버티면 결국 발송 보류 문턱을 넘는다",
      _age(_c, _NOW + timedelta(hours=22)) >= T.STALE_SNAPSHOT_BLOCK_SECONDS,
      f"{_age(_c, _NOW + timedelta(hours=22)) / 3600:.1f}시간 "
      f"vs 문턱 {T.STALE_SNAPSHOT_BLOCK_SECONDS / 3600:.0f}시간")
check("캐시를 안 쓴 리그는 아무것도 달라지지 않는다",
      _age({"at": T._iso(_NOW), "cache_age": 0}) < 60)
check("기록이 깨져 있어도 죽지 않는다 (감시가 예외로 사라지면 안 된다)",
      _no_raise(lambda: _age({"at": T._iso(_NOW), "cache_age": "몰라"})))
check("레이트리밋 백오프가 30분 제동보다 길다 (계속 두드리면 쿼터가 안 풀린다)",
      T.RETRY_AFTER_RATELIMIT_SECONDS > T.FETCH_EVERY_SECONDS,
      f"{T.RETRY_AFTER_RATELIMIT_SECONDS}s vs {T.FETCH_EVERY_SECONDS}s")
# 캐시로 버틴 기록은 백오프가 걸리게 'ratelimited'로 남는다
check("캐시로 버틴 기록은 레이트리밋으로 분류된다",
      "ratelimited" in T._RATELIMITED_BY_CACHE.lower())

# ── state/health.json — 우리가 상태를 볼 수 있는가 (fix47) ──────
#
# fix46으로 '캐시로 버팀'을 fetch.json에 기록하게 만들어 놓고, 정작 그것이
# 도는지 확인하려니 **읽을 방법이 없었다** — fetch.json은 캐시로만 나르고
# 저장소에 없다. 고친 사람이 자기 수정을 확인할 수 없으면 그 수정은 '했다'로 끝난다.
# **공개 저장소이므로 오류 원문은 절대 넣지 않는다 — 분류만.**
print("\nstate/health.json — 상태를 볼 수 있는가 (원문은 새지 않는가)")

_HNOW = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
_hflog = {
    # 캐시로 버티는 상황을 시험한다. **살아 있는 리그 이름을 쓴다** — 전에는
    # LCK였는데 2026-09-07에 발행에서 빠지면서 `_jobs()`에 없어져 KeyError가 났다.
    "NPB": {"at": T._iso(_HNOW), "count": 53, "cache_age": 3.5 * 3600,
            "error": T._RATELIMITED_BY_CACHE, "failed_at": T._iso(_HNOW)},
    "KBO": {"at": T._iso(_HNOW), "count": 348, "error": None},
    "MLB": {"at": T._iso(_HNOW), "count": 97,
            "error": "GateError: 토큰 1234567890:AAHsecretvalue 가 섞인 오류 원문"},
}


class _Cov:
    ok = True


# **살아 있는 리그로 시험한다.** 전에는 LCK를 썼는데 2026-09-07에 발행에서
# 빠지면서 `_jobs()`에 없어져 KeyError가 났다 — 검사가 옳게 잡았다.
# 시험 대상은 "캐시로 버티는 상황"이지 특정 리그가 아니다.
_no_raise(lambda: T._write_health(_HNOW, _hflog, ["NPB: 캐시로 버팀 3.5시간"],
                                  ["NPB: 스냅샷이 25.0시간 묵었습니다 — 이번 틱 발송 보류"],
                                  _Cov()))
_htxt = T.HEALTH_LOG.read_text(encoding="utf-8")
_h = json.loads(_htxt)
check("캐시로 버틴 리그의 나이가 보인다", _h["leagues"]["NPB"].get("cache_hours") == 3.5,
      str(_h["leagues"]["NPB"]))
check("발송 보류가 걸린 리그가 보인다", _h["stale_blocked"] == ["NPB"], str(_h["stale_blocked"]))
check("레이트리밋인지 게이트인지 분류가 보인다",
      _h["leagues"]["NPB"]["error_kind"] == "ratelimited"
      and _h["leagues"]["MLB"]["error_kind"] == "gate", str(_h["leagues"]))
check("정상 리그에는 오류 표시가 없다", "error_kind" not in _h["leagues"]["KBO"])

# ★ 종료 감지 분포가 health.json에 남는가 (v1.12c) — 스냅샷은 캐시로만 나르므로
#   여기 안 적으면 내일 우리가 읽을 방법이 없다(약점 119).
_HD = pathlib.Path(tempfile.mkdtemp())
_os2, _oh = T._snap_path, T.HEALTH_LOG
T._snap_path = lambda n: _HD / f"{n}.json"
T.HEALTH_LOG = _HD / "health.json"
try:
    _day = T._iso(_HNOW)[:10]
    (_HD / "KBO.json").write_text(json.dumps([
        {"league": "KBO", "season": "2026", "source_key": f"x{i}",
         "home": "HH", "away": "LT", "start_utc": T._iso(_HNOW),
         "home_tz": "Asia/Seoul", "status": "final", "score": [3, 1, "runs"],
         "first_final_at": f"{_day}T{12 + i:02d}:3{i}:00+00:00"}
        for i in range(3)] + [
        {"league": "KBO", "season": "2026", "source_key": "old",
         "home": "HH", "away": "LT", "start_utc": T._iso(_HNOW),
         "home_tz": "Asia/Seoul", "status": "final", "score": [3, 1, "runs"],
         "first_final_at": "2020-01-01T05:00:00+00:00"}],
        ensure_ascii=False), encoding="utf-8")
    _no_raise(lambda: T._write_health(_HNOW, _hflog, [], [], _Cov()))
    _h2 = json.loads(T.HEALTH_LOG.read_text(encoding="utf-8"))
    _fu = _h2["leagues"]["KBO"].get("finals_utc")
    check("★ 종료 감지 시각이 health.json에 남는다 (내일 창 크기를 정할 근거)",
          _fu == ["12:30", "13:31", "14:32"], str(_fu))
    check("  다른 날 것은 섞지 않는다", _fu is not None and "05:00" not in _fu)
    check("  시각만 남긴다 (팀·점수는 여기 있을 이유가 없다)",
          all(len(x) == 5 and ":" in x for x in (_fu or [])), str(_fu))
finally:
    T._snap_path, T.HEALTH_LOG = _os2, _oh
    shutil.rmtree(_HD, ignore_errors=True)
# ★ 공개 저장소에 커밋되는 파일이다 — 원문이 새면 배포 전 점검이 못 잡는 자리가 된다
check("★ 오류 원문이 새지 않는다 (분류만 남긴다)",
      "secret" not in _htxt and "1234567890" not in _htxt and "토큰" not in _htxt,
      _htxt[:120])
check("알림 본문도 안 남는다 (줄 수만)",
      "캐시로 버팀" not in _htxt and isinstance(_h["alert_lines"], int), _htxt[:120])
check("관측이 본 작업을 죽이지 않는다 (기록이 깨져도)",
      _no_raise(lambda: T._write_health(_HNOW, {"LCK": {"cache_age": "몰라"}},
                                        [], [], _Cov())))


# ══════════════════════════════════════════════════════════════
# 스냅샷 왕복 — **메모리에서 되는 것이 디스크를 지나면 안 된다** (fix49)
# ══════════════════════════════════════════════════════════════
#
# 카드는 수집한 games가 아니라 **저장했다가 되읽은 것**으로 그린다
# (`tick()`의 `snaps = all_games()`). 그래서 `_save_games`가 안 담는 필드는
# 카드에 절대 닿지 않는다 — 예외도 경고도 없이, 그냥 없는 것이 된다.
#
# 두 번 당했다: v1.11h(`decided_by`·`gender` → 되읽은 1,000건 중 252건 실패),
# v1.12(`line_score`·`goals` → 흐름표가 실서비스에서 한 장도 안 나감).
# 검증 809건이 둘 다 못 잡았다 — **왕복을 안 시켜봤기 때문이다.**

from contract import Goal as _Goal

# ① 구조 검사 — 목록이 아니라 '전체 − 예외'다. 필드를 늘리면 자동으로 걸린다.
_miss = T.assert_meta_persisted()
check("★ GameMeta의 모든 필드가 저장되거나 예외로 적혀 있다",
      _miss == [], f"저장 목록에서 빠진 필드: {_miss}")
check("저장 안 하는 필드마다 이유가 적혀 있다",
      all(isinstance(v, str) and len(v) > 10 for v in T.META_NOT_PERSISTED.values()))

# ② 변이시험 — 게이트가 진짜 잡는지. 안 잡으면 게이트가 아니라 장식이다.
import inspect as _insp
from dataclasses import fields as _dcf
_src_mut = _insp.getsource(T._save_games).replace('"line_score"', '"ZZZ"')
_caught = [f.name for f in _dcf(GameMeta)
           if f.name not in T.META_NOT_PERSISTED and f'"{f.name}"' not in _src_mut]
check("★ 변이시험 — 저장 목록에서 필드를 빼면 게이트가 잡는다",
      _caught == ["line_score"], str(_caught))

# ③ 실제 왕복 — 야구 흐름이 디스크를 지나 살아 돌아오는가
_rtdir = pathlib.Path(tempfile.mkdtemp())
_old_snap = T._snap_path
T._snap_path = lambda n: _rtdir / f"{n}.json"
try:
    _bg = Game(league=League.KBO, season="2026", source_key="rt1",
               home=TeamRef(League.KBO, "HH"), away=TeamRef(League.KBO, "LT"),
               start_utc=datetime(2026, 9, 5, 9, 30, tzinfo=timezone.utc),
               home_tz="Asia/Seoul", status=Status.FINAL,
               score=Score(11, 6, ScoreUnit.RUNS), venue="대전")
    _bg.meta.line_score = [(0, 1), (6, 0), (5, 5)]
    _bg.meta.line_totals = {"R": (11, 6), "H": (14, 9), "E": (0, 1)}
    _bg.meta.highlights = (("결승타", "장규현 · 8회 2사 1,3루 우중간 안타"),)
    T._save_games("rtkbo", [_bg])
    _rb = T._load_games("rtkbo")[0]
    check("★ 이닝별 점수가 디스크를 지나 살아온다",
          _rb.meta.line_score == [(0, 1), (6, 0), (5, 5)], str(_rb.meta.line_score))
    check("합계(R·H·E)도 살아온다",
          _rb.meta.line_totals == {"R": (11, 6), "H": (14, 9), "E": (0, 1)},
          str(_rb.meta.line_totals))
    check("관전 포인트도 살아온다",
          _rb.meta.highlights == (("결승타", "장규현 · 8회 2사 1,3루 우중간 안타"),),
          str(_rb.meta.highlights))

    # ④ 축구 득점 — 자책골 표시와 추가시간까지 살아야 한다
    _fg = Game(league=League.KL1, season="2026", source_key="rt2",
               home=TeamRef(League.KL1, "ULS"), away=TeamRef(League.KL1, "JBH"),
               start_utc=datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc),
               home_tz="Asia/Seoul", status=Status.FINAL,
               score=Score(2, 1, ScoreUnit.GOALS), venue="문수")
    _fg.meta.goals = (_Goal(minute=23, side="home", name="주민규"),
                      _Goal(minute=90, side="away", name="김진규",
                            own_goal=True, added=2))
    T._save_games("rtkl", [_fg])
    _rf = T._load_games("rtkl")[0]
    check("★ 득점 시각·선수가 디스크를 지나 살아온다",
          len(_rf.meta.goals) == 2 and _rf.meta.goals[0].name == "주민규",
          str(_rf.meta.goals))
    check("자책골 표시가 살아온다 (없으면 카드가 남의 골로 적는다)",
          _rf.meta.goals[1].own_goal is True, str(_rf.meta.goals[1]))
    check("추가시간이 살아온다", _rf.meta.goals[1].added == 2, str(_rf.meta.goals[1]))

    # ⑤ 옛 스냅샷 호환 — 흐름 없이 저장된 파일도 그냥 읽혀야 한다
    _snap = json.loads((_rtdir / "rtkbo.json").read_text(encoding="utf-8"))
    for _k in ("line_score", "line_totals", "goals", "highlights"):
        _snap[0].pop(_k, None)
    (_rtdir / "rtold.json").write_text(json.dumps(_snap, ensure_ascii=False),
                                       encoding="utf-8")
    _ro = T._load_games("rtold")
    check("흐름이 없던 옛 스냅샷도 그대로 읽힌다 (배포 순간 빈칸이 되지 않는다)",
          len(_ro) == 1 and _ro[0].meta.line_score == [] and _ro[0].meta.goals == (),
          str(_ro[:1]))

    # ⑥ 끝까지 — 되읽은 것으로 v5 카드가 실제로 흐름표를 그리는가
    import render_v5 as _R5
    _card = _R5.result_card(T._load_games("rtkbo"), League.KBO, "2026-09-05")
    check("★ 되읽은 스냅샷으로 흐름표 카드가 만들어진다",
          bool(_card) and "결승타" in _card[0],
          "카드 없음" if not _card else "흐름 없음(한 줄 요약으로 떨어짐)")
finally:
    T._snap_path = _old_snap
    shutil.rmtree(_rtdir, ignore_errors=True)

# ⑦ 흐름 보강 상한이 전역을 더럽히지 않는다 (fix49)
import adapters.naver_game as _ngmod
_before_max = _ngmod.MAX_GAMES_PER_TICK
_no_raise(lambda: T._enrich_flow("KBO", League.KBO, [], []))
check("흐름 상한을 넘겨도 어댑터 전역이 바뀌지 않는다",
      _ngmod.MAX_GAMES_PER_TICK == _before_max,
      f"{_before_max} → {_ngmod.MAX_GAMES_PER_TICK}")


# ══════════════════════════════════════════════════════════════
# 기록 보관본 — 기록이 막혀도 세 콘텐츠가 함께 죽지 않는다 (fix53)
# ══════════════════════════════════════════════════════════════
print("\n기록 보관본 (fix53)")
from contract import (LeaderEntry, RecordBook, Standing, StreakKind,
                      WLD, assert_recordbook)
from dataclasses import replace as _dcreplace

_ROOT_SAVE = T.ROOT
T.ROOT = pathlib.Path(tempfile.mkdtemp())
try:
    # **표본이 계약을 지켜야 시험이 의미가 있다** (약점 106).
    # 상대전적 합계가 순위표와 맞아야 하므로, 순위표를 상대전적에서 **계산해** 만든다.
    # 손으로 적으면 반드시 어긋난다 — 실제로 세 번 어긋나 게이트가 잡았다.
    _codes = ["OB", "HH", "HT", "KT", "LG", "LT", "NC", "SK", "SS", "WO"]
    _h2h = {}
    for _i, _a in enumerate(_codes):
        for _j, _b in enumerate(_codes):
            if _i >= _j:
                continue
            _w = 8 - _i                      # 위 순위일수록 많이 이긴다
            _h2h[(_a, _b)] = WLD(_w, 16 - _w, 0)
            _h2h[(_b, _a)] = WLD(16 - _w, _w, 0)
    _st = []
    for _i, _c in enumerate(_codes):
        _rows = [w for (a, _), w in _h2h.items() if a == _c]
        _tot = WLD(sum(w.win for w in _rows), sum(w.loss for w in _rows),
                   sum(w.draw for w in _rows))
        _g = _tot.win + _tot.loss + _tot.draw
        _st.append(Standing(
            league=League.KBO, season="2026", team_code=_c, rank=_i + 1,
            games=_g, record=_tot, pct=f"{_tot.win / (_tot.win + _tot.loss):.3f}",
            games_behind=f"{_i * 8.0}", last10=WLD(5, 5, 0),
            streak_kind=StreakKind.WIN, streak_len=1,
            home=WLD(_tot.win, 0, 0), away=WLD(0, _tot.loss, _tot.draw), group=None))
    _st.sort(key=lambda x: -int(round(float(x.pct) * 1000)))
    for _i, _s2 in enumerate(_st):
        _st[_i] = _dcreplace(_s2, rank=_i + 1, games_behind=f"{_i * 8.0}")
    _rb = RecordBook(
        league=League.KBO, season="2026",
        collected_utc=datetime(2026, 9, 6, 9, 0, tzinfo=timezone.utc),
        source_url="https://example.invalid/kbo",
        standings=_st, h2h=_h2h,
        leaders={"홈런": [LeaderEntry(category="홈런", stat_key="hr", rank=1,
                                    player_id="p1", name="최정",
                                    team_code="SK", value="30")]})
    assert_recordbook(_rb, now_utc=_rb.collected_utc)      # 표본이 유효한지 먼저

    T._save_record_archive("KBO", _rb)
    check("★ 게이트를 통과한 기록이 보관된다",
          T._record_archive_path("KBO").exists())

    _back = T._load_record_archive("KBO", _rb.collected_utc + timedelta(hours=1))
    check("★ 되살린 기록이 원본과 같다 (중첩 dataclass·tuple 키까지)",
          _back is not None
          and [s.team_code for s in _back.standings] == [x.team_code for x in _st]
          and _back.standings[3].record == _rb.standings[3].record
          and _back.standings[3].last10 == _rb.standings[3].last10
          and _back.h2h == _rb.h2h
          and _back.leaders["홈런"][0].name == "최정"
          and _back.collected_utc == _rb.collected_utc,
          "되살리기 실패" if _back is None else "값이 다름")

    # ★ 핵심 — 묵은 것은 되살려도 게이트가 막는다. 보관본이라고 봐주지 않는다.
    check("★ 6시간을 넘긴 보관본은 되살아나지 않는다 (묵은 순위가 카드에 실릴 일이 없다)",
          T._load_record_archive("KBO",
                                 _rb.collected_utc + timedelta(hours=7)) is None)
    check("6시간 안이면 되살아난다 (그 안에서만 버틴다)",
          T._load_record_archive("KBO",
                                 _rb.collected_utc + timedelta(hours=5)) is not None)

    # 형식이 바뀌면 조용히 썩는 대신 버려진다
    _p = T._record_archive_path("KBO")
    _d = json.loads(_p.read_text(encoding="utf-8"))
    _d["v"] = 99
    _p.write_text(json.dumps(_d, ensure_ascii=False), encoding="utf-8")
    check("★ 형식이 바뀐 보관본은 버린다 (조용히 썩지 않는다)",
          T._load_record_archive("KBO", _rb.collected_utc) is None)

    _p.write_text("{망가진", encoding="utf-8")
    check("깨진 보관본도 죽지 않고 None을 돌려준다",
          T._load_record_archive("KBO", _rb.collected_utc) is None)
    check("보관본이 아예 없어도 죽지 않는다",
          T._load_record_archive("없는리그", _rb.collected_utc) is None)

    # ★ 변이시험 — 보관을 안 하면 되살릴 것이 없다
    _p.unlink(missing_ok=True)
    check("★ 변이시험 — 보관하지 않으면 되살리지 못한다 (보관이 진짜 일한다)",
          T._load_record_archive("KBO", _rb.collected_utc) is None)
finally:
    T.ROOT = _ROOT_SAVE

# ★ 제동·백오프 중에도 보관본을 쓴다 — 그래야 매 틱 만들 기회가 생긴다
_ROOT_SAVE2 = T.ROOT
T.ROOT = pathlib.Path(tempfile.mkdtemp())
_flog_save = T.FETCH_LOG
T.FETCH_LOG = T.ROOT / "fetch.json"
try:
    _fresh_rb = _dcreplace(_rb, collected_utc=datetime.now(timezone.utc))
    T._save_record_archive("KBO", _fresh_rb)
    _hits = []
    _real_jobs = T._record_jobs
    T._record_jobs = lambda: {"KBO": lambda: _hits.append(1) or _fresh_rb}
    _nowx = datetime.now(timezone.utc)
    # 방금 성공한 것으로 기록해 제동에 걸리게 한다
    T._write_fetch_log({"record:KBO": {"at": T._iso(_nowx)}})
    _n2: list = []
    _got = T._collect_records(_nowx, _n2)
    T._record_jobs = _real_jobs
    check("★ 30분 제동에 걸린 틱에도 보관본으로 기록이 살아난다",
          "KBO" in _got, f"{sorted(_got)} · {_n2}")
    check("  그때 소스를 두드리지는 않는다 (제동의 목적은 지킨다)",
          not _hits, f"{len(_hits)}회 호출")
finally:
    T.ROOT = _ROOT_SAVE2
    T.FETCH_LOG = _flog_save

# ══════════════════════════════════════════════════════════════
# 종료 감지 시각 — **전환을 봤을 때만 찍는다** (v1.12c)
# ══════════════════════════════════════════════════════════════
print("\n종료 감지 시각 (v1.12c)")
_FF = pathlib.Path(tempfile.mkdtemp())
_old_snap2 = T._snap_path
T._snap_path = lambda n: _FF / f"{n}.json"
try:
    def _fg(key, status, score=None):
        return Game(league=League.KBO, season="2026", source_key=key,
                    home=TeamRef(League.KBO, "HH"), away=TeamRef(League.KBO, "LT"),
                    start_utc=datetime(2026, 9, 6, 9, 30, tzinfo=timezone.utc),
                    home_tz="Asia/Seoul", status=status, score=score)

    _t0 = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    _t1 = _t0 + timedelta(minutes=5)
    _t2 = _t0 + timedelta(minutes=40)

    # ① 처음 보는데 이미 종료 → 언제 끝났는지 모른다. 안 찍는다.
    _a = [_fg("g1", Status.FINAL, Score(5, 3, ScoreUnit.RUNS))]
    T._mark_first_final("ff", _a, _t0)
    check("★ 처음 봤는데 이미 종료면 안 찍는다 (언제 끝났는지 모른다)",
          _a[0].meta.first_final_at is None, str(_a[0].meta.first_final_at))
    T._save_games("ff", _a)

    # ② 예정이던 것이 종료로 바뀌면 그때 찍는다
    _b = [_fg("g2", Status.SCHEDULED)]
    T._save_games("ff", _b)
    _c = [_fg("g2", Status.FINAL, Score(5, 3, ScoreUnit.RUNS))]
    T._mark_first_final("ff", _c, _t1)
    check("★ 전환을 실제로 보면 그 시각을 찍는다",
          _c[0].meta.first_final_at == T._iso(_t1), str(_c[0].meta.first_final_at))
    T._save_games("ff", _c)

    # ③ 한 번 찍힌 것은 다시 안 바꾼다 — 안 그러면 '방금'이 영원히 '방금'이다
    _d = [_fg("g2", Status.FINAL, Score(5, 3, ScoreUnit.RUNS))]
    T._mark_first_final("ff", _d, _t2)
    check("★ 한 번 찍힌 시각은 다시 안 바뀐다 ('방금'이 영원한 방금이 되지 않는다)",
          _d[0].meta.first_final_at == T._iso(_t1), str(_d[0].meta.first_final_at))

    # ④ 디스크를 지나도 살아남는다 (어제 세운 게이트가 강제하는 바로 그것)
    T._save_games("ff", _d)
    check("★ 감지 시각이 디스크를 지나 살아온다",
          T._load_games("ff")[0].meta.first_final_at == T._iso(_t1))

    # ⑤ 취소도 종결이다 — 결과 카드가 그것도 싣는다
    _e0 = [_fg("g3", Status.SCHEDULED)]
    T._save_games("ff", _e0)
    _e = [_fg("g3", Status.CANCELED)]
    T._mark_first_final("ff", _e, _t1)
    check("취소도 종결로 보고 찍는다 (결과 카드가 취소도 싣는다)",
          _e[0].meta.first_final_at == T._iso(_t1))

    # ⑥ 옛 스냅샷(필드 없음)에서도 죽지 않는다
    _p2 = _FF / "old.json"
    _p2.write_text(json.dumps([{"league": "KBO", "season": "2026",
                                "source_key": "g9", "home": "HH", "away": "LT",
                                "start_utc": T._iso(_t0), "home_tz": "Asia/Seoul",
                                "status": "scheduled", "score": None}],
                              ensure_ascii=False), encoding="utf-8")
    _f = [_fg("g9", Status.FINAL, Score(1, 0, ScoreUnit.RUNS))]
    check("옛 스냅샷(필드 없음)에서도 전환을 알아본다",
          _no_raise(lambda: T._mark_first_final("old", _f, _t1))
          and _f[0].meta.first_final_at == T._iso(_t1),
          str(_f[0].meta.first_final_at))
finally:
    T._snap_path = _old_snap2
    shutil.rmtree(_FF, ignore_errors=True)

# ── 누락 알림 상수 ────────────────────────────────────────────
check("기록이 오래 막히면 '사라진 것'으로 올린다 (상한이 하루보다 짧다)",
      0 < T.RECORD_LOST_SECONDS <= 6 * 3600, f"{T.RECORD_LOST_SECONDS}초")
check("★ 누락 알림 유예가 상태 알림보다 짧다 (계속 사라지면 계속 말한다)",
      T.LOST_ALERT_REPEAT_SECONDS < S.ALERT_REPEAT_SECONDS,
      f"{T.LOST_ALERT_REPEAT_SECONDS} vs {S.ALERT_REPEAT_SECONDS}")

# ═════════════════════════════════════════════════════════════
print("\n★★ 경기별 발송 (v1.14) — 중복 발송 0이 유일한 합격 기준")
# ═════════════════════════════════════════════════════════════
#
# 대표님: *"한경기당 1개씩 발송 자주되어도 괜찮아, 정확한 시각에 맞춰
# 발송이 되기만하면되"* (2026-09-07)
#
# **되돌릴 수 없는 사고는 중복 발송 하나뿐이다.** 안 보낸 것은 다음 틱에
# 보내면 되지만 두 번 보낸 것은 못 되돌린다(약점 89). 그래서 이 절은
# "제대로 나가는가"보다 **"두 번 나가지 않는가"**를 먼저 본다.

_PG_DAY = "2026-08-29"
_pg_games = [
    mkgame(League.KBO, "LG", "OB", day=_PG_DAY, hh=18),
    mkgame(League.KBO, "SS", "KT", day=_PG_DAY, hh=18),   # 같은 시각 다른 경기
    mkgame(League.KBO, "HT", "NC", day=_PG_DAY, hh=14),
]
# 세 경기의 source_key가 서로 달라야 game_id도 다르다 — 여기가 무너지면 아래가 다 무의미
check("시험 표본의 경기 식별자가 서로 다르다",
      len({g.game_id for g in _pg_games}) == 3,
      str([g.game_id for g in _pg_games]))

_pg_now = datetime(2026, 8, 29, 3, 0, tzinfo=timezone.utc)     # KST 12:00
_pgq = P.build_queue(_pg_games, _pg_now, "-100test", floor_hours=0)
_kick = [i for i in _pgq if i.content_type is ContentType.KICKOFF]

# ⚠️ **2026-09-07: 킥오프가 '경기마다'에서 '같은 시각 묶음마다'로 바뀌었다.**
# 대표님 지시: *"같은시간에 시작하는 경기는, 묶어서 시작 직전 알림카드 보내자"*.
# 경기마다 한 장이면 동시 시작이 그대로 도배가 된다 —
# 유로파 18경기가 04:00에 함께 시작하고 KBO 5경기는 전부 17:00이다.
# 시험 표본은 18:30×2 + 14:30×1이라 **2묶음**이 정답이다.
_want_kick = len({C.start_alert_bucket(g) for g in _pg_games})
check(f"같은 시각 경기가 한 장으로 묶인다 ({len(_kick)}건 / 경기 {len(_pg_games)}개 "
      f"· 시각 {_want_kick}종)", len(_kick) == _want_kick,
      str([i.scope for i in _kick]))
check("★★ 킥오프 멱등키가 묶음마다 전부 다르다 (같으면 한 묶음만 나가고 나머지가 먹힌다)",
      len({i.idem_key for i in _kick}) == len(_kick),
      str(sorted(i.idem_key for i in _kick))[:200])
# **키에서 경기 식별자가 빠졌어도 중복은 여전히 구조적으로 막힌다** —
# 리그·날짜·시각이 유일하기 때문이다. 오히려 시각 고정이 더 안전하다:
# 묶음 안의 한 경기가 취소돼 내용이 바뀌어도 키가 그대로라 재발송이 안 된다.
check("★★ 킥오프 scope가 리그·날짜·시각으로 유일하다",
      all(i.scope.startswith("KBO:2026-08-29@") for i in _kick)
      and len({i.scope for i in _kick}) == len(_kick),
      str([i.scope for i in _kick]))
check("  ↳ 같은 시각 경기는 같은 묶음에 들어간다 (한 장에 다 실린다)",
      len(_kick) < len(_pg_games),
      f"묶음 {len(_kick)} vs 경기 {len(_pg_games)}")

# ═════════════════════════════════════════════════════════════
print("\n★★★ 킥오프 큐는 '상태'가 아니라 '아직 시작 안 했다'로 담는다 (v1.21)")
# ═════════════════════════════════════════════════════════════
#
# **2026-09-08·09 KBO 킥오프 누락의 뿌리다.**
# 실측(09-09 18:06 KST): KBO 소스가 18:30 경기를 시작 **24분 전**에 이미
# `LIVE`로 준다. 큐가 `status is SCHEDULED`만 담으면 킥오프 창(T-30~T-1)이
# 열리는 18:00에 담을 것이 하나도 없어 묶음이 통째로 사라지고, 큐에 흔적이
# 없으니 지각 폐기로도 안 잡힌다 — **완전히 조용한 누락**(약점 181·188).
#
# 상태는 소스가 정하지만 **시작 시각은 우리가 아는 사실이다.**

_LV_DAY = "2026-08-29"
# 18:30 시작 · 지금은 18:06(KST) — 아직 24분 남았는데 소스는 LIVE라고 한다
_lv_games = [mkgame(League.KBO, "LG", "OB", day=_LV_DAY, hh=18,
                    status=Status.LIVE),
             mkgame(League.KBO, "SS", "KT", day=_LV_DAY, hh=18,
                    status=Status.LIVE)]
_lv_now = datetime(2026, 8, 29, 9, 6, tzinfo=timezone.utc)      # KST 18:06
_lvq = P.build_queue(_lv_games, _lv_now, "-100test", floor_hours=0)
_lvk = [i for i in _lvq if i.content_type is ContentType.KICKOFF]
check("★★★ 소스가 LIVE라 해도 시작 전이면 킥오프가 큐에 담긴다 (그 사고의 재현)",
      len(_lvk) == 1, f"{len(_lvk)}건 · {[i.scope for i in _lvk]}")

# **반대쪽도 지켜야 한다** — 이미 시작한 경기에 '곧 시작'을 보내면 거짓말이다.
_lv_after = datetime(2026, 8, 29, 9, 40, tzinfo=timezone.utc)   # KST 18:40
_lvq2 = P.build_queue(_lv_games, _lv_after, "-100test", floor_hours=0)
check("  ↳ 이미 시작한 뒤에는 담지 않는다 ('곧 시작'이 거짓이 되면 안 된다)",
      not [i for i in _lvq2 if i.content_type is ContentType.KICKOFF])

# 종결(종료·취소·연기)은 시작 전이어도 담지 않는다
_lv_fin = [mkgame(League.KBO, "HT", "NC", day=_LV_DAY, hh=18,
                  status=Status.CANCELED, cancel="우천")]
check("  ↳ 취소·연기된 경기는 담지 않는다",
      not [i for i in P.build_queue(_lv_fin, _lv_now, "-100test", floor_hours=0)
           if i.content_type is ContentType.KICKOFF])

# ── 변이시험 — 옛 조건으로 되돌리면 이 검사가 **반드시** 실패해야 한다 ──
#
# 안 그러면 이 검사는 통과해도 아무것도 증명하지 않는다(약점 62·106).
_lv_mut = [g for g in _lv_games if g.status is Status.SCHEDULED]   # 옛 조건 재현
check("★★ 변이시험 — 옛 조건(status is SCHEDULED)으로는 묶음이 0건이 된다",
      not _lv_mut,
      "옛 조건이라면 담을 경기가 없다 = 그날의 침묵이 그대로 재현된다")

# ⚠️ **의무 대조는 이 조건을 쓰지 않는다** — 검사 대상의 조건을 검사에
# 재사용하면 검사가 자기 자신을 통과시킨다(약점 181). 그래서 큐가 또 틀려도
# 의무 쪽은 여전히 그 경기를 의무로 센다.
check("★★★ 의무 분모는 상태와 무관하다 (큐가 또 틀려도 감시가 잡는다)",
      len(C.kickoff_duty_groups(_lv_games)) == 1
      and len(C.kickoff_duty_groups(
          [mkgame(League.KBO, "LG", "OB", day=_LV_DAY, hh=18,
                  status=Status.SCHEDULED)])) == 1,
      "LIVE·SCHEDULED 어느 쪽이든 의무 1건")

# ═════════════════════════════════════════════════════════════
print("\n★★★ 리그·날짜 단위 다섯 종도 사라지면 알린다 (v1.22)")
# ═════════════════════════════════════════════════════════════
#
# 대표님 질문: *"모든 리그와 모든 경기가 누락없이 발송되는거지?"*
# 그때까지 의무 대조는 경기 단위 3종뿐이었고 나머지 다섯은 **사라져도
# 아무도 모르는 상태**였다. 이제 여덟 종 전부가 감시 안에 들어온다.
#
# ⚠️ **잡는 것은 '그날 통째로 0장'이다.** 부분 누락(3장 중 1장)은 못 잡는다 —
# 장수를 계약이 다시 계산하면 큐와 갈라진다(약점 45·181). 지킬 수 있는
# 정밀도로만 약속한다(약점 108).

_DD_DAY = "2026-08-29"
_dd_games = [mkgame(League.KBO, "LG", "OB", day=_DD_DAY, hh=18),
             mkgame(League.KBO, "SS", "KT", day=_DD_DAY, hh=18),
             mkgame(League.KBO, "HT", "NC", day=_DD_DAY, hh=14)]
_dd_last = max(g.start_utc for g in _dd_games)


def _dd_at(ct, hours, keys=()):
    return C.unqueued_per_day(ct, _dd_games, list(keys),
                              _dd_last + timedelta(hours=hours))


for _ct, _nm in ((ContentType.MORNING, "경기 예고"),
                 (ContentType.ANALYSIS, "경기 분석"),
                 (ContentType.LEAGUE_RESULT, "정리판"),
                 (ContentType.STANDINGS, "순위표"),
                 (ContentType.LEADERBOARD, "리더보드")):
    _g = _GS[_ct] / 3600
    # 정리판은 끝난 경기가 있어야 의무가 생긴다 — 이 표본은 전부 예정이라 면제다
    if _ct is ContentType.LEAGUE_RESULT:
        check(f"  ↳ [{_nm}] 끝난 경기가 하나도 없으면 의무가 없다 (정리할 것이 없다)",
              not _dd_at(_ct, _g + 12))
        continue
    check(f"★★ [{_nm}] 그날 한 장도 안 나갔으면 신고한다 (유예 {_g:.0f}시간 뒤)",
          len(_dd_at(_ct, _g + 12)) == 1, str(_dd_at(_ct, _g + 12)))
    check(f"  ↳ [{_nm}] 유예 안에서는 조용하다 (아직 나갈 시간이 남았다)",
          not _dd_at(_ct, _g - 0.5))
    # 대장에 한 장이라도 있으면 조용하다 — scope 모양이 콘텐츠마다 달라도 맞아야 한다
    for _sfx in ("", "#0", "#08-29 18:30"):
        _k = C.idem_key("-100test", _ct, f"KBO:{_DD_DAY}{_sfx}")
        check(f"  ↳ [{_nm}] 대장에 있으면 조용하다 (scope 꼬리 {_sfx!r})",
              not _dd_at(_ct, _g + 12, [_k]))

# ── 변이시험 — 유예 조건을 빼면 아직 나갈 시간이 남은 것까지 신고된다 ──
check("★★ (변이) 유예를 0으로 두면 경기 시작 직후부터 신고된다 (오탐의 모양)",
      len(C.unqueued_per_day(ContentType.MORNING, _dd_games, [],
                             _dd_last + timedelta(minutes=1),
                             lookback_seconds=24 * 3600)) == 0
      and len(_dd_at(ContentType.MORNING, _GS[ContentType.MORNING] / 3600 + 1)) == 1,
      "유예 안에서는 0건 · 유예 뒤에는 1건")
check("  ↳ 되짚기 창(24시간)을 넘긴 옛 날짜는 세지 않는다 (도입분을 사고로 세지 않는다)",
      not _dd_at(ContentType.MORNING, 30))
check("  ↳ 취소·연기된 경기만 있는 날은 의무가 없다",
      not C.unqueued_per_day(
          ContentType.MORNING,
          [mkgame(League.KBO, "LG", "OB", day=_DD_DAY, hh=18,
                  status=Status.CANCELED, cancel="우천")], [],
          _dd_last + timedelta(hours=12)))
check("★ 날짜 단위 의무를 만들 수 없는 콘텐츠는 조용히 통과시키지 않고 막는다",
      _raises(GateError, lambda: C.unqueued_per_day(
          ContentType.KICKOFF, _dd_games, [], _dd_last)))
# 여덟 종 전부가 감시 안에 있는지 — **표를 손으로 세지 않고 계약에서 뽑는다**(약점 161)
_watched = ({ct.value for ct in (ContentType.KICKOFF, ContentType.FINAL_FLASH,
                                 ContentType.LINEUP)} | set(C.DAILY_DUTY_CONTENT))
_live = {ct.value for ct in C.QUEUED_CONTENT_TYPES
         if ct not in C.DISABLED_CONTENT_TYPES}
check("★★★ 발행 중인 콘텐츠가 전부 의무 대조 안에 있다 (모르는 누락 0)",
      _live <= _watched, f"감시 밖: {sorted(_live - _watched)}")

check("(재확인) 킥오프 의무 분모는 상태와 무관하다",
      len(C.kickoff_duty_groups(_lv_games)) == 1
      and len(C.kickoff_duty_groups(
          [mkgame(League.KBO, "LG", "OB", day=_LV_DAY, hh=18,
                  status=Status.SCHEDULED)])) == 1,
      "LIVE·SCHEDULED 어느 쪽이든 의무 1건")

# ── 예약 시각 — 창이 [T-10분, T-1분]인가 ──────────────────────
# 묶음이 된 뒤로는 `game_id`가 대표 경기일 뿐이므로, **그 묶음의 첫 경기**로 잰다.
_bucket_first = {}
for g in _pg_games:
    _k = C.start_alert_bucket(g)
    if _k not in _bucket_first or g.start_utc < _bucket_first[_k]:
        _bucket_first[_k] = g.start_utc
_lead_ok = all(
    abs((_bucket_first[i.scope] - i.scheduled_utc).total_seconds()
        - C.KICKOFF_LEAD_SECONDS) < 1 for i in _kick)
check(f"★ 킥오프 예약이 경기 시작 {C.KICKOFF_LEAD_SECONDS // 60}분 전이다", _lead_ok,
      str([(str(i.scheduled_utc), str(_bucket_first[i.scope])) for i in _kick][:1]))
check("★★ 창 끝(예약+유예)이 경기 시작보다 앞이다 — 경기 시작 이후 발송이 구조적으로 불가능",
      all(i.scheduled_utc
          + timedelta(seconds=C.GRACE_SECONDS[ContentType.KICKOFF])
          < _bucket_first[i.scope] for i in _kick))
check("킥오프에 앞창이 없다 ('10분 뒤 시작'이 일찍 나가면 거짓말)",
      C.LOOKAHEAD_SECONDS_BY_CONTENT.get(ContentType.KICKOFF) == 0)

# ── 결과 속보 — first_final_at이 찍힌 경기만 ─────────────────
_ff_games = [mkgame(League.KBO, "LG", "OB", day=_PG_DAY, hh=18,
                    status=Status.FINAL, score=Score(5, 3, ScoreUnit.RUNS)),
             mkgame(League.KBO, "SS", "KT", day=_PG_DAY, hh=18,
                    status=Status.FINAL, score=Score(2, 1, ScoreUnit.RUNS))]
_ff_at = datetime(2026, 8, 29, 12, 5, tzinfo=timezone.utc)
_ff_games[0].meta.first_final_at = T._iso(_ff_at)
_ffq = [i for i in P.build_queue(_ff_games, _ff_at + timedelta(minutes=2),
                                 "-100test", floor_hours=0)
        if i.content_type is ContentType.FINAL_FLASH]
check("★ 종료를 알아챈 경기만 속보가 잡힌다 (아직 안 찍힌 경기는 안 잡힌다)",
      len(_ffq) == 1 and _ffq[0].game_id == _ff_games[0].game_id,
      str([(i.game_id, str(i.scheduled_utc)) for i in _ffq]))
check("속보 예약이 '종료를 알아챈 그 시각'이다 (지어낸 종료 시각이 아니다)",
      bool(_ffq) and _ffq[0].scheduled_utc == _ff_at, str(_ffq and _ffq[0].scheduled_utc))

# ── 리그 단위 카드와 섞이지 않는가 ────────────────────────────
_all_keys = [i.idem_key for i in _pgq]
_lg_keys = [i.idem_key for i in _pgq
            if i.content_type in (ContentType.START_ALERT,
                                  ContentType.LEAGUE_RESULT)]
check("★ 리그 단위 카드(시간표·결과 요약)는 그대로 남아 있다 — 개별이 놓쳐도 누락 0",
      len(_lg_keys) >= 1, str(_lg_keys))
check("★★ 경기별 키와 리그 키가 하나도 안 겹친다 (겹치면 한쪽이 '이미 보냄'에 먹힌다)",
      not (set(i.idem_key for i in _kick) & set(_lg_keys)))

# ── 순연: 시작 시각이 바뀌면 알림을 새로 연다 ──────────────────
_post = mkgame(League.KBO, "LG", "OB", day=_PG_DAY, hh=18)
_k0 = [i for i in P.build_queue([_post], _pg_now, "-100test", floor_hours=0)
       if i.content_type is ContentType.KICKOFF]
_post.start_rev = 1
_k1 = [i for i in P.build_queue([_post], _pg_now, "-100test", floor_hours=0)
       if i.content_type is ContentType.KICKOFF]
check("★ 경기가 순연되면(start_rev 증가) 킥오프 키가 새로 열린다",
      bool(_k0) and bool(_k1) and _k0[0].idem_key != _k1[0].idem_key,
      f"{_k0 and _k0[0].idem_key} vs {_k1 and _k1[0].idem_key}")

# ── ★ 변이시험 — 깨뜨려서 잡히는지 본다 ───────────────────────
#
# 멱등키에서 경기 식별자를 빼면 같은 리그·같은 날 경기들이 **같은 키**가 된다.
# 그러면 한 경기만 나가고 나머지는 '이미 보냄'으로 조용히 먹힌다.
# 위 검사가 그것을 정말 잡는지, 일부러 그렇게 만들어 확인한다.
_mut = [C.idem_key("-100test", ContentType.KICKOFF,
                   f"{League.KBO.value}:{_PG_DAY}") for _ in _pg_games]
check("★★ (변이) 키에서 경기 식별자를 빼면 세 경기가 같은 키가 된다 — 위 검사가 이것을 잡는다",
      len(set(_mut)) == 1, str(set(_mut)))

# ── 발송량 — 폭주 차단기 안인가 ───────────────────────────────
#
# 경기별로 쪼개면 하루 발송이 14건 → 60~70건이 된다. 차단기는 10분 창에
# 걸리므로, **가장 몰리는 순간**이 상한 안인지를 본다.
# 최악은 KBO처럼 전 경기가 한 틱에 끝나는 날이다(2026-09-06 실측: 5경기 동시).
_worst_tick = len(_pg_games) + 2        # 속보 5 + 리그 요약 1 + 순위표 1
check(f"★ 가장 몰리는 틱({_worst_tick}건)이 폭주 차단기 상한({C.BURST_MAX_MESSAGES}건/"
      f"{C.BURST_WINDOW_S // 60}분) 안이다",
      _worst_tick < C.BURST_MAX_MESSAGES, f"{_worst_tick} vs {C.BURST_MAX_MESSAGES}")

# ── 되돌리는 스위치 ───────────────────────────────────────────
check("PER_GAME_SENDING을 끄면 경기별 항목이 하나도 안 생긴다 (되돌리는 길)",
      _no_raise(lambda: None) and (lambda: (
          setattr(P, "PER_GAME_SENDING", False),
          len([i for i in P.build_queue(_pg_games, _pg_now, "-100test",
                                        floor_hours=0)
               if i.content_type in (ContentType.KICKOFF,
                                     ContentType.FINAL_FLASH)]) == 0,
          setattr(P, "PER_GAME_SENDING", True))[1])())

# ── 발행에서 뺀 리그 (2026-09-07 대표님: "롤은 빼자") ──────────
check("★ 뺀 리그는 수집 대상에 아예 없다 (등록해 두면 실패 기록이 쌓이고 빨간불이 켜진다)",
      not (set(T._jobs()) & {lg.value for lg in C.DISABLED_LEAGUES}),
      str(sorted(T._jobs())))
check("판정이 한 곳에만 있다 (수집·큐·감시가 따로 판정하면 하나가 빠진다)",
      all(C.league_enabled(lg) is False for lg in C.DISABLED_LEAGUES)
      and C.league_enabled(League.KBO) is True)
check("빼도 표·색·계약은 남아 있다 (되돌리기가 한 줄이어야 하고, 옛 스냅샷이 이 리그를 참조한다)",
      all(lg in C.TEAM_NAMES and lg in C.SEASON_FORMAT_BY_LEAGUE
          for lg in C.DISABLED_LEAGUES))

# ── ★★★ 큐에조차 들어오지 못한 누락 (v1.19 — 2026-09-08 KBO 사고) ────
#
# **분모가 큐면 "큐가 못 만든 것"은 영원히 안 보인다.**
# 그날 KBO 5경기(18:30)의 킥오프는 대장에 한 줄도 없었다 — 지각 폐기 8건에도
# 없었다. 즉 is_late()에 닿은 적이 없고, 그건 큐에 들어온 적이 없다는 뜻이다.
# 원인은 큐 생성부가 `status is SCHEDULED`인 경기만 담기 때문으로 좁혀졌다
# (KBO 소스는 진행 중 경기의 점수 칸을 자리표시자로 채운다 — 약점 47).
#
# 아래 검사는 **그날을 그대로 재현한다**: 상태가 SCHEDULED가 아니고,
# 대장이 비어 있고, 창이 지난 상태.
_UQ_DAY = "2026-09-08"


class _UqG:
    """의무 계산에 필요한 최소 계약. **가짜를 계약보다 좁게 만들지 않는다**(약점 172)."""

    def __init__(self, gid, hhmm, status, day=_UQ_DAY, league=League.KBO):
        self.game_id = gid
        self.league = league
        self.sports_day = day
        self.status = status
        self.start_utc = datetime.fromisoformat(f"{day}T{hhmm}:00+09:00")
        self.start_kst = self.start_utc.astimezone(C.KST)


_uq_live = [_UqG(f"g{i}", "18:30", Status.LIVE) for i in range(5)]
_uq_at = _uq_live[0].start_utc - timedelta(seconds=C.KICKOFF_LEAD_SECONDS)
_uq_now = _uq_at + timedelta(seconds=C.GRACE_SECONDS[ContentType.KICKOFF] + 60)
_uq_key = C.start_alert_bucket(_uq_live[0])

check("★★★ 그날 사고를 재현하면 잡힌다 — 상태가 '예정'이 아니라 큐에 못 담긴 킥오프",
      [k for k, _ in C.unqueued_kickoffs(_uq_live, [], _uq_now)] == [_uq_key],
      str(C.unqueued_kickoffs(_uq_live, [], _uq_now)))
check("  ↳ 대장에 그 묶음의 결과가 있으면 조용하다 (발송된 것을 누락으로 신고하지 않는다)",
      C.unqueued_kickoffs(
          _uq_live,
          [C.idem_key("-100test", ContentType.KICKOFF, _uq_key)],
          _uq_now) == [])
check("  ↳ 아직 창 안이면 조용하다 (사라진 게 아니다)",
      C.unqueued_kickoffs(_uq_live, [], _uq_at + timedelta(seconds=60)) == [])
check("  ↳ 취소된 경기에는 의무가 없다",
      C.unqueued_kickoffs(
          [_UqG(f"c{i}", "18:30", Status.CANCELED) for i in range(5)],
          [], _uq_now) == [])
check(f"  ↳ 되짚기 창({C.DUTY_LOOKBACK_SECONDS // 3600}시간)을 넘으면 매일 다시 알리지 않는다",
      C.unqueued_kickoffs(
          _uq_live, [],
          _uq_now + timedelta(seconds=C.DUTY_LOOKBACK_SECONDS + 60)) == [])
check("  ↳ 묶는 키가 큐와 **같은 함수**다 (달라지면 정상 발송을 누락으로 신고한다)",
      set(C.kickoff_duties(_uq_live)) == {C.start_alert_bucket(g)
                                          for g in _uq_live})
check("  ↳ 되돌리는 길이 한 줄이다 (DUTY_ALERT_ENABLED)",
      isinstance(C.DUTY_ALERT_ENABLED, bool))

# ── ★ 변이시험 — 옛 방식(분모=큐)으로 되돌리면 못 잡는다 ──────────
#
# 큐 생성부의 조건(`status is Status.SCHEDULED`)을 의무 계산에도 넣어 본다.
# 그러면 LIVE로 바뀐 경기가 통째로 빠져 의무가 0이 되고, 사고는 다시 침묵한다.
# **이것이 v1.18이 이 사고를 못 잡은 이유 그 자체다.**
_uq_mut = [g for g in _uq_live if g.status is Status.SCHEDULED]
check("★★ (변이) 큐와 같은 조건으로 분모를 만들면 의무가 0이 되어 사고가 다시 조용해진다",
      len(_uq_mut) == 0 and C.unqueued_kickoffs(_uq_mut, [], _uq_now) == [],
      f"의무 {len(_uq_mut)}건")
check("  ↳ 그래서 의무 계산은 큐의 상태 조건을 쓰지 않는다 (검사가 자기 자신을 통과시키지 않게)",
      len(C.kickoff_duties(_uq_live)) == 1)

# **정상 편성은 조용해야 한다** — 오탐 하나가 감시를 통째로 꺼뜨린다(약점 112·126).
_uq_ok = [_UqG(f"s{i}", "18:30", Status.SCHEDULED) for i in range(5)]
check("★ 예정 상태이고 아직 창 전이면 아무 말도 하지 않는다 (오탐 0)",
      C.unqueued_kickoffs(_uq_ok, [], _uq_at - timedelta(hours=2)) == [])

# ── ★★ 옛 규격으로 이미 나간 것을 누락으로 신고하지 않는다 ──────────
#
# **규격이 바뀌어도 옛 키는 대장에 남는다.** v1.15b 전에는 킥오프가 경기마다
# 한 장이라 scope가 `MLB:2026-09-07:MLB:2026:823175` 꼴이었고, 지금 의무는
# 시각 버킷(`MLB:2026-09-07@02:05`)으로 계산된다. 잇지 않으면 **정상 발송된
# 11건이 전부 누락으로 신고된다** — 실데이터(대장 156키 · 경기 164건)로 확인했고,
# 이 검사를 넣기 전에는 실제로 그렇게 났다.
_uq_old = [_UqG("MLB:2026:823175", "02:05", Status.FINAL,
                day="2026-09-07", league=League.MLB)]
_uq_oldkeys = [C.idem_key("-100test", ContentType.KICKOFF,
                          C.legacy_kickoff_scope(_uq_old[0]))]
_uq_oldnow = (_uq_old[0].start_utc
              - timedelta(seconds=C.KICKOFF_LEAD_SECONDS)
              + timedelta(seconds=C.GRACE_SECONDS[ContentType.KICKOFF] + 60))
check("★★ 옛 경기별 규격으로 나간 킥오프를 누락으로 신고하지 않는다 (실데이터 유형)",
      C.unqueued_kickoffs(_uq_old, _uq_oldkeys, _uq_oldnow) == [])
check("★★ (변이) 옛 키를 안 세면 정상 발송이 누락으로 신고된다 — 위 검사가 이것을 막는다",
      len(C.unqueued_kickoffs(_uq_old, [], _uq_oldnow)) == 1)
check("  ↳ 옛 규격 키를 만드는 함수가 계약에 있다 (두 곳에서 각자 만들면 어긋난다)",
      C.legacy_kickoff_scope(_uq_old[0])
      == f"{League.MLB.value}:2026-09-07:MLB:2026:823175")

# ── ★★★ 경기별 2종의 침묵 (v1.19b — 대표님: "응 미리 확인해두자") ────
#
# 킥오프에서 찾은 병이 종료 속보·선발 라인업에도 있는지 미리 본다.
# **침묵의 모양이 다르다** — 이 둘은 예약 시각이 데이터에서 나온다:
#   · 종료 속보  `meta.first_final_at` — "이전 스냅샷에서 열려 있던 것"을 봤을 때만 찍힌다
#   · 선발 라인업 `meta.lineup_seen_at` — 명단을 처음 본 시각
# 그 값이 없으면 큐가 아예 안 만들어지고, 흔적도 안 남는다.


class _PgMeta:
    def __init__(self, ffa=None, lineup=None, seen=None):
        self.first_final_at = ffa
        self.lineup = lineup
        self.lineup_seen_at = seen


class _PgG:
    """의무 계산에 필요한 최소 계약. **가짜를 계약보다 좁게 만들지 않는다**(약점 172)."""

    def __init__(self, gid, start_utc, status, meta=None, league=League.NPB,
                 day="2026-09-08"):
        self.game_id = gid
        self.league = league
        self.sports_day = day
        self.status = status
        self.start_utc = start_utc
        self.start_kst = start_utc.astimezone(C.KST)
        self.meta = meta or _PgMeta()

    @property
    def is_terminal(self):
        return self.status in C.TERMINAL_STATUSES


_PG_START = datetime.fromisoformat("2026-09-08T18:00:00+09:00")
_PG_GRACE = C.GRACE_SECONDS[ContentType.FINAL_FLASH]
_pg_fin = [_PgG(f"n{i}", _PG_START, Status.FINAL) for i in range(5)]
_pg_soon = _PG_START + timedelta(seconds=_PG_GRACE - 60)     # 아직 유예 안
_pg_late = _PG_START + timedelta(seconds=_PG_GRACE + 60)     # 유예 지남

check("★★★ 예약 시각이 없어도 **경기 시작 + 유예** 전에는 조용하다 (실측 오탐 유형)",
      C.unqueued_per_game(ContentType.FINAL_FLASH, _pg_fin, [], _pg_soon) == [],
      str(C.unqueued_per_game(ContentType.FINAL_FLASH, _pg_fin, [], _pg_soon)))
check("  ↳ 그 뒤에도 속보가 안 나갔으면 신고한다 (예약 시각이 없어 큐에 못 들어간 것)",
      len(C.unqueued_per_game(ContentType.FINAL_FLASH, _pg_fin, [], _pg_late)) == 5)
check("  ↳ 대장에 속보가 있으면 조용하다",
      C.unqueued_per_game(
          ContentType.FINAL_FLASH, _pg_fin,
          [C.idem_key("-100test", ContentType.FINAL_FLASH, C.game_scope(g))
           for g in _pg_fin], _pg_late) == [])
check("  ↳ 아직 안 끝난 경기에는 속보 의무가 없다",
      C.unqueued_per_game(
          ContentType.FINAL_FLASH,
          [_PgG("live", _PG_START, Status.LIVE)], [], _pg_late) == [])
check("  ↳ 취소된 경기에도 속보 의무가 없다",
      C.unqueued_per_game(
          ContentType.FINAL_FLASH,
          [_PgG("c", _PG_START, Status.CANCELED)], [], _pg_late) == [])

# 예약 시각이 **있는** 경우는 킥오프와 같은 꼴 — 창으로 판정한다.
_pg_st = _PgG("s", _PG_START, Status.FINAL,
              _PgMeta(ffa=(_PG_START + timedelta(hours=3)).isoformat()))
_pg_in = _PG_START + timedelta(hours=3, seconds=60)
_pg_out = _PG_START + timedelta(hours=3, seconds=_PG_GRACE + 60)
check("★ 예약이 있으면 창 안에서는 조용하다",
      C.unqueued_per_game(ContentType.FINAL_FLASH, [_pg_st], [], _pg_in) == [])
check("  ↳ 창이 지나면 신고한다",
      len(C.unqueued_per_game(ContentType.FINAL_FLASH, [_pg_st], [],
                              _pg_out)) == 1)

# 선발 라인업 — 명단이 없으면 **의무 자체가 없다**(재료가 세상에 없다 · 약점 142).
check("★ 명단을 못 본 경기에는 라인업 의무가 없다 ('모른다'와 '아니다'를 뭉개지 않는다)",
      C.unqueued_per_game(
          ContentType.LINEUP,
          [_PgG("l0", _PG_START, Status.SCHEDULED)], [], _pg_late) == [])
check("★★ 명단은 받았는데 본 시각이 없으면 라인업이 조용히 사라진다 — 잡는다",
      len(C.unqueued_per_game(
          ContentType.LINEUP,
          [_PgG("l1", _PG_START, Status.SCHEDULED,
                _PgMeta(lineup={"home": {}, "away": {}}))],
          [], _PG_START + timedelta(
              seconds=C.GRACE_SECONDS[ContentType.LINEUP] + 60))) == 1)

check(f"★ 되짚기 창({C.DUTY_LOOKBACK_SECONDS // 3600}시간) 밖의 옛 경기는 세지 않는다 "
      f"(리그를 새로 붙인 날의 도입분을 사고로 세지 않는다)",
      C.unqueued_per_game(
          ContentType.FINAL_FLASH, _pg_fin, [],
          _PG_START + timedelta(seconds=C.DUTY_LOOKBACK_SECONDS + 60)) == [])
check("★ 의무를 만들 수 없는 콘텐츠는 조용히 통과시키지 않고 막는다",
      _raises(C.GateError,
              lambda: C.unqueued_per_game(ContentType.MORNING, [], [],
                                          _pg_late)))

# ── ★ 변이시험 — '경기 시작 + 유예' 조건을 빼면 오탐이 난다 ──────────
#
# 이 조건이 없으면 **끝난 지 얼마 안 된 정상 경기가 전부 신고된다.**
# 실제로 그렇게 만들었다가 실데이터(NPB 5경기)에서 잡았다 — 그때 그 경기들은
# 유예 6시간이 넉넉히 남아 있었다.
check("★★ (변이) 시작+유예 조건을 빼면 아직 나갈 시간이 남은 경기가 신고된다",
      C.unqueued_per_game(ContentType.FINAL_FLASH, _pg_fin, [], _pg_soon,
                          ) == []
      and len(C.unqueued_per_game(ContentType.FINAL_FLASH, _pg_fin, [],
                                  _pg_soon, lookback_seconds=999999)) == 0,
      "되짚기를 넓혀도 유예 안이면 조용해야 한다")
check("  ↳ scope를 만드는 함수가 한 곳뿐이다 (두 곳에서 각자 만들면 어긋난다 — 약점 45)",
      C.game_scope(_pg_fin[0]) == C.legacy_kickoff_scope(_pg_fin[0]))

print(f"\n결과: {ok} PASS / {fail} FAIL")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
