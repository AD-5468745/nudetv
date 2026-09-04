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
import re
import sys
import threading
import time
import time as _time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from contract import (BURST_AUTO_RELEASE_S, BURST_CANARY_OBSERVE_S,
                      BURST_MAX_AUTO_RELEASES, BURST_MAX_MESSAGES,
                      BURST_WINDOW_S, channel_ref,
                      CORRECTION_DAILY_MAX, CORRECTION_MAX_PER_SCOPE,
                      CORRECTION_MIN_INTERVAL_SECONDS, CORRECTION_WINDOW_SECONDS,
                      CorrectionDecision, CorrectionSkip,
                      ContentType, GateError, KST, LEASE_SECONDS,
                      PACER_MSG_PER_MINUTE, PACER_MSG_PER_SECOND, PACER_PRIORITY,
                      QueueItem, REJUDGE_AT_SEND, SETTLED_STATES, SendMethod,
                      SendRecord, SendState, TELEGRAM_PARSE_MODE,
                      TELEGRAM_PHOTO_DIM_SUM_MAX, TELEGRAM_PHOTO_MAX_BYTES,
                      TELEGRAM_TEXT_MAX, UNPLANNED_CONTENT,
                      WEBHOOK_ALLOWED_UPDATES,
                      WEBHOOK_SECRET_HEADER, assert_sendable,
                      correction_key_from, correction_scope, decide_correction,
                      esc, is_late, plan_send_parts, quote)

API = "https://api.telegram.org"

# 텔레그램 레이트리밋 — 채널 기준.
# **계약 상수를 그대로 쓴다 (v1.11i).** 전에는 여기에 1.0 / 20 을 다시 적어 두었다.
# 같은 숫자를 두 곳에 적어두면 한쪽만 고쳐지는 날이 반드시 오고, 갈라진 순간을
# 아무도 모른다 — 실제로 `pipeline`은 계약의 TELEGRAM_TEXT_MAX를 쓰는데
# 발송기의 최종 게이트만 리터럴 4096이라, 계약을 고쳐도 발송 게이트가 안 따라왔다.
RATE_PER_SECOND = float(PACER_MSG_PER_SECOND)
RATE_PER_MINUTE = int(PACER_MSG_PER_MINUTE)

# 오류 알림은 이 두 가지에서 면제된다
ALERT_EXEMPT = True

# **재시도 상한 (v1.11i).**
# 전에는 `retry_count`가 인코딩/디코딩에만 등장하고 **증가시키는 코드가 0곳**이었다.
# FAILED로 떨어진 항목을 다음 틱이 무제한으로 다시 집었다 — 소스가 아니라
# 텔레그램이 계속 400을 주는 종류의 고장은 영원히 반복된다.
# 상한에 닿으면 격리하고 알림에 싣는다(무한 반복보다 한 번 멈추는 편이 낫다).
SEND_MAX_RETRIES = max(1, int(os.environ.get("SEND_MAX_RETRIES", "3")))

# **"API를 실제로 두드렸다"는 표시 (v1.11i).**
# 리스가 만료된 항목을 전부 NEEDS_HUMAN으로 못 박던 것이 문제였다 —
# 클레임만 하고 죽은 항목은 **한 번도 안 보냈으므로 중복 위험이 0**인데도
# 사람이 대장에 손으로 줄을 넣기 전에는 영원히 안 나갔다.
# 그래서 첫 API 호출 직전에 대장에 이 표시를 남긴다. 표시가 있으면
# "보냈는지 알 수 없음"(격리), 없으면 "클레임만 함"(큐로 되돌림)이다.
DISPATCH_MARK = "dispatching"

# **같은 알림을 이 시간 안에는 되풀이하지 않는다.**
# 시계가 5분마다 도니, 하루 종일 이어지는 문제 하나가 알림 288통이 된다.
# 그렇게 도배되면 사람은 알림을 꺼버리고 — 그 순간 감시는 없는 것이 된다.
# 6시간이면 하루 네 번. 문제가 살아 있다는 것을 알기에 충분하고,
# 무시하게 될 만큼 잦지는 않다. 내용이 달라지면 유예와 무관하게 바로 나간다.
ALERT_REPEAT_SECONDS = int(os.environ.get("ALERT_REPEAT_SECONDS", str(6 * 3600)))


