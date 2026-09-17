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


# ══════════════════════════════════════════════════════════════
print("\n6. 앵커 큐와 댓글 라우팅")
# ══════════════════════════════════════════════════════════════
import pipeline as P                                             # noqa: E402
import tick as T                                                 # noqa: E402
from contract import ContentType, Game, GameMeta, TeamRef, idem_key  # noqa: E402


def _real(a, h, *, day, hh, st=Status.SCHEDULED):
    return Game(league=League.KBO, season="2026",
                source_key=f"{day}{a}{h}", home=TeamRef(League.KBO, h),
                away=TeamRef(League.KBO, a), status=st,
                start_utc=datetime.strptime(day, "%Y-%m-%d").replace(
                    hour=hh, tzinfo=KST).astimezone(timezone.utc),
                home_tz="Asia/Seoul",
                score=None, venue="잠실", meta=GameMeta())


_NOWQ = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)      # KST 12:00
_QG = [_real("SS", "OB", day="2026-09-17", hh=18),
       _real("LG", "HT", day="2026-09-17", hh=18)]
_q = P.build_queue(_QG, _NOWQ, "-100t", floor_hours=0, horizon_hours=48)
_anchors = [i for i in _q if i.content_type is ContentType.ANCHOR]
check("★★ 경기마다 앵커가 하나씩 선다", len(_anchors) == 2, str(len(_anchors)))
check("  ↳ 경기 3시간 전에 예약된다",
      all((g.start_utc - a.scheduled_utc).total_seconds() == P.ANCHOR_LEAD_SECONDS
          for a, g in zip(sorted(_anchors, key=lambda x: x.scope), _QG)),
      str([str(a.scheduled_utc) for a in _anchors]))
check("★★ 경기마다 멱등키가 다르다 (하나가 나가면 나머지가 버려지면 안 된다)",
      len({a.idem_key for a in _anchors}) == 2)

_done = [_real("SS", "OB", day="2026-09-17", hh=18, st=Status.CANCELED)]
check("★★ 취소된 경기에는 앵커를 만들지 않는다 (아무도 안 여는 댓글방)",
      not [i for i in P.build_queue(_done, _NOWQ, "-100t", floor_hours=0)
           if i.content_type is ContentType.ANCHOR])

_late = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)     # KST 19:00 — 시작 뒤
check("★★ 시작을 넘긴 경기에도 만들지 않는다 (문패는 경기 전에만 뜻이 있다)",
      not [i for i in P.build_queue(_QG, _late, "-100t", floor_hours=0)
           if i.content_type is ContentType.ANCHOR])

_saved_en = P.ANCHOR_ENABLED
try:
    P.ANCHOR_ENABLED = False
    check("★★ 스위치 하나로 옛 방식으로 돌아간다",
          not [i for i in P.build_queue(_QG, _NOWQ, "-100t", floor_hours=0)
               if i.content_type is ContentType.ANCHOR])
finally:
    P.ANCHOR_ENABLED = _saved_en


# ── 라우팅: 앵커가 없으면 채널로 간다 ──
class _Led:
    def __init__(self, rec=None): self._rec = rec
    def get(self, key): return self._rec


class _Rec:
    def __init__(self, ids): self.message_ids = ids


class _It:
    def __init__(self, ct, gid="g1"):
        self.content_type = ct
        self.league = League.KBO
        self.sports_day = "2026-09-17"
        self.game_id = gid


_st = D.DiscussionState(pathlib.Path(tempfile.mkdtemp()) / "r.json")
_saved_chat = T.DISCUSSION_CHAT_ID
try:
    T.DISCUSSION_CHAT_ID = "-100999"
    check("★★ 앵커가 대장에 없으면 채널로 간다 (댓글 못 단다고 잃지 않는다)",
          T._thread_for(_It(ContentType.ANALYSIS), _Led(None), _st, "-100t") is None)
    _led = _Led(_Rec([983]))
    check("  ↳ 앵커는 있는데 전달을 못 봤으면 그래도 채널로",
          T._thread_for(_It(ContentType.ANALYSIS), _led, _st, "-100t") is None)
    _st.remember(983, 41)
    check("★★ 둘 다 있으면 그 경기 댓글로 간다",
          T._thread_for(_It(ContentType.ANALYSIS), _led, _st, "-100t") == 41)
    check("★★ 앵커 자신은 채널에 남는다 (자기 댓글에 자기를 달지 않는다)",
          T._thread_for(_It(ContentType.ANCHOR), _led, _st, "-100t") is None)
    check("  ↳ 순위표·정리판도 채널에 남는다",
          T._thread_for(_It(ContentType.STANDINGS), _led, _st, "-100t") is None
          and T._thread_for(_It(ContentType.LEAGUE_RESULT), _led, _st,
                            "-100t") is None)
    T.DISCUSSION_CHAT_ID = ""
    check("★★ 토론방 번호가 없으면 통째로 잠든다",
          T._thread_for(_It(ContentType.ANALYSIS), _led, _st, "-100t") is None)
