"""NPB 수집 어댑터 (v1.11 신설).

전 리그 중 **유일하게 HTML을 파싱한다.** 공식 JSON API가 없다.
그래서 게이트를 다른 어댑터보다 촘촘히 건다 — 구조가 바뀌면 조용히 틀리는 대신 막힌다.

페이지: `https://npb.jp/games/{시즌}/schedule_{월}_detail.html`

한 경기 = `<tr id="date0801">` 한 행.
  · 날짜는 `<th rowspan>`에 그 날 첫 행에만 있다 → 이월해야 한다 (KBO와 같은 함정)
  · `team1`/`team2` · `score1`/`score2` · `place` · `time`
  · **취소는 `<div class="cancel">中止</div>` — 별도 클래스다.**
    G0 4차 보고서는 "state 자리에 中止가 들어간다"고 했는데 그것도 틀렸다.
    그대로 믿고 만들었더니 2026시즌 취소 33건을 전부 놓쳤다(0건으로 읽혔다).
    "0건은 항상 의심"이 아니었으면 그대로 나갈 뻔했다.
cancel 칸에 실제로 들어오는 값 (2024~2026 전수): 中止 85 · ノーゲーム 5 · (予備日) 3
`(予備日)`는 취소가 아니라 **예비일 표시**다 — 경기 자체가 편성되지 않은 칸이라 건너뛴다.

── `state`에 관한 정정 (v1.11i 전수조사) ────────────────────────────
전에는 "`state`가 `（横浜）9回表` 같은 값을 주니 그걸로 진행 중을 판정한다"고 적혀 있었다.
**그런 값은 한 건도 없다.** 2026시즌 895행 전수 스캔 결과 `state`는
`'-'`(경기가 편성된 모든 행)와 `없음`(취소 행) 두 가지뿐이다. 점수가 있는 717행도
전부 `'-'`다. 즉 `state`로 LIVE를 판정하던 분기는 **실행 자체가 불가능한 코드**였다.
사고가 안 난 이유는 판정이 옳아서가 아니라 페이지가 늦게 갱신되기 때문이었다.

── 그럼 무엇이 '종료' 신호인가 (실제 HTML을 다시 뜯어 확인) ──────────
같은 행 안에 **세 신호가 함께** 들어온다. 2026시즌 895행 분포:
    717행  점수 O · `<div class="pit">勝：…/敗：…`(무승부면 `分：…`) · `/scores/…` 링크 O
    136행  점수 X · pit 없음 · 링크 X                       ← 미래 경기
      5행  점수 X · pit=`先発：…`(선발투수) · 링크 X          ← 오늘 편성된 경기
     34행  cancel 있음 (링크는 있음)                          ← 취소
      3행  cancel 있음 (링크 없음)
셋이 전부 같은 방향을 가리키고 예외가 0건이다. 그중 **결과투수 표기(勝/敗/分)**를
종결 신호로 쓴다 — 링크는 취소 행에도 붙지만 결과투수는 경기가 끝나야만 붙는다.
무승부까지 `分：`으로 표기되므로 "승리투수가 없으면 안 끝났다"는 함정도 피한다.

**LIVE 신호는 정말로 없다.** 오늘(2026-09-02) 18:00 JST 시작 5경기를 20:04 JST에
다시 긁었더니 점수·링크·결과투수가 전부 그대로 비어 있었다 — 이 페이지는
경기 중에 아무것도 갱신하지 않는다. 없는 신호를 있는 척할 수 없으므로
**진행 중은 시계로만 추정한다**(시작 시각이 지났는데 아직 종료 신호가 없으면 LIVE).
추정한 LIVE에는 **점수를 절대 싣지 않는다** — 실제로 아는 것이 없기 때문이다.

**`weather`는 비어 있다.** 필드는 있지만 값이 없다 —
"필드 존재 ≠ 데이터 존재"의 사례다.

── 결과 보강 (v1.11m 신설) ───────────────────────────────────────────
**문제.** 위 머리말이 적어둔 "페이지가 늦게 갱신된다"가 실제 지연의 본체였다.
지연 분해(중앙값): 시계 뜸함 70~97분(연속 운전으로 해결) + **소스 게시 지연
NPB 314분**. 실측 발송 기록에서 NPB 결과 카드는 경기 종료 추정 후 332~1038분
뒤에 나갔다. 폴링을 아무리 촘촘히 해도 소스가 안 채우면 줄지 않는다.

**확인한 사실 (2026-09-03 19:45~19:52 UTC = 09-04 04:45 JST 실측).**
  · npb.jp `schedule_09_detail.html` — 09-03 2경기(21:14 종료)는 채워져 있었고
    09-04 5경기는 점수 빈칸 + `先発`(선발투수)만. 즉 이 페이지는 결과만 늦게 준다.
  · 네이버 `api-gw.sports.naver.com/schedule/games`(wbaseball/npb) — 같은 순간
    09-02·09-03 전 경기가 `RESULT` + 점수. **점수·홈원정 방향이 npb.jp와 100% 일치**
    (09-02 5경기 + 09-03 2경기 = 7경기 전수: 巨人1:2DeNA=YO 1:2 YK, 中日2:5広島=JN 2:5 HI …).
  · 네이버 야구 피드의 실시간성 직접 측정 — NPB 경기가 없는 시각이라 **같은 피드의
    MLB로 대신 쟀다**(60초 간격 스냅샷). 네이버 `statusInfo`가 이닝 단위로 움직였고,
    한 시점에는 ESPN보다 **1분 앞서** 있었다(19:49:41 네이버 '6회초' vs ESPN 'End 5th').
    네이버는 경기 중 갱신을 하는 소스이고 npb.jp는 하지 않는다 — 이것이 차이의 전부다.

**[경고] 그런데 네이버 하나만 믿으면 안 된다 — 같은 날 실측으로 확인했다.**
  2026-09-03 20:37~21:02 UTC, 네이버 야구 피드(MLB, NPB와 **같은 엔드포인트**)가
  CHW@HOU 경기를 **틀린 최종 점수로 게시**했다. 타임라인(60초 간격 실측):
      20:36:45  네이버 6:2 (진행 중)   · ESPN 6:2 (진행 중)
      20:37:42  네이버 **2:2** (진행 중) · ESPN **종료 6:2**
      20:41:29  네이버 **2:2 `RESULT` `winner=DRAW`** ← 종료라고 선언, 점수는 틀림
      21:01:54  여전히 2:2 (24분째 그대로)
  MLB 공식 statsapi 확인: **CHW 2 : HOU 6, Final**. 즉 네이버가 틀렸다.
  중요한 것은 **틀린 값이 잠깐이 아니라 24분 넘게 유지됐다**는 점이다 —
  "두 번 같은 값을 보면 믿는다" 같은 확인 지연으로는 못 거른다.
  ※ 정착된 값은 정확하다: 2026년 8월 NPB 종료 149경기를 npb.jp와 대조하니
    **149/149 점수·홈원정 완전 일치**였다. 문제는 **경기 직후 창(窓)** —
    보강이 일하는 바로 그 시간대다. 8월 대조는 그 창을 검증하지 못한다.

**그래서 npb.jp 자신의 '試合速報' 페이지를 2차 소스로 붙였다.**
  · 일정 페이지가 안 채운 경기도 `https://npb.jp/scores/{시즌}/{월일}/{홈}-{원정}-{N}/`
    페이지는 따로 있다. 실측(2026-09-03 종료 경기):
      `<p class="game_info">【試合終了】 ◇開始 18:00 ◇終了 21:14 …`
      `<tr class="top">`=원정 · `<tr class="bottom">`=홈 · `<td class="total-1">`=득점
      팀 표기가 `<span class="hide_pc">阪神</span>`처럼 **일정 페이지와 같은 약칭**이라
      `TEAM_CODE`를 그대로 쓴다(새 매핑표를 만들지 않는다 = 새 고장점을 안 만든다).
    9/3 s-t-21: 원정 阪神 7 · 홈 ヤクルト 4 → 일정 페이지의 4:7과 일치.
    `top`=원정/`bottom`=홈은 야구 규칙 그 자체라, v1.11c의 '홈=team1' 정정을
    **독립적으로 한 번 더 확인**해 준다.
  · **그 링크는 경기 전에도 알 수 있다.** 우리가 이미 받는 일정 페이지 안에
    `<div id="header_score">` 스트립이 있고 거기에 그날 경기의 정식 링크가 들어 있다
    (실측 04:45 JST에 `/scores/2026/0904/s-d-20/` 등 5건). **추가 요청 0회.**
  · 파서 실측 검증: 종료 4경기(9/2 g-db-21·f-h-22, 9/3 s-t-21·d-c-22)에서 점수·
    홈원정·종료시각(21:29·21:46·21:14·21:25)을 정확히 읽었고, 일정 페이지 및 네이버와
    전부 일치했다. 경기 전 5장(9/4)은 `final=False`·점수 None으로 읽혔고
    **홈·원정 5/5가 일정 페이지의 team1/team2와 같았다.**

**[주의] 아직 측정하지 못한 것 — 속보 페이지가 얼마나 빨리 바뀌는가.**
이 작업을 한 시각(04:45 JST)에는 진행 중인 NPB 경기가 없었다. 그래서 속보 페이지가
경기 종료 직후 몇 분 만에 【試合終了】로 바뀌는지는 **관측하지 못했다.**
바뀌는 것은 확실하다(어제 경기가 그렇게 되어 있다). 다만 **언제**가 미측정이다.
  · 빠르면 → 결과 카드가 종료 후 5~10분에 나간다(이 파일이 노린 것).
  · 느리면 → 보강이 안 일어나고 **지금과 똑같이 동작한다**(퇴보 없음).
어느 쪽인지는 오늘 밤(18:00 JST 시작 5경기) 알림에 그대로 찍힌다 —
`네이버는 종료·npb.jp 속보는 아직` 알림의 지속 시간이 곧 그 차이다.

**설계 (여기서 지킨 것).**
  1. **1차는 npb.jp 일정 페이지다.** 결과투수+점수가 있는 경기는 아무것도 덮지 않는다.
  2. **1차가 아직 안 준 경기만** 보강하고, 무엇을 어느 소스에서 채웠는지 `note`로 남긴다.
  3. **두 소스가 모두 '종료'라고 하고 점수가 정확히 같을 때만 싣는다.**
     하나라도 다르면(점수·홈원정 방향) **결과 없음으로 둔다.** 추측하지 않는다.
     1차가 준 결과와 네이버가 다를 때도 마찬가지로 그 경기를 결과 없음으로 되돌린다.
     늦는 것보다 틀리는 것이 나쁘다 — 09-01의 '진행 중 경기를 종료 0:0으로 발송' 사고.
  4. **어느 보조 소스가 죽어도 수집은 안 멈춘다.** 실패·타임아웃·0건·짝 못 지음은
     전부 '보강 못 함'이지 오류가 아니다. 1차 결과만으로 그대로 반환한다.
  5. **종료 판정 규칙은 그대로다.** 소스가 `RESULT`·`試合終了`라고 해도 **점수가 없으면**
     종료로 치지 않는다. 시작 전 경기는 네이버가 점수를 `0:0`으로 실어 보내므로
     (실측: 09-04 5경기 전부 `BEFORE` + `0:0`) 상태를 안 보면 그 0:0이 그대로 나간다.
     — 09-01 사고가 정확히 이 모양이었다.
  6. **팀 매핑은 `scoreref.NPB_TEAMS`를 재사용한다.** 복붙하면 한쪽만 고쳐지는 날이 온다.
     매핑 안 되는 경기(올스타 CL/PL 등)는 보강하지 않고 보고한다.
  7. **`source_key`를 속보 링크로 고정한다.** 아래 [주의] 절 참조 — 이걸 안 하면 사실이
     안 바뀌었는데 정정 카드가 나간다.

**[주의] `source_key`가 흔들리는 문제와 그 처리.**
`_parse`의 `source_key`는 `/scores/…` 링크가 있으면 링크에서, 없으면
`{시즌}{월일}-{홈}-{원정}`에서 만든다. 그런데 **그 링크는 결과와 함께 붙는다**
(895행 전수: 결과 있는 717행 전부 링크 O / 오늘 편성된 5행은 `先発`만 있고 링크 X.
2026-09-02 20:04 JST 재관측에서도 경기 중 5행 전부 링크 X).
보강해서 먼저 내보낸 경기가 `20260904-YAK-CHU`로 나가고 몇 시간 뒤 npb.jp가
게시하면서 `scores-2026-0904-s-d-20`이 되면, `contract.game_fact`가 지문에
`game_id`를 넣기 때문에 **사실이 하나도 안 바뀌었는데 '정정' 카드가 나간다**
(결과 카드 21:30 JST → npb.jp 게시 중앙값 +314분 ≈ 02:40 JST →
`CORRECTION_WINDOW_SECONDS` 6시간 안쪽이라 실제로 발동한다).
→ **보강할 때 `source_key`를 속보 링크로 바꿔 미리 맞춰 둔다.** 속보 링크가
   나중에 일정 페이지에 붙는 바로 그 링크다(형식·회차 번호까지 같다:
   9/3 행 링크 `s-t-21` = 속보 페이지 제목 '21回戦'). 링크를 못 구하면
   **보강 자체를 안 한다** — 못 맞출 키로 결과를 내보내지 않는다.
   전 리그 키 규칙을 바꾸는 방법도 있지만 그건 이미 발송된 717경기의
   `game_id`를 통째로 바꿔 훨씬 큰 정정 폭풍이 된다. 그래서 여기서는 안 한다.
"""
from __future__ import annotations

