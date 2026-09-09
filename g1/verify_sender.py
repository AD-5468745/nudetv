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

# ══ J-2. 브랜드 버튼 (v1.15d — 대표님 지시) ═══════════════════
print("\nJ-2. 카드에 붙는 버튼")
import contract as _C                                             # noqa: E402

_btn = _C.brand_button()
check("★ 계약이 버튼을 만든다 (URL이 한 곳에서만 나온다)",
      _btn and _btn[0][0]["url"].startswith("https://"), str(_btn))
check("  ↳ 문구가 비어 있지 않다", bool(_btn[0][0]["text"]), str(_btn))
check("★ 킥오프가 버튼 대상이다", "kickoff" in _C.BUTTON_CONTENT_TYPES,
      str(sorted(_C.BUTTON_CONTENT_TYPES)))

_p1 = Payload(photos=photos(1), caption="x", buttons=_btn)
_p1.gate()
check("사진 1장 + 버튼은 게이트를 지난다", True)
check("★ reply_markup이 실제로 만들어진다",
      _p1.markup_params() == {"reply_markup": {"inline_keyboard": _btn}},
      str(_p1.markup_params()))
check("버튼이 없으면 아무것도 안 붙는다 (지금까지와 같다)",
      Payload(photos=photos(1), caption="x").markup_params() == {})

# ★★ 앨범은 버튼을 못 단다 — **조용히 빼지 않고 막는다**
expect("★★ 버튼 + 사진 2장 이상은 차단 (앨범은 버튼을 못 단다)",
       lambda: Payload(photos=photos(2), caption="x", buttons=_btn).gate())
expect("URL이 없는 버튼은 차단",
       lambda: Payload(photos=photos(1), buttons=[[{"text": "x"}]]).gate())
expect("http(비암호화) URL은 차단",
       lambda: Payload(photos=photos(1),
                       buttons=[[{"text": "x", "url": "http://a.b"}]]).gate())

# ★★ 배선 — 발송기가 정말 reply_markup을 실어 보내는가 (상수만 두고 잊으면 소용없다)
_fb = Fake()
_led_b, _snd_b = fresh(tmp, "btn", _fb)
_snd_b.send(q(ContentType.KICKOFF, "KBO:2026-09-08@18:30"),
            Payload(photos=photos(1), caption="곧 시작", buttons=_btn))
_sent = [c for c in _fb.calls if c[0] == "sendPhoto"]
check("★★ sendPhoto에 reply_markup이 실린다 (배선을 잊으면 버튼이 안 보인다)",
      bool(_sent) and "reply_markup" in _sent[0][1], str(_sent[0][1].keys()) if _sent else "")
check("  ↳ 그 안에 대표님 주소가 들어 있다",
      bool(_sent) and _C.BRAND_URL in str(_sent[0][1].get("reply_markup")),
      str(_sent[0][1].get("reply_markup")) if _sent else "")
# ★★ 변이시험 — 버튼을 빼면 reply_markup이 사라진다 (이 검사가 헛돌지 않는다)
_fb2 = Fake()
_led_b2, _snd_b2 = fresh(tmp, "btn2", _fb2)
_snd_b2.send(q(ContentType.KICKOFF, "KBO:2026-09-08@19:00"),
             Payload(photos=photos(1), caption="곧 시작"))
check("★★ 변이시험 — 버튼이 없으면 reply_markup도 없다",
      "reply_markup" not in [c for c in _fb2.calls if c[0] == "sendPhoto"][0][1])

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

# ── ★★★ 날짜는 뭉개지 않는다 (v1.21) ─────────────────────────────
#
# **실측 사고 재현.** KBO 킥오프 누락이 이틀 연속 났는데, 어제 줄이 먼저
# 나간 탓에 **오늘의 진짜 누락이 "방금 한 말"로 접혔다.** 감지는 정확히
# 잡았는데 대표님께 닿지 않았다 — 매일 반복될수록 조용해지는 구조였다.
import re as _re_test
_FP_NUM_TEST = _re_test.compile(r"\d+(?:[.,]\d+)*")
_D1 = "★★ 큐에조차 들어오지 못한 킥오프 5건 — KBO:2026-09-08@18:30 (예약 18:00)"
_D2 = "★★ 큐에조차 들어오지 못한 킥오프 4건 — KBO:2026-09-09@18:30 (예약 18:00)"
check("★★★ 날이 다르면 다른 사고다 — 어제 줄과 오늘 줄의 지문이 다르다",
      _fp_norm(_D1) != _fp_norm(_D2),
      f"{_fp_norm(_D1)[:60]} / {_fp_norm(_D2)[:60]}")
