"""텔레그램 발송기 (v1.10 신설).

이 파일은 시스템에서 유일하게 '되돌릴 수 없는 일'을 한다 — 구독자에게 글이 나간다.
그래서 다른 어떤 모듈보다 방어가 두껍다.

핵심 규칙 5가지:
  1. 토큰은 환경변수로만 받는다. 로그·예외 메시지·원장 어디에도 남기지 않는다.
  2. 2단계 커밋 — claimed(리스) → sent(message_ids 기록).
     claimed 상태로 리스가 만료된 항목은 needs_human으로 격리하고 **절대 자동 재발송하지 않는다.**
     "보냈는지 모르겠다"는 상태에서 재시도하면 구독자가 같은 카드를 두 번 본다.
  3. 발송 직전 유예 재판정 — 페이서 대기 3분이 "10분 뒤 시작"을 거짓말로 만든다.
  4. 429는 재시도 카운터에서 제외한다. 레이트리밋은 우리 잘못이 아니라 속도 문제다.
  5. 오류 알림은 상한·페이서에서 제외한다. 상한에 걸려 알림이 막히면 감시가 없는 것과 같다.
"""
from __future__ import annotations

import hashlib as _hashlib
import hmac
import json
import os
import pathlib
import random
import sys
import time
import time as _time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from contract import (channel_ref,
                      ContentType, GateError, KST, LEASE_SECONDS, PACER_PRIORITY,
                      QueueItem, REJUDGE_AT_SEND, SendMethod, SendRecord,
                      SendState, UNPLANNED_CONTENT, WEBHOOK_ALLOWED_UPDATES,
                      WEBHOOK_SECRET_HEADER, assert_sendable, esc, is_late,
                      plan_send_parts, quote)

API = "https://api.telegram.org"

# 텔레그램 레이트리밋 — 채널 기준
RATE_PER_SECOND = 1.0
RATE_PER_MINUTE = 20

# 오류 알림은 이 두 가지에서 면제된다
ALERT_EXEMPT = True

# **같은 알림을 이 시간 안에는 되풀이하지 않는다.**
# 시계가 5분마다 도니, 하루 종일 이어지는 문제 하나가 알림 288통이 된다.
# 그렇게 도배되면 사람은 알림을 꺼버리고 — 그 순간 감시는 없는 것이 된다.
# 6시간이면 하루 네 번. 문제가 살아 있다는 것을 알기에 충분하고,
# 무시하게 될 만큼 잦지는 않다. 내용이 달라지면 유예와 무관하게 바로 나간다.
ALERT_REPEAT_SECONDS = int(os.environ.get("ALERT_REPEAT_SECONDS", str(6 * 3600)))


# ─────────────────────────────────────────────────────────────
# 토큰 — 절대 값이 밖으로 나가지 않게 감싼다
# ─────────────────────────────────────────────────────────────

class Secret:
    """실수로 print/로그/예외에 찍히는 것을 막는 래퍼.

    문자열 포매팅으로 새는 경로가 가장 흔해서 __repr__/__str__을 둘 다 막는다.
    """
    __slots__ = ("_v",)

    def __init__(self, v: str) -> None:
        if not v:
            raise GateError("봇 토큰이 비어 있습니다 (환경변수 미설정)")
        self._v = v

    def reveal(self) -> str:
        return self._v

    def __repr__(self) -> str:
        return "<Secret …>"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return "<Secret …>"


def redact(text: str, secret: Optional[Secret]) -> str:
    """예외 메시지에 토큰이 섞여 들어오는 마지막 방어선."""
    if not secret:
        return text
    return text.replace(secret.reveal(), "<TOKEN>")


# ─────────────────────────────────────────────────────────────
# 원장 — 멱등키가 단일 진실 원천
# ─────────────────────────────────────────────────────────────