finally:
    T.DISCUSSION_CHAT_ID = _saved_chat

check("★ 댓글로 내려보낼 종류가 적혀 있다 (여기 없으면 채널로 간다)",
      ContentType.GOAL_FLASH in T.THREADED_CONTENT_TYPES
      and ContentType.ANCHOR not in T.THREADED_CONTENT_TYPES)


# ══════════════════════════════════════════════════════════════
print("\n7. 오늘의 경기 — 고정 글과 바로가기")
# ══════════════════════════════════════════════════════════════
_IDX = [_real("SS", "OB", day="2026-09-17", hh=18),
        _real("LG", "HT", day="2026-09-17", hh=18),
        _real("WO", "NC", day="2026-09-18", hh=18)]
_t = P.daily_index_text(_IDX, "2026-09-17", name_of=nm)
check("★★ 그날 경기만 싣는다 (내일 것이 섞이지 않는다)",
      "2경기" in _t and "키움" not in _t, _t[:120])
check("  ↳ 날짜와 요일을 말한다", "9월 17일" in _t and "(목)" in _t, _t[:60])
check("  ↳ 리그 묶음과 시각이 있다", "KBO" in _t and "18:00" in _t, _t[:160])
check("★★ 경기가 없으면 빈 글이다 (빈 통을 올리지 않는다)",
      P.daily_index_text(_IDX, "2026-01-01", name_of=nm) == "")

_lk = {_IDX[0].game_id: "https://t.me/c/123/45"}
_t2 = P.daily_index_text(_IDX, "2026-09-17", links=_lk, name_of=nm)
check("★★ 앵커가 선 경기에만 바로가기가 붙는다",
      _t2.count("보기") == 1 and "t.me/c/123/45" in _t2, _t2[:200])
check("  ↳ 아직 앵커가 없는 경기는 그대로 둔다 (빈 링크를 만들지 않는다)",
      "<a href=\"\">" not in _t2)

check("★★ 바로가기 주소가 그 채널의 그 글을 가리킨다 (비공개)",
      T._message_link("-1009999999999", 42) == "https://t.me/c/9999999999/42",
      T._message_link("-1009999999999", 42))
# 공개 채널은 주소 꼴이 **다르다** — 비공개 꼴을 쓰면 아무 데도 안 열린다.
check("★★ 공개 채널이면 공개 주소를 쓴다",
      T._message_link("@somechannel", 42) == "https://t.me/somechannel/42",
      T._message_link("@somechannel", 42))

# 큐에 몇 건이 서는가 — **하루 두 번**이다 (v1.50).
# 자정 글 하나만 두면 하루가 지날수록 채널 아래로 밀려 아무도 안 본다.
# 대표님: *"자주 본채널에 노출시켜서 회원들의 토론방 참여율을 높여야해"*.
_qi = T.build_all_queues({"KBO": _IDX}, _NOWQ, "-100t")
_idx = [i for i in _qi if i.content_type is ContentType.DAILY_INDEX]
check("★★ 오늘의 경기는 하루 **두 번**이다 (자정 · 저녁)",
      len(_idx) == 2, str(len(_idx)))
check("  ↳ 멱등키가 서로 다르다 (같으면 둘째 통이 조용히 사라진다)",
      len({i.idem_key for i in _idx}) == len(_idx),
      str(sorted(i.scope for i in _idx)))
check("  ↳ 리그가 없는 통합 항목이다 (리그별로 서면 15통이 나간다)",
      _idx and _idx[0].league is None)
check("  ↳ 자정 직후로 예약된다",
      _idx and _idx[0].scheduled_utc.astimezone(KST).hour == P.DAILY_INDEX_HOUR)

_saved_idx = T.DAILY_INDEX_ENABLED
try:
    T.DAILY_INDEX_ENABLED = False
    check("★★ 스위치 하나로 안 나간다",
          not [i for i in T.build_all_queues({"KBO": _IDX}, _NOWQ, "-100t")
               if i.content_type is ContentType.DAILY_INDEX])
finally:
    T.DAILY_INDEX_ENABLED = _saved_idx


