# -*- coding: utf-8 -*-
"""토론방 — 앵커 글의 댓글로 세부를 보내고, 손님 질문에 답한다 (v1.39 신설).

대표님 설계(2026-09-17): 경기마다 **앵커 한 장**을 채널에 올리고, 분석·라인업·
시작·득점·취소·결과는 **전부 그 글의 댓글**로 넣는다. 그래야 채널이 경기
순서대로 읽히고, 사람이 이야기할 자리가 남는다.

────────────────────────────────────────────────────────────────────
**어떻게 되는가.**

텔레그램 채널에 토론 그룹을 연결하면, 채널 글이 그룹으로 **자동 전달**된다.
그 전달된 글에 답장을 달면 채널에서 '댓글'로 보인다. 그래서 필요한 것은
하나뿐이다 — **전달된 글의 번호를 아는 것.**

봇 API에는 그걸 바로 물어보는 길이 없다. 대신 그룹에 들어온 소식을 받아 보면
자동 전달 글에 `is_automatic_forward: true`와 **원래 채널 글 번호**가 함께 온다.
그 둘을 짝지어 적어두면 끝이다.

    채널 글 983  ──(텔레그램이 전달)──▶  그룹 글 41
    지도: {983: 41}   → 앞으로 983의 댓글은 그룹 41에 답장으로 단다

────────────────────────────────────────────────────────────────────
⚠️ **못 찾으면 채널로 보낸다.** 토론 그룹이 아직 없거나, 전달이 늦거나,
받아오기가 막혀도 그 경기 정보를 잃지 않는다. 자리가 바뀔 뿐이다
(`게이트가 틱을 죽이면 위반보다 나쁘다`).

⚠️ **받아오기는 이 프로젝트가 처음 쓰는 기능이다.** 지금까지는 보내기만 했다.
그래서 **웹훅과 부딪히지 않는지**를 먼저 본다 — 웹훅이 걸려 있으면
`getUpdates`가 409를 돌려주고, 그때는 조용히 포기한다(설정은 사람의 몫이다).
"""
from __future__ import annotations

import json
import pathlib
from typing import Optional

# ── 켜고 끄는 자리 ─────────────────────────────────────────────
#
# **토론 그룹 번호가 없으면 아무것도 하지 않는다.** 채널에 그룹을 연결하고
# 그 번호를 비밀값(`DISCUSSION_CHAT_ID`)에 넣기 전까지, 이 파일은 통째로
# 잠들어 있고 발송은 지금까지와 똑같이 채널로 나간다.
DISCUSSION_ENABLED = True

# 한 번에 받아오는 소식 수. 5분마다 도는 시계에는 이 정도면 남는다.
UPDATE_LIMIT = 100
# 받아온 소식을 몇 초까지 기다려 주는가.
UPDATE_TIMEOUT_S = 5