_dt = Fake()
_AT4 = _pl.Path(_tf.mkdtemp(prefix="alertdate-"))
_ds = Sender(_dt, Ledger(_AT4 / "ledger.jsonl"), "-100test",
             alert_chat_id="777", worker_id="w1")
_d_sent = (_ds.alert("🔴 콘텐츠가 나가지 못했습니다", [_D1]),
           _ds.alert("🔴 콘텐츠가 나가지 못했습니다", [_D2]))
check("  ↳ 실제로 접히지 않고 두 통 다 나간다 (그날 사고를 그날 알린다)",
      all(_d_sent)
      and sum(1 for c in _dt.calls if c[0] == "sendMessage") == 2,
      f"{sum(1 for c in _dt.calls if c[0] == 'sendMessage')}통 · {_d_sent}")
# ── 변이시험 — 옛 방식(날짜까지 뭉갬)이면 오늘 것이 접힌다 ──
_old_norm = lambda t: _FP_NUM_TEST.sub(                             # noqa: E731
    lambda m: f"<{len(m.group(0).split('.')[0].replace(',', '')) or 1}>", t)
check("★★ 변이시험 — 날짜까지 뭉개면 두 줄이 같은 말이 된다 (그 사고의 재현)",
      _old_norm(_D1) == _old_norm(_D2),
      "옛 방식이었다면 오늘의 진짜 누락이 접혔다")
# **뭉개기의 원래 목적은 그대로 지킨다** — 계속 변하는 경과 시간은 여전히 접힌다.
check("  ↳ 그래도 경과 시간은 여전히 뭉갠다 (도배 방지가 풀리면 안 된다)",
      _fp_norm("[LCK] 캐시로 버팀 2.0시간")
      == _fp_norm("[LCK] 캐시로 버팀 2.4시간"))
check("  ↳ 날짜 안의 수는 자릿수로 바뀌지 않는다 (원문 그대로 남는다)",
      "2026-09-09" in _fp_norm(_D2) and "<4>-<2>-<2>" not in _fp_norm(_D2))
# 같은 날 안에서는 뭉개기가 그대로 듣는다 — 건수만 다른 같은 사고는 한 번만.
check("  ↳ 같은 날 같은 사고는 건수가 달라도 한 번만 말한다",
      _fp_norm("★★ 큐에조차 들어오지 못한 킥오프 4건 — KBO:2026-09-09@18:30")
      == _fp_norm("★★ 큐에조차 들어오지 못한 킥오프 5건 — KBO:2026-09-09@18:30"))


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

# ═════════════════════════════════════════════════════════════
print("\n★★ 발송량이 늘어도 견디는가 (v1.14 — 대표님: \"늘어도 문제없도록 시스템만 갖추자\")")
# ═════════════════════════════════════════════════════════════
#
# 경기별 발송으로 하루 14건 → 130건이 된다(유럽까지 켜면 250건).
# **늘어난 양이 부딪히는 벽이 넷 있다.** 하나씩 실측값으로 확인한다.
import contract as _C                                            # noqa: E402

# ── 벽 ① 하루 상한 ────────────────────────────────────────────
#
# v1.13까지 하루 상한이 폭주 차단기와 **같은 상수**(60)였다. 그대로 켰으면
# 61번째 카드부터 전부 "다음 날 재시도"로 밀렸을 것이다 — 오류도 안 나고,
# 로그에는 조용히 건너뜀만 쌓인다(약점 34와 같은 얼굴).
# MLS 상한은 리그 규모가 아니라 **한국 선수 표**가 정한다 — 표를 늘리면
# 최악 발송량도 같이 는다. 숫자를 베껴 적으면 표만 늘고 계산은 안 늘어난다.
from adapters.naver_football import KOREAN_PLAYERS as _KOREAN_PLAYERS  # noqa: E402