# **되풀이 방지의 지문에 '계속 변하는 수'가 들어가면 방지가 통째로 풀린다 (fix45).**
#
# LCK가 Leaguepedia 리밋에 걸려 캐시로 버티는 동안 알림 본문이
# "2.0시간 전 스냅샷" → "2.2시간 전 스냅샷" → "2.4시간 …"으로 매 틱 달라졌다.
# 상태는 하나도 안 바뀌었는데 **경과 시간이 본문에 들어 있다는 이유만으로**
# 지문이 매번 새것이 되어 6시간 유예가 한 번도 걸리지 않았다.
# 정정 지문에 시각이 섞이면 매 틱 정정이 나가던 것과 같은 병이다(약점 69번).
#
# 그래서 지문은 **수를 자릿수로 뭉개서** 만든다. 같은 상태가 이어지는 동안은
# 같은 지문이고, **자릿수가 바뀔 만큼 나빠지면**(3건 → 300건, 9시간 → 30시간)
# 다른 지문이라 유예와 무관하게 바로 나간다 — 조용해지는 것이 아니라
# '같은 말을 반복하지 않는' 것이다. 본문은 그대로 실제 값을 보여준다.
_FP_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _fp_norm(text: str) -> str:
    """알림 지문용 정규화 — 수는 자릿수만 남긴다."""
    def _bucket(m: "re.Match") -> str:
        whole = m.group(0).split(".")[0].replace(",", "")
        return f"<{len(whole) or 1}>"
    return _FP_NUM.sub(_bucket, text)


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
        # **클레임을 원자적으로 만드는 자물쇠 (v1.11j).**
        # `claim()`은 "대장을 읽고 → 판단하고 → CLAIMED로 쓴다"인데 그 사이에
        # 다른 실행 흐름이 끼어들면 **둘 다 이겼다고 믿고 둘 다 보낸다**
        # (실측: 한 프로세스 8스레드에서 8건 중 3~6건이 실제로 나갔다).
        # 지금까지는 깃허브 러너가 한 프로세스·한 흐름이라 잠복해 있었지만,
        # 정정은 사고 대응 중에 여러 경로로 불릴 수 있어 중복이 곧 사고다.
        # 프로세스가 다를 때의 중복은 여전히 리스와 대장이 막는다(그쪽이 원래 설계).
        self.lock = threading.RLock()
        # **깨진 줄 하나가 전체 발행을 죽이지 않게 한다 (v1.11h).**
        # 전에는 `json.loads`가 그대로 터져 틱 전체가 죽었다(실측: 잘린 마지막 줄,
        # 필드 빠진 줄 둘 다 재현). 대장은 append-only라 앞선 줄은 여전히 유효하다.
        # **다만 조용히 넘기지 않는다** — 건너뛴 줄은 '이미 보냄'을 잃은 것이므로
        # 중복 발송 위험이다. 반드시 사람에게 올린다.
        self.broken_lines = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    self._rows[d["idem_key"]] = self._decode(d)
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    self.broken_lines += 1

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
            last_error=d.get("last_error"), sent_count=d.get("sent_count", 0),
            # ── 정정 추적 (v1.11j) ──────────────────────────────
            # **없으면 None이다.** 이 기능을 켜기 전에 나간 줄에는 이 칸이 아예 없고,
            # None은 `decide_correction`에서 '정정 대상 아님'으로 떨어진다.
            # 여기서 빈 문자열이나 기본 지문을 채워 넣으면 **옛 발송분 전체가
            # "내용이 바뀌었다"가 되어 다시 나간다.**
            revision=d.get("revision", 0),
            content_digest=d.get("content_digest"),
            origin_idem_key=d.get("origin_idem_key"),
            corrects_idem_key=d.get("corrects_idem_key"))

    @staticmethod
    def _encode(r: SendRecord) -> dict:
        out = {"idem_key": r.idem_key, "state": r.state.value, "chat_id": r.chat_id,
               "content_type": r.content_type.value, "message_ids": r.message_ids,
               "file_ids": r.file_ids,
               "sent_at_utc": r.sent_at_utc.isoformat() if r.sent_at_utc else None,
               "claimed_by": r.claimed_by,
               "lease_expires_utc": (r.lease_expires_utc.isoformat()
                                     if r.lease_expires_utc else None),
               "retry_count": r.retry_count, "retry_429_count": r.retry_429_count,
               "last_error": r.last_error, "sent_count": r.sent_count}
        # 정정 칸은 **값이 있을 때만** 적는다. 항상 적으면 지금까지의 모든 줄에
        # null 네 칸이 붙어 대장이 커지고, 지문 없는 옛 줄과 구별도 안 된다.
        # (지문은 단방향 해시라 내용 원문은 대장에 남지 않는다 — channel_ref와 같은 이유.)
        if r.revision:
            out["revision"] = r.revision
        if r.content_digest:
            out["content_digest"] = r.content_digest
        if r.origin_idem_key:
            out["origin_idem_key"] = r.origin_idem_key
        if r.corrects_idem_key:
            out["corrects_idem_key"] = r.corrects_idem_key
        return out

    def get(self, key: str) -> Optional[SendRecord]:
        return self._rows.get(key)

    def put(self, r: SendRecord) -> None:
        """append-only. 크래시 중간에 잘린 줄이 생겨도 앞선 줄은 살아 있다.

        **파일에 먼저 쓰고 메모리를 나중에 고친다 (v1.11i).**
        전에는 `self._rows[key] = r`이 파일 쓰기보다 **앞에** 있었다. 그래서
        디스크가 꽉 차거나 권한이 막혀 쓰기가 터지면, 이 프로세스만 "CLAIMED로
        바꿨다"고 믿고 다음 실행은 그 줄을 못 본다 — CLAIMED 기록이 유실된 채
        발송이 진행되면 다음 실행이 같은 것을 다시 보낸다.
        순서를 뒤집으면 쓰기가 실패했을 때 예외가 그대로 올라가고
        메모리도 옛 상태 그대로라, 최악이 '재발송'이 아니라 '미발송'이 된다.
        """
        with self.lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(self._encode(r), ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())      # 크래시 시나리오의 핵심 — 버퍼에만 있으면 유실된다
            self._rows[r.idem_key] = r

    # ── 대장 유실 감시용 지표 (v1.11i) ─────────────────────────
    # 대장이 커밋되지 않으면 다음 실행은 그 회차 발송분을 '안 보낸 것'으로 보고
    # 전량 재발송한다. 그래서 틱이 매번 이 두 숫자를 캐시에 남기고,
    # 다음 틱에 **줄어들었는지** 본다 (tick._ledger_mark 참조).
    def row_count(self) -> int:
        return len(self._rows)

    def sent_count_total(self) -> int:
        return sum(1 for r in self._rows.values() if r.state is SendState.SENT)

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

    def count_corrections_today(self, day_kst: str) -> int:
        """오늘 실제로 나간 정정 건수.

        **왜 따로 세나.** 정정은 `UNPLANNED_CONTENT`라 일반 하루 상한
        (`count_sent_today`)에서 **면제**된다. 면제만 해두면 정정에는 뚜껑이 없다 —
        폭주 차단기는 10분 60건짜리 홍수만 잡지, 하루 종일 이어지는 정정 스무 건은
        못 잡는다. 정정이 폭주하면 원래 사고보다 나쁘다.
        """
        n = 0
        for r in self._rows.values():
            if r.state is not SendState.SENT or not r.sent_at_utc:
                continue
            if r.content_type is not ContentType.CORRECTION:
                continue
            if r.sent_at_utc.astimezone(KST).strftime("%Y-%m-%d") == day_kst:
                n += 1
        return n

    def correction_chain(self, origin_idem_key: str,
                         max_revisions: int = CORRECTION_MAX_PER_SCOPE) -> list[SendRecord]:
        """원본에 달린 정정본들을 개정 번호 순으로.

        **키를 훑지 않고 계산해서 찾는다.** 정정본 키는 원본 키에서 결정적으로
        만들어지므로(`correction_key_from`), r1·r2… 를 직접 조회하면 된다.
        대장 전체를 스캔하는 방식은 줄이 수만 개가 되면 매 틱 느려지고,
        무엇보다 '같은 scope'를 문자열로 짐작해야 해서 틀리기 쉽다.

        상한보다 한 칸 더 본다 — 상한을 넘겨 만들어진(그러나 못 나간) 줄이
        있으면 다음 개정 번호가 그 줄과 충돌하기 때문이다.
        """
        out: list[SendRecord] = []
        for rev in range(1, max(1, max_revisions) + 2):
            r = self._rows.get(correction_key_from(origin_idem_key, rev))
            if r is None:
                break
            out.append(replace(r, revision=r.revision or rev))
        return out

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
# 폭주 차단기 (v1.11i 신설 — 계약에는 v1.9부터 있었는데 구현이 없었다)
#
# **왜 이제야 만드나.** BURST_WINDOW_S · BURST_MAX_MESSAGES ·
# BURST_AUTO_RELEASE_S · BURST_CANARY_OBSERVE_S · BURST_MAX_AUTO_RELEASES
# 다섯 상수가 계약에 있는데 운영 코드에서 **참조 0회**였다. 유일한 방어는
# `daily_max=60` 하나였고 그마저 도달해도 조용했다(로그·알림 어디에도 안 실렸다).
#
# **왜 파일에 남기나.** 매 실행이 새 컨테이너다. 메모리에 두면 차단이 걸린
# 그 실행에서만 유효하고 다음 실행은 아무것도 모른 채 다시 폭주한다.
# 대장 옆(state/burst.json)에 두고 워크플로가 대장과 함께 커밋한다 —
# `alerts.json`과 정확히 같은 이유다.
#
# **동작.** 10분 창에 60건을 넘기면 차단 → 30분 뒤 자동 해제하되 **1건만**
# 내보내 5분간 관찰(카나리) → 관찰을 통과해야 정상 재개.
# 자동 해제는 3회까지, 그 뒤는 사람이 풀어야 한다.
# ─────────────────────────────────────────────────────────────

