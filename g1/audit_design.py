"""디자인 실측 감사 — 눈으로 보는 대신 픽셀과 CSS를 잰다."""
import sys, pathlib, json, math
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from datetime import datetime
from PIL import Image
from playwright.sync_api import sync_playwright

from adapters.kbo_records import KboRecordAdapter
from adapters.kbo import KboAdapter
from contract import (CARD_MIN_FONT_PX, CARD_MAX_ASPECT, CARD_MAX_HEIGHT_PX,
                      CARD_WIDTH_PX, KST, Status, League, LEAGUE_COLORS)
import pipeline as P

def srgb_lin(c):
    c/=255
    return c/12.92 if c<=0.04045 else ((c+0.055)/1.055)**2.4
def relL(rgb):
    r,g,b=[srgb_lin(x) for x in rgb]
    return 0.2126*r+0.7152*g+0.0722*b
def contrast(a,b):
    la,lb=relL(a),relL(b)
    hi,lo=max(la,lb),min(la,lb)
    return (hi+0.05)/(lo+0.05)
def lab_L(rgb):
    y=relL(rgb)
    return 116*(y**(1/3))-16 if y>0.008856 else 903.3*y

rb = KboRecordAdapter().fetch(2026)
games = KboAdapter().fetch(2026, ["08","09"])
day = datetime.now(KST).strftime("%Y-%m-%d")
fin=[g for g in games if g.status is Status.FINAL]
sch=[g for g in games if g.status is Status.SCHEDULED]
rday=max(g.sports_day for g in fin); nday=min(g.sports_day for g in sch)

cards = {
 "모닝 브리핑": P.render_morning([g for g in games if g.sports_day==nday], nday),
 "경기 결과":   P.render_result([g for g in games if g.sports_day==rday], rday),
 "팀 순위":     P.render_standings(rb, day, highlight=rb.standings[0].team_code),
 "리더보드 타격": P.render_leaders(rb, day, 0),
 "리더보드 투수": P.render_leaders(rb, day, 1),
 "맞대결 분석":  P.render_matchup(rb, sch[0], day),
}
css = P._css() + P.EXTRA_CSS
out = pathlib.Path("dryrun/audit"); out.mkdir(parents=True, exist_ok=True)