import json
import re
import sys
import time
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _http import fetch as _fetch, make_opener
from _notices import NoticeMixin

from contract import (GateError, Game, GameMeta, League, STATUS_MAP, Score, ScoreUnit,
                      Status, TEAM_NAMES, TeamRef, UnknownStatus)

JST = ZoneInfo("Asia/Tokyo")
_BASE = "https://npb.jp/games"
_OPENER = make_opener()

# 일본어 구단명 → 코드. 한글 표기는 계약의 TEAM_NAMES가 갖는다.
TEAM_CODE = {
    "巨人": "YOG", "ＤｅＮＡ": "DEN", "DeNA": "DEN", "阪神": "HAN",
    "中日": "CHU", "ヤクルト": "YAK", "広島": "HIR",
    "ソフトバンク": "SOF", "日本ハム": "NIP", "ロッテ": "LOT",
    "楽天": "RAK", "オリックス": "ORI", "西武": "SEI",
    # 올스타전은 리그 대항전이라 구단이 아니라 리그 이름이 팀 자리에 온다.
    # 게이트가 먼저 잡아줬다 — 실제 경기이므로 KBL 올스타와 같이 발송 대상으로 둔다.
    "セ・リーグ": "CEN", "パ・リーグ": "PAC",
}

_ROW = re.compile(r'<tr id="date(\d{4})"[^>]*>(.*?)</tr>', re.S)
_DIV = lambda cls: re.compile(rf'<div class="{cls}">(.*?)</div>', re.S)
_DATE_TH = re.compile(r'<th[^>]*>(\d{1,2})/(\d{1,2})', re.S)
_LINK = re.compile(r'href="(/scores/\d{4}/\d{4}/[^"]+)"')
_PIT = re.compile(r'<div class="pit">(.*?)</div>', re.S)

