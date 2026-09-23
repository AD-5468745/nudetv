---
name: nudetv-telegram-architecture
description: nudetv 텔레그램 채널의 앵커+댓글 발송 구조 — 포럼 토픽이 아니라 linked-discussion 댓글 흉내라는 점이 핵심
metadata:
  type: project
---

nudetv(이 저장소)는 경기당 **앵커(사진 카드) 1장만 채널**에 올리고, 나머지 세부 콘텐츠(분석·라인업·킥오프·득점·구간·종료·박스스코어)는 **연결된 토론방의 그 앵커 댓글**로 내려간다.

**핵심 오해 주의**: 이 "댓글" 구조는 텔레그램의 **포럼 토픽(forum topics, `message_thread_id`)이 아니다.** 실제로는 "연결된 토론 그룹(linked discussion)" 기능을 쓴다 — 채널 글이 토론 그룹으로 자동 전달되면(`is_automatic_forward`), 봇이 `getUpdates`로 그 전달글을 받아 "채널 글 번호 ↔ 그룹 글 번호"를 짝짓고(`g1/discussion.py`), 세부 카드를 그 그룹 글에 **답장(reply)**으로 붙인다. 포럼 토픽 API(`createForumTopic` 등)는 코드 어디에도 안 쓰인다.

관련 상수·함수:
- `g1/tick.py:2684` `THREADED_CONTENT_TYPES` — 댓글로 내려가는 콘텐츠 종류
- `g1/tick.py:2711` `_thread_for()` — 경기 앵커 채널글번호 → 토론방글번호 매칭
- `g1/tick.py:3206` `ContentType.ANCHOR` — 경기당 1장, 항상 채널로
- `g1/discussion.py` — 전달짝 관리·댓글창 딥링크(`comment_link`) 생성

**Bot API 공식 확인 사실(2026-09-23)**: 채널(channel) 자체는 포럼 토픽을 지원하지 않는다 — `message_thread_id`는 "for supergroups and private chats only", `Chat.is_forum`은 슈퍼그룹 전용. 채널에 연결된 토론 그룹은 슈퍼그룹이라 포럼 전환 대상은 될 수 있으나, 일반 그룹을 봇 API로 포럼 모드로 전환하는 API는 확인 못함(사람이 앱에서 켜야 할 가능성).

리그 15개 구성: 야구(KBO·MLB·NPB) 3 · 축구(KL1·EPL·LALIGA·SERIEA·BUNDESLIGA·LIGUE1·UCL·UEL·MLS) 9 · 농구(KBL) 1 · 배구(VLEAGUE_M·VLEAGUE_W) 2. (`contract.py:76-98`)

관련: [[nudetv-rate-limit-facts]], [[nudetv-2026-09-23-ledger-regression]]