# ══════════════════════════════════════════════════════════════
print("\n8. 앵커 버튼 — 토론방으로 가는 자리")
# ══════════════════════════════════════════════════════════════
from contract import BUTTON_CONTENT_TYPES, DISCUSSION_BUTTON_TEXT  # noqa: E402
from sender import SendState                                       # noqa: E402

check("★★ 앵커에 버튼이 붙는다 (채널에 나가는 유일한 장이다)",
      "anchor" in BUTTON_CONTENT_TYPES, str(sorted(BUTTON_CONTENT_TYPES)))
check("  ↳ 문구가 정해져 있다", "토론방" in DISCUSSION_BUTTON_TEXT)

check("★★ 공개 그룹과 비공개 그룹의 주소 꼴이 다르다",
      D.thread_link("@g", 41) == "https://t.me/g/41"
      and D.thread_link("-1009999999999", 41) == "https://t.me/c/9999999999/41",
      D.thread_link("@g", 41))


class _BtnTr:
    def __init__(self): self.sets = []
    def call(self, method, payload, files=None):
        if method == "editMessageReplyMarkup":
            self.sets.append(payload)
            return {"ok": True}
        return {"message_id": 1}


_bst = D.DiscussionState(pathlib.Path(tempfile.mkdtemp()) / "b.json")
_bst.remember(983, 41)      # 앵커
_bst.remember(984, 42)      # 득점 속보 — 텔레그램은 **모든 채널 글**을 전달한다


class _LedA:
    """대장 대역 — 983번만 앵커다."""
    def __init__(self):
        from contract import ContentType as _CT
        class _R:
            state = SendState.SENT
            message_ids = [983]
        self._rows = {f"ch|{_CT.ANCHOR.value}|KBO:2026-09-17:g1|s0|r0": _R()}


_saved_dc = T.DISCUSSION_CHAT_ID
try:
    T.DISCUSSION_CHAT_ID = "@somegroup"
    _btr = _BtnTr()
    T._fill_thread_buttons(_btr, _bst, "@mych", _LedA())
    check("★★ 전달 번호를 알면 그 앵커에 버튼을 채운다", len(_btr.sets) == 1,
          str(len(_btr.sets)))
    _rows = _btr.sets[0]["reply_markup"]["inline_keyboard"] if _btr.sets else []
    check("★★★ 버튼이 **그 채널 글의 댓글창**을 가리킨다 (그룹 입장이 아니라)",
          _rows and _rows[0][0]["url"] == "https://t.me/mych/983?comment=41",
          str(_rows[:1]))
    check("  ↳ 문구가 '토론방'이다", _rows and "토론방" in _rows[0][0]["text"])
    check("★★★ 앵커가 아닌 글에는 안 단다 (모든 카드에 붙었던 사고)",
          len(_btr.sets) == 1
          and int(_btr.sets[0]["message_id"]) == 983, str(_btr.sets))
    _btr2 = _BtnTr()
    T._fill_thread_buttons(_btr2, _bst, "@mych", _LedA())
    check("★★ 같은 앵커를 두 번 고치지 않는다 (텔레그램이 오류를 준다)",
          len(_btr2.sets) == 0, str(len(_btr2.sets)))
    T.DISCUSSION_CHAT_ID = ""
    _btr3 = _BtnTr()
    T._fill_thread_buttons(_btr3, D.DiscussionState(
        pathlib.Path(tempfile.mkdtemp()) / "c.json"), "@mych", _LedA())
    check("★★ 토론방이 없으면 아무것도 안 한다", len(_btr3.sets) == 0)
finally:
    T.DISCUSSION_CHAT_ID = _saved_dc


# ══════════════════════════════════════════════════════════════
print("\n9. 받아오기 자가 진단 (v1.40)")
# ══════════════════════════════════════════════════════════════
# 실측: 지도가 계속 비어 있는데 로그에 아무것도 안 남아 원인을 몇 시간 못 찾았다.


class _ProbeTr:
    def __init__(self, url="", pend=0): self.url, self.pend = url, pend
    def call(self, m, p, files=None):
        if m == "getMe":
            return {"username": "b"}
        if m == "getWebhookInfo":
            return {"url": self.url, "pending_update_count": self.pend}
        return []


D._probed = False
_m = D.probe(_ProbeTr(url="https://x/hook"))
check("★★★ 웹훅이 걸려 있으면 그 사실을 말한다 (받아오기가 막히는 1순위 원인)",
      "웹훅이 걸려 있어" in _m, _m)
