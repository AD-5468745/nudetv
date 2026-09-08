"""제어판 시트 생성기 — 값을 손으로 적지 않고 contract.py에서 뽑는다.

시트와 코드가 어긋나는 것이 이런 제어판의 가장 흔한 사고다.
그래서 리그코드·콘텐츠코드·유예·우선순위는 전부 계약에서 읽어 채운다.
"""
import sys, pathlib
# 저장소 뿌리를 경로에 넣는다. **개발 컴퓨터의 절대경로를 박지 않는다** —
# 박아 두면 배포 환경에서 죽고, 공개 저장소 검사(verify_public)가 그것을 잡는다(약점 22).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import contract as C
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.comments import Comment

F = "Arial"
INK   = Font(name=F, size=10)
BOLD  = Font(name=F, size=10, bold=True)
TITLE = Font(name=F, size=15, bold=True, color="1B1F28")
SUB   = Font(name=F, size=10, color="6B6B6B")
HEADF = Font(name=F, size=9.5, bold=True, color="FFFFFF")
LOCKF = Font(name=F, size=10, color="5A5A5A")

HEAD   = PatternFill("solid", fgColor="2F3D54")   # 표 머리
FILLME = PatternFill("solid", fgColor="FFF3C4")   # 노랑 = 대표님이 채우는 칸
LOCKED = PatternFill("solid", fgColor="EDEDED")   # 회색 = 수정 금지
BAND   = PatternFill("solid", fgColor="F7F5F1")
WARN   = PatternFill("solid", fgColor="FDE7E4")
OKF    = PatternFill("solid", fgColor="E4F1E9")

thin = Side(style="thin", color="D9D4CA")
BOX  = Border(left=thin, right=thin, top=thin, bottom=thin)

LEAGUE_KO = {
 "KBO":"KBO 리그(야구)", "KBL":"KBL(농구)", "VLEAGUE_M":"V리그 남자부",
 "VLEAGUE_W":"V리그 여자부", "KL1":"K리그1(축구)", "LCK":"LCK(롤)",
 "INTL_LOL":"롤 국제대회(MSI·월즈)", "MLB":"MLB(미국야구)", "NPB":"NPB(일본야구)",
 "EPL":"프리미어리그", "LALIGA":"라리가", "SERIEA":"세리에A",
 "BUNDESLIGA":"분데스리가", "LIGUE1":"리그1", "UCL":"챔피언스리그",
 "UEL":"유로파리그", "MLS":"MLS(미국축구)"}

# **표에 빠진 리그가 있으면 임포트 시점에 막는다** (2026-09-08).
# v1.15에서 유로파리그·MLS를 계약에 넣고 이 표를 안 고쳤다. 그래서 이 생성기는
# 그날 이후 **한 번도 돌지 못했다**(`KeyError: 'UEL'`) — 아무도 몰랐다.
# 시트가 8월 28일 값에 멈춰 있던 이유 중 하나가 이것이다.
# 약점 140의 재발: **검사 목록에 없는 표는 게이트 밖이다.**
_missing = sorted({lg.value for lg in C.League} - set(LEAGUE_KO))
assert not _missing, (
    f"LEAGUE_KO에 빠진 리그가 있습니다: {_missing} — "
    "계약에 리그를 추가하면 이 표도 같은 커밋에서 채웁니다")

LEAGUE_SEASON = {"KBO":"2026","MLB":"2026","NPB":"2026","KL1":"2026","LCK":"2026",
 "INTL_LOL":"2026","MLS":"2026"}
# 지금 실제로 수집·발행하는 리그 = 계약이 정한다. 손으로 적지 않는다
# (예전에는 {"KBO"} 하나가 박혀 있어 시트가 "KBO만 준비됨"이라고 계속 말했다).
IMPLEMENTED = {lg.value for lg in C.League} - {lg.value for lg in C.DISABLED_LEAGUES}