class BurstBreaker:
    """폭주 차단기. 상태는 파일에 남는다 — 프로세스가 죽어도 살아남아야 뜻이 있다."""

    def __init__(self, path: pathlib.Path,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.path = path
        self.now = now

    # ── 상태 파일 ───────────────────────────────────────────
    _EMPTY = {"sends": [], "blocked_at": None, "auto_releases": 0,
              "canary_at": None, "canary_pending": False, "manual_only": False}

    def load(self) -> dict:
        st = dict(self._EMPTY)
        st["sends"] = []
        try:
            if self.path.exists():
                got = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(got, dict):
                    st.update({k: got.get(k, v) for k, v in self._EMPTY.items()})
        except (json.JSONDecodeError, OSError, TypeError):
            pass          # 깨졌으면 '차단 이력 없음'으로 다룬다 — 발송을 막지는 않는다
        st["sends"] = [float(x) for x in (st.get("sends") or [])]
        return st

    def _save(self, st: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st), encoding="utf-8")
            tmp.replace(self.path)          # 원자적 교체 — 반쪽 파일이 남지 않게
        except OSError:
            pass                            # 기록 실패로 발송을 막지는 않는다

    def _t(self) -> float:
        return self.now().timestamp()

    @staticmethod
    def _prune(st: dict, t: float) -> None:
        st["sends"] = [x for x in st["sends"] if t - x < BURST_WINDOW_S]

    # ── 판정 ────────────────────────────────────────────────

    def allow(self) -> tuple[bool, str]:
        """지금 한 건 내보내도 되는가. (허용, 사람이 읽을 사유).

        **부수효과가 있다** — 자동 해제/카나리 소비가 여기서 일어난다.
        그래서 호출자는 '정말 지금 보낼 것'이 확정된 뒤에만 부른다.
        """
        st = self.load()
        t = self._t()
        self._prune(st, t)

        if st["manual_only"]:
            return False, (f"폭주 차단(수동 해제 전용) — 자동 해제를 "
                           f"{BURST_MAX_AUTO_RELEASES}회 다 썼습니다. "
                           f"사람이 {self.path.name}을 확인·삭제해야 풀립니다")

        if st["blocked_at"]:
            left = BURST_AUTO_RELEASE_S - (t - float(st["blocked_at"]))
            if left > 0:
                return False, (f"폭주 차단 중 — {int(left // 60)}분 {int(left % 60)}초 뒤 "
                               f"자동 해제(카나리 1건)")
            if st["auto_releases"] >= BURST_MAX_AUTO_RELEASES:
                st["manual_only"] = True
                self._save(st)
                return False, (f"폭주 차단(수동 해제 전용) — 자동 해제 "
                               f"{BURST_MAX_AUTO_RELEASES}회를 다 썼습니다. "
                               f"이제부터 사람이 풀어야 합니다")
            st["auto_releases"] = int(st["auto_releases"]) + 1
            st["blocked_at"] = None
            st["canary_pending"] = True
            st["canary_at"] = None
            st["sends"] = []            # 창을 비우고 처음부터 다시 센다
            self._save(st)
            return True, (f"폭주 자동 해제 {st['auto_releases']}/{BURST_MAX_AUTO_RELEASES}회차 — "
                          f"카나리 1건만 내보내고 {BURST_CANARY_OBSERVE_S // 60}분 관찰합니다")

        if st["canary_pending"]:
            return True, "카나리 1건 (관찰용)"

        if st["canary_at"]:
            left = BURST_CANARY_OBSERVE_S - (t - float(st["canary_at"]))
            if left > 0:
                return False, (f"카나리 관찰 중 — {int(left)}초 뒤 정상 재개 "
                               f"(해제 직후 전량 재개는 폭주를 되풀이한다)")
            st["canary_at"] = None      # 관찰 통과 — 정상 재개
            self._save(st)
        return True, ""

    def record(self, n: int) -> Optional[str]:
        """실제로 나간 메시지 수를 기록. **새로** 차단이 걸리면 사유를 돌려준다."""
        if n <= 0:
            return None
        st = self.load()
        t = self._t()
        if st.get("canary_pending"):
            st["canary_pending"] = False
            st["canary_at"] = t         # 여기서부터 관찰 시작
        st["sends"] = list(st["sends"]) + [t] * int(n)
        self._prune(st, t)
        msg = None
        if len(st["sends"]) > BURST_MAX_MESSAGES and not st["blocked_at"]:
            st["blocked_at"] = t
            msg = (f"🚨 폭주 차단 발동 — {BURST_WINDOW_S // 60}분 창에 "
                   f"{len(st['sends'])}건(상한 {BURST_MAX_MESSAGES}). "
                   f"{BURST_AUTO_RELEASE_S // 60}분 뒤 카나리 1건으로 자동 해제합니다")
        self._save(st)
        return msg

    def status_line(self) -> Optional[str]:
        """지금 차단·관찰 상태면 알림에 실을 한 줄. 정상이면 None."""
        st = self.load()
        t = self._t()
        if st["manual_only"]:
            return (f"폭주 차단(수동 해제 전용) — 자동 해제 "
                    f"{st['auto_releases']}/{BURST_MAX_AUTO_RELEASES}회 소진")
        if st["blocked_at"]:
            left = max(0, BURST_AUTO_RELEASE_S - (t - float(st["blocked_at"])))
            return f"폭주 차단 중 — {int(left // 60)}분 뒤 자동 해제(카나리 1건)"
        if st["canary_pending"]:
            return "폭주 해제 대기 — 다음 1건이 카나리입니다"
        if st["canary_at"] and (t - float(st["canary_at"])) < BURST_CANARY_OBSERVE_S:
            return (f"폭주 해제 후 카나리 관찰 중 "
                    f"({int(BURST_CANARY_OBSERVE_S - (t - float(st['canary_at'])))}초 남음)")
        return None


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

    # **정정본을 원본에 답장으로 단다 (v1.11j).**
    # 정정은 원본을 '대체'하지 못한다 — 텔레그램에서 이미 읽힌 메시지를 되돌릴 방법은
    # 없고, 편집(editMessageMedia)은 알림을 안 띄워 아무도 정정을 못 본다.
    # 그래서 **새 메시지**로 내보내되, 원본에 답장으로 달아 구독자가 '무엇의 정정인지'를
    # 스크롤 없이 알게 한다. 원본 message_id는 대장에 있다.
    reply_to_message_id: Optional[int] = None

    def reply_params(self) -> dict:
        """텔레그램 답장 파라미터. 답장 대상이 없으면 빈 dict.

        `allow_sending_without_reply=True`가 핵심이다 — 원본이 지워졌거나 48시간
        보관 창을 넘겼을 때 이것이 없으면 API가 400을 주고 **정정이 통째로 못 나간다.**
        정정은 답장으로 붙는 편이 낫지만, 붙지 못한다고 안 내보내는 것은 더 나쁘다.
        """
        if not self.reply_to_message_id:
            return {}
        return {"reply_parameters": {"message_id": int(self.reply_to_message_id),
                                     "allow_sending_without_reply": True}}

    def gate(self) -> None:
        for name, data, w, h in self.photos:
            assert_sendable(self.caption or "", w, h, len(data))
            # **API 하드 상한도 여기서 직접 본다 (v1.11i).**
            # assert_sendable이 보는 것은 자체 여유값(GATE_PHOTO_*: 9MB·9500)이다.
            # 누군가 여유값을 넓히면 텔레그램 하드 상한(10MB·10000)을 넘긴 채
            # 게이트를 통과하고, 그때는 API가 400으로 되받아 발송이 실패한다.
            # 하드 상한은 우리가 정하는 값이 아니므로 별도로 못 박는다.
            if len(data) > TELEGRAM_PHOTO_MAX_BYTES:
                raise GateError(f"이미지 {len(data)}B > 텔레그램 하드 상한 "
                                f"{TELEGRAM_PHOTO_MAX_BYTES}")
            if w + h > TELEGRAM_PHOTO_DIM_SUM_MAX:
                raise GateError(f"width+height {w + h} > 텔레그램 하드 상한 "
                                f"{TELEGRAM_PHOTO_DIM_SUM_MAX}")
        if self.text and len(self.text) > TELEGRAM_TEXT_MAX:
            raise GateError(f"텍스트 {len(self.text)}자 > {TELEGRAM_TEXT_MAX}")
        for i, t in enumerate(self.follow_texts):
            if len(t) > TELEGRAM_TEXT_MAX:
                raise GateError(f"이어지는 텍스트 {i + 1}번이 {len(t)}자 > {TELEGRAM_TEXT_MAX}")
        if self.follow_texts and not self.photos:
            raise GateError("이어지는 텍스트는 사진이 있을 때만 쓴다 "
                            "(사진 없으면 text 하나로 보낸다)")

    @classmethod
    def from_parts(cls, photos, parts: list[str]) -> "Payload":
        """pipeline의 caption_*(as_parts=True) 결과를 그대로 받는다."""
        if not parts:
            raise GateError("캡션 파트가 비었다")
        return cls(photos=list(photos), caption=parts[0], follow_texts=list(parts[1:]))