D._probed = False
_m2 = D.probe(_ProbeTr(pend=7))
check("★★ 웹훅이 없으면 없다고 말한다", "웹훅 없음" in _m2, _m2)
check("  ↳ 밀린 소식 수를 함께 말한다 (0이면 봇이 그룹 글을 못 보는 것)",
      "밀린 소식 7건" in _m2, _m2)
check("★ 실행당 한 번만 묻는다 (매 틱 두 번씩 부르지 않는다)",
      D.probe(_ProbeTr(pend=9)) == "")


class _ProbeBoom:
    def call(self, *a, **k): raise RuntimeError("막힘")


D._probed = False
check("★★ 진단이 실패해도 한 줄로 알린다 (조용히 넘어가지 않는다)",
      "상태 확인 실패" in D.probe(_ProbeBoom()))
D._probed = False


# ══════════════════════════════════════════════════════════════
print("\n10. 경기별 콘텐츠는 토론방에만 (v1.41)")
# ══════════════════════════════════════════════════════════════
# 분석이 **경기마다 한 장**이 되면서 생긴 위험: 토론방 짝을 못 찾은 채
# 채널로 보내면 그날 경기 수만큼(실측 최대 62장) 채널에 쏟아진다.
import inspect as _insp0                                     # noqa: E402
check("★★★ 무거운 경기별 콘텐츠는 자리가 생길 때까지 기다린다",
      ContentType.ANALYSIS in T.WAIT_FOR_THREAD_TYPES
      and ContentType.LINEUP in T.WAIT_FOR_THREAD_TYPES,
      str(sorted(x.value for x in T.WAIT_FOR_THREAD_TYPES)))
# v1.41 — 대표님 지시: *"앵커가 올라가는 채널에는 나오지 않고, 각 토론방에만
# 올라가도록"*. 실측(2026-09-17)으로 그날 경기별 콘텐츠 26건 중 18건이
# 채널로 새어 나가고 있었다. 이제 다섯 종류 **전부** 자리를 기다린다.
check("★★★ 속보도 채널로 새지 않는다 (경기별 콘텐츠는 토론방에만)",
      ContentType.GOAL_FLASH in T.WAIT_FOR_THREAD_TYPES
      and ContentType.KICKOFF in T.WAIT_FOR_THREAD_TYPES
      and ContentType.FINAL_FLASH in T.WAIT_FOR_THREAD_TYPES)
# ══════════════════════════════════════════════════════════════
# 본채널 경기 목록 버튼 (v1.50)
# ══════════════════════════════════════════════════════════════
#
# 대표님: *"그날 진행하는 원하는 경기를 쉽게 찾아볼 수 있도록 쉽게
# 선택하고, 클릭해서 토론방으로 넘어가지게 · 자주 본채널에 노출시켜서
# 회원들의 토론방 참여율을 높여야해"*.
import pipeline as _P50                                          # noqa: E402
from datetime import datetime as _dt50, timedelta as _td50       # noqa: E402


class _BRef:
    def __init__(self, c):
        self.team_code = c


class _BG:
    def __init__(self, gid, hours, terminal=False, lg=None):
        from contract import League as _L
        self.game_id = gid
        self.sports_day = "2026-09-18"
        self.start_utc = _dt50(2026, 9, 18, 3, 0, tzinfo=timezone.utc) \
            + _td50(hours=hours)
        self.home, self.away = _BRef("두산"), _BRef("삼성")
        self.league = lg or _L.KBO
        self.is_terminal = terminal


_gs50 = [_BG("a", 0), _BG("b", 2), _BG("c", 4, terminal=True)]
_lk50 = {"a": "https://t.me/ch/1?comment=1", "b": "https://t.me/ch/2?comment=2",
         "c": "https://t.me/ch/3?comment=3"}
_btn = _P50.daily_index_buttons(_gs50, "2026-09-18", links=_lk50,
                                name_of=lambda lg, t: t.team_code,
                                now=_dt50(2026, 9, 18, 3, tzinfo=timezone.utc))
check("★★★ 경기마다 버튼 하나 — 누르면 그 경기 토론방으로",
      len(_btn) == 2 and all(r[0]["url"].startswith("https://t.me/")
                             for r in _btn), str(_btn))
check("★★ 끝난 경기는 목록에서 뺀다 (지나간 버튼이 쌓이면 못 찾는다)",
      all("3?comment=3" not in r[0]["url"] for r in _btn), str(_btn))