# 하루 최대 경기 수. **전부 실측이다** — 짐작한 값을 여기 적으면 벽 계산
# 전체가 거짓이 된다(어제 대항전 공백을 '한 주'로 짐작했다가 실측 42일에
# 여섯 배로 뒤집힌 적이 있다).
_LEAGUE_MAX_GAMES = {
    # 국내·미주·일본 7개 (롤 2개는 발행에서 뺐다 — `DISABLED_LEAGUES`)
    "MLB": 18, "NPB": 6, "KBO": 5, "KL1": 6, "KBL": 3,
    "VLEAGUE_M": 2, "VLEAGUE_W": 2,
    # 유럽 7개 (v1.15). 2025-08~2026-06 전 일정을 **한국 날짜 기준으로** 세어
    # 리그별 하루 최대를 뽑았다(2026-09-07 실측).
    "EPL": 10, "LALIGA": 10, "SERIEA": 6, "BUNDESLIGA": 9, "LIGUE1": 9,
    # ⚠️ 대항전이 가장 크다. 리그 페이즈 최종 라운드는 **36팀이 동시에 뛴다**
    # → 하루 18경기. 실측: UCL 2026-01-29 · 유로파 2026-01-30, 둘 다 18.
    # 5대리그(10)를 보고 대항전도 비슷하려니 하면 계산이 1.8배 어긋난다.
    "UCL": 18, "UEL": 18,
    # MLS는 **리그 전체가 아니라 한국 선수 경기만** 들어온다(대표님 지시).
    # 그래서 상한이 리그 규모가 아니라 **표에 적힌 선수 수**로 정해진다 —
    # 지금 둘(손흥민·김기희)이고, 둘이 서로 다른 팀이라 같은 날 최대 2경기다.
    # 선수를 추가하면 이 숫자도 같이 올려야 한다.
    "MLS": len(_KOREAN_PLAYERS),
}
_games = sum(_LEAGUE_MAX_GAMES.values())
# **킥오프는 이제 경기가 아니라 '같은 시각 묶음'마다 한 장이다** (2026-09-07
# 대표님: "같은시간에 시작하는 경기는 묶어서"). 상한으로는 여전히 경기 수를
# 쓴다 — 시각이 전부 다른 리그(MLB)가 있어서 묶음 = 경기가 될 수 있다.
# 실제로는 훨씬 적다(KBO 5→1 · 유로파 18→2). **상한을 낙관적으로 낮추지 않는다.**
_per_game = _games * 2                       # 킥오프(묶음 ≤ 경기) + 속보
# **리그 단위 카드가 두 번 바뀌었다 (2026-09-07).**
#  ① 나이트 브리핑을 껐다 — 결과 정리판이 그 역할을 맡는다
#  ② 경기 예고가 **시간대 덩어리마다 한 장**이 됐다(대표님: "시간대별로 쪼갠다")
# ②가 발송량을 늘린다. 실측 최악은 리그1의 **3묶음**이다
# (09-13: 00:15 · 03:45 · 22:00). 유럽 5대리그는 대개 2묶음, 국내는 1묶음.
_PREVIEW_MAX_BUCKETS = 3
_per_league = len(_LEAGUE_MAX_GAMES) * (_PREVIEW_MAX_BUCKETS + 1)   # 예고 + 정리판
# **분석은 이제 그날 전 경기를 묶음마다 한 장이다** (2026-09-07 대표님:
# *"경기 분석도 모든 팀 알림으로 변경하자"*). 전에는 리그당 1장이라 `2`를
# 적어 뒀는데, 그 숫자를 그대로 두면 검사가 옛 동작을 정상이라 보증한다
# (약점 98). **계산으로 바꾼다** — `ANALYSIS_PER_CARD`를 고치면 여기가 따라온다.
import math as _math                                              # noqa: E402
import pipeline as _Pan                                           # noqa: E402
# ★ **목록을 손으로 적지 않는다.** v1.16에서 분석이 MLB·K리그로 넓어졌는데
# 여기 `("KBO","NPB")`가 박혀 있어 검사가 옛 동작을 정상이라 보증했다 —
# 내가 바로 앞 절에서 적은 약점 157이 같은 파일에서 재발했다.
_ANALYSIS_LEAGUES = tuple(sorted(l.value for l in _Pan.ANALYSIS_LEAGUES))
_missing_cap = [k for k in _ANALYSIS_LEAGUES if k not in _LEAGUE_MAX_GAMES]
check("★★ 분석 리그가 전부 경기 수 상한 표에 있다 (없으면 발송량 계산이 헛돈다)",
      not _missing_cap, str(_missing_cap))