# ── 건너뜀 사유 코드 (v1.11i 신설) ────────────────────────────
#
# **왜 필요한가.** 하루 상한 도달도 429도 둘 다 `SendOutcome(QUEUED, ...)`였고,
# 틱은 둘 다 `else: skipped += 1`로 셌다. 그런데 알림 본문에는 `skipped`가
# 아예 실리지 않아서 — 유럽 리그가 들어와 정상 편성이 45건이 되는 날이나
# 텔레그램이 레이트리밋을 거는 날 **채널도 조용하고 알림도 조용했다.**
# 사유를 코드로 분리해야 틱이 "무엇 때문에 안 나갔는지"를 말할 수 있다.
class SkipReason:
    DAILY_CAP = "daily_cap"            # 하루 발송 상한 도달
    CORRECTION_CAP = "correction_cap"  # 정정 전용 하루 상한 도달 (v1.11j)
    RATE_LIMITED = "rate_limited"      # 텔레그램 429
    BURST_BLOCKED = "burst_blocked"    # 폭주 차단기
    LATE_AT_SEND = "late_at_send"      # 발송 직전 재판정에서 유예 초과
    LEDGER_BROKEN = "ledger_broken"    # 대장이 깨져 중복 위험 — 강행 금지
    ALREADY = "already"                # 대장에 이미 종결로 남아 있다
    OTHER = "other"


SKIP_REASON_LABEL = {
    SkipReason.DAILY_CAP: "하루 상한 초과",
    SkipReason.CORRECTION_CAP: "정정 하루 상한 초과",
    SkipReason.RATE_LIMITED: "레이트리밋(429)",
    SkipReason.BURST_BLOCKED: "폭주 차단",
    SkipReason.LATE_AT_SEND: "발송 직전 지각",
    SkipReason.LEDGER_BROKEN: "대장 손상",
    SkipReason.ALREADY: "이미 종결",
    SkipReason.OTHER: "기타",
}


@dataclass
class SendOutcome:
    state: SendState
    message_ids: list[int] = field(default_factory=list)
    reason: str = ""
    # 왜 안 나갔는지. 빈 문자열이면 정상 발송이다.
    reason_code: str = ""
    # **이번에 실제로 내보냈나, 아니면 대장에 이미 있어서 안 보냈나.**
    # 둘 다 state는 SENT다 — 대장에 있는 것은 '보내진 상태'가 맞기 때문이다.
    # 그런데 이 둘을 구분하지 않으면 로그가 거짓말을 한다: 시계가 돌 때마다
    # "발송 성공 2"가 찍혀, 채널에 카드가 계속 쌓이는 것처럼 보인다.
    # (실제로 첫날 밤 로그가 그랬다. 중복 발송은 없었지만 로그만 봐서는 알 수 없었다.)
    already: bool = False