check("★★★ 주소를 모르는 경기는 버튼을 안 만든다 (갈 곳 없는 버튼 금지)",
      _P50.daily_index_buttons(_gs50, "2026-09-18", links={},
                               name_of=lambda lg, t: t.team_code) == [])
check("★★ 버튼 수에 상한이 있다 (스크롤로 찾으면 '쉽게'가 아니다)",
      0 < _P50.DAILY_INDEX_MAX_BUTTONS <= 30)
check("★★★ 목록을 하루 두 번 올린다 (자정 글은 저녁이면 아래로 밀린다)",
      len(_P50.DAILY_INDEX_SLOTS) == 2, str(_P50.DAILY_INDEX_SLOTS))
import inspect as _insp50                                        # noqa: E402
check("  ↳ 글과 버튼을 **한 번에** 고친다 (따로 고치면 어긋나는 순간이 생긴다)",
      "buttons" in _insp50.signature(D.edit_text).parameters)

check("★★★ 경기 전 정보도 토론방으로만 간다 (2차 · v1.44)",
      ContentType.PREGAME in T.THREADED_CONTENT_TYPES
      and ContentType.PREGAME in T.WAIT_FOR_THREAD_TYPES)
check("  ↳ 댓글로 갈 종류와 기다릴 종류가 **같은 표**다 (하나만 고치면 샌다)",
      T.WAIT_FOR_THREAD_TYPES == T.THREADED_CONTENT_TYPES,
      str(sorted(x.value for x in
                 T.THREADED_CONTENT_TYPES ^ T.WAIT_FOR_THREAD_TYPES)))
check("★★★ 그 대신 집을 못 찾은 것은 **알린다** (조용히 잃지 않는다)",
      "_homeless" in dir(T)
      and "_homeless" in _insp0.getsource(T.alert_lines)
      if hasattr(T, "alert_lines") else "_homeless" in dir(T))
check("  ↳ 기다림은 '사라짐'과 다른 이름으로 기록된다",
      "thread_not_ready" in T._SKIP_LABEL_LOCAL
      and "기다림" in T._SKIP_LABEL_LOCAL["thread_not_ready"])
check("★★ 기다리는 종류는 모두 댓글로 갈 종류다 (안 그러면 영영 안 나간다)",
      T.WAIT_FOR_THREAD_TYPES <= T.THREADED_CONTENT_TYPES)

# 기다림의 전제는 "집이 곧 생긴다"는 것이다. 앵커가 없는 경기까지 기다리면
# 그 분석은 채널에도 토론방에도 안 나오고 조용히 만료된다.
class _Rec:
    def __init__(self, ids): self.message_ids = ids


class _Led:
    def __init__(self, ids): self._ids = ids
    def get(self, key): return _Rec(self._ids) if self._ids else None


class _It:
    league = list(League)[0]
    sports_day = "2026-09-17"
    game_id = "g1"


# v1.41부터 `_anchor_is_up`은 **보낼지 말지**가 아니라 **알릴지 말지**를 정한다.
# 앵커가 없는 경기도 채널로는 안 보낸다(대표님 지시). 대신 그런 항목은
# 끝내 자리를 못 찾고 사라지므로, 사라지기 전에 운영 알림에 싣는다.
check("★★★ 앵커가 나간 경기는 집이 있다고 본다",
      T._anchor_is_up(_It(), _Led([1234]), "ch"))
check("★★★ 앵커가 없으면 집이 없다고 본다 (그래야 알림이 뜬다)",
      not T._anchor_is_up(_It(), _Led(None), "ch"))

# 답이 **그 경기 댓글창 안**에 남아야 다른 손님도 본다. 대표님 지적:
# *"봇이 그 주제 안에서 바로 대답해줘야, 다른 손님들도 볼 수 있지"*.
import inspect as _insp2                                         # noqa: E402
_src_answer = _insp2.getsource(T._answer_questions)
check("★★★ 봇 답은 개인 메시지가 아니라 **그 대화방**으로 간다",
      'a.get("chat_id")' in _src_answer and '"chat_id": a["user"]' not in _src_answer)
check("★★★ 답을 그 경기 실타래에 못 박는다 (원글이 지워져도 새지 않게)",
      "message_thread_id" in _src_answer, "실타래 번호를 안 넘깁니다")
check("  ↳ 받아올 때부터 실타래 번호를 들고 온다",
      '"thread_id"' in _insp2.getsource(D.poll))


print()
print("=" * 64)
print(f"결과: {PASS} PASS / {len(FAIL)} FAIL")
for line in FAIL:
    print(f"  ✗ {line}")
print("=" * 64)
sys.exit(1 if FAIL else 0)
