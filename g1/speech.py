# -*- coding: utf-8 -*-
"""말투 — 채널 글을 **존댓말 한 목소리**로 맞춘다 (v1.37 신설).

대표님 지시(2026-09-17): *"사람이 직접 쓴듯한 자연스러운 대화체로"*.

지금 산문은 전부 기사체(`두산이 1회말 두 점을 먼저 냈다`)다. 새로 쓰는
총평만 존댓말로 하면 **한 메시지 안에 두 목소리**가 섞인다 — 그게 가장
어색하다. 그래서 문장을 고치는 대신 **내보내기 직전에 어미만 바꾼다**:
산문 생성기 700줄을 건드리지 않으므로 사실 관계가 흔들리지 않는다.

────────────────────────────────────────────────────────────────────
**모르는 어미는 손대지 않는다.**

한국어 어미 변환은 규칙이 겹친다 — `없다`(형용사)는 `없습니다`지만
`4패다`(명사)는 `4패입니다`이고, 둘 다 '받침 + 다'다. 규칙으로 뭉뚱그리면
언젠가 `4패습니다`가 채널에 나간다.

그래서 **실제로 쓰이는 어미를 전수로 뽑아 표로 박았다**(2026-09-17 실측:
최근 40경기 산문 241문단에서 서로 다른 어미 28가지). 표에 없는 어미는
**그대로 둔다** — 바꾸지 않은 문장은 어색할 뿐이지만, 잘못 바꾼 문장은
틀린 말이 된다. 못 바꾼 것은 `unconverted()`가 세어 검증이 잡는다.
"""
from __future__ import annotations

import re

# ── 어미 표 ────────────────────────────────────────────────────
#
# **긴 것부터 본다.** `이다`보다 `중이다`가 먼저 걸려야 `중입니다`가 된다.
# (지금은 둘 다 `입니다`로 끝나 결과가 같지만, 표가 늘면 달라진다.)

# ① 통째로 바꾸는 어미 — 앞 글자와 무관하게 성립한다.
_WHOLE: tuple[tuple[str, str], ...] = (
    ("이다", "입니다"),
    ("있다", "있습니다"),
    ("없다", "없습니다"),
    ("아니다", "아닙니다"),
    ("같다", "같습니다"),
    ("많다", "많습니다"),
    ("낫다", "낫습니다"),
    ("좋다", "좋습니다"),
    ("이르다", "이릅니다"),
    ("크다", "큽니다"),
    ("하다", "합니다"),          # 팽팽하다 → 팽팽합니다
)

# ② 과거형 — **받침이 ㅆ인 음절 뒤의 `다`는 언제나 `습니다`다.**
#    했다·냈다·졌다·붙었다·벌렸다·갈렸다 … 글자를 표로 박지 않고 받침을 직접 본다.
#    (처음엔 음절 표로 만들었다가 `벌렸다`·`갈렸다`를 놓쳤다 — 표는 언제나 불완전하다.)
_FINAL_SS = 20                # 한글 종성 표에서 ㅆ 의 자리
_FINAL_N = 4                  # ㄴ
_FINAL_B = 17                 # ㅂ

# ③ 현재형 `-는다` — 받침 있는 어간. 맞는다·먹는다·잡는다 → 맞습니다
_NEUN = "는다"

# ④ 명사 + `다` — `6승 4패다` → `6승 4패입니다`.
#    **명사를 표로 박는다.** 형용사와 구분할 방법이 이것뿐이다.
_NOUN_TAIL = ("패", "승", "무", "점", "차", "위", "회", "번", "건", "개", "명")


def _final(ch: str) -> int:
    """그 한글 음절의 종성 번호. 한글이 아니면 -1."""
    if not ("가" <= ch <= "힣"):
        return -1
    return (ord(ch) - 0xAC00) % 28


def _swap_final(ch: str, new_final: int) -> str:
    """음절의 종성만 갈아 끼운다. `선` + ㅂ = `섭`."""
    base = ord(ch) - 0xAC00
    return chr(0xAC00 + (base - base % 28) + new_final)


def polite_sentence(s: str) -> str:
    """문장 하나를 존댓말로. **못 바꾸면 그대로 돌려준다.**"""
    t = s.rstrip()
    if not t.endswith("다"):
        return s                       # 문장이 아니거나 이미 다른 꼴이다
    tail = s[len(t):]                  # 뒤에 붙은 공백·문장부호는 그대로 살린다

    for a, b in _WHOLE:
        if t.endswith(a):
            return t[: -len(a)] + b + tail

    if t.endswith(_NEUN):
        return t[: -len(_NEUN)] + "습니다" + tail

    if len(t) >= 2:
        prev = t[-2]
        fin = _final(prev)
        if fin == _FINAL_SS:
            return t[:-1] + "습니다" + tail
        if fin == _FINAL_N:
            # 현재형 `-ㄴ다`: 앞선다 → 앞섭니다 · 간다 → 갑니다.
            # 종성 ㄴ을 ㅂ으로 갈고 `니다`를 붙인다.
            return t[:-2] + _swap_final(prev, _FINAL_B) + "니다" + tail
        if prev in _NOUN_TAIL:
            return t[:-1] + "입니다" + tail
        # 그 밖의 받침 + 다 (좁다·짧다 꼴)는 명사와 구분할 방법이 없어 손대지
        # 않는다. 받침 없는 어간(가다·사다)도 마찬가지다 —
        # `ㅂ니다`로 뭉개면 `산다`(사다)와 `산다`(살다)가 같아진다.

    return s                           # 모르는 어미 — 그대로 둔다


# 문장 경계. 마침표 뒤 공백, 또는 문단 끝.
_SPLIT = re.compile(r"(?<=[.!?])(\s+)")


def polite(text: str) -> str:
    """문단 하나를 존댓말로. 문장 단위로 나눠 각각 바꾼다.

    **HTML 태그가 섞인 문자열에는 쓰지 않는다** — 캡션의 `<b>`·`<blockquote>`는
    `cards_v5.caption`이 나중에 붙인다. 여기 들어오는 것은 순수 문장이다.
    """
    if not text:
        return text
    out = []
    for piece in _SPLIT.split(text):
        if _SPLIT.fullmatch(piece or ""):
            out.append(piece)          # 구분자(공백)는 그대로
            continue
        # 마침표를 떼고 바꾼 뒤 다시 붙인다
        m = re.match(r"^(.*?)([.!?]?)$", piece, re.S)
        body, dot = m.group(1), m.group(2)
        out.append(polite_sentence(body) + dot)
    return "".join(out)


def polite_lines(lines) -> list:
    """문단 여러 개. 빈 줄·소제목(`■ …`)은 그대로 둔다."""
    return [polite(x) if isinstance(x, str) else x for x in (lines or [])]


def unconverted(text: str) -> list:
    """아직 기사체로 남은 문장들. **검증이 이걸 센다.**

    운영에서는 막지 않는다 — 어미 하나 때문에 그 경기 글을 통째로 잃는 것이
    더 나쁘다(`게이트가 틱을 죽이면 위반보다 나쁘다`).
    """
    bad = []
    for piece in _SPLIT.split(text or ""):
        if _SPLIT.fullmatch(piece or ""):
            continue
        body = re.sub(r"[.!?]$", "", piece).rstrip()
        # `앞섭니다`처럼 ㅂ이 앞 음절에 합쳐진 꼴도 있으므로 **`니다`로 본다.**
        # (`습니다`·`입니다`만 보다가 바꾼 문장을 안 바꿨다고 셌다.)
        if body.endswith("다") and not body.endswith("니다"):
            bad.append(body[-12:])
    return bad
