"""공개 저장소 안전 점검 — 밖에 나가면 안 되는 것이 섞여 있는지 (v1.11c 신설).

저장소를 **공개**로 두기로 했으므로, 이 폴더에 있는 모든 것이 인터넷에 공개된다.
"토큰만 안 새면 된다"는 맞지만, 새는 경로가 토큰만은 아니다:

  · 봇 토큰                — 암호 보관함(Secrets)에 있으므로 코드에는 없어야 한다
  · **채널 ID**            — 발송 대장에 평문으로 들어가던 것을 지문으로 바꿨다
  · 개인 chat_id           — 오류 알림 목적지. 대표님 개인 텔레그램 ID다
  · 개발 컴퓨터 경로       — 사람 이름·폴더 구조가 드러난다
  · football-data 키       — 나중에 발급받을 것. 미리 막아둔다

**손으로 grep 하면 반드시 한 번은 빼먹는다.** 그래서 스크립트로 만든다
(최소 폰트 28px을 상수로만 두었다가 하루 만에 어긴 것과 같은 이유다).

저장소를 공개로 바꾸기 전, 그리고 코드를 고칠 때마다 이걸 돌린다.

    python g1/verify_public.py

`0 FAIL`이면 공개해도 안전하다.
"""
from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROOT = pathlib.Path(__file__).resolve().parents[1]

ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail += 1
        print(f"  FAIL  {name}\n        {detail}")


# 검사에서 뺄 것 — 이 파일 자신(패턴이 예시로 들어 있다)과 캐시·산출물
SKIP_DIRS = {".git", "__pycache__", "dryrun", "cache", "render", "node_modules",
             ".cache", ".npm", ".local", "site-packages", ".venv", "venv"}
SELF = pathlib.Path(__file__).name


def files() -> list[pathlib.Path]:
    out = []
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name == SELF:
            continue
        if p.suffix in {".png", ".jpg", ".jpeg", ".zip", ".woff", ".woff2", ".ttf"}:
            continue
        out.append(p)
    return out


def scan(pattern: str, label: str, flags=0) -> list[str]:
    """패턴에 걸리는 곳을 모은다. 파일:줄 형태로."""
    rx = re.compile(pattern, flags)
    hits = []
    for p in files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}  {line.strip()[:90]}")
    return hits


print("=" * 62)
print("공개 저장소 안전 점검")
print("=" * 62)

print("\n1. 자격증명")
# 텔레그램 봇 토큰: 숫자 8~10자리 : 영숫자 35자
h = scan(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}", "봇 토큰")
check("봇 토큰 형태가 없다", not h, "\n        ".join(h[:3]))

h = scan(r"\bAIza[A-Za-z0-9_-]{30,}|\bghp_[A-Za-z0-9]{30,}|\bsk-[A-Za-z0-9]{20,}", "기타 키")
check("구글·깃허브·기타 API 키 형태가 없다", not h, "\n        ".join(h[:3]))

# 환경변수·시크릿 참조가 아닌 실제 대입
h = [x for x in scan(r"(?i)(token|secret|api_key|apikey|password)\s*=\s*[\"'][^\"'{$<][^\"']{11,}",
                     "하드코딩")
     if not any(w in x for w in ("os.environ", "getenv", "secrets.", "TEST", "dummy",
                                 "realtoken", "T_X", "example"))]
check("코드에 자격증명을 직접 적은 곳이 없다", not h, "\n        ".join(h[:3]))

print("\n2. 채널·개인 식별자")
# 텔레그램 채널 ID: -100으로 시작하는 13자리
h = [x for x in scan(r"-100\d{10}", "채널 ID") if "9999999999" not in x]
check("실제 채널 ID가 없다 (대장은 지문으로 저장)", not h, "\n        ".join(h[:3]))

# 개인 chat_id — 9~10자리 단독 숫자가 chat 관련 문맥에
h = scan(r"(?i)(alert_chat_id|my_chat_id|개인\s*chat)\s*[=:]\s*[\"']?\d{6,}", "개인 ID")
check("개인 chat_id가 없다", not h, "\n        ".join(h[:3]))

print("\n3. 개발 환경 흔적")
h = [x for x in scan(r"/home/[a-z]+/|/Users/[A-Za-z]+/|C:\\\\Users\\\\", "로컬 경로")
     if "sys.path" not in x]
check("개발 컴퓨터 경로가 없다", not h, "\n        ".join(h[:3]))

h = scan(r"[a-zA-Z0-9._%+-]+@(?!users\.noreply\.github\.com)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
         "이메일")
h = [x for x in h if "example." not in x and "@2x" not in x]
check("개인 이메일이 없다", not h, "\n        ".join(h[:3]))

print("\n4. 발송 대장 — 커밋되는 파일이라 특히 중요")
led = ROOT / "state" / "ledger.jsonl"
if led.exists():
    txt = led.read_text(encoding="utf-8", errors="ignore")
    check("대장에 실제 채널 ID가 없다", not re.search(r"-100\d{10}", txt))
    check("대장에 지문 형식이 쓰인다", ("\"chat_id\": \"ch" in txt) or not txt.strip())
else:
    check("대장이 아직 없다 (첫 발송 전 — 정상)", True)

# 지문이 실제로 되돌릴 수 없는지 (해시 길이·형식)
from contract import channel_ref                              # noqa: E402
ref = channel_ref("-1004387121384")
check(f"채널 지문이 원본을 안 담는다 ({ref})",
      "1004387121384" not in ref and ref.startswith("ch") and len(ref) == 14, ref)
check("같은 채널은 같은 지문 (중복 방지가 계속 작동)",
      channel_ref("-100111") == channel_ref("-100111"))
check("다른 채널은 다른 지문",
      channel_ref("-100111") != channel_ref("-100222"))

print("\n5. .gitignore — 커밋하면 안 되는 것이 빠져 있나")
gi = ROOT / ".gitignore"
if gi.exists():
    g = gi.read_text(encoding="utf-8")
    check("경기 데이터는 커밋 제외 (5분마다 커밋 폭증 방지)", "state/games" in g)
    check("렌더 산출물은 커밋 제외", "render" in g)
    # 대장은 **반드시** 커밋되어야 한다 — 무시되면 중복 발송이 난다
    ignored = re.search(r"^\s*state/(\*|ledger|$)", g, re.M)
    check("발송 대장은 커밋 대상 (무시하면 중복 발송)", not ignored,
          "state/ledger.jsonl 이 .gitignore에 걸려 있다")
else:
    check(".gitignore 존재", False, ".gitignore가 없다")

print("\n6. 워크플로 — 비밀값을 파일에 적지 않았나")
wf = ROOT / ".github" / "workflows" / "tick.yml"
if wf.exists():
    w = wf.read_text(encoding="utf-8")
    secrets_used = re.findall(r"\$\{\{\s*secrets\.(\w+)\s*\}\}", w)
    check(f"비밀값을 secrets로만 참조 ({len(secrets_used)}개)", len(secrets_used) >= 3,
          str(secrets_used))
    check("워크플로에 실제 값이 없다",
          not re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}|-100\d{10}", w))
    check("대장을 항상 커밋한다 (실패해도)", "if: always()" in w)
else:
    check("워크플로 존재", False)

print(f"\n결과: {ok} PASS / {fail} FAIL")
if fail:
    print("\n⚠️ 위 항목을 고치기 전에는 저장소를 공개로 두지 마세요.")
else:
    print("\n공개 저장소로 두어도 안전합니다.")
sys.exit(1 if fail else 0)