class Ledger:
    """멱등키 → SendRecord. 지금은 파일, G2에서 DB로 바꾼다.

    바꿔도 되는 이유는 이 클래스 밖에서 파일을 직접 읽는 코드가 없기 때문이다.
    """

    def __init__(self, path: pathlib.Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, SendRecord] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                self._rows[d["idem_key"]] = self._decode(d)

    @staticmethod
    def _decode(d: dict) -> SendRecord:
        return SendRecord(
            idem_key=d["idem_key"], state=SendState(d["state"]), chat_id=d["chat_id"],
            content_type=ContentType(d["content_type"]),
            message_ids=d.get("message_ids", []), file_ids=d.get("file_ids", []),
            sent_at_utc=(datetime.fromisoformat(d["sent_at_utc"])
                         if d.get("sent_at_utc") else None),
            claimed_by=d.get("claimed_by"),
            lease_expires_utc=(datetime.fromisoformat(d["lease_expires_utc"])
                               if d.get("lease_expires_utc") else None),
            retry_count=d.get("retry_count", 0), retry_429_count=d.get("retry_429_count", 0),
            last_error=d.get("last_error"), sent_count=d.get("sent_count", 0))

    @staticmethod
    def _encode(r: SendRecord) -> dict:
        return {"idem_key": r.idem_key, "state": r.state.value, "chat_id": r.chat_id,
                "content_type": r.content_type.value, "message_ids": r.message_ids,
                "file_ids": r.file_ids,
                "sent_at_utc": r.sent_at_utc.isoformat() if r.sent_at_utc else None,
                "claimed_by": r.claimed_by,
                "lease_expires_utc": (r.lease_expires_utc.isoformat()
                                      if r.lease_expires_utc else None),
                "retry_count": r.retry_count, "retry_429_count": r.retry_429_count,
                "last_error": r.last_error, "sent_count": r.sent_count}

    def get(self, key: str) -> Optional[SendRecord]:
        return self._rows.get(key)

    def put(self, r: SendRecord) -> None:
        """append-only. 크래시 중간에 잘린 줄이 생겨도 앞선 줄은 살아 있다."""
        self._rows[r.idem_key] = r
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self._encode(r), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())          # 크래시 시나리오의 핵심 — 버퍼에만 있으면 유실된다

    def count_sent_today(self, day_kst: str) -> int:
        n = 0
        for r in self._rows.values():
            if r.state is not SendState.SENT or not r.sent_at_utc:
                continue
            if r.content_type in UNPLANNED_CONTENT or r.content_type is ContentType.CORRECTION:
                continue
            if r.sent_at_utc.astimezone(KST).strftime("%Y-%m-%d") == day_kst:
                n += r.sent_count or 1
        return n

    def needs_human(self) -> list[SendRecord]:
        return [r for r in self._rows.values() if r.state is SendState.NEEDS_HUMAN]


# ─────────────────────────────────────────────────────────────
# 전송 계층 — 실제 HTTP는 여기 하나뿐. 테스트는 이걸 갈아 끼운다
# ─────────────────────────────────────────────────────────────

class TelegramError(Exception):
    def __init__(self, status: int, description: str, retry_after: int = 0) -> None:
        super().__init__(f"HTTP {status}: {description}")
        self.status = status
        self.description = description
        self.retry_after = retry_after


class AmbiguousSend(Exception):
    """요청은 나갔는데 응답을 못 받았다. 보냈는지 안 보냈는지 알 수 없다.

    이 예외가 뜨면 절대 재시도하지 않는다 — needs_human으로 격리한다.
    """


class PartialSend(Exception):
    """사진은 나갔는데 이어지는 텍스트를 못 보냈다.

    전체 재발송하면 **사진이 중복**된다. 그래서 재시도하지 않고 needs_human으로 격리한다.
    이미 나간 message_ids를 함께 들고 있어야 사람이 무엇이 나갔는지 볼 수 있다.
    캡션에 "나머지 N건은 이어지는 메시지에"라고 예고해두므로 채널에서도 티가 난다.
    """

    def __init__(self, sent_ids: list[int], missing: int, cause: Exception) -> None:
        super().__init__(f"사진 {len(sent_ids)}건 발송 후 후속 텍스트 {missing}건 실패: {cause}")
        self.sent_ids = sent_ids
        self.missing = missing