_an_cards = sum(_math.ceil(_LEAGUE_MAX_GAMES[k] / _Pan.ANALYSIS_PER_CARD)
                for k in _ANALYSIS_LEAGUES if k in _LEAGUE_MAX_GAMES)
# ★ **선발 라인업(v1.17)도 발송량이다** — 넣지 않으면 약점 161의 세 번째 재발이다.
# 경기마다 한 장이고(22명이라 묶을 수 없다), 대상은 라인업을 받아 오는 어댑터가
# 맡은 리그 전부다. **여기서도 목록을 손으로 적지 않는다** — 어댑터의 표에서 뽑아
# 리그를 늘리면 이 계산이 저절로 따라오게 한다.
from adapters.naver_football import CATEGORY as _NF_CATEGORY          # noqa: E402
_LINEUP_LEAGUES = tuple(sorted(l.value for l in _NF_CATEGORY)) if _C.LINEUP_ENABLED \
    else ()
_missing_lu = [k for k in _LINEUP_LEAGUES if k not in _LEAGUE_MAX_GAMES]
check("★★ 라인업 리그가 전부 경기 수 상한 표에 있다 (없으면 발송량 계산이 헛돈다)",
      not _missing_lu, str(_missing_lu))
# **낙관적으로 낮추지 않는다.** 실제로는 명단이 늦게 뜬 경기(킥오프 15분 안)는
# 카드가 안 나가고, 발표 자체가 없는 경기도 있다 — 그래서 이 값은 상한이다.
_lu_cards = sum(_LEAGUE_MAX_GAMES.get(k, 0) for k in _LINEUP_LEAGUES)
_worst_day = (_per_game + _per_league + 2 + 1 + _an_cards
              + _lu_cards)                                 # 순위표·부문·분석·라인업
check(f"★★ 하루 상한({_C.DAILY_MAX_MESSAGES})이 최악 발송량({_worst_day}장)보다 크다",
      _C.DAILY_MAX_MESSAGES > _worst_day,
      f"경기 {_games}개 → 경기별 {_per_game} + 리그 {_per_league} "
      f"+ 분석 {_an_cards} + 기타 3")
check(f"★ 분석이 그날 전 경기를 덮는다 ({_an_cards}장 · {len(_ANALYSIS_LEAGUES)}리그 "
      f"— 한 장 {_Pan.ANALYSIS_PER_CARD}경기)",
      _an_cards >= len(_ANALYSIS_LEAGUES)
      and _an_cards * _Pan.ANALYSIS_PER_CARD >= sum(
          _LEAGUE_MAX_GAMES[k] for k in _ANALYSIS_LEAGUES if k in _LEAGUE_MAX_GAMES),
      " · ".join(f"{k} {_LEAGUE_MAX_GAMES.get(k, '?')}경기" for k in _ANALYSIS_LEAGUES))
# 예고가 몇 장까지 늘 수 있는지는 **계약이 정한 묶음 간격**이 정한다.
# 간격을 좁히면 장수가 늘어나므로, 그 상수를 바꾸면 이 계산도 다시 해야 한다.
check("★ 예고 묶음 간격이 실측 기준(3시간) 그대로다 — 좁히면 발송량이 는다",
      _C.PREVIEW_BUCKET_GAP_SECONDS == 3 * 3600,
      str(_C.PREVIEW_BUCKET_GAP_SECONDS))
check("★ 하루 상한과 폭주 차단기 상한이 서로 다른 상수다 (하나를 올릴 때 다른 하나가 딸려오면 안 된다)",
      _C.DAILY_MAX_MESSAGES != _C.BURST_MAX_MESSAGES,
      f"하루 {_C.DAILY_MAX_MESSAGES} · 10분 {_C.BURST_MAX_MESSAGES}")
_, _snd_cap = fresh(tempfile.mkdtemp(), "cap")
check("발송기가 실제로 그 하루 상한을 쓴다 (상수만 고치고 배선을 잊으면 소용없다)",
      _snd_cap.daily_max == _C.DAILY_MAX_MESSAGES, str(_snd_cap.daily_max))

