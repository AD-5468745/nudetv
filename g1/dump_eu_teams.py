"""유럽 대회 팀 목록을 저장소에 남긴다 (v1.14b 신설).

**왜 필요한가 — 닭과 달걀이었다.**

`football_data.FootballDataAdapter`는 `contract.TEAM_NAMES[리그]`가 비어 있으면
생성자에서 막는다(카드에 'MCI'·'ARS' 같은 코드가 찍히는 것을 막으려고).
그런데 그 표를 채우려면 소스가 쓰는 **TLA(세 글자 코드)**를 알아야 하고,
TLA는 그 어댑터를 통해서만 받을 수 있었다. 서로가 서로를 막고 있었다.

이 도구는 **어댑터를 지나지 않고** 팀 목록만 받아 `state/eu_teams.json`에 남긴다.
그러면 사람이(또는 다음 세션이) 그 파일을 읽어 한글 표기표를 만들 수 있다.

**왜 저장소에 남기나.** 캐시(`g1/cache`)는 깃허브 액션 캐시로만 나르므로
저장소에는 없다 — 우리가 읽을 방법이 없다. 고친 사람이 자기 수정을 확인할 수
없으면 그 수정은 '했다'로 끝난다(약점 119·124).
`state/`에 두면 워크플로가 대장과 함께 커밋해 클론만으로 읽을 수 있다.

**공개 저장소에 안전한가.** 남기는 것은 대회 코드·팀 TLA·팀 이름·나라뿐이다.
전부 소스가 인증 없이도 공개하는 값이고, 토큰은 어디에도 안 들어간다.

**덤으로 무료 등급 경계를 실측한다.** 유로파리그(`EL`)·MLS(`MLS`)는 코드가
API에 실재하지만 무료 등급에 포함되는지는 웹 문서로만 알고 있었다.
여기서 한 번 찔러 보면 403인지 200인지로 **확정**된다 —
웹 글을 믿고 유료 결제부터 하지 않아도 된다.

돌리는 법:  FOOTBALL_DATA_TOKEN=... python3 g1/dump_eu_teams.py
(틱이 하루 한 번 자동으로 부른다. 아래 `maybe_dump`)
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

_API = "https://api.football-data.org/v4"
TOKEN_ENV = "FOOTBALL_DATA_TOKEN"

# 무료 등급이 준다고 알려진 상시 리그·대회 10개 + **경계를 재 볼 둘**.
# 순서가 곧 시도 순서다. 무료 10개를 먼저 받고 경계를 마지막에 찌른다 —
# 분당 10회 제한이 있으므로 정말 필요한 것부터 받는다.
FREE_CODES = ["PL", "PD", "SA", "BL1", "FL1", "CL",
              "DED", "PPL", "ELC", "BSA"]
PROBE_CODES = ["EL", "MLS"]          # 무료인지 **모른다** — 응답 코드로 확정한다

# 분당 10회 제한. 12개를 연달아 받으면 반드시 걸린다.
SLEEP_BETWEEN = 7.0

OUT = pathlib.Path(os.environ.get("NUDETV_STATE", "state")).resolve() / "eu_teams.json"
# 팀 구성은 시즌 중에 거의 안 바뀐다. 하루 한 번이면 넉넉하다.
REFRESH_SECONDS = 24 * 3600


def _get(code: str, token: str) -> tuple[int, dict | None]:
    """(HTTP 상태, 본문). 403은 오류가 아니라 **답**이다 — 무료 등급 밖이라는 뜻."""
    req = urllib.request.Request(
        f"{_API}/competitions/{code}/teams",
        headers={"X-Auth-Token": token, "User-Agent": "nudetv-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:                                        # noqa: BLE001
        return 0, None


def dump(token: str) -> dict:
    """대회별 팀 목록을 모은다. **한 대회가 막혀도 나머지는 계속 받는다.**"""
    out: dict = {"at": datetime.now(timezone.utc).isoformat(), "competitions": {}}
    for i, code in enumerate(FREE_CODES + PROBE_CODES):
        if i:
            time.sleep(SLEEP_BETWEEN)
        st, body = _get(code, token)
        rec: dict = {"http": st}
        if st == 200 and body:
            rec["teams"] = [
                # **소스가 준 값만 담는다.** 한글 이름은 여기서 지어내지 않는다 —
                # 사람이 표준 표기를 보고 채운다(약점 90: 음역을 지어내지 않는다).
                {"tla": t.get("tla"), "name": t.get("name"),
                 "short": t.get("shortName"),
                 "area": (t.get("area") or {}).get("name")}
                for t in (body.get("teams") or [])]
            rec["count"] = len(rec["teams"])
        elif st == 403:
            rec["note"] = "무료 등급 밖 (구독 필요)"
        elif st == 429:
            rec["note"] = "레이트리밋 — 다음 실행에서 다시 받는다"
        elif st == 0:
            rec["note"] = "네트워크 실패"
        out["competitions"][code] = rec
        print(f"  {code:5} HTTP {st}  {rec.get('count', rec.get('note', ''))}")
    return out


def maybe_dump(now: datetime | None = None) -> str | None:
    """틱이 부른다. 키가 없거나 아직 신선하면 **아무 일도 안 한다.**

    돌려주는 값은 사람이 볼 한 줄(없으면 None) — 틱 로그에 그대로 실린다.
    """
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        return None                       # 키가 없다 — 조용히 넘어간다(정상 상태)
    now = now or datetime.now(timezone.utc)
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
            at = datetime.fromisoformat(prev["at"])
            if (now - at).total_seconds() < REFRESH_SECONDS:
                return None               # 아직 신선하다
        except (OSError, ValueError, KeyError):
            pass                          # 깨졌으면 새로 받는다
    data = dump(token)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = [c for c, r in data["competitions"].items() if r.get("count")]
    no = [c for c, r in data["competitions"].items() if r["http"] == 403]
    return (f"유럽 팀 목록 갱신 — 받음 {len(ok)}개 대회"
            + (f" · 구독 밖 {','.join(no)}" if no else ""))


if __name__ == "__main__":
    tok = os.environ.get(TOKEN_ENV, "").strip()
    if not tok:
        print(f"{TOKEN_ENV} 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(2)
    d = dump(tok)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {OUT}")