@dataclass
class Transport:
    """실제 텔레그램 API. 테스트에서는 FakeTransport로 교체한다."""
    token: Secret
    timeout: float = 25.0

    def call(self, method: str, payload: dict,
             files: Optional[dict[str, tuple[str, bytes]]] = None) -> dict:
        import urllib.error
        import urllib.request
        url = f"{API}/bot{self.token.reveal()}/{method}"
        if files:
            body, ctype = _multipart(payload, files)
            req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        else:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                out = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                j = json.loads(raw)
            except ValueError:
                j = {}
            raise TelegramError(e.code, redact(j.get("description", raw[:200]), self.token),
                                int(j.get("parameters", {}).get("retry_after", 0)))
        except (TimeoutError, OSError) as e:
            # 요청이 서버에 닿았는지 알 수 없다 — 재시도 금지 대상
            raise AmbiguousSend(redact(f"{e.__class__.__name__}: {e}", self.token))
        if not out.get("ok"):
            raise TelegramError(200, redact(str(out.get("description", "")), self.token))
        return out["result"]


def _multipart(fields: dict, files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        out += (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)).encode() + b"\r\n"
    for k, (fname, data) in files.items():
        out += (f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n').encode()
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


# ─────────────────────────────────────────────────────────────
# 페이서 — 1초 1건 · 1분 20건. 우선순위 낮은 숫자가 먼저
# ─────────────────────────────────────────────────────────────

class Pacer:
    def __init__(self, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sleep, self._clock = sleep, clock
        self._last = 0.0
        self._minute: list[float] = []

    def wait(self) -> float:
        waited = 0.0
        gap = self._clock() - self._last
        if gap < RATE_PER_SECOND:
            d = RATE_PER_SECOND - gap
            self._sleep(d); waited += d
        self._minute = [t for t in self._minute if self._clock() - t < 60]
        if len(self._minute) >= RATE_PER_MINUTE:
            d = 60 - (self._clock() - self._minute[0]) + 0.05
            if d > 0:
                self._sleep(d); waited += d
            self._minute = [t for t in self._minute if self._clock() - t < 60]
        self._last = self._clock()
        self._minute.append(self._last)
        return waited

    @staticmethod
    def order(items: list[QueueItem]) -> list[QueueItem]:
        return sorted(items, key=lambda i: (PACER_PRIORITY[i.content_type], i.scheduled_utc))


# ─────────────────────────────────────────────────────────────
# 발송기
# ─────────────────────────────────────────────────────────────

@dataclass
class Payload:
    """발송 1건의 내용. 카드(사진)와 텍스트를 한 형태로 다룬다."""
    text: Optional[str] = None
    photos: list[tuple[str, bytes, int, int]] = field(default_factory=list)  # (파일명, 바이트, w, h)
    caption: Optional[str] = None
    # 캡션 1024자에 다 안 들어간 나머지. 사진 뒤에 이어 보낸다 (대표님 지시:
    # "카드에 다 안 들어가는 내용은 상위 옵션을, 전체 내용은 텍스트로").
    # pipeline의 caption_*(as_parts=True)가 [캡션, 후속...]을 준다.
    follow_texts: list[str] = field(default_factory=list)

    def gate(self) -> None:
        for name, data, w, h in self.photos:
            assert_sendable(self.caption or "", w, h, len(data))
        if self.text and len(self.text) > 4096:
            raise GateError(f"텍스트 {len(self.text)}자 > 4096")
        for i, t in enumerate(self.follow_texts):
            if len(t) > 4096:
                raise GateError(f"이어지는 텍스트 {i + 1}번이 {len(t)}자 > 4096")
        if self.follow_texts and not self.photos:
            raise GateError("이어지는 텍스트는 사진이 있을 때만 쓴다 "
                            "(사진 없으면 text 하나로 보낸다)")

    @classmethod
    def from_parts(cls, photos, parts: list[str]) -> "Payload":
        """pipeline의 caption_*(as_parts=True) 결과를 그대로 받는다."""
        if not parts:
            raise GateError("캡션 파트가 비었다")
        return cls(photos=list(photos), caption=parts[0], follow_texts=list(parts[1:]))


@dataclass
class SendOutcome:
    state: SendState
    message_ids: list[int] = field(default_factory=list)
    reason: str = ""


class Sender:
    def __init__(self, transport, ledger: Ledger, chat_id: str, *,
                 worker_id: Optional[str] = None, daily_max: int = 20,
                 pacer: Optional[Pacer] = None,
                 alert_chat_id: Optional[str] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.tr = transport
        self.led = ledger
        self.chat_id = chat_id
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.daily_max = daily_max
        self.pacer = pacer or Pacer()
        # 알림 목적지. 시트의 ALERT_TARGET이 '발행 채널'이면 chat_id와 같다.
        self.alert_chat_id = alert_chat_id or chat_id
        self.now = now

    # ── 클레임 ──────────────────────────────────────────────

    def claim(self, item: QueueItem) -> Optional[SendRecord]:
        """이미 보냈거나 남이 잡고 있으면 None. 리스 만료 항목은 격리한다."""
        now = self.now()
        r = self.led.get(item.idem_key)

        if r and r.state is SendState.SENT:
            return None                                  # 멱등 — 두 번 보내지 않는다
        if r and r.state is SendState.NEEDS_HUMAN:
            return None
        if r and r.state is SendState.CLAIMED:
            if r.claimed_by == self.worker_id and r.lease_expires_utc and r.lease_expires_utc > now:
                return r                                 # 내가 잡은 것 — 이어서 진행
            if r.lease_expires_utc and r.lease_expires_utc > now:
                return None                              # 남이 작업 중
            # 리스 만료 = 발송 도중 죽었을 수 있다. 보냈는지 알 수 없으므로 재시도 금지.
            self.led.put(replace(r, state=SendState.NEEDS_HUMAN,
                                 last_error="리스 만료 — 발송 여부 불명, 자동 재발송 금지"))
            return None

        lease = LEASE_SECONDS[item.content_type]
        rec = SendRecord(idem_key=item.idem_key, state=SendState.CLAIMED,
                         # 대장은 저장소에 커밋된다 — 실제 채널 ID가 아니라 지문을 남긴다
                         # (공개 저장소에서 운영 구조가 통째로 드러나는 것을 막는다).
                         chat_id=channel_ref(self.chat_id),
                         content_type=item.content_type,
                         claimed_by=self.worker_id,
                         lease_expires_utc=now + timedelta(seconds=lease))
        self.led.put(rec)
        return rec

    # ── 발송 ────────────────────────────────────────────────

    def send(self, item: QueueItem, payload: Payload) -> SendOutcome:
        rec = self.claim(item)
        if rec is None:
            prev = self.led.get(item.idem_key)
            return SendOutcome(prev.state if prev else SendState.SUPERSEDED,
                               prev.message_ids if prev else [], "이미 처리됨")

        payload.gate()

        day = self.now().astimezone(KST).strftime("%Y-%m-%d")
        if (item.content_type not in UNPLANNED_CONTENT
                and self.led.count_sent_today(day) >= self.daily_max):
            return self._finish(rec, SendState.FAILED, [], "하루 발송 상한 도달 (폭주 방지)")

        self.pacer.wait()

        # 페이서 대기 뒤에 다시 판정한다 — 문안에 시각이 박힌 콘텐츠는 여기서 거짓말이 된다
        if item.content_type in REJUDGE_AT_SEND and is_late(
                item.scheduled_utc, self.now(), item.content_type):
            return self._finish(rec, SendState.SKIPPED_ALREADY_STARTED, [],
                                "발송 직전 재판정 — 유예 초과")

        try:
            ids = self._dispatch(payload)
        except PartialSend as e:
            # 사진은 나갔고 이어지는 텍스트가 빠졌다. 재발송하면 사진이 중복된다.
            return self._finish(rec, SendState.NEEDS_HUMAN, e.sent_ids,
                                f"부분 발송 — 이어지는 텍스트 {e.missing}건 누락: {e}")
        except AmbiguousSend as e:
            # 가장 위험한 경우. 보냈을 수도 있다 → 사람이 눈으로 확인해야 한다
            return self._finish(rec, SendState.NEEDS_HUMAN, [],
                                f"응답 유실 — 발송 여부 불명: {e}")
        except TelegramError as e:
            if e.status == 429:
                self.led.put(replace(rec, retry_429_count=rec.retry_429_count + 1,
                                     last_error=f"429 retry_after={e.retry_after}"))
                return SendOutcome(SendState.QUEUED, [], f"레이트리밋 — {e.retry_after}초 뒤 재시도")
            return self._finish(rec, SendState.FAILED, [], f"{e}")

        return self._finish(rec, SendState.SENT, ids, "")

    def _dispatch(self, p: Payload) -> list[int]:
        if not p.photos:
            res = self.tr.call("sendMessage", {
                "chat_id": self.chat_id, "text": p.text, "parse_mode": "HTML",
                "disable_web_page_preview": True})
            return [res["message_id"]]

        ids: list[int] = []
        idx = 0
        for method, n in plan_send_parts(len(p.photos)):
            chunk = p.photos[idx:idx + n]
            idx += n
            cap = p.caption if not ids else None      # 캡션은 첫 파트에만
            if method is SendMethod.PHOTO:
                name, data, _, _ = chunk[0]
                res = self.tr.call("sendPhoto",
                                   {"chat_id": self.chat_id, "parse_mode": "HTML",
                                    **({"caption": cap} if cap else {})},
                                   files={"photo": (name, data)})
                ids.append(res["message_id"])
            else:
                media, files = [], {}
                for i, (name, data, _, _) in enumerate(chunk):
                    k = f"f{i}"
                    m = {"type": "photo", "media": f"attach://{k}"}
                    if i == 0 and cap:
                        m |= {"caption": cap, "parse_mode": "HTML"}
                    media.append(m)
                    files[k] = (name, data)
                res = self.tr.call("sendMediaGroup",
                                   {"chat_id": self.chat_id, "media": media}, files=files)
                ids += [m["message_id"] for m in res]
            if idx < len(p.photos):
                self.pacer.wait()

        # 캡션에 다 못 담은 나머지를 이어 보낸다.
        # 여기서 실패하면 사진은 이미 나갔으므로 전체 재발송은 중복을 만든다 → 격리.
        for i, t in enumerate(p.follow_texts):
            self.pacer.wait()
            try:
                res = self.tr.call("sendMessage", {
                    "chat_id": self.chat_id, "text": t, "parse_mode": "HTML",
                    "disable_web_page_preview": True})
            except (TelegramError, AmbiguousSend) as e:
                raise PartialSend(ids, len(p.follow_texts) - i, e) from e
            ids.append(res["message_id"])
        return ids

    def _finish(self, rec: SendRecord, state: SendState, ids: list[int],
                reason: str) -> SendOutcome:
        self.led.put(replace(rec, state=state, message_ids=ids,
                             sent_at_utc=self.now() if state is SendState.SENT else None,
                             sent_count=len(ids), last_error=reason or None,
                             claimed_by=None, lease_expires_utc=None))
        return SendOutcome(state, ids, reason)

    # ── 오류 알림 ───────────────────────────────────────────

    def alert(self, title: str, lines: list[str], *, repeat_after: int | None = None) -> bool:
        """장애 알림. 카드가 아니라 텍스트로 낸다 — 렌더가 깨져서 난 오류일 수도 있으므로.

        상한·페이서 우선순위와 무관하게 나간다. 상한에 막히면 감시가 없는 것과 같다.

        **같은 내용은 되풀이하지 않는다 (v1.11d).**
        시계가 5분마다 돌기 때문에, 하루 종일 이어지는 문제 하나가 알림 288통이 된다.
        그렇게 도배되면 사람은 알림을 끄고, 그 순간 감시는 없는 것이 된다.
        그래서 **내용이 같으면 `repeat_after` 안에는 다시 보내지 않는다.**
        내용이 달라지면(새 문제가 생기면) 유예와 무관하게 바로 나간다 —
        조용해지는 것이 아니라 '같은 말을 반복하지 않는' 것이다.
        """
        body = f"⚠️ <b>{esc(title)}</b>\n" + quote([esc(x) for x in lines])
        gap = ALERT_REPEAT_SECONDS if repeat_after is None else repeat_after
        fp = _hashlib.sha256("\n".join([title] + list(lines)).encode()).hexdigest()[:16]
        if gap > 0 and not self._alert_is_new(fp, gap):
            return False                      # 방금 같은 말을 했다
        try:
            self.tr.call("sendMessage", {"chat_id": self.alert_chat_id, "text": body[:4096],
                                         "parse_mode": "HTML",
                                         "disable_web_page_preview": True})
            self._alert_mark(fp)
            return True
        except (TelegramError, AmbiguousSend):
            return False      # 알림 실패로 본 작업을 죽이지 않는다

    # ── 알림 되풀이 방지 ──────────────────────────────────────
    # 대장(ledger)과 같은 폴더에 작은 기록을 둔다. 실행마다 컨테이너가 새로 뜨므로
    # 메모리에 두면 아무 소용이 없다 — 매 틱이 '처음 보는 알림'으로 판단한다.

    def _alert_log_path(self) -> pathlib.Path:
        return pathlib.Path(self.led.path).parent / "alerts.json"

    def _alert_log(self) -> dict:
        p = self._alert_log_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}                         # 깨졌으면 '보낸 적 없음'과 같게 다룬다

    def _alert_is_new(self, fp: str, gap: int) -> bool:
        at = self._alert_log().get(fp)
        if not at:
            return True
        return (_time.time() - float(at)) >= gap

    def _alert_mark(self, fp: str) -> None:
        log = self._alert_log()
        log[fp] = _time.time()
        # 오래된 것은 버린다 — 파일이 무한히 자라면 그 자체가 사고다.
        cutoff = _time.time() - max(ALERT_REPEAT_SECONDS * 4, 86400)
        log = {k: v for k, v in log.items() if float(v) >= cutoff}
        p = self._alert_log_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(log), encoding="utf-8")
            tmp.replace(p)
        except OSError:
            pass                              # 기록 실패로 알림을 막지 않는다


# ─────────────────────────────────────────────────────────────
# 웹훅 서명 검증 (S-5)
# ─────────────────────────────────────────────────────────────

def verify_webhook(headers: dict, expected: Secret) -> None:
    """텔레그램이 보낸 요청인지 확인한다.

    헤더 이름은 대소문자를 가리지 않는다(WSGI/프록시가 바꾼다).
    비교는 상수 시간으로 한다 — 한 글자씩 비교하면 응답 시간으로 값을 알아낼 수 있다.
    """
    got = next((v for k, v in headers.items()
                if k.lower() == WEBHOOK_SECRET_HEADER.lower()), None)
    if got is None:
        raise GateError("웹훅: 서명 헤더 없음 — 텔레그램이 보낸 요청이 아니다")
    if not hmac.compare_digest(got, expected.reveal()):
        raise GateError("웹훅: 서명 불일치 — 위조 요청")


def webhook_setup_payload(url: str, secret: Secret) -> dict:
    return {"url": url, "secret_token": secret.reveal(),
            "allowed_updates": WEBHOOK_ALLOWED_UPDATES,
            "drop_pending_updates": True}


def new_webhook_secret() -> str:
    """텔레그램 규격: 1~256자, A-Z a-z 0-9 _ -"""
    import secrets
    return secrets.token_urlsafe(32).replace("=", "")


# ─────────────────────────────────────────────────────────────
# 토큰 로딩 — 환경변수 하나만 본다
# ─────────────────────────────────────────────────────────────

def load_token(env: str = "TELEGRAM_BOT_TOKEN") -> Secret:
    v = os.environ.get(env, "").strip()
    if not v:
        raise GateError(f"{env} 환경변수가 없습니다. 토큰은 코드·시트·문서에 두지 않습니다.")
    if ":" not in v or not v.split(":", 1)[0].isdigit():
        raise GateError(f"{env} 형식이 봇 토큰이 아닙니다 (숫자:문자열)")
    return Secret(v)
