"""어댑터가 조용히 버린 것을 담는 공통 그릇 (v1.11i 신설).

**왜 만들었나.** 어댑터들은 건너뛴 것·못 푼 것·캐시로 버틴 것을 이미 전부 적어두고
있었다. 그런데 이름이 어댑터마다 달랐다 —
`MlbAdapter.skipped_unknown` · `MlbAdapter.skipped_types` ·
`KblAdapter.unresolved` · `KblAdapter.skipped_categories` ·
`LckAdapter.skipped_placeholder` · `LckAdapter.cache_age_seconds` ·
`KboAdapter.unknown_notes`.
읽는 곳이 검증 스크립트뿐이라 **운영(tick)은 한 번도 안 읽었다.**
실제로 LCK가 Leaguepedia 레이트리밋에 걸려 48시간 묵은 스냅샷으로 카드를 렌더하는
동안 어디에도 표시가 없었다. 이름이 제각각이면 읽는 쪽이 리그마다 새로 짜야 하고,
새 리그를 붙일 때마다 또 잊는다. 그래서 **이름 하나로 통일한다.**

계약 (tick이 이것만 알면 된다):
  · `adapter.skipped_report()` → dict. 비어 있으면 이번 수집에 버린 것이 없다.
        {라벨: 개수}        — 건수만 세는 것 (예: "미등록 상태로 건너뜀": 3)
        {라벨: [예시, …]}   — 예시가 필요한 것 (예: 처음 보는 취소 사유)
        {라벨: "문장"}      — 상태 설명 (예: "캐시로 버팀": "48.2시간 묵음")
  · `adapter.notices`        → list[str]. 위 dict를 사람이 읽는 한 줄씩으로 편 것.
  · `adapter.cache_age_seconds` → float. **0보다 크면 묵은 캐시로 렌더 중이다.**
        캐시를 쓰는 어댑터만 값이 실리지만, 속성 자체는 전 어댑터가 갖는다
        (읽는 쪽이 `hasattr`을 매번 하지 않아도 되게).
        이 값은 `skipped_report()` 안에도 **반드시 함께** 들어간다 —
        속성만 두면 알림에 싣는 것을 또 잊는다. 리그가 빠지는 것보다
        묵은 데이터를 새것인 척 내보내는 것이 나쁘다는 판단이 이 파일의 이유다.

수집을 시작할 때마다 `reset_notices()`를 부른다. 안 부르면 지난 틱의 건수가
누적돼 "3건 건너뜀"이 며칠 뒤 "300건"이 된다 — 경보가 소음이 되는 전형적인 경로다.
"""
from __future__ import annotations

from typing import Any


# 예시는 몇 개까지 들고 있을 것인가. 전부 들고 있으면 DM이 수백 줄이 된다.
MAX_SAMPLES = 5


class NoticeMixin:
    """어댑터에 섞어 쓴다. `__init__`을 요구하지 않는다.

    어댑터마다 생성자 모양이 달라서, 믹스인이 `__init__`을 강제하면
    모든 어댑터의 생성자를 고쳐야 한다. 저장소를 지연 생성해 그 결합을 없앤다.
    """

    # 캐시를 안 쓰는 어댑터도 이 속성을 갖는다(읽는 쪽에서 hasattr 분기 금지).
    cache_age_seconds: float = 0.0

    # ── 내부 저장소 ────────────────────────────────────────────
    @property
    def _notice_counts(self) -> dict[str, int]:
        d = self.__dict__.get("_nc")
        if d is None:
            d = self.__dict__["_nc"] = {}
        return d

    @property
    def _notice_samples(self) -> dict[str, list[str]]:
        d = self.__dict__.get("_ns")
        if d is None:
            d = self.__dict__["_ns"] = {}
        return d

    @property
    def _notice_texts(self) -> dict[str, str]:
        d = self.__dict__.get("_nt")
        if d is None:
            d = self.__dict__["_nt"] = {}
        return d

    # ── 기록 ──────────────────────────────────────────────────
    def reset_notices(self) -> None:
        """수집 시작마다 부른다. 지난 틱의 건수가 누적되면 경보가 소음이 된다."""
        self._notice_counts.clear()
        self._notice_samples.clear()
        self._notice_texts.clear()
        self.cache_age_seconds = 0.0

    def note(self, label: str, sample: Any = None, *, count: int = 1) -> None:
        """건수를 센다. 예시를 주면 앞의 몇 개만 함께 보관한다."""
        self._notice_counts[label] = self._notice_counts.get(label, 0) + count
        if sample is not None:
            lst = self._notice_samples.setdefault(label, [])
            s = str(sample)[:140]
            if len(lst) < MAX_SAMPLES and s not in lst:
                lst.append(s)

    def note_text(self, label: str, text: str) -> None:
        """건수가 아니라 상태를 적는다(예: '48.2시간 묵은 캐시로 버팀')."""
        if text:
            self._notice_texts[label] = str(text)[:200]

    def note_cache_age(self, age_seconds: float) -> None:
        """캐시로 버틴 사실은 속성과 보고서 **양쪽**에 남긴다.

        속성에만 두면 알림에 싣는 것을 잊는다 — 실제로 그렇게 48시간 묵은
        스냅샷이 아무 표시 없이 나갔다.
        """
        age = float(age_seconds or 0.0)
        self.cache_age_seconds = age
        if age > 0:
            self.note_text("캐시로 버팀(묵은 데이터)", f"{age / 3600:.1f}시간 전 스냅샷")

    # ── 읽기 ──────────────────────────────────────────────────
    def skipped_report(self) -> dict[str, Any]:
        """tick이 알림에 싣는 값. 버린 것이 없으면 빈 dict.

        **건수와 예시를 다른 항목으로 낸다.** 예시 목록을 값으로 그대로 주면
        읽는 쪽이 목록 길이를 건수로 읽는다 — 예시는 앞의 몇 개만 들고 있으므로
        28건 버린 것이 '5건'으로 보고된다. 실제로 그렇게 축소 보고될 뻔했다.
        """
        out: dict[str, Any] = {}
        for label, n in sorted(self._notice_counts.items()):
            if n <= 0:
                continue
            out[label] = n                     # ← 진짜 건수
            samples = self._notice_samples.get(label)
            if samples:
                out[f"{label} 예시"] = " / ".join(samples[:2])
        out.update(self._notice_texts)
        return out

    @property
    def notices(self) -> list[str]:
        """사람이 읽는 한 줄씩."""
        lines: list[str] = []
        for label, n in sorted(self._notice_counts.items()):
            if n <= 0:
                continue
            ex = self._notice_samples.get(label)
            lines.append(f"{label} {n}건" + (f" 예) {ex[0]}" if ex else ""))
        for label, txt in sorted(self._notice_texts.items()):
            lines.append(f"{label}: {txt}")
        return lines