CONTENT_KO = {
 "morning":"모닝 브리핑","night_brief":"나이트 브리핑","poll":"예측 투표",
 "poll_close":"투표 마감 알림","poll_settlement":"투표 정산","analysis":"맞대결 분석",
 "start_alert":"경기 시작 알림","inplay_board":"인플레이 보드","final_flash":"종료 속보",
 "league_result":"경기 결과 카드","standings":"팀 순위표","korean_daily":"코리안리거 데일리",
 "leaderboard":"부문 리더보드","weekly_preview":"주간 프리뷰","weekly_column":"주간 칼럼",
 "quiz":"기록 퀴즈","milestone":"기록 감시 알림","correction":"정정 안내",
 "evergreen":"대체 발송(에버그린)",
 "kickoff":"곧 시작 (같은 시각 묶음)","lineup":"선발 라인업"}
CONTENT_WHEN = {
 "morning":("고정 시각","07:30"), "night_brief":("고정 시각","23:00"),
 "kickoff":("상대 시각","경기 T-30분 ~ T-1분"),
 "lineup":("이벤트","선발 명단을 처음 본 시각"),
 "leaderboard":("고정 시각","12:00"), "standings":("이벤트","결과 카드와 동반"),
 "poll":("상대 시각","경기 T-3시간"), "analysis":("상대 시각","경기 T-3시간"),
 "poll_close":("상대 시각","경기 T-0"), "start_alert":("상대 시각","경기 T-10분"),
 "inplay_board":("이벤트","2쿼터·2세트 종료"), "final_flash":("이벤트","경기 종료 감지"),
 "league_result":("이벤트","리그 그날 미종결 0건"), "poll_settlement":("고정 시각","22:00"),
 "korean_daily":("고정 시각","10:00"), "weekly_preview":("고정 시각","금 16:00"),
 "weekly_column":("고정 시각","월 10:00"), "quiz":("고정 시각","14:00"),
 "milestone":("이벤트","기록 접근 감지"), "correction":("이벤트","변경 감지 즉시"),
 "evergreen":("고정 시각","12:00 (경기 없는 날)")}

# 리그와 같은 이유로 콘텐츠 표도 계약과 대조한다. v1.17에서 '곧 시작'과 '선발 라인업'을
# 넣고 이 두 표를 안 고쳐 생성기가 `KeyError: 'kickoff'`로 죽어 있었다.
_missing_ct = sorted({ct.value for ct in C.ContentType} - set(CONTENT_KO))
assert not _missing_ct, (
    f"CONTENT_KO에 빠진 콘텐츠가 있습니다: {_missing_ct} — "
    "계약에 콘텐츠를 추가하면 이 표도 같은 커밋에서 채웁니다")
_missing_when = sorted({ct.value for ct in C.ContentType} - set(CONTENT_WHEN))
assert not _missing_when, (
    f"CONTENT_WHEN에 빠진 콘텐츠가 있습니다: {_missing_when}")
# **지금 실제로 큐가 도는 콘텐츠 = 계약이 정한다.** 손으로 적으면 반드시 낡는다 —
# 이 집합이 8월 28일 값에 멈춰 있어서 시트가 '종료 속보 꺼짐 · 경기 시작 알림 켜짐'이라고
# 말했다. 실제는 정반대였다(속보는 가동 중, 시작 알림은 2026-09-07에 폐지).
READY = {ct.value for ct in C.QUEUED_CONTENT_TYPES}
RETIRED = {ct.value for ct in C.DISABLED_CONTENT_TYPES}
NO_DATA = {"inplay_board","korean_daily"}

wb = Workbook(); wb.remove(wb.active)

def sheet(name, widths, title, sub=None):
    ws = wb.create_sheet(name)
    ws.sheet_view.showGridLines = False
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = title; ws["A1"].font = TITLE
    ws.row_dimensions[1].height = 26
    if sub:
        ws["A2"] = sub; ws["A2"].font = SUB
    return ws

