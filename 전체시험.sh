#!/bin/sh
# 검증 전 벌을 돌리고 **하나라도 실패하면 0이 아닌 코드로 끝난다.**
#
# 왜 이 파일이 있나 — 2026-09-25에 한 벌이 실패했는데 그 뒤에 `echo` 가
# 붙어 있어서 `&&` 사슬이 끊기지 않았고, **실패한 채로 배포가 나갔다**
# (v2.08 → 한국 선수 복원이 빠진 판이 잠깐 운영에 올라갔다).
# 배포 전에는 반드시 이것으로 잰다:  ./전체시험.sh && git push ...
cd "$(dirname "$0")" || exit 2
: "${NUDETV_STATE:=$PWD/state}"
export NUDETV_STATE PYTHONPATH="g1:."
pass=0
fail=0
for f in g1/verify_*.py; do
  if python3 "$f" >/dev/null 2>&1; then
    pass=$((pass + 1))
  else
    fail=$((fail + 1))
    echo "  ✗ $(basename "$f")"
    python3 "$f" 2>&1 | grep -E "^  FAIL|Error" | head -3
  fi
done
echo "시험벌 통과 $pass · 실패 $fail"
[ "$fail" -eq 0 ] || exit 1