# 결과투수 표기 — 경기가 끝나야만 붙는다. `先発`(선발)은 경기 전에도 붙으므로 제외한다.
# 무승부는 승리투수가 없고 `分：`이 붙는다(2026시즌 12건 실측: 4/05·4/25·5/05·5/14·
# 5/27·6/05·6/06·6/07·6/28·7/02·7/03·8/20 전부 `分：`).
_RESULT_PITCHER = ("勝：", "敗：", "分：")

# ── 試合速報(박스스코어) 페이지 파싱 ────────────────────────────────
# 일정 페이지 안의 `<div id="header_score">` 스트립 = 그날 경기의 정식 링크.
# 우리가 이미 받는 HTML에 들어 있으므로 **추가 요청 없이** 링크를 얻는다.
_STRIP = re.compile(r'<div id="header_score">(.*?)</header>', re.S)
_STRIP_DATE = re.compile(r'<div class="score_box date">.*?(\d{4})<br>\s*(\d{1,2})/(\d{1,2})', re.S)
_STRIP_LINK = re.compile(r'href="(/scores/\d{4}/(\d{4})/[^"]+)"')
# 박스스코어 본문
_GAME_INFO = re.compile(r'<p class="game_info">(.*?)</p>', re.S)
_LS_DATE = re.compile(r'<time>(\d{4})年(\d{1,2})月(\d{1,2})日')
_LS_ROW = re.compile(r'<tr class="(top|bottom)">(.*?)</tr>', re.S)
_LS_SHORT = re.compile(r'<span class="hide_pc">(.*?)</span>', re.S)
_LS_TOTAL = re.compile(r'<td class="total-1">(.*?)</td>', re.S)
# 종료 표기. 【試合開始前】·【試合中】 같은 다른 값이면 종료로 안 친다(닫힌 실패).
_BOX_FINAL_MARK = "試合終了"

# 시작 시각이 지났는데 종료 신호가 없을 때 '진행 중'으로 볼 최소 경과 시간(초).
# 페이지가 경기 중에 갱신되지 않으므로 이 판정은 **시계 추정**이다. 0으로 두면
# 예정 시각 1초 뒤부터 '진행 중'이 되는데, 우천 지연으로 아직 시작도 안 한 경기가
# 그렇게 찍힌다. 야구는 지연이 흔하므로 30분 여유를 둔다.
LIVE_AFTER_SECONDS = 30 * 60


# ─────────────────────────────────────────────────────────────────────
# 네이버 보강 — 예산과 제동
# ─────────────────────────────────────────────────────────────────────
#
# 시계가 5분마다 도니 제동이 없으면 하루 288회다. 세 겹으로 막는다.
#
# (1) **구조적 제동** — 보강할 이유가 있을 때만 두드린다.
#     · 시작 후 90분이 안 지난 경기는 끝났을 리가 없다(2026시즌 최단 경기도 2시간대).
#       18:00 JST 시작이므로 첫 요청은 19:30 JST다.
#     · 1차가 이미 결과를 준 날은 **아예 요청하지 않는다.** npb.jp가 채우는 순간
#       그 날짜는 요청 대상에서 사라진다.
#     · 한 번 수집에서 물어보는 날짜는 최대 2일(어제·오늘). 3개월치를 긁어도
#       외부 요청은 최대 2회다.
# (2) **캐시** — `scoreref.ScoreReference`를 그대로 쓴다(같은 캐시 폴더·같은 형식).
#     TTL만 420초로 따로 둔다. 발행 직전 대조기의 TTL은 120초인데, 그 값을 쓰면
#     5분 시계에서 캐시가 **한 번도 안 맞아** 매 틱 네트워크로 나간다.
#     420초면 두 틱에 한 번만 나가므로 요청이 절반이 된다.
#     **왜 캐시를 늘려도 되는가**: 끝난 경기의 점수는 변하지 않는다.
#     **왜 무한정 늘리면 안 되는가**: 방금 끝난 경기를 그만큼 늦게 본다.
#     420초는 "요청 절반 / 추가 지연 최대 7분"의 교환이고, 줄이려는 지연이
#     314분이므로 이 교환은 남는 장사다.
#     ※ 캐시를 늘린 데는 두 번째 이유가 더 크다 — **깜빡임 방지**.
#       매 틱 네트워크로 나가면 네이버가 한 틱 실패했을 때 그 틱만 결과가 사라져
#       FINAL→LIVE→FINAL로 요동친다. 그건 정정 카드를 부르는 길이다.
# (3) **시간 예산** — 보강 때문에 틱이 밀리면 그 자체가 발송 지연이다.
#     전체 12초를 넘기면 남은 날짜는 '보강 못 함'으로 두고 나온다.
ENRICH_MIN_ELAPSED_SECONDS = 90 * 60        # 시작 후 이만큼 지나야 물어본다
ENRICH_MAX_ELAPSED_SECONDS = 30 * 3600      # 이보다 묵은 경기는 더 두드리지 않는다
ENRICH_MAX_DAYS = 2                         # 한 수집당 외부 요청 상한(날짜 수)
ENRICH_CACHE_TTL_SECONDS = 420.0
ENRICH_BUDGET_SECONDS = 12.0

