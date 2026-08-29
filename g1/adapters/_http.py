"""수집 공통 HTTP — 재시도·백오프 (v1.11 신설).

리그를 늘리다가 KBO에서 `Connection reset by peer`가 났다.
일시적인 끊김인데 수집 전체가 죽었다. 리그가 15개면 이런 일이 15배 자주 난다.

**재시도해도 되는 것과 안 되는 것을 가른다.**
  · 재시도 O — 연결 끊김·타임아웃·5xx·429 (서버/네트워크의 일시 문제)
  · 재시도 X — 4xx (우리 요청이 틀렸다. 반복해도 같다)
발송(sender.py)과는 규칙이 정반대다. 수집은 여러 번 읽어도 부작용이 없지만
발송은 한 번 더 보내면 구독자가 같은 카드를 두 번 본다.
"""
from __future__ import annotations

import http.cookiejar
import random
import ssl
import sys
import pathlib
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from contract import GateError

RETRIES = 3
BACKOFF = 1.6          # 1.6s → 2.6s → 4.1s
TIMEOUT = 30.0

# 재시도해도 되는 예외 — 요청이 서버에 닿았는지와 무관하게, 읽기는 부작용이 없다
_TRANSIENT = (TimeoutError, ConnectionResetError, ConnectionAbortedError,
              ConnectionRefusedError, urllib.error.URLError, OSError)


def make_opener(cookies: bool = False) -> urllib.request.OpenerDirector:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    handlers: list = [urllib.request.HTTPSHandler(context=ctx)]
    if cookies:
        handlers.insert(0, urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    return urllib.request.build_opener(*handlers)


def fetch(opener: urllib.request.OpenerDirector,
          req: urllib.request.Request | str, *,
          label: str, timeout: float = TIMEOUT,
          retries: int = RETRIES,
          sleep: Callable[[float], None] = time.sleep) -> bytes:
    """읽기 요청 하나. 일시 실패는 재시도하고, 우리 잘못은 즉시 포기한다."""
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return opener.open(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (408, 429) or 500 <= e.code < 600:
                last = e                                   # 서버 쪽 문제 — 다시 해본다
            else:
                raise GateError(f"{label}: HTTP {e.code}")  # 우리 요청이 틀렸다
        except _TRANSIENT as e:
            last = e
        if attempt < retries:
            # 지터를 섞는다. 여러 리그가 같은 초에 몰려 재시도하면 소스에 부하가 겹친다.
            sleep(BACKOFF ** (attempt + 1) + random.uniform(0, 0.4))
    raise GateError(f"{label}: {retries + 1}회 시도 모두 실패 "
                    f"({last.__class__.__name__ if last else '원인 불명'})")