class DiscussionState:
    """어디까지 읽었는지 · 어느 채널 글이 어느 그룹 글인지.

    **파일 하나에 담는다.** 대장(`ledger.jsonl`)과 섞지 않는다 — 대장은
    '무엇을 보냈나'의 단일 진실 원천이고, 여기는 '받은 것'이라 성격이 다르다.
    """

    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.offset: int = 0
        self.map: dict = {}          # 채널 글 번호(str) → 그룹 글 번호(int)
        self.answered: dict = {}     # 그룹 글 번호(str) → True (두 번 답하지 않는다)
        # 버튼을 이미 채운 채널 글. **두 번 고치면 텔레그램이 오류를 준다.**
        self.buttoned: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.offset = int(d.get("offset") or 0)
        self.map = dict(d.get("map") or {})
        self.answered = dict(d.get("answered") or {})
        self.buttoned = dict(d.get("buttoned") or {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # **지도는 무한정 자라지 않는다.** 경기 하나의 댓글 창은 하루면 닫힌다 —
        # 최근 것만 남겨도 잃는 것이 없고, 파일이 커지면 매 틱 읽기가 느려진다.
        if len(self.map) > 2000:
            keep = sorted(self.map, key=lambda k: int(k))[-1000:]
            self.map = {k: self.map[k] for k in keep}
        if len(self.answered) > 4000:
            keep = sorted(self.answered, key=lambda k: int(k))[-2000:]
            self.answered = {k: self.answered[k] for k in keep}
        if len(self.buttoned) > 2000:
            keep = sorted(self.buttoned, key=lambda k: int(k))[-1000:]
            self.buttoned = {k: self.buttoned[k] for k in keep}
        self.path.write_text(json.dumps(
            {"offset": self.offset, "map": self.map,
             "answered": self.answered, "buttoned": self.buttoned},
            ensure_ascii=False), encoding="utf-8")

    # ── 지도 ──────────────────────────────────────────────────
    def thread_of(self, channel_message_id) -> Optional[int]:
        """그 채널 글의 댓글이 달릴 그룹 글 번호. 모르면 None."""
        v = self.map.get(str(channel_message_id))
        return int(v) if v else None

    def remember(self, channel_message_id, group_message_id) -> None:
        self.map[str(channel_message_id)] = int(group_message_id)


_probed = False


_username_cache: dict = {}


def public_username(transport, chat_id) -> str:
    """그 채널의 **공개 아이디**(`@` 없이). 비공개면 빈 문자열. 실행당 한 번.

    ★ **왜 물어보는가** (2026-09-17).
    주소 꼴을 비밀값 생김새로 판단하고 있었다 — `@아이디`면 공개,
    숫자면 비공개. 그런데 대표님이 채널을 **공개로 바꾸셔도 비밀값은
    숫자 그대로**다. 그러면 코드는 계속 `t.me/c/<내부번호>/…`를 만들고,
    거기 붙인 `?comment=`를 텔레그램이 **말없이 무시한다** — 버튼을 눌러도
    그 경기 댓글창이 안 열린다. 실제로 그렇게 막혀 있었다.

    **생김새로 짐작하지 말고 소스에 묻는다.** 채널이 공개가 되든 다시
    비공개가 되든, 이름이 바뀌든, 다음 실행이 알아서 따라간다.

    실패하면 빈 문자열이다 — 그때는 지금까지처럼 `t.me/c/…`로 떨어진다.
    """
    key = str(chat_id or "")
    if key in _username_cache:
        return _username_cache[key]
    name = ""
    try:
        chat = transport.call("getChat", {"chat_id": chat_id}) or {}
        name = str(chat.get("username") or "").lstrip("@")
    except Exception:                                    # noqa: BLE001
        name = ""
    _username_cache[key] = name
    return name


def probe(transport) -> str:
    """받아오기가 왜 막히는지 **스스로 확인한다** (v1.40). 실행당 한 번.

    실측(2026-09-17): 지도가 계속 비어 있는데 로그에 아무것도 안 남아
    원인을 좁히는 데 몇 시간이 걸렸다. 가장 흔한 두 가지를 코드가 직접 묻는다.

      ① **웹훅이 걸려 있으면** `getUpdates`는 409로 막힌다. 둘은 함께 못 쓴다.
      ② **밀린 소식 수**가 0이면 봇이 그룹 글을 아예 못 보고 있다는 뜻이다
         (그룹 프라이버시가 켜져 있거나, 켠 뒤 봇을 다시 안 넣었을 때).

    돌려주는 것은 사람이 읽을 한 줄. 실패해도 발송을 막지 않는다.
    """
    global _probed
    if _probed:
        return ""
    _probed = True
    try:
        me = transport.call("getMe", {})
        wh = transport.call("getWebhookInfo", {})
    except Exception as e:                               # noqa: BLE001
        return f"⚠️ [토론방] 상태 확인 실패 — {e.__class__.__name__}: {str(e)[:80]}"
    url = (wh or {}).get("url") or ""
    pend = (wh or {}).get("pending_update_count")
    out = [f"ⓘ [토론방] 봇 @{(me or {}).get('username','?')}",
           f"밀린 소식 {pend}건"]
    if url:
        out.append("⚠️ **웹훅이 걸려 있어 받아오기가 막힙니다** — "
                   "웹훅을 끄거나(deleteWebhook) 받아오기를 포기해야 합니다")
    else:
        out.append("웹훅 없음(받아오기 가능)")
    return " · ".join(out)


def poll(transport, state: DiscussionState, *, limit: int = UPDATE_LIMIT) -> list:
    """새 소식을 받아 **자동 전달 짝**을 적고, 사람이 쓴 글만 돌려준다.

    돌려주는 것: `[{"chat_id", "message_id", "thread_id", "text", "user"}]`

    **받아오기가 막히면 빈 목록이다.** 웹훅 충돌(409)·네트워크 실패 어느 쪽이든
    이 함수는 조용히 물러난다 — 받기가 안 된다고 보내기가 멈추면 안 된다.
    """
    if not DISCUSSION_ENABLED:
        return []
    _p = probe(transport)
    if _p:
        print(f"    {_p}")
    try:
        res = transport.call("getUpdates", {
            "offset": state.offset, "limit": int(limit),
            "timeout": UPDATE_TIMEOUT_S,
            # **필요한 것만 받는다.** 전부 받으면 남의 채널 소식까지 섞여 온다.
            "allowed_updates": ["message"]})
    except Exception as e:                               # noqa: BLE001
        # ⚠️ **조용히 물러나지 않는다** (2026-09-17).
        # 처음엔 그냥 `return []` 이었다. 그래서 받아오기가 막혔는데도
        # 로그에 한 줄도 안 남아, 채널에서 "왜 댓글로 안 가지"만 보였다.
        # 실패해도 발송은 계속하되 **이유는 반드시 남긴다.**
        print(f"    ⚠️ [토론방] 받아오기 실패 — {e.__class__.__name__}: "
              f"{str(e)[:120]}")
        return []
    if not isinstance(res, list):
        print(f"    ⚠️ [토론방] 받아오기 응답이 목록이 아닙니다: {str(res)[:120]}")
        return []
    if not res:
        print(f"    ⓘ [토론방] 새 소식 0건 (읽은 데까지 {state.offset})")

    asks: list = []
    for u in res:
        try:
            state.offset = max(state.offset, int(u.get("update_id", 0)) + 1)
        except (TypeError, ValueError):
            pass
        m = u.get("message") or {}
        chat = m.get("chat") or {}
        if not m or not chat:
            continue
        # ① 자동 전달 — 채널 글 ↔ 그룹 글을 짝짓는다
        if m.get("is_automatic_forward"):
            src = ((m.get("forward_origin") or {}).get("message_id")
                   or m.get("forward_from_message_id"))
            if src:
                state.remember(src, m.get("message_id"))
            continue
        # ② 사람이 쓴 글만 남긴다 — 봇 글에 봇이 답하면 메아리가 된다
        frm = m.get("from") or {}
        if frm.get("is_bot"):
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        asks.append({
            "chat_id": chat.get("id"),
            "message_id": m.get("message_id"),
            # 댓글은 전부 **같은 실타래** 안에 있다. 답을 그 실타래에 달아야
            # 채널에서 그 경기의 댓글로 보인다.
            "thread_id": m.get("message_thread_id"),
            "text": text,
            "user": (frm.get("first_name") or "").strip(),
        })
    if res:
        print(f"    ⓘ [토론방] 소식 {len(res)}건 · 전달짝 {len(state.map)}개 · "
              f"질문 {len(asks)}건")
    return asks


def edit_text(transport, chat_id, message_id, text: str, *,
              parse_mode: str = "HTML") -> bool:
    """이미 올린 글을 고쳐 쓴다. 실패하면 False (막지 않는다).

    '오늘의 경기' 글이 이걸 쓴다 — 자정에 목록만 올리고, 앵커가 하나 생길
    때마다 그 글에 바로가기를 채워 넣는다.
    """
    try:
        transport.call("editMessageText", {
            "chat_id": chat_id, "message_id": int(message_id), "text": text,
            "parse_mode": parse_mode, "disable_web_page_preview": True})
        return True
    except Exception:                                    # noqa: BLE001
        # **같은 내용으로 고치면 텔레그램이 오류를 준다.** 그건 실패가 아니다.
        return False


def set_buttons(transport, chat_id, message_id, buttons) -> bool:
    """이미 올린 글의 버튼을 갈아 끼운다 (v1.39). 실패하면 False.

    **앵커 버튼이 나중에 붙는 이유.** 버튼 주소는 그 경기 토론방 글을
    가리켜야 하는데, 그 번호는 앵커가 채널에 올라간 **뒤에야** 텔레그램이
    만든다(자동 전달). 그래서 보낼 때는 없고, 알게 된 다음 틱에 채운다.
    """
    try:
        transport.call("editMessageReplyMarkup", {
            "chat_id": chat_id, "message_id": int(message_id),
            "reply_markup": {"inline_keyboard": buttons}})
        return True
    except Exception:                                    # noqa: BLE001
        return False


def thread_link(discussion_chat_id: str, thread_message_id: int) -> str:
    """그 경기 토론방 글로 바로 가는 주소.

    공개 그룹은 `@아이디`, 비공개는 `-100…` 번호다 — 주소 꼴이 서로 다르다.
    """
    cid = str(discussion_chat_id or "").strip()
    if cid.startswith("@"):
        return f"https://t.me/{cid[1:]}/{int(thread_message_id)}"
    inner = cid[4:] if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{inner}/{int(thread_message_id)}"


def comment_link(channel_chat_id: str, post_id: int,
                 thread_message_id: int, *, username: str = "") -> str:
    """그 **채널 글의 댓글창**으로 바로 가는 주소 (v1.39).

    ⚠️ **토론 그룹 주소를 쓰면 안 된다.** `t.me/<그룹>/<번호>`는 그룹을 여는
    주소라, 아직 그룹에 안 들어온 손님에게는 **그룹 입장 화면**이 뜬다
    (2026-09-17 실채널 확인 — 대표님이 그 경기가 아니라 그룹으로 들어갔다).

    채널 글에 `?comment=` 를 붙이면 텔레그램이 **그 글의 댓글창**을 연다.
    앵커를 눌렀을 때와 똑같은 자리다.
    """
    return f"{post_link(channel_chat_id, post_id, username=username)}" \
           f"?comment={int(thread_message_id)}"


def post_link(channel_chat_id: str, post_id: int, *, username: str = "") -> str:
    """그 채널 글로 가는 주소.

    **공개 아이디가 있으면 그것을 쓴다.** `t.me/<아이디>/<번호>`는 아직 채널에
    안 들어온 사람에게도 열리고, `?comment=`도 여기서만 동작한다.
    없으면 `t.me/c/<내부번호>/<번호>` — 이미 들어온 사람에게만 열린다.
    """
    u = str(username or "").lstrip("@").strip()
    if u:
        return f"https://t.me/{u}/{int(post_id)}"
    cid = str(channel_chat_id or "").strip()
    if cid.startswith("@"):
        return f"https://t.me/{cid[1:]}/{int(post_id)}"
    inner = cid[4:] if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{inner}/{int(post_id)}"


def pin(transport, chat_id, message_id, *, notify: bool = False) -> bool:
    """맨 위에 고정. 실패하면 False."""
    try:
        transport.call("pinChatMessage", {
            "chat_id": chat_id, "message_id": int(message_id),
            "disable_notification": not notify})
        return True
    except Exception:                                    # noqa: BLE001
        return False


def unpin(transport, chat_id, message_id) -> bool:
    try:
        transport.call("unpinChatMessage", {
            "chat_id": chat_id, "message_id": int(message_id)})
        return True
    except Exception:                                    # noqa: BLE001
        return False