# 속보 페이지는 **네이버가 '종료'라고 말한 경기만** 본다. 네이버 1회(하루치 전부)가
# 싸고 빠른 방아쇠이고, 속보는 경기당 한 장이라 비싸다. 순서를 반대로 두면
# 매 틱 5~6장을 받는다.
ENRICH_MAX_BOXSCORES = 8                    # 한 수집에서 받을 속보 페이지 상한
# 종료된 경기의 박스스코어는 **다시 안 바뀐다.** 그래서 길게 캐시한다 —
# 하룻밤에 경기당 한 번만 받게 된다. 아직 안 끝난 페이지는 짧게만 캐시한다
# (곧 바뀔 값이라 오래 들고 있으면 그만큼 늦게 본다).
BOX_CACHE_TTL_FINAL = 6 * 3600
BOX_CACHE_TTL_OPEN = 240.0
BOX_CACHE_DIR = pathlib.Path(__file__).resolve().parents[1] / "cache"
BOX_HTTP_TIMEOUT = 6.0
# **재시도하지 않는다.** 이건 보조 경로이고, 5분마다 도는 시계가 곧 재시도다.
# 여기서 재시도를 켜면 한 장에 최대 14초가 들어 수집 전체가 밀린다.
BOX_HTTP_RETRIES = 0

# **2차 소스 없이는 결과를 싣지 않는다.**
# 위 [경고] 절 실측(네이버가 틀린 최종 점수를 24분 넘게 게시)이 이 값의 근거다.
# 만약 운영에서 "속보 페이지도 느리더라 / 그래도 네이버만으로 내보내자"는 판단이
# 서면 **여기 한 줄만 False로** 바꾸면 된다. 그때는 '틀린 카드가 나갈 수 있다'를
# 받아들이는 결정이므로, 코드가 아니라 사람이 결정하도록 한 줄로 분리해 둔다.
REQUIRE_SECOND_SOURCE = True

# **대조기를 못 불러와도 수집은 살아 있어야 한다**(원칙 4).
# 여기서 그냥 import하면 scoreref 쪽 오류 하나가 NPB 수집 전체를 죽인다.
_ENRICH_REF = None
_ENRICH_LOAD_ERROR = ""
try:
    from scoreref import (NPB_TEAMS as NAVER_TEAM_CODES, RefGame, RefStatus,
                          ScoreReference, SOURCE_NAME)
    # 발행 직전 대조기(`scoreref.SCOREREF`)와 **캐시 폴더·호스트 최소 간격을 공유한다.**
    # (최소 간격 `_last_call`은 scoreref 모듈 전역이라 인스턴스를 나눠도 함께 지켜진다.)
    _ENRICH_REF = ScoreReference(cache_ttl=ENRICH_CACHE_TTL_SECONDS)
    _NAVER_SOURCE = SOURCE_NAME.get(League.NPB, "네이버")
except Exception as _e:                                            # noqa: BLE001
    _ENRICH_LOAD_ERROR = f"{type(_e).__name__}: {str(_e)[:100]}"
    _NAVER_SOURCE = "네이버"


def _txt(s: str | None) -> str:
    if s is None:
        return ""
    return re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", s)).replace(" ", " ").strip()


# ─────────────────────────────────────────────────────────────────────
# 試合速報(박스스코어) — npb.jp 자신의 2차 소스
# ─────────────────────────────────────────────────────────────────────
def strip_links(html: str) -> tuple[str | None, dict[str, str]]:
    """일정 페이지 머리의 `header_score` 스트립에서 '그날' 경기의 정식 링크를 뽑는다.

    반환 (그날 sports_day, {링크}). 스트립은 **하루치만** 싣는다 —
    JST 자정이 지나면 다음 날로 넘어가므로 **어제 경기는 여기서 못 얻는다.**
    그래서 보강은 사실상 '경기 종료 ~ JST 자정' 구간에서 일한다.
    그 구간이 우리가 노리는 구간(종료 직후)이라 손해가 아니다.
    """
    m = _STRIP.search(html)
    if not m:
        return None, {}
    body = m.group(1)
    d = _STRIP_DATE.search(body)
    if not d:
        return None, {}
    yy, mo, dd = int(d.group(1)), int(d.group(2)), int(d.group(3))
    day = f"{yy:04d}-{mo:02d}-{dd:02d}"
    links: dict[str, str] = {}
    for href, mmdd in _STRIP_LINK.findall(body):
        # 스트립 날짜와 링크 안의 월일이 어긋나면 그 링크는 안 쓴다.
        if mmdd == f"{mo:02d}{dd:02d}":
            links[href] = href
    return day, links


def parse_box(html: str) -> dict | None:
    """속보 페이지 한 장 → {'final','day','home','away','hs','as','end'} 또는 None.

    **모르면 None을 돌려준다.** 구조가 바뀌면 조용히 틀린 값을 만드는 대신 막힌다.
    """
    info = _GAME_INFO.search(html)
    dt = _LS_DATE.search(html)
    if not info or not dt:
        return None
    info_txt = _txt(info.group(1))
    day = f"{int(dt.group(1)):04d}-{int(dt.group(2)):02d}-{int(dt.group(3)):02d}"

    sides: dict[str, tuple[str, str]] = {}
    for side, row in _LS_ROW.findall(html):
        name = _LS_SHORT.search(row)
        total = _LS_TOTAL.search(row)
        if not name or not total:
            return None
        sides[side] = (_txt(name.group(1)), _txt(total.group(1)))
    # `top`=원정(먼저 친다) · `bottom`=홈. 야구 규칙 그대로다.
    if set(sides) != {"top", "bottom"}:
        return None
    away_name, away_runs = sides["top"]
    home_name, home_runs = sides["bottom"]
    if home_name not in TEAM_CODE or away_name not in TEAM_CODE:
        return None
    final = (_BOX_FINAL_MARK in info_txt
             and home_runs.isdigit() and away_runs.isdigit())
    m = re.search(r"◇終了\s*(\d{1,2}:\d{2})", info_txt)
    return {"final": final, "day": day,
            "home": TEAM_CODE[home_name], "away": TEAM_CODE[away_name],
            "hs": int(home_runs) if home_runs.isdigit() else None,
            "as": int(away_runs) if away_runs.isdigit() else None,
            "end": m.group(1) if m else "", "info": info_txt[:60]}


