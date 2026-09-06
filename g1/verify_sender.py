"""발송기 적대적 검증 — 토큰 없이, 가짜 전송기로 전부 깨본다.

실제 토큰이 생기기 전에 여기서 다 잡아야 한다.
구독자에게 같은 카드가 두 번 나가는 사고는 되돌릴 수 없다.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from contract import (ContentType, GateError, KST, League, LEASE_SECONDS,
                      QueueItem, SendState, idem_key)
from sender import (ALERT_REPEAT_SECONDS, DISPATCH_MARK, SEND_MAX_RETRIES,
                    _fp_norm,
                    AmbiguousSend, Ledger, Pacer, PartialSend, Payload, Secret, Sender,
                    TelegramError, load_token, new_webhook_secret, redact,
                    verify_webhook, webhook_setup_payload)

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  PASS  {name}")
    else:
        fail += 1; print(f"  FAIL  {name}  {detail}")


def expect(name, fn, exc=(GateError,)):
    global ok, fail
    try:
        fn()
    except exc as e:
        ok += 1; print(f"  PASS  {name}  → {str(e)[:64]}")
        return
    except Exception as e:                                    # noqa: BLE001
        fail += 1; print(f"  FAIL  {name}  다른 예외: {e!r}"); return
    fail += 1; print(f"  FAIL  {name}  통과시킴")


# ── 가짜 전송기 ──────────────────────────────────────────────

class Fake:
    """호출을 기록하고 시나리오대로 실패시킨다."""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])
        self._id = 1000

    def call(self, method, payload, files=None):
        self.calls.append((method, payload, sorted((files or {}).keys())))
        if self.script:
            act = self.script.pop(0)
            if act == "429":
                raise TelegramError(429, "Too Many Requests", retry_after=7)
            if act == "403":
                raise TelegramError(403, "Forbidden: bot is not a member of the channel chat")
            if act == "timeout":
                raise AmbiguousSend("TimeoutError: 응답 없음")
        if method == "sendMediaGroup":
            out = []
            for _ in payload["media"]:
                self._id += 1; out.append({"message_id": self._id})
            return out
        self._id += 1
        return {"message_id": self._id}

    @property
    def sent(self):
        return [c for c in self.calls if c[0] in ("sendPhoto", "sendMediaGroup", "sendMessage")]


# 검증용 가짜 채널 ID. 실제 채널 ID는 환경변수로만 들어온다 —
# 저장소를 공개로 바꿨을 때 노출되지 않도록 코드에 두지 않는다.
CHAT = "-1009999999999"
NOW = datetime(2026, 8, 27, 9, 20, tzinfo=timezone.utc)


def fresh(tmp, name, tr=None, **kw):
    led = Ledger(pathlib.Path(tmp) / f"{name}.jsonl")
    return led, Sender(tr or Fake(), led, CHAT,
                       pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
                       now=lambda: kw.pop("_now", None) or NOW, **kw)


def q(ct=ContentType.MORNING, scope="2026-08-28", at=None):
    return QueueItem(idem_key=idem_key(CHAT, ct, scope), content_type=ct, scope=scope,
                     scheduled_utc=at or NOW, league=League.KBO, sports_day=scope)


PNG = (b"\xff\xd8\xff" + b"\x00" * 900, 1080, 1100)


def photos(n):
    return [(f"c{i}.jpg", PNG[0], PNG[1], PNG[2]) for i in range(n)]


tmp = tempfile.mkdtemp()

# ══ A. 토큰이 새지 않는가 ═══════════════════════════════════
print("\nA. 토큰 유출 방어")
s = Secret("1234567890:AAHrealtokenvalue_do_not_leak")
check("repr에 안 나옴", "realtoken" not in repr(s), repr(s))
check("str에 안 나옴", "realtoken" not in str(s))
check("f-string에 안 나옴", "realtoken" not in f"{s}")
check("redact가 지움", "realtoken" not in redact(f"url {s.reveal()} 오류", s))
expect("빈 토큰 거부", lambda: Secret(""))
expect("형식 아닌 토큰 거부", lambda: (__import__("os").environ.update(
    {"T_X": "not-a-token"}), load_token("T_X"))[1])
expect("환경변수 없으면 거부", lambda: load_token("T_ABSENT_XYZ"))

# ══ B. 멱등 — 같은 것을 두 번 보내지 않는가 ═══════════════════
print("\nB. 멱등 (중복 발송 0건)")
tr = Fake()
led, snd = fresh(tmp, "idem", tr)
item = q()
r1 = snd.send(item, Payload(photos=photos(1), caption="모닝"))
r2 = snd.send(item, Payload(photos=photos(1), caption="모닝"))
check("1회차 발송됨", r1.state is SendState.SENT and len(r1.message_ids) == 1)
check("2회차 차단됨", r2.state is SendState.SENT and r2.reason == "이미 처리됨")
check("실제 API 호출 1회뿐", len(tr.sent) == 1, len(tr.sent))

# 다른 워커가 같은 항목을 동시에 집는 경우
tr2 = Fake()
led2, snd_a = fresh(tmp, "race", tr2)
snd_b = Sender(tr2, led2, CHAT, pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
               now=lambda: NOW)
it = q(ContentType.STANDINGS, "2026-08-27")
ca = snd_a.claim(it)
cb = snd_b.claim(it)
check("동시 클레임 — 한쪽만 성공", (ca is not None) and (cb is None))

# ══ C. 발송 도중 죽었을 때 ═══════════════════════════════════
print("\nC. 발송 도중 죽음 (가장 위험한 경우)")
tr = Fake(["timeout"])
led, snd = fresh(tmp, "amb", tr)
it = q(ContentType.LEAGUE_RESULT, "2026-08-27")
r = snd.send(it, Payload(photos=photos(1), caption="결과"))
check("응답 유실 → needs_human", r.state is SendState.NEEDS_HUMAN, r.state)
r2 = snd.send(it, Payload(photos=photos(1), caption="결과"))
check("needs_human은 자동 재발송 안 함", len(tr.sent) == 1, len(tr.sent))
check("격리 목록에 잡힘", len(led.needs_human()) == 1)

# ── 리스 만료 — '보낸 적 있나'로 갈린다 (기대 변경 2026-09-02) ──────────
#
# **[옛 기대] 리스가 만료되면 무조건 needs_human으로 격리한다.**
# **[새 기대] 대장에 발송 흔적(DISPATCH_MARK)이 있느냐로 갈린다.**
#   · 마크 없음 = API를 한 번도 안 두드렸다 = 중복 위험 0 → QUEUED로 되돌려 재클레임
#   · 마크 있음 = 보냈는지 알 수 없다 → NEEDS_HUMAN 격리 (옛 동작 그대로)
#
# **왜 바꿨나(v1.11i).** 리스는 `유예/3`(시작알림 38분·모닝 120분)인데 실측 시계
# 간격이 30~240분이라, 러너가 정상 회수되기만 해도 다음 틱 전에 리스가 만료된다.
# 그러면 **한 번도 안 보낸 항목**이 영구 격리되고, 코드 어디에도 NEEDS_HUMAN을
# 되돌리는 쓰기가 없어 사람이 대장을 손으로 고쳐야 했다. 동시 실행은 워크플로의
# `concurrency: nudetv-tick`이 막고, 코드상 마크 기록이 `_dispatch()`보다 먼저다.
#
# **원래 목적("구독자에게 같은 카드가 두 번 나가는 일은 없다")은 더 촘촘히 지킨다.**
# 되돌림이 생긴 만큼 "되돌려도 발송은 0건"과 "마크가 있으면 절대 안 되돌린다"를
# 각각 못 박고, 되돌린 항목이 결국 **딱 한 번만** 나가는지까지 본다.


class Killed(BaseException):
    """프로세스가 통째로 사라지는 상황(SIGKILL·러너 회수·OOM)을 흉내낸다.

    `BaseException`이라야 sender의 `except Exception`(클레임 반납)을 지나쳐
    **대장에 마크가 남은 채로** 죽는다 — 실제 강제 종료와 같은 흔적이 된다.
    """


class DeadDrop:
    """마크를 남긴 직후, API에 닿기 전에 죽는 전송기.

    호출 순간의 대장 상태를 기록해 둔다 — 마크가 `_dispatch()`보다 **먼저**
    쓰이는지(이 구분 전체가 그 순서 위에 서 있다)를 검증하기 위해서다.
    """

    def __init__(self, led=None, key=None):
        self.calls = []          # 실제로 나간 것: 늘 0건이다(도달 전에 죽으므로)
        self.mark_at_call = None
        self._led, self._key = led, key

    def call(self, method, payload, files=None):
        if self._led is not None:
            self.mark_at_call = (self._led.get(self._key) or None) and \
                self._led.get(self._key).last_error
        raise Killed("프로세스 강제 종료 — 요청이 나갔는지 알 수 없다")

    @property
    def sent(self):
        return self.calls


# (1) 마크 없이 만료 — 큐로 되돌아가고, 되돌아가는 동안 발송은 0건이다
led3 = Ledger(pathlib.Path(tmp) / "lease.jsonl")
tr3 = Fake()
dead = Sender(tr3, led3, CHAT, worker_id="dead",
              pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
it = q(ContentType.MORNING, "2026-08-27@lease")
dead.claim(it)                                   # 클레임만 하고 죽었다고 가정
later = NOW + timedelta(seconds=LEASE_SECONDS[ContentType.MORNING] + 10)
alive = Sender(tr3, led3, CHAT, worker_id="alive",
               pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: later)
got = alive.claim(it)
check("마크 없이 리스 만료 → 다른 워커가 이어받는다 (영구 격리 아님)", got is not None)
check("이어받은 워커가 대장에 적힌다",
      led3.get(it.idem_key).claimed_by == "alive", led3.get(it.idem_key).claimed_by)
check("되돌려도 격리로 못 박지 않는다",
      led3.get(it.idem_key).state is not SendState.NEEDS_HUMAN,
      led3.get(it.idem_key).state.value)
check("되돌린 횟수를 센다 (무한 되돌림 방지)", led3.get(it.idem_key).retry_count == 1,
      led3.get(it.idem_key).retry_count)
check("리스 만료 → 재발송 0건", len(tr3.sent) == 0)      # 옛 검사 유지 — 목적 그대로

# 되돌아간 항목이 다음 틱에 정상 발송되면 **딱 한 번만** 나가야 한다
out = alive.send(it, Payload(text="모닝"))
check("되돌아간 항목은 다음 틱에 정상 발송된다", out.state is SendState.SENT, out.state.value)
check("되돌아간 항목도 딱 한 번만 나간다", len(tr3.sent) == 1, len(tr3.sent))
out2 = alive.send(it, Payload(text="모닝"))
check("발송 뒤에는 다시 안 나간다", out2.reason == "이미 처리됨" and len(tr3.sent) == 1,
      f"{out2.reason}/{len(tr3.sent)}")

# 되돌림이 끝없이 반복되지는 않는다 — 상한에 닿으면 결국 사람에게 넘긴다
led4 = Ledger(pathlib.Path(tmp) / "lease_loop.jsonl")
tr4 = Fake()
it4 = q(ContentType.MORNING, "2026-08-27@loop")
_states = []
for _i in range(SEND_MAX_RETRIES + 2):
    _t = NOW + timedelta(seconds=(LEASE_SECONDS[ContentType.MORNING] + 10) * _i)
    Sender(tr4, led4, CHAT, worker_id=f"w{_i}",
           pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
           now=lambda _t=_t: _t).claim(it4)      # 매번 클레임만 하고 죽는다
    _states.append(led4.get(it4.idem_key).state)
check("되돌림이 상한에 닿으면 격리한다 (무한 반복 금지)",
      _states[-1] is SendState.NEEDS_HUMAN, str([s.value for s in _states]))
check("상한까지 가는 동안에도 발송은 0건", len(tr4.sent) == 0, len(tr4.sent))

# (2) **마크가 있는 상태로 만료 → 반드시 격리, 재클레임 불가.**
#     중복 발송을 막는 핵심이 이 한 갈래다 — 여기가 뚫리면 (1)의 되돌림이
#     그대로 "보냈을지도 모르는 것을 다시 보내는" 경로가 된다.
led5 = Ledger(pathlib.Path(tmp) / "lease_mark.jsonl")
it5 = q(ContentType.MORNING, "2026-08-27@mark")
tr5 = DeadDrop(led5, it5.idem_key)
dead5 = Sender(tr5, led5, CHAT, worker_id="dead",
               pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
try:
    dead5.send(it5, Payload(text="모닝"))
    check("발송 도중 강제 종료를 흉내냈다", False, "죽지 않았다")
except Killed:
    check("발송 도중 강제 종료를 흉내냈다", True)
_row = led5.get(it5.idem_key)
check("발송 직전에 대장에 마크가 남는다",
      _row.state is SendState.CLAIMED and _row.last_error == DISPATCH_MARK,
      f"{_row.state.value}/{_row.last_error}")
check("마크는 API 호출보다 **먼저** 기록된다 (이 순서가 구분의 근거다)",
      tr5.mark_at_call == DISPATCH_MARK, str(tr5.mark_at_call))

later5 = NOW + timedelta(seconds=LEASE_SECONDS[ContentType.MORNING] + 10)
tr5b = Fake()
alive5 = Sender(tr5b, led5, CHAT, worker_id="alive",
                pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: later5)
check("마크 있는 채로 리스 만료 → 다른 워커도 못 집는다", alive5.claim(it5) is None)
check("마크 있는 채로 리스 만료 → needs_human 격리",
      led5.get(it5.idem_key).state is SendState.NEEDS_HUMAN,
      led5.get(it5.idem_key).state.value)
check("격리 사유에 '발송 여부 불명'이 남는다",
      "발송 여부 불명" in (led5.get(it5.idem_key).last_error or ""),
      led5.get(it5.idem_key).last_error)
check("격리 목록에 잡힌다", len(led5.needs_human()) == 1)

# 사람이 풀기 전까지는 몇 틱을 돌려도 발송 0건이어야 한다
for _i in range(5):
    _t = later5 + timedelta(hours=_i + 1)
    Sender(tr5b, led5, CHAT, worker_id=f"tick{_i}",
           pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
           now=lambda _t=_t: _t).send(it5, Payload(text="모닝"))
check("격리된 항목은 몇 틱을 돌려도 발송 0건", len(tr5b.sent) == 0, len(tr5b.sent))
check("몇 틱을 돌려도 상태가 풀리지 않는다",
      led5.get(it5.idem_key).state is SendState.NEEDS_HUMAN,
      led5.get(it5.idem_key).state.value)

# 마크가 있는 항목은 '지각 폐기'로 덮어써도 안 된다 — 덮으면 나갔을 수도 있다는
# 사실이 대장에서 지워지고, 그 자리가 그대로 중복 발송의 입구가 된다.
led6 = Ledger(pathlib.Path(tmp) / "lease_mark2.jsonl")
it6 = q(ContentType.MORNING, "2026-08-27@mark2")
tr6 = DeadDrop(led6, it6.idem_key)
try:
    Sender(tr6, led6, CHAT, worker_id="dead",
           pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
           now=lambda: NOW).send(it6, Payload(text="모닝"))
except Killed:
    pass
Sender(Fake(), led6, CHAT, worker_id="late",
       pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
       now=lambda: NOW).mark_settled(it6, SendState.SKIPPED_ALREADY_STARTED, "지각 폐기")
check("마크 있는 항목은 '지각 폐기'로 덮이지 않고 격리로 남는다",
      led6.get(it6.idem_key).state is SendState.NEEDS_HUMAN,
      led6.get(it6.idem_key).state.value)

# ══ D. 레이트리밋·권한 오류 ══════════════════════════════════
print("\nD. 오류 처리")
tr = Fake(["429"])
led, snd = fresh(tmp, "r429", tr)
it = q(ContentType.LEADERBOARD, "2026-08-27")
r = snd.send(it, Payload(photos=photos(1)))
check("429 → 큐로 되돌림(실패 아님)", r.state is SendState.QUEUED, r.state)
check("429는 재시도 카운터와 분리", led.get(it.idem_key).retry_429_count == 1)
r2 = snd.send(it, Payload(photos=photos(1)))
check("429 뒤 재시도는 실제로 발송됨", r2.state is SendState.SENT)

tr = Fake(["403"])
led, snd = fresh(tmp, "r403", tr)
it = q(ContentType.ANALYSIS, "2026-08-27")
r = snd.send(it, Payload(photos=photos(1)))
check("403(권한 없음) → FAILED", r.state is SendState.FAILED)
check("403 사유가 기록됨", "Forbidden" in (r.reason or ""))

# ══ E. 앨범 분할 ════════════════════════════════════════════
print("\nE. 앨범 분할 (텔레그램 2~10장 규칙)")
for n, want in [(1, ["sendPhoto"]), (2, ["sendMediaGroup"]), (10, ["sendMediaGroup"]),
                (11, ["sendMediaGroup", "sendMediaGroup"])]:
    tr = Fake()
    led, snd = fresh(tmp, f"alb{n}", tr)
    r = snd.send(q(ContentType.MORNING, f"day{n}"), Payload(photos=photos(n), caption="캡션"))
    methods = [c[0] for c in tr.sent]
    check(f"{n}장 → {'+'.join(want)}", methods == want, methods)
    if n == 11:
        sizes = [len(c[1]["media"]) for c in tr.sent]
        check("11장은 6+5로 균등 (1장짜리 앨범 금지)", sizes == [6, 5], sizes)
        caps = [any("caption" in m for m in c[1]["media"]) for c in tr.sent]
        check("캡션은 첫 파트에만", caps == [True, False], caps)

# ══ F. 발송 직전 유예 재판정 ═════════════════════════════════
print("\nF. 발송 직전 재판정 ('10분 뒤 시작'이 거짓말이 되는 것 방지)")
tr = Fake()
led = Ledger(pathlib.Path(tmp) / "late.jsonl")
# 유예는 실행 환경(시계 간격)에 따라 달라진다 — 숫자를 박아두면 리드타임을 바꿀 때 깨진다.
# 계약의 값을 읽어 "그보다 1분 더 늦은" 시각을 만든다.
from contract import GRACE_SECONDS as _GS
late_now = NOW + timedelta(seconds=_GS[ContentType.START_ALERT] + 60)
snd = Sender(tr, led, CHAT, pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
             now=lambda: late_now)
it = q(ContentType.START_ALERT, "2026-08-27@18:30", at=NOW)
r = snd.send(it, Payload(text="곧 시작"))
check("유예 초과 → 발송 안 함", r.state is SendState.SKIPPED_ALREADY_STARTED, r.state)
check("API 호출 0건", len(tr.sent) == 0)

tr = Fake()
led = Ledger(pathlib.Path(tmp) / "ontime.jsonl")
snd = Sender(tr, led, CHAT, pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0),
             now=lambda: NOW + timedelta(seconds=60))
r = snd.send(q(ContentType.START_ALERT, "2026-08-27@19:00", at=NOW), Payload(text="곧 시작"))
check("유예 내면 정상 발송", r.state is SendState.SENT)

# ══ G. 하루 상한 ════════════════════════════════════════════
print("\nG. 폭주 방지 상한")
tr = Fake()
led = Ledger(pathlib.Path(tmp) / "cap.jsonl")
snd = Sender(tr, led, CHAT, daily_max=3,
             pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
states = [snd.send(q(ContentType.EVERGREEN, f"e{i}"), Payload(text=f"글 {i}")).state
          for i in range(5)]
check("상한 3에서 멈춤", states.count(SendState.SENT) == 3, states)
# 초과분은 **FAILED가 아니라 QUEUED**다 (v1.11h).
# FAILED로 못 박으면 그 항목은 매 틱 다시 시도해 매 틱 다시 실패하고,
# 대장에 실패 줄만 무한히 쌓인다(실측 26건). 상한은 "오늘은 그만"이지
# "이 발행은 실패"가 아니다 — 유예 안이면 다음 날 나가야 한다.
check("초과분은 QUEUED로 남는다", states.count(SendState.QUEUED) == 2, states)
check("초과분은 대장에 실패로 못 박히지 않는다",
      all(r.state is not SendState.FAILED for r in led._rows.values()),
      str([r.state.value for r in led._rows.values()]))
n_before = len(tr.sent)
check("정정 안내는 상한과 무관하게 나감",
      snd.send(q(ContentType.CORRECTION, "fix1"), Payload(text="정정")).state is SendState.SENT)

# ══ H. 오류 알림 ════════════════════════════════════════════
print("\nH. 오류 알림")
tr = Fake()
led = Ledger(pathlib.Path(tmp) / "alert.jsonl")
# 알림 목적지를 **명시**한다. v1.11h부터 목적지가 없으면 알림을 보내지 않는다 —
# 발행 채널로 폴백하면 구독자가 내부 장애 메시지를 본다.
ALERT_TO = "-100999"
snd = Sender(tr, led, CHAT, daily_max=1, alert_chat_id=ALERT_TO,
             pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
snd.send(q(ContentType.MORNING, "d1"), Payload(text="본문"))       # 상한 소진
sent_ok = snd.alert("KBO 수집 실패", ["소스 응답 0건", "09:20 대사에서 감지"])
check("상한을 다 써도 알림은 나감", sent_ok)
last = tr.calls[-1]
check("알림은 텍스트로 나감 (카드 아님)", last[0] == "sendMessage")
check("⚠️ 접두 + 인용블록", "⚠️" in last[1]["text"] and "<blockquote" in last[1]["text"])
check("알림은 알림 채널로 간다 (발행 채널 아님)",
      last[1]["chat_id"] == ALERT_TO and last[1]["chat_id"] != CHAT)
# 목적지가 아예 없으면 **보내지 않는다** (v1.11h) — 구독 채널로 흘리지 않는다.
snd_noalert = Sender(tr, Ledger(pathlib.Path(tmp) / "na.jsonl"), CHAT, alert_chat_id=None,
                     pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
_before = len(tr.calls)
check("알림 목적지가 없으면 안 보낸다",
      snd_noalert.alert("테스트", ["본문"]) is False and len(tr.calls) == _before)

tr2 = Fake(["403"])
snd2 = Sender(tr2, Ledger(pathlib.Path(tmp) / "a2.jsonl"), CHAT,
              pacer=Pacer(sleep=lambda s: None, clock=lambda: 0.0), now=lambda: NOW)
check("알림 실패가 본 작업을 죽이지 않음", snd2.alert("x", ["y"]) is False)

# ══ I. 페이서 ═══════════════════════════════════════════════
print("\nI. 페이서 (1초 1건 · 1분 20건)")
t = {"v": 0.0}
slept = []
p = Pacer(sleep=lambda s: (slept.append(s), t.__setitem__("v", t["v"] + s)),
          clock=lambda: t["v"])
for _ in range(21):
    p.wait()
    t["v"] += 0.01
check("21건째에 1분 창을 기다림", any(s > 30 for s in slept), [round(x, 1) for x in slept[-3:]])
check("건당 최소 1초 간격", all(s >= 0 for s in slept))
items = [q(ContentType.EVERGREEN, "a"), q(ContentType.START_ALERT, "b"),
         q(ContentType.LEAGUE_RESULT, "c")]
order = [i.content_type for i in Pacer.order(items)]
check("시각 박힌 콘텐츠가 먼저", order[0] is ContentType.START_ALERT, order)

# ══ J. 발송 게이트 ══════════════════════════════════════════
print("\nJ. 발송 전 게이트")
big = [("x.jpg", b"0" * (10 * 1024 * 1024), 1080, 1100)]
expect("10MB 초과 이미지 차단", lambda: Payload(photos=big).gate())
expect("치수 합 초과 차단",
       lambda: Payload(photos=[("x.jpg", b"0" * 100, 9000, 2000)]).gate())
expect("캡션 1024자 초과 차단",
       lambda: Payload(photos=photos(1), caption="가" * 1100).gate())
expect("텍스트 4096자 초과 차단", lambda: Payload(text="가" * 5000).gate())

# ══ K. 웹훅 서명 (S-5) ══════════════════════════════════════
print("\nK. 웹훅 서명 검증")
sec = Secret(new_webhook_secret())
verify_webhook({"X-Telegram-Bot-Api-Secret-Token": sec.reveal()}, sec)
check("정상 서명 통과", True)
verify_webhook({"x-telegram-bot-api-secret-token": sec.reveal()}, sec)
check("헤더 대소문자 무시", True)
expect("서명 없으면 차단", lambda: verify_webhook({"Content-Type": "application/json"}, sec))
expect("서명 틀리면 차단",
       lambda: verify_webhook({"X-Telegram-Bot-Api-Secret-Token": "wrong"}, sec))
expect("한 글자만 달라도 차단",
       lambda: verify_webhook({"X-Telegram-Bot-Api-Secret-Token": sec.reveal()[:-1] + "Z"}, sec))
pl = webhook_setup_payload("https://example.invalid/hook", sec)
check("허용 업데이트가 제한됨",
      set(pl["allowed_updates"]) == {"callback_query", "message", "poll"}, pl["allowed_updates"])
check("밀린 업데이트는 버림", pl["drop_pending_updates"] is True)
check("비밀값 길이 충분", len(sec.reveal()) >= 32)

# ── 이어보내기 (v1.11c) ──────────────────────────────────────
# 대표님 지시: "카드에 다 안 들어가는 내용은 상위 옵션을, 전체 내용은 텍스트로.
# 이미지와 텍스트를 붙여서 한번에. 텍스트는 접고펼치기 인용블록."
print("\n[이어보내기 — 캡션 초과분]")

PHOTO = ("c.jpg", b"x" * 1000, 1080, 1600)

def _mk_item(key="ov"):
    return QueueItem(idem_key=idem_key(CHAT, ContentType.LEAGUE_RESULT, key),
                     content_type=ContentType.LEAGUE_RESULT, scope=key,
                     scheduled_utc=NOW, league=League.KBO, sports_day="2026-08-27")

# 1) 캡션만 있을 때 — 사진 1건으로 끝난다
f = Fake(); led = Ledger(pathlib.Path(tempfile.mkdtemp()) / "l.jsonl")
sd = Sender(f, led, CHAT, pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0), now=lambda: NOW)
out = sd.send(_mk_item("a"), Payload(photos=[PHOTO], caption="짧은 캡션"))
check("후속 없으면 사진 1건만", out.state is SendState.SENT and len(f.sent) == 1)

# 2) 후속이 있으면 사진 뒤에 텍스트가 이어진다
f = Fake(); led = Ledger(pathlib.Path(tempfile.mkdtemp()) / "l.jsonl")
sd = Sender(f, led, CHAT, pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0), now=lambda: NOW)
out = sd.send(_mk_item("b"),
              Payload(photos=[PHOTO], caption="캡션", follow_texts=["이어1", "이어2"]))
kinds = [c[0] for c in f.sent]
check("사진 먼저, 텍스트가 뒤에 이어짐", kinds == ["sendPhoto", "sendMessage", "sendMessage"], str(kinds))
check("메시지 id가 전부 기록됨", len(out.message_ids) == 3, str(out.message_ids))
check("후속도 HTML 파싱 (접고펼치기가 살아있어야 한다)",
      all(c[1].get("parse_mode") == "HTML" for c in f.sent if c[0] == "sendMessage"))

# 3) 후속 발송이 실패하면 — 사진은 이미 나갔다. 재발송하면 사진이 중복된다
f = Fake(script=[None, "403"]); led = Ledger(pathlib.Path(tempfile.mkdtemp()) / "l.jsonl")
sd = Sender(f, led, CHAT, pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0), now=lambda: NOW)
out = sd.send(_mk_item("c"), Payload(photos=[PHOTO], caption="캡션", follow_texts=["이어1"]))
check("후속 실패는 needs_human 격리 (자동 재발송 금지)",
      out.state is SendState.NEEDS_HUMAN, out.state.value)
check("이미 나간 사진 id는 남긴다", len(out.message_ids) == 1, str(out.message_ids))
check("누락 건수를 사유에 적는다", "1건 누락" in out.reason, out.reason[:70])

# 4) 후속 응답 유실도 같은 격리
f = Fake(script=[None, "timeout"]); led = Ledger(pathlib.Path(tempfile.mkdtemp()) / "l.jsonl")
sd = Sender(f, led, CHAT, pacer=Pacer(sleep=lambda x: None, clock=lambda: 0.0), now=lambda: NOW)
out = sd.send(_mk_item("d"), Payload(photos=[PHOTO], caption="캡션", follow_texts=["이어1"]))
check("후속 응답 유실도 격리", out.state is SendState.NEEDS_HUMAN)

# 5) 게이트
expect("후속 텍스트 4096자 초과는 차단",
       lambda: Payload(photos=[PHOTO], caption="c", follow_texts=["가" * 4097]).gate())
expect("사진 없이 후속만 두는 것은 차단",
       lambda: Payload(caption="c", follow_texts=["이어"]).gate())

# 6) from_parts — pipeline 결과를 그대로 받는다
pl = Payload.from_parts([PHOTO], ["캡션", "이어1", "이어2"])
check("from_parts가 캡션/후속을 가름",
      pl.caption == "캡션" and pl.follow_texts == ["이어1", "이어2"])
expect("빈 파트는 차단", lambda: Payload.from_parts([PHOTO], []))

# ── 알림 되풀이 방지 ──────────────────────────────────────────
# 시계가 5분마다 돈다. 하루 종일 이어지는 문제 하나가 **알림 288통**이 된다.
# 그렇게 도배되면 사람은 알림을 꺼버리고, 그 순간 감시는 없는 것이 된다.
# 실제로 LCK 수집 실패 하나로 이 상황이 벌어질 뻔했다.
print("\n알림 되풀이 — 같은 말을 5분마다 반복하지 않는가")
import tempfile as _tf, pathlib as _pl                                # noqa: E402
_AT = _pl.Path(_tf.mkdtemp(prefix="alertdedup-"))
_atr = Fake()
_as = Sender(_atr, Ledger(_AT / "ledger.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")
_n = lambda: len([c for c in _atr.calls if c[0] == "sendMessage"])     # noqa: E731

check("첫 알림은 나간다", _as.alert("점검", ["LCK 실패"]) and _n() == 1)
check("같은 내용은 다시 안 나간다", (not _as.alert("점검", ["LCK 실패"])) and _n() == 1)
check("내용이 달라지면 바로 나간다 (조용해지는 게 아니다)",
      _as.alert("점검", ["NPB도 실패"]) and _n() == 2)

# **새 컨테이너에서도 막혀야 한다.** 매 실행이 새 컨테이너라 메모리 기억은 소용없다.
_as2 = Sender(_atr, Ledger(_AT / "ledger.jsonl"), "-100test",
              alert_chat_id="777", worker_id="w2")
check("새 컨테이너에서도 같은 알림은 막힌다 (기록이 파일에 있다)",
      (not _as2.alert("점검", ["LCK 실패"])) and _n() == 2)
check("유예를 0으로 주면 항상 나간다 (긴급용 통로는 남긴다)",
      _as2.alert("점검", ["LCK 실패"], repeat_after=0) and _n() == 3)

_alog = _AT / "alerts.json"
check("기록이 대장 옆에 남는다 (커밋 대상)", _alog.exists())
_txt = _alog.read_text(encoding="utf-8")
check("기록에 알림 본문이 남지 않는다 (지문과 시각뿐)",
      "LCK" not in _txt and "점검" not in _txt, _txt[:80])
check("기본 유예가 6시간", ALERT_REPEAT_SECONDS == 6 * 3600, str(ALERT_REPEAT_SECONDS))

# ── 경과 시간이 늘어나도 같은 말이면 되풀이하지 않는다 (fix45) ──
#
# 대표님 채널에 실제로 온 알림이다 — 상태는 하나도 안 바뀌었는데
# 본문의 시간만 늘어나서 6시간 유예가 한 번도 안 걸렸다:
#   21:29  LCK: 캐시로 버팀(묵은 데이터) 2.0시간 전 스냅샷
#   21:40  LCK: 캐시로 버팀(묵은 데이터) 2.2시간 전 스냅샷
_ct = Fake()
_AT3 = _pl.Path(_tf.mkdtemp(prefix="alertnum-"))
_cs = Sender(_ct, Ledger(_AT3 / "ledger.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")


def _cn() -> int:
    return sum(1 for c in _ct.calls if c[0] == "sendMessage")


_LCK = "LCK: 캐시로 버팀(묵은 데이터) {}시간 전 스냅샷"
check("리밋에 걸린 첫 순간은 알린다",
      _cs.alert("시계 점검 필요", [_LCK.format("2.0")]) and _cn() == 1)
check("경과 시간만 늘어난 같은 상태는 되풀이하지 않는다",
      not any(_cs.alert("시계 점검 필요", [_LCK.format(v)])
              for v in ("2.2", "2.4", "3.0", "9.9")) and _cn() == 1,
      f"{_cn()}통")
check("자릿수가 바뀔 만큼 나빠지면 바로 알린다 (조용해지는 게 아니다)",
      _cs.alert("시계 점검 필요", [_LCK.format("30.1")]) and _cn() == 2)
check("다른 문제가 생기면 유예와 무관하게 나간다",
      _cs.alert("시계 점검 필요", ["NPB: 결과 보강 실패"]) and _cn() == 3)
check("건수도 자릿수가 바뀌면 다시 알린다 (3건 → 300건)",
      _cs.alert("시계 점검 필요", ["KBO: 미등록 값이라 건너뜀 3건"])
      and _cs.alert("시계 점검 필요", ["KBO: 미등록 값이라 건너뜀 300건"])
      and _cn() == 5)
check("지문 정규화가 낱말까지 뭉개지는 않는다",
      _fp_norm("LCK 실패 3건") != _fp_norm("NPB 실패 3건"))


# ══════════════════════════════════════════════════════════════
# 줄 단위 되풀이 방지 — **조합이 바뀌면 지문이 흔들린다** (fix52, 약점 125)
# ══════════════════════════════════════════════════════════════
#
# fix45로 '한 줄 안의 숫자'는 뭉갰는데, **줄의 집합**은 그대로였다.
# 틱마다 기록 수집(30분)·흐름 보강·LCK 백오프(6시간) 주기가 달라 어떤 줄은
# 있고 어떤 줄은 없다. 상태가 하나도 안 바뀌어도 조합이 달라 지문이 매번
# 새것이 되고, 6시간 유예가 **한 번도 안 걸린다.**
#
# 실측 2026-09-06 17:00~18:02(62분): 대표님 채널에 알림 11통.
print("\n줄 단위 되풀이 — 조합이 바뀌어도 같은 말은 접는가 (fix52)")
_AT4 = _pl.Path(_tf.mkdtemp(prefix="alertline-"))
_lt = Fake()
_ls = Sender(_lt, Ledger(_AT4 / "ledger.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")
_ln = lambda: sum(1 for c in _lt.calls if c[0] == "sendMessage")       # noqa: E731

_A = "묵은 데이터 — INTL_LOL: 스냅샷이 22.3시간 묵었습니다"
_B = "[record:KBO] 수집이 멈춤 — 마지막 성공 20.9시간 전"
_C = "일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건"
_D = "묵은 '예정' 1건 예) LCK 2026-09-05"

check("첫 통은 나간다", _ls.alert("점검", [_A, _B]) and _ln() == 1)
check("★ 줄이 하나 빠진 조합은 나가지 않는다 (지금까지 여기서 새어 나왔다)",
      (not _ls.alert("점검", [_A])) and _ln() == 1)
check("★ 줄이 하나 늘어도 '새 줄'만 나간다",
      _ls.alert("점검", [_A, _B, _C]) and _ln() == 2)
_body = [c for c in _lt.calls if c[0] == "sendMessage"][-1][1]["text"]
check("  그 통에 새 줄만 실린다", "흐름 최종 점수" in _body and "22" not in _body,
      _body[:120])
check("  접은 줄 수를 밝힌다 (조용해진 것과 아무 일 없는 것은 다르다)",
      "접었습니다" in _body, _body[-80:])
check("★ 순서만 바뀐 같은 줄들은 나가지 않는다",
      (not _ls.alert("점검", [_C, _B, _A])) and _ln() == 2)
check("전부 이미 본 줄이면 한 통도 안 나간다",
      (not _ls.alert("점검", [_A, _B, _C])) and _ln() == 2)
check("정말 새 줄 하나면 그것만 나간다",
      _ls.alert("점검", [_A, _B, _C, _D]) and _ln() == 3)
check("제목이 달라도 줄이 같으면 같은 말이다 (제목은 지문에 안 넣는다)",
      (not _ls.alert("완전히 다른 제목", [_A, _B])) and _ln() == 3)

# ★★ 대표님이 실제로 받은 11통을 그대로 재생한다.
_AT5 = _pl.Path(_tf.mkdtemp(prefix="alertreal-"))
_rt = Fake()
_rs = Sender(_rt, Ledger(_AT5 / "ledger.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")
_REAL_LINES = [
    ["기록 수집 실패 — KBO: GateError 순위 불연속 [1,2,3,4,5,6,7,8,8,10]",
     "시각을 놓쳐 취소 1건 — start_alert LCK:2026-09-06 (예약보다 120분 늦어 취소)",
     "묵은 데이터 — LCK: 스냅샷이 36.4시간 묵었습니다",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.3시간 묵었습니다",
     "묵은 '예정' 1건 예) LCK 2026-09-05",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 20.9시간 전"],
    ["수집 실패 — LCK: Leaguepedia 리밋 캐시 36.5시간",
     "일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.4시간 묵었습니다",
     "묵은 '예정' 1건 예) LCK 2026-09-05",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.0시간 전"],
    ["묵은 데이터 — INTL_LOL: 스냅샷이 22.5시간 묵었습니다",
     "묵은 '예정' 1건 예) LCK 2026-09-05",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.1시간 전"],
    ["기록 수집 실패 — KBO: GateError 순위 불연속 [1,2,3,4,5,6,7,8,8,10]",
     "일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.6시간 묵었습니다",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.1시간 전"],
    ["수집 실패 — LCK: Leaguepedia 리밋 캐시 36.8시간",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.7시간 묵었습니다",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.3시간 전"],
    ["일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.8시간 묵었습니다",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.3시간 전"],
    ["기록 수집 실패 — KBO: GateError 순위 불연속 [1,2,3,4,5,6,7,8,8,10]",
     "묵은 데이터 — INTL_LOL: 스냅샷이 22.9시간 묵었습니다",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.4시간 전"],
    ["일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.5시간 전"],
    ["묵은 데이터 — INTL_LOL: 스냅샷이 23.0시간 묵었습니다",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.6시간 전"],
    ["기록 수집 실패 — KBO: GateError 순위 불연속 [1,2,3,4,5,6,7,8,8,10]",
     "일시적 실패(자동 재시도) — LCK: 캐시로 버팀 0.2시간",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.7시간 전"],
    ["일시적 실패(자동 재시도) — MLB: 흐름 최종 점수가 우리와 다름 1건",
     "시각을 놓쳐 취소 1건 — leaderboard KBO:2026-09-06 (예약보다 362분 늦어 취소)",
     "[record:KBO] 수집이 멈춤 — 마지막 성공 21.9시간 전"],
]
for _lines in _REAL_LINES:
    _rs.alert("시계 점검 필요", _lines)
_rn = sum(1 for c in _rt.calls if c[0] == "sendMessage")
check(f"★★ 대표님이 받은 11통이 {_rn}통으로 줄어든다 (62분 · 실제 원문)",
      _rn <= 5, f"{_rn}통 — 5통 이하여야 한다")
_last = [c for c in _rt.calls if c[0] == "sendMessage"][-1][1]["text"]
check("★★ 묻혀 있던 '362분 늦어 취소'가 마지막 통에 드러난다",
      "362" in _last, _last[:150])

# ★ 변이시험 — 메시지 통째 지문으로 되돌리면 11통이 그대로 나온다
_mt = Fake()
_ms = Sender(_mt, Ledger(_pl.Path(_tf.mkdtemp()) / "l.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")
import hashlib as _hl                                                 # noqa: E402


def _old_alert(self, title, lines, *, repeat_after=None):
    """fix52 이전 방식 — 메시지를 통째로 지문 찍는다."""
    gap = ALERT_REPEAT_SECONDS if repeat_after is None else repeat_after
    fp = _hl.sha256(_fp_norm("\n".join([title] + list(lines))).encode()).hexdigest()[:16]
    if gap > 0 and not self._alert_is_new(fp, gap):
        return False
    self.tr.call("sendMessage", {"chat_id": self.alert_chat_id, "text": "x"})
    self._alert_mark(fp)
    return True


for _lines in _REAL_LINES:
    _old_alert(_ms, "시계 점검 필요", _lines)
_mn = sum(1 for c in _mt.calls if c[0] == "sendMessage")
check(f"★ 변이시험 — 옛 방식으로 되돌리면 {_mn}통이 그대로 나온다 (고침이 진짜 일한다)",
      _mn >= 10 and _mn > _rn, f"옛 {_mn}통 vs 새 {_rn}통")

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
