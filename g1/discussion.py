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
        self._load()

    def _load(self) -> None:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.offset = int(d.get("offset") or 0)
        self.map = dict(d.get("map") or {})
        self.answered = dict(d.get("answered") or {})

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
        self.path.write_text(json.dumps(
            {"offset": self.offset, "map": self.map, "answered": self.answered},
            ensure_ascii=False), encoding="utf-8")

    # ── 지도 ──────────────────────────────────────────────────
    def thread_of(self, channel_message_id) -> Optional[int]:
        """그 채널 글의 댓글이 달릴 그룹 글 번호. 모르면 None."""
        v = self.map.get(str(channel_message_id))
        return int(v) if v else None

    def remember(self, channel_message_id, group_message_id) -> None:
        self.map[str(channel_message_id)] = int(group_message_id)


def poll(transport, state: DiscussionState, *, limit: int = UPDATE_LIMIT) -> list:
    """새 소식을 받아 **자동 전달 짝**을 적고, 사람이 쓴 글만 돌려준다.

    돌려주는 것: `[{"chat_id", "message_id", "thread_id", "text", "user"}]`

    **받아오기가 막히면 빈 목록이다.** 웹훅 충돌(409)·네트워크 실패 어느 쪽이든
    이 함수는 조용히 물러난다 — 받기가 안 된다고 보내기가 멈추면 안 된다.
    """
    if not DISCUSSION_ENABLED:
        return []
    try:
        res = transport.call("getUpdates", {
            "offset": state.offset, "limit": int(limit),
            "timeout": UPDATE_TIMEOUT_S,
            # **필요한 것만 받는다.** 전부 받으면 남의 채널 소식까지 섞여 온다.
            "allowed_updates": ["message"]})
    except Exception:                                    # noqa: BLE001
        return []
    if not isinstance(res, list):
        return []

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