def header(ws, row, cols):
    for i, h in enumerate(cols, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.fill = HEAD; c.font = HEADF; c.border = BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 24
    ws.freeze_panes = ws.cell(row=row+1, column=1)

def put(ws, r, c, v, *, fill=None, font=INK, wrap=False, align=None):
    cell = ws.cell(row=r, column=c, value=v)
    cell.font = font; cell.border = BOX
    if fill: cell.fill = fill
    cell.alignment = Alignment(horizontal=align or "left", vertical="center", wrap_text=wrap)
    return cell

YN = lambda: DataValidation(type="list", formula1='"Y,N"', allow_blank=False)

# ══ 1. 사용법 ═══════════════════════════════════════════════
ws = sheet("사용법", [4, 30, 78], "누드TV 발행 제어판 — 보관용 자료 (연결되지 않음)",
           "보관용 자료입니다. 현재 시스템과 연결되어 있지 않아, 값을 바꾸거나 "
           "전면정지를 선택해도 발행에 반영되지 않습니다.")
rows = [
 ("", "", ""),
 ("■", "⛔ 먼저 읽어주세요", ""),
 ("", "이 시트는 시스템을 조종하지 않습니다",
      "2026-09-08 실측: 코드 어디에도 이 시트를 읽는 경로가 없습니다(g1/ 전체와 워크플로에서 0곳). "
      "여기서 무엇을 바꿔도 발행에 반영되지 않습니다."),
 ("", "'전체 정지'도 동작하지 않습니다",
      "KILL_SWITCH를 읽는 코드가 0곳입니다. 급할 때 멈추는 법은 아래 '급할 때' 항목을 보세요."),
 ("", "값이 낡았습니다",
      "이 시트의 값은 2026-08-28 상태입니다. 지금과 크게 다릅니다 — 리그 1개 vs 15개 · "
      "하루 상한 20 vs 500 · 콘텐츠 6종 vs 8종. 현재 설정은 contract.py가 갖습니다."),
 ("", "지우지 않는 이유",
      "초기 설계 의도의 기록이고, 같은 실수를 되풀이하지 않기 위한 증거입니다."),
 ("", "", ""),
 ("■", "★ 급할 때 — 전부 멈추기 (시트가 아닙니다)", ""),
 ("1", "텔레그램에서 봇의 '메시지 게시' 권한 끄기",
      "채널 관리자 설정에서 발행 봇의 게시 권한을 끕니다. 새 게시가 즉시 막힙니다 — "
      "시스템 상태와 무관하게 확실합니다. 대표님이 직접 하실 수 있습니다."),
 ("2", "깃허브 Actions 비활성화 + 실행 취소",
      "①만으로는 시스템이 멈추지 않습니다(계속 시도하다 실패합니다). "
      "워크플로를 비활성화하고 실행 중인 것을 취소합니다. "
      "비활성화와 취소는 별개이고, 취소를 눌렀다고 곧 종료된 것은 아닙니다 — "
      "종료를 확인할 때까지 ①을 유지하세요."),
 ("!", "①을 하면 오류 경고가 올 수 있습니다",
      "봇이 게시에 실패해 재시도하다 격리되기 때문입니다. 정지가 안 먹은 것이 아닙니다."),
 ("!", "권한을 되돌리는 것만으로 재개하지 마세요",
      "정지 구간에 밀린 것이 무엇인지 확인한 뒤 여세요."),
 ("", "", ""),
 ("■", "칸 색깔이 규칙입니다 (당시 설계)", ""),
 ("", "노란 칸", "대표님이 채우는 곳으로 설계했습니다. 지금은 채워도 반영되지 않습니다."),
 ("", "회색 칸", "코드가 이 값으로 찾도록 설계했습니다. 그 코드는 만들어지지 않았습니다."),
 ("", "흰 칸", "자유롭게 바꾸셔도 됩니다(어차피 반영되지 않습니다)."),
 ("", "", ""),
 ("■", "절대 여기 넣지 마세요", ""),
 ("", "봇 토큰 · API 키 · 비밀번호", "이 시트에는 그런 칸을 일부러 만들지 않았습니다. 시트는 링크만 있으면 열리는 문서라 비밀을 두는 곳이 아닙니다. "
      "토큰은 GitHub 비밀 보관함에 대표님이 직접 넣습니다. "
      "⚠️ 다만 채널 ID(-100…)가 이 시트에 평문으로 있습니다 — 저장소에서는 같은 값을 해시로 감춥니다."),
 ("", "", ""),
 ("■", "탭 안내", ""),
 ("", "운영", "채널·상한·속도·킬 스위치. 시스템의 메인 스위치판입니다."),
 ("", "리그", "15개 대회를 켜고 끕니다. 리그별 시즌·가중치도 여기."),
 ("", "편성", "콘텐츠 19종을 켜고 끄고, 발송 시각을 정합니다."),
 ("", "선발점수", "'오늘 어느 경기를 크게 다룰까'를 정하는 점수 가중치."),
 ("", "에버그린", "경기 없는 날 내보낼 글을 미리 채워두는 곳."),
 ("", "코드표", "리그·콘텐츠·팀 코드 참조표. 읽기 전용입니다."),
 ("", "변경기록", "무엇을 언제 왜 바꿨는지 남기는 곳. 장애가 났을 때 원인을 여기서 찾습니다."),
]
r = 4
for a, b, c in rows:
    if a == "■":
        cell = put(ws, r, 2, b, font=Font(name=F, size=11, bold=True, color="B35C0A"))
        cell.border = Border(bottom=Side(style="medium", color="B35C0A"))
        ws.cell(row=r, column=3).border = Border(bottom=Side(style="medium", color="B35C0A"))
        ws.row_dimensions[r].height = 22
    elif b:
        put(ws, r, 1, a, align="center", font=BOLD)
        put(ws, r, 2, b, font=BOLD)
        put(ws, r, 3, c, wrap=True)
        ws.row_dimensions[r].height = 30 if len(c) > 60 else 18
    r += 1
ws["B14"].fill = WARN; ws["C14"].fill = WARN

# ══ 2. 운영 ════════════════════════════════════════════════
ws = sheet("운영", [22, 30, 26, 16, 60], "운영 — 메인 스위치판",
           "노란 칸을 채우시면 그 기능이 켜집니다. 회색 '항목키'는 코드가 찾는 이름이라 수정 금지입니다.")
header(ws, 4, ["항목키 (수정 금지)", "항목", "값", "기본값", "설명"])
OPS = [
 ("MODE","발행 모드","테스트","테스트","'테스트'면 테스트 채널로, '운영'이면 운영 채널로 나갑니다. 7일 무사고 전에는 테스트.", "list", '"테스트,운영"'),
 ("KILL_SWITCH","전체 정지 ⛔동작 안 함","정상","정상","⛔ 이 칸은 아무 일도 하지 않습니다 — 읽는 코드가 0곳입니다(2026-09-08 실측). 급할 때는 텔레그램에서 봇의 '메시지 게시' 권한을 끄세요. 사용법 탭 참조.", "list", '"정상,전면정지"'),
 ("CHANNEL_TEST","테스트 채널 ID","","","-100 으로 시작하는 숫자. @userinfobot으로 확인합니다.", "fill", None),
 ("CHANNEL_LIVE","운영 채널 ID","","","테스트 7일 통과 후에 채우시면 됩니다. 지금은 비워두세요.", "fill", None),
 ("ALERT_TARGET","오류 알림 보낼 곳","발행 채널","발행 채널",
  "테스트 기간에는 '발행 채널'이 낫습니다 — 그 채널이 곧 상황판이니까요. 운영 전환 때 'DM'으로 바꿉니다.", "list", '"발행 채널,개인 DM"'),
 ("ALERT_CHAT_ID","비상 알림 chat_id (선택)","","",
  "채널로 아예 못 보내는 상황(봇 권한 박탈·채널 삭제·API 차단)에서만 쓰는 우회 경로입니다. "
  "비워두셔도 시스템은 돕니다 — 다만 그 상황에서는 알림이 못 갑니다.", "opt", None),
 # **숫자를 손으로 적지 않는다.** 20으로 박혀 있던 탓에 시트가 11일 동안
 # "하루 20장"이라고 말했다 — 실제는 500이었다(리그가 1개에서 15개로 늘었다).
 ("DAILY_MAX","하루 발송 상한", C.DAILY_MAX_MESSAGES, C.DAILY_MAX_MESSAGES,
  f"정상 운영의 천장입니다. 폭주는 별도로 10분 창 {C.BURST_MAX_MESSAGES}건이 잡습니다.", None, None),
 ("BURST_MAX","10분 창 상한", C.BURST_MAX_MESSAGES, C.BURST_MAX_MESSAGES,
  "'지금 뭔가 미쳤다'를 잡는 값입니다. 하루 상한과 뜻이 다릅니다.", None, None),
 ("RECORD_MAX_AGE_H","기록 스냅샷 최대 나이(시간)", C.RECORD_MAX_AGE_SECONDS//3600, C.RECORD_MAX_AGE_SECONDS//3600,
  "순위·기록이 이보다 오래되면 카드를 만들지 않습니다. 묵은 순위를 오늘 것처럼 내보내는 사고를 막습니다.", None, None),
 ("COVERAGE_HORIZON_DAYS","일정 선적재 일수", C.COVERAGE_HORIZON_DAYS, C.COVERAGE_HORIZON_DAYS,
  "며칠 앞 일정까지 미리 받아둘지. '인기 리그 절대 누락 금지'의 기본 장치입니다.", None, None),
 ("COVERAGE_AUDIT_TIMES","누락 점검 시각", ",".join(C.COVERAGE_AUDIT_TIMES_KST), ",".join(C.COVERAGE_AUDIT_TIMES_KST),
  "하루 3번 '빠진 경기 없나'를 대조합니다. 한 건이라도 어긋나면 DM.", None, None),
 ("FACT_LOCK","사실 잠금","켜짐","켜짐","수집한 데이터에 없는 사실은 절대 만들지 않습니다. 끌 수 없습니다.", "lock", None),
]
dv_cache = []
r = 5
for key, name, val, dft, desc, kind, opts in OPS:
    put(ws, r, 1, key, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, name, font=BOLD)
    c = put(ws, r, 3, val, align="center")
    if kind == "fill":
        c.fill = FILLME
        c.comment = Comment("여기에 값을 넣어주세요. 비어 있으면 이 기능이 켜지지 않습니다.", "제어판")
    elif kind == "opt":
        c.fill = PatternFill("solid", fgColor="FFF9E6")
        c.comment = Comment("선택 항목입니다. 비워두셔도 됩니다.\n"
                            "채널 발송 자체가 막힌 상황에서만 쓰이는 우회 경로입니다.", "제어판")
    elif kind == "lock":
        c.fill = LOCKED; c.font = LOCKF
    elif kind == "list":
        dv = DataValidation(type="list", formula1=opts, allow_blank=False)
        ws.add_data_validation(dv); dv.add(c)
    put(ws, r, 4, dft, fill=LOCKED, font=LOCKF, align="center")
    put(ws, r, 5, desc, wrap=True)
    ws.row_dimensions[r].height = 28
    r += 1

r += 2
put(ws, r, 2, "현재 상태 (자동 계산)", font=Font(name=F, size=11, bold=True, color="B35C0A"))
r += 1
for label, formula in [
  ("켜진 리그", '=COUNTIF(리그!C:C,"Y")&" / "&COUNTA(리그!A5:A19)&" 개"'),
  ("켜진 콘텐츠", '=COUNTIF(편성!C:C,"Y")&" / "&COUNTA(편성!A5:A23)&" 종"'),
  ("바로 발행 가능한 콘텐츠", '=COUNTIFS(편성!C:C,"Y",편성!H:H,"준비됨")&" 종"'),
  ("에버그린 보유", '=COUNTIF(에버그린!D:D,"Y")&" 편"'),
]:
    put(ws, r, 2, label, font=BOLD)
    put(ws, r, 3, formula, align="center", font=Font(name=F, size=10, bold=True, color="1D6B46"))
    r += 1

# ══ 3. 리그 ════════════════════════════════════════════════
ws = sheet("리그", [16, 24, 9, 12, 14, 12, 16, 16, 40], "리그 — 무엇을 다룰지",
           "'사용'을 Y로 바꾸면 그 리그가 켜집니다. 수집기가 아직 없는 리그는 Y로 해도 조용히 넘어갑니다(오류 아님).")
header(ws, 4, ["리그코드 (수정 금지)","리그명","사용","시즌","수집주기(분)","리그가중치",
               "정상 무발행 한계(시간)","수집기 상태","비고"])
dv = YN(); ws.add_data_validation(dv)
r = 5
for l in C.League:
    k = l.value
    put(ws, r, 1, k, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, LEAGUE_KO[k], font=BOLD)
    c = put(ws, r, 3, "Y" if k in IMPLEMENTED else "N", align="center"); dv.add(c)
    put(ws, r, 4, LEAGUE_SEASON.get(k, "2026-27"), align="center")
    put(ws, r, 5, 10, align="center")
    put(ws, r, 6, 1.0, align="center")
    put(ws, r, 7, C.NORMAL_SILENCE_HOURS[l], fill=LOCKED, font=LOCKF, align="center")
    ok = k in IMPLEMENTED
    put(ws, r, 8, "준비됨" if ok else "대기", fill=OKF if ok else BAND, align="center",
        font=Font(name=F, size=10, bold=True, color="1D6B46" if ok else "8A6D1F"))
    note = ("일정·결과 + 순위·상대전적·29개 부문 기록까지 확보" if ok else "소스는 확인 완료. 수집기 구현 대기")
    put(ws, r, 9, note, wrap=True)
    if r % 2 == 0:
        for cc in range(1, 10):
            if ws.cell(row=r, column=cc).fill.fgColor.rgb in (None, "00000000"):
                ws.cell(row=r, column=cc).fill = BAND
    r += 1
ws.cell(row=5, column=7).comment = Comment(
  "이 시간 넘게 그 리그에서 아무것도 안 나가면 '뭔가 고장났다'로 보고 DM이 갑니다.\n계약에서 가져온 값이라 수정하지 마세요.", "제어판")

# ══ 4. 편성 ════════════════════════════════════════════════
ws = sheet("편성", [22, 22, 9, 14, 22, 12, 12, 14, 34], "편성 — 무엇을 언제 내보낼지",
           "'사용' N으로 두면 그 콘텐츠는 아예 만들어지지 않습니다. 유예·우선순위는 계약 기본값이라 함부로 바꾸면 위험합니다.")
header(ws, 4, ["콘텐츠코드 (수정 금지)","이름","사용","발송 기준","값(시각·조건)","유예(초)",
               "우선순위","현재 데이터","설명"])
dv = YN(); ws.add_data_validation(dv)
r = 5
for ct in C.ContentType:
    k = ct.value
    when, val = CONTENT_WHEN[k]
    put(ws, r, 1, k, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, CONTENT_KO[k], font=BOLD)
    c = put(ws, r, 3, "Y" if k in READY else "N", align="center"); dv.add(c)
    put(ws, r, 4, when, align="center")
    put(ws, r, 5, val, align="center")
    put(ws, r, 6, C.GRACE_SECONDS[ct], fill=LOCKED, font=LOCKF, align="center")
    put(ws, r, 7, C.PACER_PRIORITY[ct], fill=LOCKED, font=LOCKF, align="center")
    if k in READY:
        st, fl, col = "발행 중", OKF, "1D6B46"
        note = "지금 실제로 나가고 있습니다."
    elif k in RETIRED:
        st, fl, col = "폐지됨", BAND, "6B6B6B"
        note = "다른 콘텐츠와 겹쳐서 껐습니다(계약 DISABLED_CONTENT_TYPES)."
    elif k in NO_DATA:
        st, fl, col = "데이터 없음", WARN, "A3332A"
        note = "필요한 데이터를 아직 못 구했습니다. 켜도 안 나갑니다."
    else:
        st, fl, col = "엔진 대기", BAND, "8A6D1F"
        note = "재료는 있고 만드는 코드가 아직입니다."
    put(ws, r, 8, st, fill=fl, align="center", font=Font(name=F, size=10, bold=True, color=col))
    put(ws, r, 9, note, wrap=True)
    r += 1
ws.cell(row=5, column=6).comment = Comment(
  "발송이 이 시간보다 늦어지면 '차라리 안 보낸다'로 판단해 건너뜁니다.\n"
  "예: 시작 알림은 480초(8분). 경기가 시작된 뒤 '곧 시작'이 나가면 거짓말이 되니까요.", "제어판")
ws.cell(row=5, column=7).comment = Comment(
  "낮을수록 먼저 나갑니다. 시각이 문안에 박힌 것(시작 알림·투표 마감)이 0번입니다.", "제어판")

# ══ 5. 선발점수 ═════════════════════════════════════════════
ws = sheet("선발점수", [22, 14, 12, 14, 62], "선발 점수 — 오늘 어느 경기를 크게 다룰까",
           "점수는 합격선이 아니라 줄 세우기입니다. 그날 상위 N경기를 무조건 뽑으므로 콘텐츠가 끊기지 않습니다.")
header(ws, 6, ["항목키 (수정 금지)","가중치","단위","적용","설명"])
W = [
 ("POP_TEAM_S","인기팀 S등급",60,"점","팀당","아래 등급표에서 S인 팀이 나오는 경기"),
 ("POP_TEAM_A","인기팀 A등급",30,"점","팀당","아래 등급표에서 A인 팀"),
 ("KOREAN_PLAYER","코리안리거 소속",40,"점","경기당","해외 리그에 한국 선수가 뛰는 경기"),
 ("RIVALRY","라이벌전",50,"점","경기당","아래 라이벌 표에 등록된 조합"),
 ("CLOSE_RANK","순위 근접 맞대결",35,"점","경기당","승차 1.5경기 이내 팀끼리"),
 ("STREAK","연승·연패 서사",25,"점","경기당","한쪽이 5연승 이상 또는 5연패 이상"),
 ("SEASON_STAKE","시즌 국면",20,"점","경기당","막판 순위 싸움 등"),
 ("LEAGUE_WEIGHT","리그 가중치","리그 탭 참조","배","경기당","리그 탭의 '리그가중치'를 최종 점수에 곱합니다"),
]
r = 7
for k, n, v, u, ap, d in W:
    put(ws, r, 1, k, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, v, align="center", font=BOLD)
    put(ws, r, 3, u, align="center")
    put(ws, r, 4, ap, align="center")
    put(ws, r, 5, n + " — " + d, wrap=True)
    r += 1

r += 2
put(ws, r, 1, "인기팀 등급표 (KBO)", font=Font(name=F, size=11, bold=True, color="B35C0A")); r += 1
header(ws, r, ["팀코드 (수정 금지)","팀명","등급","가점(자동)","설명"]); hdr = r; r += 1
GRADE = {"LG":"S","OB":"S","HT":"S","LT":"S","SS":"A","KT":"A","SK":"A","HH":"A","NC":"A","WO":"A"}
KO = {"LG":"LG","OB":"두산","KT":"KT","SK":"SSG","NC":"NC","WO":"키움","HT":"KIA","LT":"롯데","SS":"삼성","HH":"한화"}
dvg = DataValidation(type="list", formula1='"S,A,B"', allow_blank=False); ws.add_data_validation(dvg)
for code, g in GRADE.items():
    put(ws, r, 1, code, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, KO[code], font=BOLD)
    c = put(ws, r, 3, g, align="center"); dvg.add(c)
    put(ws, r, 4, f'=IF(C{r}="S",$B$7,IF(C{r}="A",$B$8,0))', align="center",
        font=Font(name=F, size=10, color="1D6B46", bold=True))
    put(ws, r, 5, "등급만 바꾸시면 가점은 위 가중치에서 자동으로 따라옵니다.", wrap=True)
    r += 1

r += 2
put(ws, r, 1, "라이벌전", font=Font(name=F, size=11, bold=True, color="B35C0A")); r += 1
header(ws, r, ["팀A","팀B","가점(자동)","사용","설명"]); r += 1
dvr = YN(); ws.add_data_validation(dvr)
for a, b, why in [("LG","OB","잠실 더비"),("HT","SS","전통 라이벌"),
                  ("LT","SS","낙동강 더비"),("LG","HT","최근 상위권 맞대결")]:
    put(ws, r, 1, a, align="center"); put(ws, r, 2, b, align="center")
    put(ws, r, 3, "=$B$10", align="center", font=Font(name=F, size=10, color="1D6B46", bold=True))
    c = put(ws, r, 4, "Y", align="center"); dvr.add(c)
    put(ws, r, 5, why, wrap=True)
    r += 1
put(ws, r, 1, "", fill=FILLME); put(ws, r, 2, "", fill=FILLME)
put(ws, r, 3, "=$B$10", align="center"); put(ws, r, 4, "N", align="center")
put(ws, r, 5, "빈 줄입니다. 추가하고 싶은 라이벌 조합을 팀코드로 넣으세요(코드표 참조).", wrap=True)

# ══ 6. 에버그린 ═════════════════════════════════════════════
ws = sheet("에버그린", [8, 34, 74, 9, 16], "에버그린 — 경기 없는 날 내보낼 글",
           "경기가 하나도 없는 날 12:00에 이 중 하나가 나갑니다. 오래된 것부터 순환합니다.")
header(ws, 4, ["번호","제목","본문","사용","마지막 발송일"])
put(ws, 5, 1, 1, align="center")
put(ws, 5, 2, "(예시) KBO 역대 한 시즌 최다 홈런", font=BOLD)
put(ws, 5, 3, "예시 줄입니다. 이 줄을 지우고 대표님 글을 넣으세요. "
              "본문은 1024자 이내로 써주세요 — 텔레그램 사진 설명 글자 제한입니다.", wrap=True)
put(ws, 5, 4, "N", align="center")
put(ws, 5, 5, "", align="center")
ws.row_dimensions[5].height = 40
ws.cell(row=5, column=1).fill = BAND
dv = YN(); ws.add_data_validation(dv)
for i in range(2, 21):
    r = i + 4
    put(ws, r, 1, i, align="center")
    put(ws, r, 2, "", fill=FILLME); put(ws, r, 3, "", fill=FILLME)
    c = put(ws, r, 4, "N", align="center"); dv.add(c)
    put(ws, r, 5, "", align="center")
dv.add(ws["D5"])

# ══ 7. 코드표 ═══════════════════════════════════════════════
ws = sheet("코드표", [22, 26, 18, 62], "코드표 — 읽기 전용 참조",
           "다른 탭에서 코드를 넣을 때 여기서 보고 쓰세요. 이 탭은 고치지 마세요.")
r = 4
put(ws, r, 1, "리그 코드", font=Font(name=F, size=11, bold=True, color="B35C0A")); r += 1
header(ws, r, ["리그코드","리그명","점수 단위","시즌 표기"]); r += 1
for l in C.League:
    put(ws, r, 1, l.value, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, LEAGUE_KO[l.value])
    put(ws, r, 3, C.SCORE_UNIT_BY_LEAGUE[l].value, align="center")
    put(ws, r, 4, "2026" if "^\\\\d{4}$" in C.SEASON_FORMAT_BY_LEAGUE[l].pattern else "2026-27", align="center")
    r += 1
r += 2
put(ws, r, 1, "KBO 팀 코드", font=Font(name=F, size=11, bold=True, color="B35C0A")); r += 1
header(ws, r, ["팀코드","팀명","",""]); r += 1
for code, ko in KO.items():
    put(ws, r, 1, code, fill=LOCKED, font=LOCKF); put(ws, r, 2, ko)
    put(ws, r, 3, ""); put(ws, r, 4, "")
    r += 1
r += 2
put(ws, r, 1, "콘텐츠 코드", font=Font(name=F, size=11, bold=True, color="B35C0A")); r += 1
header(ws, r, ["콘텐츠코드","이름","유예(초)","우선순위"]); r += 1
for ct in C.ContentType:
    put(ws, r, 1, ct.value, fill=LOCKED, font=LOCKF)
    put(ws, r, 2, CONTENT_KO[ct.value])
    put(ws, r, 3, C.GRACE_SECONDS[ct], align="center")
    put(ws, r, 4, C.PACER_PRIORITY[ct], align="center")
    r += 1

# ══ 8. 변경기록 ═════════════════════════════════════════════
ws = sheet("변경기록", [14, 12, 14, 24, 20, 20, 46], "변경 기록 — 무엇을 언제 왜 바꿨나",
           "장애가 나면 원인을 여기서 찾습니다. 값을 바꾸실 때마다 한 줄 남겨주세요.")
header(ws, 4, ["날짜","변경자","탭","항목","이전 값","새 값","사유"])
put(ws, 5, 1, "2026-08-27", align="center"); put(ws, 5, 2, "Claude", align="center")
put(ws, 5, 3, "전체", align="center"); put(ws, 5, 4, "제어판 최초 생성")
put(ws, 5, 5, "—", align="center"); put(ws, 5, 6, "—", align="center")
put(ws, 5, 7, "예시 줄입니다. 아래에 이어서 적어주세요.", wrap=True)
for r in range(6, 31):
    for c in range(1, 8):
        put(ws, r, c, "", fill=BAND if r % 2 == 0 else None)

out = pathlib.Path(__file__).resolve().parent / "누드TV_발행_제어판.xlsx"
wb.save(out)
print("saved", out, out.stat().st_size)