def _box_cache_path(href: str) -> pathlib.Path:
    return BOX_CACHE_DIR / ("npb_box_" + href.strip("/").replace("/", "_") + ".json")


def fetch_box(href: str) -> dict | None:
    """속보 페이지 하나를 캐시와 함께 읽는다. 실패는 None(=모름)이지 오류가 아니다."""
    path = _box_cache_path(href)
    try:
        age = time.time() - path.stat().st_mtime
        cached = json.loads(path.read_text())
        ttl = BOX_CACHE_TTL_FINAL if cached.get("final") else BOX_CACHE_TTL_OPEN
        if age <= ttl:
            return cached
    except (OSError, ValueError):
        pass
    try:
        html = _fetch(_OPENER, f"https://npb.jp{href}", label=f"NPB 속보 {href}",
                      timeout=BOX_HTTP_TIMEOUT, retries=BOX_HTTP_RETRIES
                      ).decode("utf-8", "replace")
    except Exception:                                              # noqa: BLE001
        return None                            # 죽은 소스는 '모름'이지 오류가 아니다
    box = parse_box(html)
    if box is None:
        return None
    try:
        BOX_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(box, ensure_ascii=False))
        _prune_boxes()
    except OSError:
        pass                                   # 캐시 못 써도 값은 쓴다
    return box


def _prune_boxes(max_age: float = 48 * 3600) -> None:
    """묵은 속보 캐시를 지운다.

    가장 긴 TTL이 6시간이라 48시간 지난 파일은 다시 안 읽힌다. 안 지우면
    시즌 내내 하루 6개씩 쌓인다(≈840개). `scoreref_*.json`·`lck_*.json`은
    다른 주인의 캐시이므로 건드리지 않는다.
    """
    try:
        now = time.time()
        for p in BOX_CACHE_DIR.glob("npb_box_*.json"):
            if now - p.stat().st_mtime > max_age:
                p.unlink()
    except OSError:
        pass