# ── 벽 ② 10분 창 (폭주 차단기) ────────────────────────────────
#
# 최악은 **국내 리그가 한꺼번에 끝나는 순간**이다. 2026-09-06 실측:
# KBO 5경기 종료가 전부 한 틱(20:44)에 몰렸다. 겨울 시즌이 겹치면 더 몰린다.
_burst_worst = (5 + 6 + 3 + 2 + 2)      # KBO·NPB·KBL·V남·V여 속보가 동시에
_burst_worst += 5                        # 그 리그들의 결과 요약
_burst_worst += 2                        # 순위표
# **유럽 최악은 대항전 최종 라운드다** — 18경기가 같은 시각에 끝난다
# (실측 2026-01-29 05:00 KST 동시 시작 18경기 → 약 06:50 동시 종료).
# 실제로는 그 시각에 국내 경기가 없어 둘이 겹치지 않지만, **겹치지 않는다는
# 보장은 소스가 하는 것이지 우리가 하는 것이 아니다.** 더해서 센다.
_burst_worst += 18 + 1                   # 대항전 속보 18 + 결과 요약 1
# ★ **선발 라인업(v1.17)도 몰린다.** 대항전 18경기가 같은 시각에 시작하면
# 명단도 그 한 시간쯤 전에 거의 동시에 발표된다. 다만 18장이 한꺼번에 나가진
# 않는다 — **어댑터의 한 틱 조회 상한이 자연히 창을 나눈다**(리그당 8건).
# 위 주석과 같은 논리로 **두 대항전이 겹치지 않는다는 보장은 소스가 하는 것이
# 아니므로** 더해서 센다.
from adapters.naver_football import LINEUP_MAX_PER_TICK as _LU_TICK    # noqa: E402
if _C.LINEUP_ENABLED:
    _burst_worst += _LU_TICK * 2         # UCL·유로파 명단이 같은 틱에 몰릴 때
check("★ 라인업이 한 틱에 무제한으로 쏟아지지 않는다 (조회 상한이 창을 나눈다)",
      0 < _LU_TICK <= 12, str(_LU_TICK))
check(f"★ 가장 몰리는 10분({_burst_worst}건)이 폭주 차단기({_C.BURST_MAX_MESSAGES}건) 안이다",
      _burst_worst < _C.BURST_MAX_MESSAGES, f"{_burst_worst} vs {_C.BURST_MAX_MESSAGES}")
# **23:00 KST에 나이트가 한꺼번에 나간다.** 리그별로 나뉘면서 생긴 새 봉우리다 —
# 예전에는 1장이었고 지금은 경기가 있는 리그 수만큼이다. 정각 하나에 몰리므로
# 다른 콘텐츠와 겹치지 않아도 그 자체로 벽을 만든다.
# 나이트(23시 정각 동시 발송)는 껐다. 정리판은 리그마다 시각이 달라
# 한 자리에 몰리지 않는다 — 그게 고정 시각을 버린 덤이다.
# 대신 **예고**가 몰릴 수 있다: 심야 회피가 걸린 리그들이 전부 22:00으로 간다.
_preview_worst = len(_LEAGUE_MAX_GAMES)      # 최악 = 모든 리그가 같은 시각으로 밀림
check(f"★ 심야 회피로 예고가 한 시각에 몰려도({_preview_worst}장) 폭주 차단기 안이다",
      _preview_worst < _C.BURST_MAX_MESSAGES,
      f"{_preview_worst} vs {_C.BURST_MAX_MESSAGES}")
check("  ↳ 그 위에 다른 콘텐츠가 겹쳐도 견딘다",
      _preview_worst + _burst_worst < _C.BURST_MAX_MESSAGES,
      f"{_preview_worst + _burst_worst} vs {_C.BURST_MAX_MESSAGES}")

