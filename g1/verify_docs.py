#!/usr/bin/env python3
"""문서와 코드가 어긋나면 짖는다 — **말도 계약이다**.

이 저장소에서 하루에 네 번 뚫린 규칙이 있다: **동작을 바꿔 놓고 코드만
고치고 「말」을 안 고친다.** 대표님이 직접 잡으신 적도 있다 —
*"서버주소와 열쇠넣으라고 글귀가 아직 그대로 있네"*.
2026-09-25 전수 대조에서 **옛 말이 41군데** 나왔다. 사람이 사실과 반대로
알면 사고보다 나쁘다(심각도 높음).

그래서 대조를 여기 **게이트로 굳힌다.** 값은 손으로 적지 않고 계약·워크플로에서
직접 읽어 문서와 맞춘다 — 그래야 한쪽만 바뀌는 날 여기서 걸린다.
"""
import pathlib
import re
import sys

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]
import contract as C                                          # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASS, FAIL = 0, []


def check(name: str, ok, detail: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  FAIL  {name}  {detail}")


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


GUIDE = read("운영가이드.md")
README = read("README.md")
FLOW = read(".github/workflows/tick.yml")
DOCS = GUIDE + "\n" + README

print("문서 대조 — 사람 눈에 닿는 말이 지금 코드와 맞는가")

# ── ① 리그 수 ─────────────────────────────────────────────────────
_n_league = len(list(C.League))
check(f"리그 수가 문서와 맞는다 (지금 {_n_league}개)",
      f"{_n_league}개 리그" in DOCS and "9개 리그" not in DOCS,
      "문서에 '9개 리그'가 남아 있거나 실제 수가 안 적혀 있다")

# ── ② 시계 간격 ───────────────────────────────────────────────────
_m = re.search(r"TICK_INTERVAL_MINUTES:\s*'(\d+)'", FLOW)
_iv = int(_m.group(1)) if _m else 0
check(f"시계 간격이 문서와 맞는다 (워크플로 {_iv}분)",
      _iv > 0 and f"{_iv}분마다" in DOCS and "5분마다" not in DOCS,
      f"워크플로 {_iv}분인데 문서에 '5분마다'가 남아 있다")
check("  ↳ 워크플로 주석에도 옛 간격이 없다",
      "5분마다" not in FLOW and "하루 288번" not in FLOW,
      "주석이 틀리면 사람이 틀린 값을 믿는다 (이 파일이 스스로 그렇게 적어 뒀다)")

# ── ③ 꺼 둔 콘텐츠를 "나갑니다"라고 적지 않는다 ─────────────────────
_off = {c.value for c in C.DISABLED_CONTENT_TYPES}
check(f"꺼 둔 콘텐츠({', '.join(sorted(_off))})를 나간다고 적지 않는다",
      "start_alert" not in _off or "**시작 알림도 나갑니다" not in DOCS,
      "start_alert 는 꺼져 있는데 문서가 나간다고 말한다")

# ── ④ 없어진 카드 경로 ────────────────────────────────────────────
_v4_alive = (ROOT / "cards" / "v4.html").exists()
check("없어진 카드 경로를 '여기만 고치면 된다'고 안내하지 않는다",
      "`cards/v4.html` 하나만 고치면" not in DOCS,
      f"cards/v4.html 존재={_v4_alive} — 고쳐도 채널 카드는 안 바뀐다")

# ── ⑤ 알림 대상이 비었을 때의 동작 ────────────────────────────────
_tick = read("g1/tick.py")
_silent = "오류 알림을 보내지 않습니다" in _tick
check("ALERT_CHAT_ID 를 비웠을 때의 동작이 문서와 같다",
      not _silent or "발행 채널로 갑니다" not in DOCS,
      "코드는 '아무 데도 안 보냄'인데 문서는 '발행 채널로 감'이라고 적었다")

# ── ⑥ 살아 있는 콘텐츠가 문서에 빠져 있지 않다 ────────────────────
#   투표는 실제로 나가는데 문서 어디에도 없었다(2026-09-25).
_live_missing = [
    c.value for c in (C.ContentType.POLL, C.ContentType.PERIOD_FLASH)
    if c not in C.DISABLED_CONTENT_TYPES and c not in C.NOT_BUILT_YET]
_words = {"poll": "투표", "period_flash": "구간 속보"}
_gone = [v for v in _live_missing if _words[v] not in GUIDE]
check("살아 있는 콘텐츠가 운영가이드 표에 다 있다",
      not _gone, f"표에 없는 것: {_gone} — 표가 완전한 목록인 줄 알게 된다")

# ── ⑦ 문서가 스스로와 모순되지 않는다 ─────────────────────────────
check("오늘의 경기를 '한 통'이라고 적지 않는다 (종목별로 쪼갰다)",
      "오늘의 경기 1 +" not in GUIDE,
      "v1.92에서 목차 + 종목별 글로 쪼갰다")

# ── ⑧ 검증 합계가 실제와 크게 어긋나지 않는다 ─────────────────────
_m2 = re.search(r"\*\*([\d,]+)건 통과", GUIDE)
_claim = int(_m2.group(1).replace(",", "")) if _m2 else 0
_suites = len(list((ROOT / "g1").glob("verify_*.py")))
check(f"문서가 적은 검증 벌 수가 실제와 맞는다 (실제 {_suites}벌)",
      f"{_suites}벌" in GUIDE, f"문서: {GUIDE.count('벌')}회 언급")
check(f"문서가 적은 검증 합계({_claim:,}건)가 비어 있지 않다",
      _claim > 0, "합계를 안 적으면 아무도 줄어든 것을 모른다")

if __name__ == "__main__":
    print(f"\n결과: {PASS} PASS / {len(FAIL)} FAIL")
    for line in FAIL:
        print(f"  ✗ {line}")
    sys.exit(1 if FAIL else 0)