class Sender:
    def __init__(self, transport, ledger: Ledger, chat_id: str, *,
                 worker_id: Optional[str] = None,
                 daily_max: int = BURST_MAX_MESSAGES,
                 correction_daily_max: int = CORRECTION_DAILY_MAX,
                 pacer: Optional[Pacer] = None,
                 alert_chat_id: Optional[str] = None,
                 burst: Optional[BurstBreaker] = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.tr = transport
        self.led = ledger
        self.chat_id = chat_id
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        # **하루 발송 상한** — 폭주 차단기이지 편성 상한이 아니다.
        # 20이던 시절 실측: 시즌 리그 5개로 이미 15건(리그당 모닝·시작알림·결과 3건).
        # 11월 KBL·V리그가 열리면 7리그 21건, 유럽 키까지 들어오면 15리그 45건이라
        # **정상 편성이 상한에 막힌다.** 9리그 정상치(27) + 유럽까지(45)를 넘고
        # 폭주는 여전히 잡는 값으로 둔다.
        self.daily_max = daily_max
        # **정정 전용 하루 상한 (v1.11j).** 정정은 UNPLANNED_CONTENT라 위 상한에서
        # 면제된다 — 사고가 났을 때 편성 상한에 막혀 정정이 못 나가면 안 되기 때문이다.
        # 그런데 면제만 해두면 정정에는 아무 뚜껑이 없다. 폭주 차단기(10분 60건)는
        # 하루 종일 이어지는 정정 스무 건을 못 잡는다. 그래서 별도 뚜껑을 둔다.
        self.correction_daily_max = correction_daily_max
        self.pacer = pacer or Pacer()
        # 알림 목적지. 시트의 ALERT_TARGET이 '발행 채널'이면 chat_id와 같다.
        # 폴백하지 않는다. 없으면 알림을 보내지 않는다 —
        # 내부 장애 메시지가 구독 채널에 나가는 것이 더 나쁘다.
        self.alert_chat_id = alert_chat_id
        self.now = now
        # 폭주 차단기. 상태는 대장 옆 파일에 남는다(프로세스가 죽어도 살아남아야 한다).
        self.burst = burst or BurstBreaker(
            pathlib.Path(ledger.path).parent / "burst.json", now)
        # 이번 실행에서 사람이 봐야 할 일들(폭주 차단 발동·자동 해제·격리 등).
        # 틱이 읽어 알림에 싣는다 — 조용히 막히는 것이 가장 나쁘다.
        self.notes: list[str] = []

    # ── 클레임 ──────────────────────────────────────────────

    def claim(self, item: QueueItem, **kw) -> Optional[SendRecord]:
        """클레임. **대장 자물쇠 안에서 통째로** 수행한다 (v1.11j).

        클레임은 "읽고 → 판단하고 → CLAIMED로 쓴다"인데, 그 사이에 다른 실행
        흐름이 끼어들면 둘 다 이겼다고 믿고 **둘 다 보낸다**(실측: 한 프로세스
        8스레드에서 8건 중 3~6건이 실제로 나갔다). 이 구멍은 정정 이전부터
        있었지만, 정정은 사고 대응 중에 여러 경로로 불릴 수 있어 중복이 곧 사고다.
        판단과 쓰기를 한 덩어리로 묶어 승자를 하나로 만든다.

        (프로세스가 다를 때의 중복은 여전히 리스와 append-only 대장이 막는다 —
         그쪽이 원래 설계이고, 파일 자물쇠는 러너가 하나뿐이라 필요 없다.)
        """
        with self.led.lock:
            return self._claim_locked(item, **kw)

    def _claim_locked(self, item: QueueItem, *,
                      content_digest: Optional[str] = None,
                      origin_idem_key: Optional[str] = None,
                      corrects_idem_key: Optional[str] = None,
                      revision: int = 0) -> Optional[SendRecord]:
        """이미 종결됐거나 남이 잡고 있으면 None.

        **리스 만료 자체는 사고가 아니다 (v1.11i).**
        전에는 리스가 만료된 항목을 전부 NEEDS_HUMAN으로 못 박았다. 그런데
        리스는 `유예/3`(시작알림 38분·모닝 120분)인데 **실측 시계 간격이 30~240분**
        이라, 정상적으로 컨테이너가 회수되기만 해도 다음 틱 전에 리스가 만료된다.
        그러면 **한 번도 안 보낸 항목**이 영구 격리되고, 코드 어디에도
        NEEDS_HUMAN을 되돌리는 쓰기가 없어 사람이 대장에 손으로 줄을 넣어야 했다.

        그래서 두 가지를 구분한다:
          · 클레임만 하고 **발송 흔적이 없는 것** → 중복 위험 0 → 큐로 되돌린다
          · **발송을 시도한 흔적(DISPATCH_MARK)이 있는 것** → 보냈는지 알 수 없다 → 격리
        """
        now = self.now()
        r = self.led.get(item.idem_key)

        # 종결 상태(SENT·SKIPPED_*·SUPERSEDED)는 전부 다시 집지 않는다.
        # 전에는 SENT만 봤다 — 그래서 '지각으로 버림'을 대장에 남겨도
        # 다음 틱이 그 항목을 다시 집어 되살아났다.
        if r and r.state in SETTLED_STATES:
            return None
        if r and r.state is SendState.NEEDS_HUMAN:
            return None

        if r and r.state is SendState.CLAIMED:
            if r.claimed_by == self.worker_id and r.lease_expires_utc and r.lease_expires_utc > now:
                # 내가 잡은 것 — 이어서 진행. 다만 이번에 계산한 지문이 있으면 붙인다.
                # (안 붙이면 이 발송의 지문이 대장에 안 남아 다음 비교가 '지문 없음'이 된다.)
                if content_digest and r.content_digest != content_digest:
                    r = replace(r, content_digest=content_digest)
                    self.led.put(r)
                return r
            if r.lease_expires_utc and r.lease_expires_utc > now:
                return None                              # 남이 작업 중
            if (r.last_error or "") == DISPATCH_MARK:
                # API를 두드린 뒤 죽었다 — 나갔는지 알 수 없다. 여기만 격리 대상이다.
                self.led.put(replace(r, state=SendState.NEEDS_HUMAN,
                                     last_error="발송 시도 중 리스 만료 — "
                                                "발송 여부 불명, 자동 재발송 금지"))
                self.notes.append(f"격리(발송 여부 불명) {item.content_type.value} {item.scope}")
                return None
            # 클레임만 하고 죽었다(러너 회수·OOM·SIGKILL). 아무것도 안 나갔으므로
            # 큐로 되돌려 이번 틱이 이어서 보낸다. 다만 무한 반복은 막는다.
            n = r.retry_count + 1
            if n >= SEND_MAX_RETRIES:
                self.led.put(replace(r, state=SendState.NEEDS_HUMAN, retry_count=n,
                                     last_error=f"클레임 회수 {n}회 반복 — 상한 초과, 사람 확인 필요"))
                self.notes.append(f"격리(클레임 회수 반복 {n}회) "
                                  f"{item.content_type.value} {item.scope}")
                return None
            r = replace(r, state=SendState.QUEUED, claimed_by=None,
                        lease_expires_utc=None, retry_count=n,
                        last_error="리스 만료(발송 흔적 없음) — 큐로 되돌림")
            self.led.put(r)

        # FAILED/QUEUED로 되돌아온 항목의 재시도 상한.
        if r and r.retry_count >= SEND_MAX_RETRIES:
            self.led.put(replace(r, state=SendState.NEEDS_HUMAN,
                                 last_error=f"재시도 {r.retry_count}회 — 상한"
                                            f"({SEND_MAX_RETRIES}) 도달, 자동 재시도 중단"))
            self.notes.append(f"격리(재시도 상한 {r.retry_count}회) "
                              f"{item.content_type.value} {item.scope}: {r.last_error or ''}"[:160])
            return None

        lease = LEASE_SECONDS[item.content_type]
        rec = SendRecord(idem_key=item.idem_key, state=SendState.CLAIMED,
                         # 대장은 저장소에 커밋된다 — 실제 채널 ID가 아니라 지문을 남긴다
                         # (공개 저장소에서 운영 구조가 통째로 드러나는 것을 막는다).
                         chat_id=channel_ref(self.chat_id),
                         content_type=item.content_type,
                         claimed_by=self.worker_id,
                         # 재시도 횟수는 이어받는다 — 새로 클레임할 때마다 0으로
                         # 되돌리면 상한이 영원히 안 걸린다.
                         retry_count=(r.retry_count if r else 0),
                         retry_429_count=(r.retry_429_count if r else 0),
                         # 내용 지문·정정 관계는 클레임 줄에서부터 남긴다 (v1.11j).
                         # 발송 성공 뒤에만 적으면, 크래시로 SENT 줄을 못 남긴 발송의
                         # 지문이 통째로 사라져 다음 비교가 '지문 없음'이 된다.
                         content_digest=(content_digest
                                         or (r.content_digest if r else None)),
                         origin_idem_key=(origin_idem_key
                                          or (r.origin_idem_key if r else None)),
                         corrects_idem_key=(corrects_idem_key
                                            or (r.corrects_idem_key if r else None)),
                         revision=(revision or (r.revision if r else 0)),
                         lease_expires_utc=now + timedelta(seconds=lease))
        self.led.put(rec)
        return rec

    # ── 발송 흔적 남기기 ────────────────────────────────────

    def mark_settled(self, item: QueueItem, state: SendState, reason: str) -> bool:
        """종결 표기도 읽고-쓰기라 클레임과 같은 자물쇠 안에서 한다 (v1.11j)."""
        with self.led.lock:
            return self._mark_settled_locked(item, state, reason)

    def _mark_settled_locked(self, item: QueueItem, state: SendState, reason: str) -> bool:
        """발송하지 않고 **종결**로 못 박는다 (지각 폐기 등). 이미 종결이면 False.

        **왜 필요한가.** 지각으로 버린 항목이 대장에 아무 흔적을 안 남기면
        다음 틱이 같은 항목을 또 집어 같은 로그·같은 알림을 되풀이한다.
        종결 상태로 남겨야 `claim()`이 SETTLED_STATES에서 걸러낸다.
        """
        if state not in SETTLED_STATES:
            raise GateError(f"mark_settled에는 종결 상태만 쓴다 ({state.value})")
        prev = self.led.get(item.idem_key)
        if prev and prev.state in SETTLED_STATES:
            return False                                # 이미 기록했다 — 두 번 안 적는다
        if prev and prev.state is SendState.NEEDS_HUMAN:
            return False                                # 격리된 것은 건드리지 않는다
        if (prev and prev.state is SendState.CLAIMED
                and (prev.last_error or "") == DISPATCH_MARK):
            # 발송을 시도한 흔적이 있는 항목은 '건너뜀'으로 덮으면 안 된다 —
            # 나갔을 수도 있다는 사실이 지워진다. 사람이 볼 수 있게 격리로 남긴다.
            self.led.put(replace(prev, state=SendState.NEEDS_HUMAN,
                                 claimed_by=None, lease_expires_utc=None,
                                 last_error=f"{reason} · 다만 발송 시도 흔적이 있어 "
                                            f"발송 여부 불명 — 사람 확인 필요"))
            self.notes.append(f"격리(지각 + 발송 여부 불명) "
                              f"{item.content_type.value} {item.scope}")
            return True
        base = prev or SendRecord(idem_key=item.idem_key, state=state,
                                  chat_id=channel_ref(self.chat_id),
                                  content_type=item.content_type)
        self.led.put(replace(base, state=state, message_ids=[], sent_at_utc=None,
                             claimed_by=None, lease_expires_utc=None,
                             last_error=reason))
        return True

    # ── 발송 ────────────────────────────────────────────────

    def _release(self, rec: "SendRecord", reason: str = "",
                 count_retry: bool = True) -> None:
        """클레임을 반납해 QUEUED로 되돌린다.

        **클레임한 뒤 예외가 나면 그 항목은 영구 격리된다.** 리스가 만료되면
        `claim()`이 NEEDS_HUMAN으로 못 박기 때문이다 — 한 번도 안 보냈는데도.
        게다가 워커 ID가 실행마다 달라(`gha-{run_id}`) "내가 잡은 것 이어서"
        분기는 절대 안 탄다. 그래서 보내지 못했으면 **반드시 반납**한다.

        **`count_retry`가 왜 있나.** 실패해서 되돌리는 것과, 우리 쪽 안전장치가
        막아서 되돌리는 것은 다르다. 폭주 차단으로 30분 막히는 동안 틱이 세 번 돌면
        멀쩡한 항목이 재시도 상한에 걸려 격리된다 — 안전장치가 발행을 죽이는 셈이다.
        그래서 **우리가 일부러 막은 경우는 세지 않는다.**
        """
        self.led.put(replace(rec, state=SendState.QUEUED,
                             claimed_by=None, lease_expires_utc=None,
                             # 되돌릴 때마다 한 번씩 센다 — 안 세면 상한이 영원히 안 걸린다.
                             retry_count=rec.retry_count + (1 if count_retry else 0),
                             last_error=(reason or rec.last_error)))

    def send(self, item: QueueItem, payload: Payload, *,
             content_digest: Optional[str] = None,
             origin_idem_key: Optional[str] = None,
             corrects_idem_key: Optional[str] = None,
             revision: int = 0) -> SendOutcome:
        # **보내기 전 검사는 클레임 전에 한다.** 클레임 뒤에 터지면
        # 그 항목이 영구 격리된다(실측: 캡션 1200자 → 3시간 뒤 needs_human).
        payload.gate()

        # **대장이 깨졌으면 발송을 강행하지 않는다 (v1.11i).**
        # 건너뛴 줄이 SENT였다면 그 항목은 '안 보낸 것'이 되어 재발송된다.
        # 중복 발송은 되돌릴 수 없고, 미발송은 사람이 고칠 수 있다.
        if getattr(self.led, "broken_lines", 0):
            return SendOutcome(SendState.QUEUED, [],
                               f"발송 대장 {self.led.broken_lines}줄 손상 — "
                               f"중복 발송 위험이 있어 발송하지 않습니다",
                               reason_code=SkipReason.LEDGER_BROKEN)

        day = self.now().astimezone(KST).strftime("%Y-%m-%d")
        if (item.content_type not in UNPLANNED_CONTENT
                and self.led.count_sent_today(day) >= self.daily_max):
            return SendOutcome(SendState.QUEUED, [],
                               f"하루 발송 상한({self.daily_max}) 도달 (폭주 방지) — 다음 날 재시도",
                               reason_code=SkipReason.DAILY_CAP)
        # 정정은 위 상한에서 면제되므로 **자기 상한**을 따로 받는다 (v1.11j).
        # 여기서 막히는 것은 조용히 넘길 일이 아니다 — 정정이 필요한 상황이
        # 하루에 여섯 번 넘게 생겼다는 뜻이므로 사람이 봐야 한다.
        if item.content_type is ContentType.CORRECTION:
            used = self.led.count_corrections_today(day)
            if used >= self.correction_daily_max:
                why = (f"정정 하루 상한({self.correction_daily_max}) 도달 — "
                       f"오늘 정정 {used}건. 정정 폭주는 원래 사고보다 나쁩니다")
                self.notes.append(why)
                return SendOutcome(SendState.QUEUED, [], why,
                                   reason_code=SkipReason.CORRECTION_CAP)

        rec = self.claim(item, content_digest=content_digest,
                         origin_idem_key=origin_idem_key,
                         corrects_idem_key=corrects_idem_key, revision=revision)
        if rec is None:
            prev = self.led.get(item.idem_key)
            return SendOutcome(prev.state if prev else SendState.SUPERSEDED,
                               prev.message_ids if prev else [], "이미 처리됨",
                               reason_code=SkipReason.ALREADY, already=True)

        self.pacer.wait()

        # 페이서 대기 뒤에 다시 판정한다 — 문안에 시각이 박힌 콘텐츠는 여기서 거짓말이 된다
        if item.content_type in REJUDGE_AT_SEND and is_late(
                item.scheduled_utc, self.now(), item.content_type):
            return self._finish(rec, SendState.SKIPPED_ALREADY_STARTED, [],
                                "발송 직전 재판정 — 유예 초과",
                                reason_code=SkipReason.LATE_AT_SEND)

        # **폭주 차단기 — 정말 지금 보낼 것이 확정된 뒤에 묻는다.**
        # allow()는 자동 해제·카나리를 소비하는 부수효과가 있어서, 앞에서 물으면
        # 다른 이유로 건너뛴 항목이 카나리 한 장을 태워버린다.
        ok, why = self.burst.allow()
        if not ok:
            self.notes.append(why)
            # 우리 안전장치가 막은 것이므로 재시도 횟수를 세지 않는다 —
            # 세면 30분 차단 동안 멀쩡한 항목이 상한에 걸려 격리된다.
            self._release(rec, reason=why, count_retry=False)
            return SendOutcome(SendState.QUEUED, [], why,
                               reason_code=SkipReason.BURST_BLOCKED)
        if why:
            self.notes.append(why)          # 자동 해제·카나리도 사람이 알아야 한다

        # **여기서부터는 "보냈는지 알 수 없는" 구간이다.**
        # 표시를 남겨두면, 프로세스가 강제 종료돼 리스가 만료됐을 때
        # claim()이 '클레임만 함'과 구분해 이 항목만 격리한다.
        rec = replace(rec, last_error=DISPATCH_MARK)
        self.led.put(rec)

        try:
            ids = self._dispatch(payload)
        except PartialSend as e:
            # 사진은 나갔고 나머지가 빠졌다. 재발송하면 사진이 중복된다.
            return self._finish(rec, SendState.NEEDS_HUMAN, e.sent_ids,
                                f"부분 발송 — 나머지 {e.missing}건 누락: {e}")
        except AmbiguousSend as e:
            # 가장 위험한 경우. 보냈을 수도 있다 → 사람이 눈으로 확인해야 한다
            return self._finish(rec, SendState.NEEDS_HUMAN, [],
                                f"응답 유실 — 발송 여부 불명: {e}")
        except TelegramError as e:
            if e.status == 429:
                # 클레임을 **반납**한다. 잡아둔 채로 두면 다음 실행(다른 워커 ID)이
                # "남이 작업 중"으로 건너뛰다가 리스 만료와 함께 영구 격리된다.
                # 429는 우리 잘못이 아니라 속도 문제라 retry_count에서 제외한다(규칙 4).
                self.led.put(replace(rec, state=SendState.QUEUED,
                                     claimed_by=None, lease_expires_utc=None,
                                     retry_429_count=rec.retry_429_count + 1,
                                     last_error=f"429 retry_after={e.retry_after}"))
                return SendOutcome(SendState.QUEUED, [],
                                   f"레이트리밋 — {e.retry_after}초 뒤 재시도",
                                   reason_code=SkipReason.RATE_LIMITED)
            return self._finish(rec, SendState.FAILED, [], f"{e}", retry=True)
        except Exception:                                       # noqa: BLE001
            # 예상 못 한 예외로 클레임이 남으면 그 발행은 영구히 죽는다.
            # (PartialSend·AmbiguousSend·TelegramError는 위에서 이미 잡혔으므로
            #  전에 여기 있던 `except (…): raise` 절은 도달 불가능한 죽은 코드였다.)
            self._release(rec, reason="예상 못 한 예외로 반납")
            raise

        # 실제로 나간 건수를 폭주 차단기에 기록한다. 여기서 새로 차단이 걸릴 수 있다.
        blocked = self.burst.record(len(ids))
        if blocked:
            self.notes.append(blocked)
        return self._finish(rec, SendState.SENT, ids, "")

    def _dispatch(self, p: Payload) -> list[int]:
        # 답장은 **첫 메시지에만** 단다. 앨범의 모든 장과 후속 텍스트까지 답장으로
        # 달면 채널이 인용 더미가 된다 — 정정 한 건이 원본 한 건을 가리키면 충분하다.
        reply = p.reply_params()
        if not p.photos:
            res = self.tr.call("sendMessage", {
                "chat_id": self.chat_id, "text": p.text,
                "parse_mode": TELEGRAM_PARSE_MODE,
                "disable_web_page_preview": True, **reply})
            return [res["message_id"]]

        ids: list[int] = []
        idx = 0
        done_photos = 0
        for method, n in plan_send_parts(len(p.photos)):
            chunk = p.photos[idx:idx + n]
            idx += n
            cap = p.caption if not ids else None      # 캡션은 첫 파트에만
            # **여러 파트로 갈리는 앨범의 중간 실패에서 이미 나간 id를 잃지 않는다 (v1.11i).**
            # 전에는 여기서 TelegramError가 나면 그때까지 모은 `ids`가 그대로 버려지고
            # `_finish(FAILED, ids=[])`로 끝났다 → 다음 틱이 **처음부터 다시** 보내
            # 앞 파트의 사진이 채널에 두 번 남는다. `PartialSend`가 후속 텍스트 루프에만
            # 걸려 있어서 생긴 구멍이다. 지금은 카드가 항상 1장이라 잠복 상태지만
            # 2장이 되는 순간(plan_send_parts가 파트를 나누는 순간) 활성화된다.
            try:
                if method is SendMethod.PHOTO:
                    name, data, _, _ = chunk[0]
                    res = self.tr.call("sendPhoto",
                                       {"chat_id": self.chat_id,
                                        "parse_mode": TELEGRAM_PARSE_MODE,
                                        **({"caption": cap} if cap else {}),
                                        **(reply if not ids else {})},
                                       files={"photo": (name, data)})
                    ids.append(res["message_id"])
                else:
                    media, files = [], {}
                    for i, (name, data, _, _) in enumerate(chunk):
                        k = f"f{i}"
                        m = {"type": "photo", "media": f"attach://{k}"}
                        if i == 0 and cap:
                            m |= {"caption": cap, "parse_mode": TELEGRAM_PARSE_MODE}
                        media.append(m)
                        files[k] = (name, data)
                    res = self.tr.call("sendMediaGroup",
                                       {"chat_id": self.chat_id, "media": media,
                                        **(reply if not ids else {})},
                                       files=files)
                    ids += [m["message_id"] for m in res]
            except (TelegramError, AmbiguousSend) as e:
                if ids:
                    raise PartialSend(
                        ids, (len(p.photos) - done_photos) + len(p.follow_texts), e) from e
                raise            # 아직 한 장도 안 나갔다 — 통째로 재시도해도 안전하다
            done_photos += len(chunk)
            if idx < len(p.photos):
                self.pacer.wait()

        # 캡션에 다 못 담은 나머지를 이어 보낸다.
        # 여기서 실패하면 사진은 이미 나갔으므로 전체 재발송은 중복을 만든다 → 격리.
        for i, t in enumerate(p.follow_texts):
            self.pacer.wait()
            try:
                res = self.tr.call("sendMessage", {
                    "chat_id": self.chat_id, "text": t,
                    "parse_mode": TELEGRAM_PARSE_MODE,
                    "disable_web_page_preview": True})
            except (TelegramError, AmbiguousSend) as e:
                raise PartialSend(ids, len(p.follow_texts) - i, e) from e
            ids.append(res["message_id"])
        return ids

    def _finish(self, rec: SendRecord, state: SendState, ids: list[int],
                reason: str, *, reason_code: str = "",
                retry: bool = False) -> SendOutcome:
        self.led.put(replace(rec, state=state, message_ids=ids,
                             sent_at_utc=self.now() if state is SendState.SENT else None,
                             sent_count=len(ids), last_error=reason or None,
                             # 실패로 끝난 것만 재시도 횟수를 센다. 상한에 닿으면
                             # 다음 클레임에서 격리된다(무한 재시도 방지).
                             retry_count=rec.retry_count + (1 if retry else 0),
                             claimed_by=None, lease_expires_utc=None))
        if state is SendState.NEEDS_HUMAN:
            self.notes.append(f"격리 {rec.content_type.value}: {reason}"[:200])
        return SendOutcome(state, ids, reason, reason_code=reason_code)

    # ── 정정 (v1.11j 신설) ──────────────────────────────────
    #
    # **왜 필요한가.** 2026-09-01 19:18, 진행 중인 KBO 5경기가 '종료 0:0 무승부'로
    # 실제 채널에 나갔다. 원인은 고쳤지만 이미 나간 카드를 되돌릴 방법이 없었다.
    # 멱등키가 같아 두 번째 발송은 언제나 '이미 보냄'으로 버려지기 때문이다.
    #
    # **정정은 원본을 대체하지 않는다.** 텔레그램에서 이미 읽힌 메시지를 되돌릴 수는
    # 없고, 편집은 알림을 안 띄워 아무도 정정을 못 본다. 그래서 **새 메시지**로 내고,
    # 원본에 답장으로 달아 무엇의 정정인지 보이게 한다.
    # 원본 키의 상태는 그대로 SENT로 남는다 — 원본은 실제로 나갔기 때문이다.

    def evaluate_correction(self, origin_idem_key: str, new_digest: Optional[str], *,
                            max_corrections: int = CORRECTION_MAX_PER_SCOPE,
                            window_seconds: int = CORRECTION_WINDOW_SECONDS,
                            min_interval_seconds: int = CORRECTION_MIN_INTERVAL_SECONDS,
                            quiet: bool = False) -> CorrectionDecision:
        """이 원본에 정정을 낼 것인가. 대장을 읽어 계약의 판정 함수에 넘긴다.

        **막힌 이유는 조용히 넘기지 않는다** — 상한·기한·간격에 걸린 건은
        `self.notes`에 실어 틱이 알림에 담게 한다(조용히 막히는 것이 가장 나쁘다).
        """
        origin = self.led.get(origin_idem_key)
        priors: list[SendRecord] = []
        if origin is not None:
            try:
                priors = self.led.correction_chain(origin_idem_key, max_corrections)
            except GateError:
                # 키 형식이 아니면 정정 사슬을 만들 수 없다. 발송을 막지는 않되
                # (원본은 이미 나갔다) 사람에게는 올린다.
                self.notes.append(f"정정 불가 — 멱등키 형식이 아님: {origin_idem_key}")
                origin = None
        d = decide_correction(origin, priors, new_digest, now_utc=self.now(),
                              max_corrections=max_corrections,
                              window_seconds=window_seconds,
                              min_interval_seconds=min_interval_seconds)
        if d.blocked and not quiet:
            self.notes.append(d.note())
        return d

    def correction_item(self, origin_item: QueueItem, decision: CorrectionDecision, *,
                        scheduled_utc: Optional[datetime] = None) -> QueueItem:
        """정정본 큐 항목. 키는 원본 키에서 결정적으로 만든다.

        예약 시각은 **지금**이다 — 정정의 예약 시각은 '사실이 바뀐 순간'이고,
        원본의 예약 시각을 물려받으면 이미 유예를 넘긴 채 태어나 곧바로 버려진다.
        """
        if not decision.should_send:
            raise GateError(f"정정을 내지 않기로 한 판정으로 항목을 만들 수 없다 "
                            f"({decision.code}: {decision.reason})")
        if decision.origin_idem_key != origin_item.idem_key:
            raise GateError("정정 판정의 원본 키와 넘긴 항목의 키가 다르다 "
                            "— 엉뚱한 발송의 정정이 만들어진다")
        return QueueItem(
            idem_key=correction_key_from(origin_item.idem_key, decision.revision),
            content_type=ContentType.CORRECTION,
            scope=correction_scope(origin_item.content_type, origin_item.scope),
            scheduled_utc=scheduled_utc or self.now(),
            league=origin_item.league, sports_day=origin_item.sports_day,
            game_id=origin_item.game_id)

    def send_correction(self, origin_item: QueueItem, payload: Payload,
                        decision: CorrectionDecision, *,
                        scheduled_utc: Optional[datetime] = None) -> SendOutcome:
        """정정 1건 발송. 원본에 답장으로 달고, 새 지문을 대장에 남긴다."""
        item = self.correction_item(origin_item, decision, scheduled_utc=scheduled_utc)
        if decision.origin_message_id and not payload.reply_to_message_id:
            payload = replace(payload, reply_to_message_id=decision.origin_message_id)
        return self.send(item, payload,
                         content_digest=decision.new_digest,
                         origin_idem_key=decision.origin_idem_key,
                         corrects_idem_key=decision.corrects_idem_key,
                         revision=decision.revision)

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
        if not self.alert_chat_id:
            # 목적지가 없으면 보내지 않는다. 발행 채널로 폴백하면
            # 구독자가 내부 장애 메시지를 본다.
            return False
        body = f"⚠️ <b>{esc(title)}</b>\n" + quote([esc(x) for x in lines])
        gap = ALERT_REPEAT_SECONDS if repeat_after is None else repeat_after
        fp = _hashlib.sha256(
            _fp_norm("\n".join([title] + list(lines))).encode()).hexdigest()[:16]
        if gap > 0 and not self._alert_is_new(fp, gap):
            return False                      # 방금 같은 말을 했다
        try:
            self.tr.call("sendMessage", {"chat_id": self.alert_chat_id,
                                         "text": body[:TELEGRAM_TEXT_MAX],
                                         "parse_mode": TELEGRAM_PARSE_MODE,
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