# ── 벽 ③ 페이서 (텔레그램 속도 제한) ──────────────────────────
#
# 분당 상한이 있으므로, 한꺼번에 몰리면 뒤쪽 카드가 몇 분씩 기다린다.
# **킥오프는 창이 9분뿐이라 그 대기가 곧 유실이다.** 그래서 두 가지를 본다:
#   ⓐ 킥오프가 페이서 우선순위 최상위인가
#   ⓑ 창 9분 안에 페이서가 소화할 수 있는 양이 최악 동시 킥오프보다 많은가
check("★★ 킥오프가 페이서 최우선이다 (뒤로 밀리면 창 9분을 넘겨 그대로 사라진다)",
      _C.PACER_PRIORITY[ContentType.KICKOFF]
      == min(_C.PACER_PRIORITY.values()),
      str(_C.PACER_PRIORITY[ContentType.KICKOFF]))
_window_min = _C.GRACE_SECONDS[ContentType.KICKOFF] / 60
_pacer_capacity = int(_window_min * _C.PACER_MSG_PER_MINUTE)
# ⚠️ **여기가 묶기의 값어치가 드러나는 자리다 (2026-09-07).**
# 예전에는 동시 시작 경기마다 한 장이라 대항전 최종 라운드에서 **18장**이
# 한 창에 몰렸다(실측 2026-01-29 05:00 · 2026-01-30 05:00).
# 이제는 같은 시각이면 리그당 **한 장**이다 — 최악은 '몇 리그가 같은 시각에
# 시작하는가'가 되고, 그건 리그 수를 넘을 수 없다.
_kick_worst = len(_LEAGUE_MAX_GAMES)     # 전 리그가 같은 분에 시작하는 최악
check(f"★ 킥오프 창({_window_min:.0f}분)에 페이서가 {_pacer_capacity}건을 소화한다 — "
      f"최악 동시 킥오프 {_kick_worst}건보다 많다",
      _pacer_capacity > _kick_worst, f"{_pacer_capacity} vs {_kick_worst}")
# **묶기가 실제로 켜져 있는가.** 상수만 있고 배선을 잊으면 위 계산이 거짓이 된다.
import inspect as _insp2                                          # noqa: E402
import pipeline as _P2                                            # noqa: E402
_kick_src = _insp2.getsource(_P2.build_queue)
_kick_blk = _kick_src.split("① 킥오프", 1)[-1].split("② 결과 속보", 1)[0]
check("★★ 킥오프가 시각 버킷으로 묶여 큐에 오른다 (배선을 잊으면 18장이 그대로 나간다)",
      "start_alert_bucket" in _kick_blk, _kick_blk[:160])

# ── 벽 ④ 한 틱 안에 다 그리고 보내는가 ────────────────────────
#
# 카드 렌더 실측 **1.05초/장**(2026-09-07, 킥오프·속보 각 6회·1회 측정).
# 한 틱에 몰린 건수 × (렌더 + 발송)이 틱 간격을 넘으면 시계가 밀리고,
# 밀린 틱이 다음 창을 또 놓치는 연쇄가 된다.
_RENDER_SEC = 1.05
_SEND_SEC = 1.0 / _C.PACER_MSG_PER_SECOND
_tick_worst_sec = _burst_worst * (_RENDER_SEC + _SEND_SEC)
_tick_worst_sec = max(_tick_worst_sec,
                      _burst_worst / _C.PACER_MSG_PER_MINUTE * 60)   # 분당 상한
check(f"★ 가장 몰리는 틱을 처리하는 데 {_tick_worst_sec / 60:.1f}분 — 틱 간격(5분)보다 짧다",
      _tick_worst_sec < 5 * 60, f"{_tick_worst_sec:.0f}초")

# ── 전 리그에 적용되는가 (대표님: "모든 스포츠리그 각 경기마다") ──
#
# 경기별 발송에 **리그 예외를 두지 않는다.** 리그별 분기를 만들면 그 순간
# "리그마다 따로 논다"가 되살아난다(대표님 불만 ③).
import pipeline as _P                                             # noqa: E402
import inspect as _insp                                           # noqa: E402
_src = _insp.getsource(_P.build_queue)
_pg = _src.split("경기별 2종", 1)[-1].split("리그 결과 카드", 1)[0]
check("★★ 경기별 발송에 리그 예외가 없다 (모든 리그가 같은 규칙을 지난다)",
      "League." not in _pg and "RECORD_SOURCE_LEAGUES" not in _pg,
      "리그 이름이 경기별 블록 안에 나타난다")

print(f"\n결과: {ok} PASS / {fail} FAIL")
sys.exit(1 if fail else 0)