JS = """
() => {
  // 워터마크(.wm/.wm3)는 '읽으라고 넣은 글자'가 아니므로 본문 감사에서 제외한다.
  const isWM = e => e.closest('.wm, .wmc') !== null;
  const cv=document.createElement('canvas'); cv.width=cv.height=1;
  const cx=cv.getContext('2d',{willReadFrequently:true});
  const toRGBA = c => { cx.clearRect(0,0,1,1); cx.fillStyle=c; cx.fillRect(0,0,1,1);
    const d=cx.getImageData(0,0,1,1).data; return [d[0],d[1],d[2],d[3]/255]; };
  const over = (fg,bg) => [0,1,2].map(i=>Math.round(fg[i]*fg[3]+bg[i]*(1-fg[3])));
  // 배경은 조상 체인을 아래에서 위로 합성한다 (color-mix·알파 배경까지 반영)
  // 카드 바닥은 그라데이션이라 backgroundColor가 transparent다.
  // --bg 토큰을 실제 바닥으로 삼는다(다크·라이트 테마 모두에서 맞는다).
  const card=document.querySelector('#card');
  const cs0=getComputedStyle(card);
  const pick=(...names)=>{ for(const n of names){ const v=cs0.getPropertyValue(n).trim();
      if(v) return v; } return '#1e2939'; };
  const rootBg=toRGBA(pick('--ground','--bg','--paper'));
  const bgOf = e => { const chain=[]; let n=e;
    while(n && n!==card){ chain.push(n); n=n.parentElement; }
    let base=rootBg.slice(0,3);
    for(const el of chain.reverse()){
      const c=toRGBA(getComputedStyle(el).backgroundColor);
      if(c[3]>0) base=over(c,base);
    }
    return 'rgb('+base.join(',')+')'; };
  const res=[]; const walk=(el)=>{
    if(isWM(el)) return;
    for(const n of el.childNodes){
      if(n.nodeType===3 && n.textContent.trim()){
        const cs=getComputedStyle(el);
        const r=el.getBoundingClientRect();
        if(r.width>0&&r.height>0)
          res.push({t:n.textContent.trim().slice(0,18),
                    fs:parseFloat(cs.fontSize), col:'rgb('+toRGBA(cs.color).slice(0,3).join(',')+')', bg:bgOf(el),
                    fw:cs.fontWeight,
                    cls:(el.className||'').toString().slice(0,30),
                    x:Math.round(r.x),y:Math.round(r.y),
                    w:Math.round(r.width),h:Math.round(r.height)});
      } else if(n.nodeType===1) walk(n);
    }
  };
  walk(document.querySelector('#card'));
  return res;
}
"""
rows=[]
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1200,"height":900})
    for name, html in cards.items():
        doc=f"<!DOCTYPE html><html><head><meta charset='UTF-8'><style>{css}</style></head><body>{html}</body></html>"
        f=(out/f"{abs(hash(name))}.html").resolve(); f.write_text(doc,encoding="utf-8")
        pg.goto(f"file://{f}"); pg.wait_for_timeout(700)
        png = out/f"{abs(hash(name))}.png"
        pg.query_selector("#card").screenshot(path=str(png))
        texts = pg.evaluate(JS)
        # 워터마크 존재
        wm = pg.eval_on_selector_all(".wm,.wmc","e=>e.length")
        im = Image.open(png).convert("RGB"); W,H = im.size
        px = list(im.getdata())
        Ls = sorted(lab_L(p_) for p_ in px[::37])
        med = Ls[len(Ls)//2]
        # 텍스트별 대비 — 해당 위치 배경을 글자 주변 최빈 어두운 픽셀로 근사
        worst=None
        def parse(c):
            return [int(float(v)) for v in c.replace("rgb(","").replace("rgba(","").replace(")","").split(",")[:3]]
        lows=[]
        for t in texts:
            c=contrast(parse(t["col"]), parse(t["bg"]))
            lows.append((c,t["t"],t["cls"],int(t["fs"]),int(t["fw"])))
            if worst is None or c<worst[0]: worst=(c,t["t"],t["cls"],"")
        lows.sort()
        mn=min(t["fs"] for t in texts)
        mnt=[t for t in texts if t["fs"]==mn][0]
        rows.append(dict(name=name,W=W,H=H,aspect=round(H/W,3),minfs=mn,lows=lows[:3],
                         minfs_text=mnt["t"],minfs_cls=mnt["cls"],
                         medL=round(med,1),wm=wm,n=len(texts),
                         worst=round(worst[0],2),worst_t=worst[1],worst_cls=worst[2]))
    b.close()

print(f"{'카드':14s} {'크기':>11s} {'비율':>6s} {'최소폰트':>8s} {'medL*':>6s} {'WM':>3s} {'최저대비':>7s}")
print("-"*76)
bad=[]
for r in rows:
    flag=""
    if r["W"]!=CARD_WIDTH_PX or r["H"]>CARD_MAX_HEIGHT_PX or r["aspect"]>CARD_MAX_ASPECT: flag+=" [규격]"; bad.append(r)
    if r["minfs"]<CARD_MIN_FONT_PX: flag+=" [폰트]"; bad.append(r)
    if r["worst"]<4.5: flag+=" [대비]"
    print(f'{r["name"]:14s} {r["W"]}x{r["H"]:<6d} {r["aspect"]:>6.3f} '
          f'{r["minfs"]:>7.0f}px {r["medL"]:>6.1f} {r["wm"]:>3d} {r["worst"]:>6.2f}{flag}')
    if r["minfs"]<32: print(f'      ↳ 최소폰트 대상: "{r["minfs_text"]}" (.{r["minfs_cls"]})')
    for c,t,cls,fs,fw in r["lows"]:
        mark = "LARGE" if (fs>=24 and fw>=700) or fs>=32 else "small"
        need = 3.0 if mark=="LARGE" else 4.5
        print(f'      ↳ 대비 {c:5.2f} ({"OK " if c>=need else "낮음"} 기준{need}) {fs}px/{fw} "{t}" .{cls}')
print()
print(f"규격 기준: 폭 {CARD_WIDTH_PX} · 높이 ≤{CARD_MAX_HEIGHT_PX} · 비율 ≤{CARD_MAX_ASPECT} · 최소폰트 ≥{CARD_MIN_FONT_PX}px")
print(f"밝기: v1.x 이전 median L* 7.6 → 현재 {min(r['medL'] for r in rows):.1f}~{max(r['medL'] for r in rows):.1f}")
print(f"워터마크 레이어: 전 카드 {min(r['wm'] for r in rows)}층 (대각타일+대형아웃라인, 노이즈는 ::before)")
print("리그색:", {l.value: LEAGUE_COLORS[l][0] for l in list(League)[:5]})
