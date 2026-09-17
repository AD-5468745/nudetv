# -*- coding: utf-8 -*-
"""football-data.org 무료 등급에 **무엇이 들어 있는지** 재는 스크립트.

대표님이 직접 돌리십니다 — **키는 제가 보지 않습니다.**

    export FOOTBALL_DATA_TOKEN='...'          ← 대표님 키
    PYTHONPATH=. python3 g1/probe_football_data.py

────────────────────────────────────────────────────────────────────
**이 스크립트는 읽기만 합니다.** 조회(GET)만 하고, 아무것도 보내지 않고,
파일도 안 고칩니다. 채널에도 영향이 없습니다.

**키는 화면에 찍지 않습니다.** 마지막에 나오는 보고에도 키가 안 들어가므로
그 내용을 그대로 저에게 주셔도 됩니다.

무엇을 알아보나 — 유럽 축구에 **경기 전/후 자세한 정보**를 붙일 수 있는지:
  · 경기 하나에 심판·관중·라인업·교체·경기 기록이 오는가
  · 맞대결(head2head)이 오는가
  · 부상·결장이 오는가

무료 등급에서 막히는 칸은 403으로 돌아옵니다 — 그것도 **답**이므로
그대로 적습니다.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.football-data.org/v4"
TOKEN_ENV = "FOOTBALL_DATA_TOKEN"
GAP_SECONDS = 6.5          # 무료 등급은 분당 10회다 — 넉넉히 띄운다
TIMEOUT = 15

# 우리가 실제로 쓰는 대회 하나로만 본다. 여러 개를 두드리면 분당 한도에 걸린다.
COMPETITION = "PL"         # 프리미어리그


def _get(path: str, token: str):
    """(상태, 응답). 실패해도 예외를 내지 않는다 — 상태 자체가 답이다."""
    req = urllib.request.Request(
        API + path, headers={"X-Auth-Token": token,
                             "User-Agent": "nudetv-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:                                # noqa: BLE001
        return -1, f"{type(e).__name__}"


def _shape(v, depth=0):
    """값의 **모양**만 적는다 — 내용은 길게 안 찍는다."""
    if isinstance(v, dict):
        if not v:
            return "빈 dict"
        if depth >= 1:
            return f"dict({len(v)}칸)"
        return "{" + ", ".join(f"{k}: {_shape(x, depth + 1)}"
                               for k, x in list(v.items())[:10]) + "}"
    if isinstance(v, list):
        if not v:
            return "빈 목록"
        return f"목록{len(v)} → {_shape(v[0], depth + 1)}"
    if v is None:
        return "없음(null)"
    return type(v).__name__


def main() -> int:
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        print(f"⚠️  {TOKEN_ENV} 가 없습니다.\n"
              f"    export {TOKEN_ENV}='대표님 키' 를 먼저 하신 뒤 다시 돌려 주세요.")
        return 2

    print("=" * 62)
    print("football-data.org 무료 등급 — 무엇이 오는지 재기")
    print("=" * 62)
    print("(키는 화면에 안 찍힙니다. 아래 내용을 그대로 주시면 됩니다.)\n")

    # ① 최근 끝난 경기 하나를 찾는다
    st, d = _get(f"/competitions/{COMPETITION}/matches?status=FINISHED", token)
    if st != 200 or not isinstance(d, dict):
        print(f"① 경기 목록: ✗ 상태 {st}")
        print("   → 키가 이 대회를 못 보거나, 키 자체가 막혀 있습니다.")
        return 1
    ms = d.get("matches") or []
    print(f"① 경기 목록: ✓ 끝난 경기 {len(ms)}건")
    if not ms:
        print("   → 이번 시즌 끝난 경기가 없습니다. 시즌 중에 다시 돌려 주세요.")
        return 1
    m = ms[-1]
    mid = m.get("id")
    home = ((m.get("homeTeam") or {}).get("shortName")
            or (m.get("homeTeam") or {}).get("name"))
    away = ((m.get("awayTeam") or {}).get("shortName")
            or (m.get("awayTeam") or {}).get("name"))
    print(f"   표본 경기: {away} vs {home} (번호 {mid})")
    print(f"   목록의 한 경기에 오는 칸: {sorted(m)}\n")

    checks = (
        ("② 경기 하나 자세히", f"/matches/{mid}"),
        ("③ 맞대결(head2head)", f"/matches/{mid}/head2head?limit=5"),
        ("④ 순위표", f"/competitions/{COMPETITION}/standings"),
        ("⑤ 득점 순위", f"/competitions/{COMPETITION}/scorers?limit=5"),
    )
    for label, path in checks:
        time.sleep(GAP_SECONDS)
        st, r = _get(path, token)
        if st != 200:
            why = {403: "무료 등급에서 막힘", 429: "분당 한도 초과 — 잠시 뒤 다시",
                   404: "그런 창구 없음"}.get(st, "실패")
            print(f"{label}: ✗ 상태 {st} ({why})")
            continue
        if not isinstance(r, dict):
            print(f"{label}: ✗ 응답이 dict가 아님")
            continue
        print(f"{label}: ✓")
        for k, v in list(r.items())[:14]:
            print(f"     {k}: {_shape(v)}")
        # ③④는 **칸 이름이 정확히 무엇인지**가 중요하다 — 그걸로 코드를 쓴다.
        if label.startswith("③"):
            agg = r.get("aggregates") or {}
            print(f"     · 누적 칸: {sorted(agg)}")
            for side in ("homeTeam", "awayTeam"):
                if isinstance(agg.get(side), dict):
                    print(f"     · {side} 칸: {sorted(agg[side])}")
            ms = r.get("matches") or []
            if ms:
                print(f"     · 지난 경기 한 건 칸: {sorted(ms[0])}")
        if label.startswith("④"):
            tb = (r.get("standings") or [{}])[0]
            print(f"     · 표 칸: {sorted(tb)}")
            rows = tb.get("table") or []
            if rows:
                print(f"     · 한 팀 줄 칸: {sorted(rows[0])}")
                _r0 = rows[0]
                _t = _r0.get("team") or {}
                print(f"     · 팀 칸: {sorted(_t)}")
                print(f"     · 1위 표본: 순위 {_r0.get('position')} · "
                      f"{_t.get('tla')} · {_r0.get('playedGames')}경기 · "
                      f"승{_r0.get('won')} 무{_r0.get('draw')} 패{_r0.get('lost')} · "
                      f"승점 {_r0.get('points')} · 폼 {_r0.get('form')}")
        # 우리가 특히 궁금한 칸
        if label.startswith("②"):
            for want, ko in (("referees", "심판"), ("attendance", "관중"),
                             ("lineup", "라인업"), ("bench", "교체 대기"),
                             ("statistics", "경기 기록"), ("goals", "득점자"),
                             ("bookings", "경고·퇴장"),
                             ("substitutions", "교체")):
                here = want in r or any(
                    isinstance(x, dict) and want in x
                    for x in (r.get("homeTeam"), r.get("awayTeam")) if x)
                print(f"     · {ko}({want}): {'있음' if here else '없음'}")
        print()

    print("=" * 62)
    print("여기까지입니다. 위 내용을 그대로 클로드에게 주세요.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