class NpbAdapter(NoticeMixin):
    league = League.NPB
    # 클래스 기본값을 둔다 — `fetch()`를 거치지 않고 보강만 부르는 자리(검증·시험)에서
    # 속성이 없어 예외가 나면, 그 예외가 '보강 실패'가 아니라 수집 실패로 보인다.
    _strip_day: str | None = None
    _strip_links: list[str] = []

    def fetch(self, season: int, months: list[str],
              *, now_utc: datetime | None = None) -> list[Game]:
        """now_utc는 '진행 중' 추정에만 쓴다(소스에 LIVE 신호가 없다 — 파일 머리말 참조)."""
        self.reset_notices()
        self._strip_day: str | None = None      # 일정 페이지 스트립이 싣는 '오늘'
        self._strip_links: list[str] = []       # 그날 경기의 정식 속보 링크
        now = now_utc or datetime.now(timezone.utc)
        out: list[Game] = []
        for mm in months:
            out += self._month(season, mm, now)
        if not out:
            raise GateError(f"NPB: {season}년 {months} 일정 0건 — 0건은 항상 의심")

        # **여기서부터는 보조다.** 1차 수집이 끝난 뒤에 부르고, 어떤 예외도
        # 밖으로 내보내지 않는다 — 보조 소스 장애가 NPB 수집 장애가 되면
        # 지연을 고치려다 리그 하나를 침묵시키는 것이다(원칙 4).
        try:
            self._enrich(out, now)
        except Exception as e:                                     # noqa: BLE001
            self.note("결과 보강 실패(수집은 계속)", f"{type(e).__name__}: {str(e)[:100]}")
        return out

    # ── 결과 보강 ───────────────────────────────────────────────
    def _enrich(self, games: list[Game], now: datetime) -> None:
        """1차가 아직 결과를 안 준 경기를 채운다. 파일 머리말의 7원칙 참조."""
        if _ENRICH_REF is None:
            self.note_text("네이버 보강", f"불가 — 대조기 로드 실패({_ENRICH_LOAD_ERROR})")
            return

        # 매핑표는 `scoreref.NPB_TEAMS` 하나만 쓴다(복붙하면 한쪽만 고쳐지는 날이 온다).
        # 다만 그 표가 우리 계약과 어긋나면 조용히 엉뚱한 팀에 점수가 붙으므로 여기서 댄다.
        # 2026-09-03 확인: 네이버 12개 코드 전부 TEAM_NAMES[NPB]에 있다.
        # (올스타 CL·PL은 표에 일부러 없다 → 올스타는 보강되지 않고 '매핑 못 함'으로 보고된다.)
        strays = sorted(set(NAVER_TEAM_CODES.values()) - set(TEAM_NAMES.get(League.NPB, {})))
        if strays:
            self.note("네이버 매핑표에 우리 계약에 없는 팀 코드가 있음", ", ".join(strays))

        # 창(窓) 밖의 경기는 쳐다보지도 않는다. 3개월치를 긁어도 여기서 이틀로 줄어든다.
        by_day: dict[str, list[Game]] = {}
        pending: dict[str, int] = {}
        for g in games:
            elapsed = (now - g.start_utc).total_seconds()
            if not (ENRICH_MIN_ELAPSED_SECONDS <= elapsed <= ENRICH_MAX_ELAPSED_SECONDS):
                continue
            by_day.setdefault(g.sports_day, []).append(g)
            if self._needs_result(g):
                pending[g.sports_day] = pending.get(g.sports_day, 0) + 1

        if not pending:
            return                       # 1차가 다 채웠다 — 외부를 두드릴 이유가 없다

        days = sorted(pending, reverse=True)[:ENRICH_MAX_DAYS]
        if len(pending) > len(days):
            self.note("결과 없는 날짜가 상한(2일)보다 많아 오래된 날은 건너뜀",
                      f"건너뜀 {sorted(set(pending) - set(days))}")

        started = time.monotonic()
        filled = conflict = 0
        for day in days:
            if time.monotonic() - started > ENRICH_BUDGET_SECONDS:
                self.note("네이버 보강 예산(12초) 초과로 건너뜀", day)
                continue
            try:
                refs = _ENRICH_REF.games_for(League.NPB, day)
            except Exception as e:                                 # noqa: BLE001
                # 실패·타임아웃·0건 전부 여기로 온다. **오류가 아니라 '보강 못 함'이다.**
                # (`RefUnavailable`만 잡으면 타임아웃 같은 OSError가 새어 나가
                #  외부 사이트 장애가 우리 버그로 보고된다 — scoreref가 같은 이유로
                #  `OSError`를 함께 잡는다.)
                self.note("네이버 보강 못 함(외부 소스)", f"{day} {type(e).__name__} {str(e)[:80]}")
                continue
            f, c = self._merge_day(day, by_day.get(day, []), refs, started)
            filled += f
            conflict += c

        self.note_text_info("결과 보강",
                       f"결과 대기 {sum(pending.values())}건 · 조회 {len(days)}일 · "
                       f"보강 {filled}건 · 소스 불일치로 결과 보류 {conflict}건 "
                       f"(1차 npb.jp 일정 / 2차 npb.jp 속보 + {_NAVER_SOURCE}"
                       + ("" if REQUIRE_SECOND_SOURCE else " · 2차 확인 꺼짐") + ")")

    @staticmethod
    def _needs_result(g: Game) -> bool:
        """1차가 아직 결과를 안 준 경기인가.

        취소·연기·서스펜디드는 소스가 이미 말을 한 것이므로 보강 대상이 아니다.
        (서스펜디드는 종결 상태가 아니라서 `is_terminal`만 보면 새어 들어온다.)
        """
        return (g.status is Status.LIVE and g.score is None
                and not g.meta.cancel_reason)

    def _merge_day(self, day: str, ours: list[Game],
                   refs: list["RefGame"], started: float) -> tuple[int, int]:
        """하루치를 짝지어 합친다. 반환 (보강 건수, 충돌로 보류한 건수)."""
        # 짝 키는 **(두 팀의 집합)**이다. 홈·원정을 키에 넣으면 방향이 뒤집힌 경우가
        # '외부에 그 경기 없음'으로 조용히 새어나간다 — 방향이 뒤집힌 채 점수를 실으면
        # 승패가 반대로 나간다(v1.11c에 실제로 있었던 사고).
        their: dict[frozenset, list["RefGame"]] = {}
        unmapped = 0
        for r in refs:
            if not (r.mapped and r.home_code in TEAM_NAMES.get(League.NPB, {})
                    and r.away_code in TEAM_NAMES.get(League.NPB, {})):
                # 올스타(CL/PL)와 처음 보는 코드가 여기로 온다. **억지로 맞추지 않는다.**
                unmapped += 1
                self.note("네이버 팀 코드를 우리 코드로 매핑 못 함(보강 안 함)",
                          f"{day} {r.raw_home} vs {r.raw_away}")
                continue
            their.setdefault(frozenset((r.home_code, r.away_code)), []).append(r)

        mine: dict[frozenset, list[Game]] = {}
        for g in ours:
            mine.setdefault(frozenset((g.home.team_code, g.away.team_code)), []).append(g)

        # 짝이 하나로 특정되는 경기만 남긴다.
        pairs: list[tuple[frozenset, Game, "RefGame"]] = []
        filled = conflict = 0
        for key, gl in mine.items():
            rl = their.get(key) or []
            if not rl:
                if any(self._needs_result(g) for g in gl):
                    self.note("네이버 일정에 그 경기가 없어 보강 못 함",
                              f"{day} {'/'.join(sorted(key))}"
                              + (f" (매핑 못 한 네이버 경기 {unmapped}건 있음)" if unmapped else ""))
                continue
            if len(gl) != 1 or len(rl) != 1:
                # 같은 날 같은 두 팀이 두 경기(더블헤더)면 어느 쪽이 어느 쪽인지
                # 확신할 수 없다. **확신 없이 점수를 싣느니 늦게 나가는 편이 낫다.**
                self.note("같은 날 같은 두 팀이 여러 경기 — 짝을 특정 못 해 보강 안 함",
                          f"{day} {'/'.join(sorted(key))} 우리 {len(gl)}건 / 네이버 {len(rl)}건")
                continue
            pairs.append((key, gl[0], rl[0]))

        # 2차 소스(npb.jp 속보)는 **네이버가 '종료'라고 말한 대기 경기에만** 받는다.
        # 순서가 반대면 아직 진행 중인 경기까지 매 틱 한 장씩 받게 된다.
        need = {key for key, g, r in pairs
                if self._needs_result(g) and r.status is RefStatus.FINAL
                and r.home_score is not None and r.away_score is not None}
        boxes = self._boxes(day, need, started) if need else {}

        for key, g, r in pairs:
            f, c = self._merge_one(day, g, r, boxes.get(key))
            filled += f
            conflict += c
        return filled, conflict

    # ── 2차 소스: npb.jp 試合速報 ───────────────────────────────
    def _boxes(self, day: str, need: set[frozenset],
               started: float) -> dict[frozenset, tuple[str, dict]]:
        """필요한 경기의 속보 페이지를 (링크, 파싱결과)로 돌려준다. 못 구하면 빠진다."""
        if day != self._strip_day or not self._strip_links:
            # 스트립은 npb.jp의 '오늘'만 싣는다. 어제 경기는 정식 링크를 못 구하므로
            # **보강하지 않는다** — 나중에 바뀔 키로 결과를 내보내지 않기 위해서다.
            self.note("속보 링크를 못 구해 2차 확인 불가(스트립은 npb.jp의 '오늘'만 싣는다)",
                      f"{day} 대기 {len(need)}건 / 스트립 {self._strip_day}",
                      count=len(need))
            return {}

        # 이미 받아 둔 캐시로 '어느 링크가 어느 경기인지'를 먼저 안다.
        # 팀 짝은 안 변하므로 나이를 따지지 않는다 — 신선도는 fetch_box가 본다.
        hint: dict[frozenset, str] = {}
        for href in self._strip_links:
            try:
                c = json.loads(_box_cache_path(href).read_text())
            except (OSError, ValueError):
                continue
            if c.get("home") and c.get("away"):
                hint[frozenset((c["home"], c["away"]))] = href

        out: dict[frozenset, tuple[str, dict]] = {}
        budget = ENRICH_MAX_BOXSCORES
        for key in list(need):
            href = hint.get(key)
            if not href:
                continue
            box = fetch_box(href)
            if box and box["day"] == day:      # 날짜까지 대 본다(캐시가 섞이면 엉뚱한 경기다)
                out[key] = (href, box)

        unknown = [h for h in self._strip_links if h not in hint.values()]
        for href in unknown:
            if len(out) >= len(need) or budget <= 0:
                break
            if time.monotonic() - started > ENRICH_BUDGET_SECONDS:
                self.note("보강 예산 초과로 속보 확인 중단", f"{day} 남은 링크 {len(unknown)}건")
                break
            budget -= 1
            box = fetch_box(href)
            if not box or box["day"] != day:
                continue
            k = frozenset((box["home"], box["away"]))
            if k in need:
                out[k] = (href, box)
        return out

    def _merge_one(self, day: str, g: Game, r: "RefGame",
                   box: tuple[str, dict] | None) -> tuple[int, int]:
        tag = f"{day} {g.home.team_code}-{g.away.team_code}"

        # 방향이 다르면 점수를 비교할 수도, 실을 수도 없다. 억지로 뒤집지 않는다
        # (NPB는 중립 구장 리그가 아니다 — 홈·원정이 실재한다).
        # **여기서 1차 결과를 보류시키지는 않는다.** 방향 불일치는 발행 직전
        # 대조기(`scoreref`)가 이미 BLOCK으로 막는다. 그런데 그 판단을 수집 단계까지
        # 겹쳐 놓으면, 네이버가 표기 관례를 바꾸는 날 **NPB 결과가 통째로 사라진다** —
        # LCK가 정확히 그 방식으로 13경기 100% 차단됐던 전례가 scoreref 주석에 있다.
        # 수집은 보고만 하고, 막는 것은 막는 자리에서 한다.
        if r.home_code != g.home.team_code:
            self.note("네이버와 홈·원정 방향이 다름 — 보강도 대조도 안 함",
                      f"{tag} / 네이버 홈 {r.home_code}")
            return 0, 0

        their_final = (r.status is RefStatus.FINAL
                       and r.home_score is not None and r.away_score is not None)

        # ── 1차가 이미 결과를 준 경기 ──
        # 덮지 않는다. 다만 **점수가 다르면 그 경기는 결과 없음으로 되돌린다**(원칙 3).
        if g.status is Status.FINAL:
            if not (their_final and g.score):
                return 0, 0
            if (g.score.home, g.score.away) == (r.home_score, r.away_score):
                return 0, 0
            ours_txt = f"{g.score.home}:{g.score.away}"
            g.status = Status.LIVE
            g.score = None
            self.note("두 소스의 점수가 달라 결과를 보류함(추측 금지)",
                      f"{tag} npb.jp {ours_txt} vs {_NAVER_SOURCE} "
                      f"{r.home_score}:{r.away_score}")
            return 0, 1

        if not self._needs_result(g):
            return 0, 0                  # 취소·연기·서스펜디드 — 소스가 이미 말했다

        # ── 1차가 아직 결과를 안 준 경기 ──
        if r.status is RefStatus.OTHER:
            self.note("네이버가 취소·중단으로 표기 — 보강 안 함",
                      f"{tag} {r.raw_status}")
            return 0, 0
        if r.status is RefStatus.UNKNOWN:
            self.note("처음 보는 네이버 상태 코드 — 보강 안 함", f"{tag} {r.raw_status!r}")
            return 0, 0
        if r.status is not RefStatus.FINAL:
            return 0, 0                  # 네이버도 아직 진행 중이다. 정상이므로 조용히 넘긴다
        if not their_final:
            # **'종료'라는 말만으로는 종료로 안 친다**(원칙 5 = 지금 규칙 유지).
            self.note("네이버가 종료라는데 점수가 없어 보강 안 함", f"{tag} {r.raw_status}")
            return 0, 0

        # ── 2차 소스 확인 ──
        # **한 소스만 믿고 싣지 않는다.** 파일 머리말 [경고] 절 참조 —
        # 같은 네이버 야구 피드가 2026-09-03 실측에서 틀린 최종 점수(2:2, 실제 6:2)를
        # `RESULT`로 24분 넘게 게시했다. 그때 우리가 혼자 믿었으면 그 카드가 나갔다.
        src_note = f"{_NAVER_SOURCE} {r.home_score}:{r.away_score}"
        href = ""
        if REQUIRE_SECOND_SOURCE:
            if box is None:
                self.note("네이버는 종료라는데 npb.jp 속보를 못 읽어 보류", f"{tag} {src_note}")
                return 0, 0
            href, b = box
            if not b.get("final"):
                # 속보가 아직 종료로 안 바뀌었다. **네이버가 빠른 만큼 정상적인 상태다.**
                # 이 알림이 곧 '두 소스의 시차'를 재는 자다 — 운영이 그 숫자를 본다.
                self.note("네이버는 종료·npb.jp 속보는 아직 — 다음 틱에 다시 본다",
                          f"{tag} {src_note} / 속보 {b.get('info', '')[:24]}")
                return 0, 0
            if b["home"] != g.home.team_code or b["away"] != g.away.team_code:
                self.note("속보의 홈·원정이 일정 페이지와 다름 — 보강 안 함",
                          f"{tag} / 속보 홈 {b['home']} 원정 {b['away']}")
                return 0, 0
            if (b["hs"], b["as"]) != (r.home_score, r.away_score):
                # **여기가 이번 작업의 핵심 안전장치다.** 두 소스가 다르면 안 싣는다.
                self.note("두 소스의 점수가 달라 결과를 보류함(추측 금지)",
                          f"{tag} npb.jp 속보 {b['hs']}:{b['as']} vs {src_note}")
                return 0, 1
            src_note += f" + npb.jp 속보 {b['hs']}:{b['as']}(종료 {b.get('end', '')})"
        elif box is not None:
            # 2차 확인을 껐더라도 **키는 맞춰 둔다**(정정 폭풍 방지 — 파일 머리말 [주의] 절).
            href = box[0]
        if not href:
            # 속보 링크가 없으면 나중에 바뀔 키로 결과를 내보내게 된다.
            # 그건 사실이 안 바뀌었는데 정정이 나가는 길이라, 차라리 보강을 안 한다.
            self.note("속보 링크가 없어 키를 못 맞춤 — 보강 안 함(정정 폭풍 방지)", tag)
            return 0, 0

        prev = (g.status, g.score, g.source_key)
        g.status = Status.FINAL
        g.score = Score(int(r.home_score), int(r.away_score), ScoreUnit.RUNS)
        if href:
            # **키를 미리 맞춘다**(원칙 7 / 파일 머리말 [주의] 절).
            # 나중에 일정 페이지가 붙일 바로 그 링크라, 여기서 맞춰 두면
            # 사실이 안 바뀌었는데 정정이 나가는 일이 없다.
            g.source_key = href.strip("/").replace("/", "-")
        try:
            g.validate()
        except GateError as e:
            g.status, g.score, g.source_key = prev   # 계약을 못 지키는 값은 안 싣는다
            self.note("보강 값이 계약 검증에 걸려 되돌림", f"{tag} {str(e)[:80]}")
            return 0, 0
        self.note("결과 보강(1차 npb.jp 일정이 아직 미게시)", f"{tag} {src_note}")
        return 1, 0

    def _month(self, season: int, mm: str, now: datetime) -> list[Game]:
        url = f"{_BASE}/{season}/schedule_{mm}_detail.html"
        html = _fetch(_OPENER, url, label=f"NPB {season}-{mm}").decode("utf-8", "replace")

        rows = _ROW.findall(html)
        if not rows:
            # HTML 파싱은 구조 변경에 약하다. 조용히 0건을 반환하지 않는다.
            raise GateError(f"NPB {season}-{mm}: 경기 행 0건 — 페이지 구조 변경 의심")

        # **추가 요청 없이** 그날 경기의 정식 속보 링크를 같은 HTML에서 챙긴다.
        # 어느 달 페이지를 받아도 같은 스트립이 들어 있으므로 한 번만 잡는다.
        if self._strip_day is None:
            d, links = strip_links(html)
            if d:
                self._strip_day, self._strip_links = d, list(links)
            else:
                self.note_text("속보 스트립", "일정 페이지에서 header_score를 못 읽음"
                                              " — 2차 확인 없이는 보강하지 않는다")

        out: list[Game] = []
        for mmdd, body in rows:
            g = self._parse(season, mmdd, body, now)
            if g:
                out.append(g)
        return out

    def _parse(self, season: int, mmdd: str, body: str, now: datetime) -> Game | None:
        t1 = _txt(_DIV("team1").search(body).group(1)) if _DIV("team1").search(body) else ""
        t2 = _txt(_DIV("team2").search(body).group(1)) if _DIV("team2").search(body) else ""
        if not t1 or not t2:
            return None                      # 경기 없는 행(휴식일 등)

        if t1 not in TEAM_CODE or t2 not in TEAM_CODE:
            raise UnknownStatus(f"NPB: 미등록 팀명 {t1!r} / {t2!r}")

        cancel = _txt(_DIV("cancel").search(body).group(1)) if _DIV("cancel").search(body) else ""
        s1 = _txt(_DIV("score1").search(body).group(1)) if _DIV("score1").search(body) else ""
        s2 = _txt(_DIV("score2").search(body).group(1)) if _DIV("score2").search(body) else ""
        state = _txt(_DIV("state").search(body).group(1)) if _DIV("state").search(body) else ""
        place = _txt(_DIV("place").search(body).group(1)) if _DIV("place").search(body) else ""
        tm = _txt(_DIV("time").search(body).group(1)) if _DIV("time").search(body) else ""

        month, day = int(mmdd[:2]), int(mmdd[2:])
        hh, mi = (tm.split(":") + ["0"])[:2] if ":" in tm else ("00", "00")
        try:
            start = datetime(season, month, day, int(hh), int(mi), tzinfo=JST)
        except ValueError:
            raise UnknownStatus(f"NPB: 날짜·시각 해석 불가 {mmdd!r} {tm!r}")

        # 예비일은 경기가 아니다. 취소로 세면 없는 경기가 취소된 것처럼 나간다.
        if cancel and "予備" in cancel:
            return None

        # ── 상태 판정 ──
        #
        # 신호의 근거는 파일 머리말의 895행 전수 분포에 있다. 요약하면
        #   종료  = 점수 두 칸이 숫자 + `pit`에 결과투수(勝/敗/分)
        #   그 외 = 소스가 아무 말도 하지 않는다(진행 중 표기 자체가 없다)
        # `state`는 어느 행에서도 `-` 하나뿐이라 판정에 쓸 수 없다.
        #
        # 1) cancel 칸이 있으면 그날 안 열렸다. 사유를 그대로 보관한다.
        # 2) 점수 + 결과투수가 함께 있으면 종료.
        # 3) 점수는 있는데 결과투수가 없으면 **종료라고 말하지 않는다.**
        #    2026시즌 실측에는 그런 행이 0건이라, 나오면 그 자체가 구조 변경 신호다.
        #    끝났다고 단정하면 v1.11g의 "0:0 다섯 경기 종료" 사고와 같은 계열이 된다.
        # 4) 점수가 없으면 시계로 가른다 — 시작 시각 + 30분이 지났으면 진행 중,
        #    아니면 예정. 진행 중에는 점수를 싣지 않는다(아는 것이 없다).
        pit = " ".join(_txt(p) for p in _PIT.findall(body))
        decided = any(mark in pit for mark in _RESULT_PITCHER)
        has_score = s1.isdigit() and s2.isdigit()

        # `state`는 판정에 쓰지 않는다(895행 전부 '-' 또는 없음). 다만 값이 생기면
        # 소스가 진행 상태를 싣기 시작했다는 뜻이므로 — LIVE를 시계로 추정하지 않아도
        # 되는 날이 온 것이다 — 조용히 넘기지 않고 알린다.
        if state and state != "-":
            self.note("state 칸에 새 값이 보임(진행 신호가 생겼을 수 있음)",
                      f"{season}-{mmdd} {state!r}")

        score = None
        if cancel:
            mapped = STATUS_MAP.get(League.NPB, {}).get(cancel)
            if mapped is None:
                raise UnknownStatus(f"NPB: 미등록 취소 표기 {cancel!r} ({mmdd})")
            status = mapped
        elif has_score and decided:
            status = Status.FINAL
            # **team1이 홈이다** (v1.11c에서 바로잡음).
            # 전에는 "화면 배치대로 team1=원정"으로 읽었다. 그 상태로 카드를 그렸더니
            # 8/30 여섯 경기가 전부 경기장과 어긋났다 — 에스콘필드(니혼햄 홈구장)
            # 경기의 홈팀이 지바롯데로, 고시엔(한신 홈) 경기의 홈팀이 요미우리로 찍혔다.
            # 여섯 경기 전부 어긋나므로 우연이 아니다. 점수도 함께 뒤집혀 있었으니
            # 결과 카드는 승패를 반대로 내보냈을 것이다.
            score = Score(int(s1), int(s2), ScoreUnit.RUNS)
        elif has_score:
            # 점수만 있고 결과투수가 없다 — 2026시즌 895행에 0건인 조합이다.
            status = Status.LIVE
            self.note("점수는 있는데 결과투수 표기가 없어 '종료'로 안 봄",
                      f"{season}-{mmdd} {t1} {s1}:{s2} {t2} pit={pit[:40]!r}")
        elif (now - start.astimezone(timezone.utc)).total_seconds() >= LIVE_AFTER_SECONDS:
            status = Status.LIVE
        else:
            status = Status.SCHEDULED

        link = _LINK.search(body)
        key = (link.group(1).strip("/").replace("/", "-") if link
               else f"{season}{mmdd}-{TEAM_CODE[t1]}-{TEAM_CODE[t2]}")

        g = Game(
            league=League.NPB, season=str(season), source_key=key,
            home=TeamRef(League.NPB, TEAM_CODE[t1]),
            away=TeamRef(League.NPB, TEAM_CODE[t2]),
            start_utc=start.astimezone(ZoneInfo("UTC")), home_tz="Asia/Tokyo",
            status=status, score=score, venue=place or None,
            meta=GameMeta(cancel_reason=cancel or None),
        )
        g.validate()
        return g
