#!/usr/bin/env python3
"""토론방 · 질문 답변 전수 검증 (v1.39 신설).

**이 파일이 지키는 것은 '봇이 답하는가'가 아니라 '답이 참인가'다.**
답은 손님이 읽는 글이고, 숫자 하나가 틀리면 채널 전체가 거짓말한 것이 된다.

돌리는 법:  python3 g1/verify_answers.py
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
from datetime import datetime, timedelta, timezone

G1 = pathlib.Path(__file__).resolve().parent
ROOT = G1.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(G1))

import answers as A                                              # noqa: E402
import discussion as D                                           # noqa: E402
from contract import KST, League, Score, ScoreUnit, Status, WLD  # noqa: E402

PASS = 0
FAIL: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name}  {detail}")
        print(f"  FAIL  {name}  {detail}")


# ── 시험용 대역 ────────────────────────────────────────────────
class _TR:
    def __init__(self, c): self.team_code = c


class _G:
    def __init__(self, a, h, *, day, hh, st=Status.SCHEDULED, score=None,
                 venue="잠실"):
        self.away, self.home = _TR(a), _TR(h)
        self.status = st
        self.score = score
        self.venue = venue
        self.sports_day = day
        self.start_utc = datetime.strptime(day, "%Y-%m-%d").replace(
            hour=hh, tzinfo=KST).astimezone(timezone.utc)


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=KST)
TODAY = "2026-09-17"


class _St:
    def __init__(self, rank, w, l, d=0, l10=None):
        self.rank, self.record, self.pct = rank, WLD(w, l, d), f"{w/(w+l):.3f}"
        self.last10 = l10


class _RB:
    def __init__(self, table): self._t = table
    def team(self, code): return self._t.get(code)


GAMES = [
    _G("SK", "NC", day=TODAY, hh=18, venue="창원"),
    _G("WO", "HT", day=TODAY, hh=18, venue="광주"),
    _G("SS", "HH", day="2026-09-18", hh=18, venue="대전"),
    _G("SS", "OB", day="2026-09-16", hh=18, st=Status.FINAL,
       score=Score(3, 1, ScoreUnit.RUNS)),
]
RB = {"KBO": _RB({"LG": _St(3, 72, 55, 1, WLD(6, 4, 0)),
                  "SS": _St(2, 75, 50, 3, WLD(5, 5, 0)),
                  "HT": _St(4, 68, 57, 2, None)})}


def nm(lg, t):
    from contract import TEAM_NAMES
    code = getattr(t, "team_code", t)
    return TEAM_NAMES.get(lg, {}).get(code, code)


KW = dict(games_by_league={League.KBO: GAMES}, records=RB, now=NOW, name_of=nm)


# ══════════════════════════════════════════════════════════════
print("1. 묻는 말을 알아보는가")
# ══════════════════════════════════════════════════════════════
# `LG`는 야구에도 농구에도 있다 — 어느 리그인지 알려 줘야 갈린다.
check("팀 이름을 찾는다",
      A.find_team("LG 몇위?", prefer={League.KBO})[2] == "LG")
check("★ 소리 나는 대로 써도 찾는다 (엘지 → LG)",
      A.find_team("엘지 순위", prefer={League.KBO})[2] == "LG",
      str(A.find_team("엘지 순위", prefer={League.KBO})))
check("  ↳ 기아·쓱도 마찬가지",
      A.find_team("기아 결과")[2] == "KIA"
      and A.find_team("쓱 결과")[2] == "SSG")
check("★★ 같은 이름이 여러 리그에 있으면 아무 쪽이나 고르지 않는다",
      A.find_team("삼성 순위") is None, str(A.find_team("삼성 순위")))
check("  ↳ 경기가 있는 리그를 알려 주면 갈린다",
      A.find_team("삼성 순위", prefer={League.KBO})[0] is League.KBO)
check("  ↳ 리그를 함께 말해도 갈린다",
      A.find_team("KBO 삼성 순위", prefer={League.KBO})[2] == "삼성")
check("모르는 팀은 None", A.find_team("맨체스터시티유나이티드컴바인") is None)
check("리그 이름을 찾는다", A.find_league("오늘 야구 뭐해") is League.KBO)


# ══════════════════════════════════════════════════════════════
print("\n2. 답이 참인가 — 자료에서 그대로 꺼내 읽는가")
# ══════════════════════════════════════════════════════════════
_r = A.answer("LG 몇위?", **KW)
check("★★ 순위 답이 자료의 값과 같다",
      _r and "3위" in _r and "72승 55패 1무" in _r and "0.567" in _r, str(_r))
check("  ↳ 최근 10경기도 자료 그대로", "6승 4패" in (_r or ""), str(_r))

_r2 = A.answer("오늘 KBO 경기 뭐해?", **KW)
check("★★ 오늘 경기는 **그날 것만** 센다 (내일·어제가 섞이지 않는다)",
      _r2 and "2경기" in _r2 and "18:00" in _r2
      and "한화" not in _r2 and "두산" not in _r2, str(_r2))

_r3 = A.answer("삼성 다음 경기 언제?", **KW)
check("★★ 다음 경기는 **지금 뒤**의 가장 이른 경기다",
      _r3 and "09월 18일" in _r3 and "한화" in _r3, str(_r3))
check("  ↳ 시각을 한 문장에 두 번 쓰지 않는다",
      (_r3 or "").count("18:00") == 1, str(_r3))

_r4 = A.answer("삼성 결과", **KW)
# 원정이 삼성(1점) · 홈이 두산(3점)이다 — **원정 : 홈** 순서를 지키는지 본다.
check("★★ 결과는 **끝난 경기**만 본다",
      _r4 and "삼성 1 : 3 두산" in _r4 and "09월 16일" in _r4, str(_r4))

check("★★ 자료가 없으면 지어내지 않는다",
      A.answer("LG 몇위?", games_by_league={}, records={}, now=NOW,
               name_of=nm) is None)


# ══════════════════════════════════════════════════════════════
print("\n3. 모르면 모른다고 한다 — 침묵하지 않는다")
# ══════════════════════════════════════════════════════════════
check("★★ 못 알아들은 질문에도 **답은 한다**",
      A.reply_for("내일 날씨 어때?", **KW) == A.UNKNOWN)
check("  ↳ 그 답이 '모른다'는 말이다", "못 찾습니다" in A.UNKNOWN)
check("도움말을 물으면 쓰는 법을 알려준다",
      "순위" in A.reply_for("도움말", **KW))
check("★ 너무 긴 글은 질문으로 보지 않는다 (도배 방지)",
      A.answer("가" * 300, **KW) is None)
check("★ 빈 글도 마찬가지", A.answer("   ", **KW) is None)


# ══════════════════════════════════════════════════════════════
print("\n4. AI 자리 — 켜지 않았다")
# ══════════════════════════════════════════════════════════════
check("★★ AI는 꺼져 있다", A.AI_ENABLED is False)
check("  ↳ 꺼진 채로 부르면 None (조용히 지나간다)",
      A.ask_ai("아무거나", "자료") is None)
check("★★ 켜지 않았어도 손님은 답을 받는다 (규칙이 먼저다)",
      A.reply_for("LG 몇위?", **KW).startswith("<b>LG</b>"))
_saved = A.AI_ENABLED
try:
    A.AI_ENABLED = True
    _raised = False
    try:
        A.ask_ai("x", "y")
    except NotImplementedError:
        _raised = True
    check("★★ (변이) 켜 놓고 안 채우면 **시끄럽게 막힌다** (조용히 빈 답 아님)",
          _raised)
finally:
    A.AI_ENABLED = _saved


# ══════════════════════════════════════════════════════════════
print("\n5. 토론방 배선")
# ══════════════════════════════════════════════════════════════
_tmp = pathlib.Path(tempfile.mkdtemp()) / "disc.json"
st = D.DiscussionState(_tmp)
check("처음에는 아무것도 모른다", st.offset == 0 and st.thread_of(983) is None)


class _FakeTr:
    def __init__(self, updates): self.updates = updates; self.calls = []
    def call(self, method, payload, files=None):
        self.calls.append((method, payload))
        if method == "getUpdates":
            return self.updates
        return {"message_id": 1}


UPD = [
    {"update_id": 10, "message": {
        "message_id": 41, "chat": {"id": -100999},
        "is_automatic_forward": True,
        "forward_origin": {"message_id": 983}, "text": "카드"}},
    {"update_id": 11, "message": {
        "message_id": 42, "chat": {"id": -100999},
        "message_thread_id": 41, "from": {"first_name": "손님"},
        "text": "LG 몇위?"}},
    {"update_id": 12, "message": {
        "message_id": 43, "chat": {"id": -100999},
        "from": {"first_name": "봇", "is_bot": True}, "text": "나는 봇"}},
]
tr = _FakeTr(UPD)
asks = D.poll(tr, st)
check("★★ 자동 전달을 보고 **채널 글 ↔ 그룹 글**을 짝짓는다",
      st.thread_of(983) == 41, str(st.map))
check("★★ 사람이 쓴 글만 질문으로 본다 (봇 글에 봇이 답하면 메아리가 된다)",
      len(asks) == 1 and asks[0]["text"] == "LG 몇위?", str(asks))
check("  ↳ 어느 실타래인지 함께 가져온다", asks[0]["thread_id"] == 41)
check("★ 읽은 데까지 표시가 앞으로 간다 (같은 글을 두 번 안 읽는다)",
      st.offset == 13, str(st.offset))

st.save()
st2 = D.DiscussionState(_tmp)
check("★★ 다시 켜도 기억한다 (실행이 바뀌어도 댓글이 이어진다)",
      st2.thread_of(983) == 41 and st2.offset == 13)


class _BoomTr:
    def call(self, *a, **k): raise RuntimeError("막힘")


check("★★ 받아오기가 막혀도 조용히 물러난다 (보내기를 멈추지 않는다)",
      D.poll(_BoomTr(), st) == [])
check("  ↳ 고쳐쓰기·고정도 마찬가지",
      D.edit_text(_BoomTr(), 1, 2, "x") is False
      and D.pin(_BoomTr(), 1, 2) is False)

_saved_en = D.DISCUSSION_ENABLED
try:
    D.DISCUSSION_ENABLED = False
    check("★★ 스위치 하나로 통째로 잠든다", D.poll(_FakeTr(UPD), st) == [])
finally:
    D.DISCUSSION_ENABLED = _saved_en

# 지도가 무한정 자라지 않는가
st3 = D.DiscussionState(pathlib.Path(tempfile.mkdtemp()) / "big.json")
for i in range(2500):
    st3.remember(i, i)
st3.save()
check("★ 지도가 무한정 자라지 않는다 (매 틱 읽는 파일이다)",
      len(st3.map) <= 1000, str(len(st3.map)))
check("  ↳ 남기는 것은 **최근** 것이다", st3.thread_of(2499) == 2499)


print()
print("=" * 64)
print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
for line in FAIL:
    print(f"  ✗ {line}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
