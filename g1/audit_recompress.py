"""텔레그램 재압축 판독성 검수 (P1-2).

원본과 '채널에 올라간 뒤 다시 받은' 이미지를 나란히 재서
어두운 배경 위 작은 글자가 실제로 살아남았는지 판정한다.

핵심 지표는 파일 크기가 아니라 **글자 가장자리의 대비 손실**이다.
크로마 서브샘플링(4:4:4 → 4:2:0)은 유채색 소형 글자를 먼저 무너뜨린다.
"""
import pathlib, sys
import numpy as np
from PIL import Image

def lin(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def luma(im):
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    r, g, b = lin(a[..., 0]), lin(a[..., 1]), lin(a[..., 2])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def edge_energy(y):
    """글자 획이 만드는 밝기 변화량. 뭉개지면 줄어든다."""
    gx = np.abs(np.diff(y, axis=1))
    gy = np.abs(np.diff(y, axis=0))
    return gx.mean() + gy.mean()

def chroma_spread(im):
    """유채색 정보의 분산. 4:2:0으로 깎이면 줄어든다."""
    a = np.asarray(im.convert("YCbCr"), dtype=np.float64)
    return float(a[..., 1].std() + a[..., 2].std())

# 획 보존 상한 — 라이트 테마 기준.
#
# 다크 테마(v3)에서는 109~111%였다. v4 라이트로 바꾸니 110~114%로 올라갔다.
# 밝은 바닥 위 어두운 글자가 JPEG DCT에서 더 큰 진폭 변화를 만들어
# 링잉(글자 주변 후광)이 커지기 때문이다.
#
# 발송 품질을 올려 줄일 수 있는지 실측했다 — q88 125.9% / q92 117.2% /
# q95 119.1% / q97 116.6%. 품질을 67% 더 써도 개선이 없다.
# 지배 요인은 우리 발송 품질이 아니라 **텔레그램의 재압축 자체**이고,
# 그건 우리가 통제할 수 없다. 그래서 발송 품질은 q92로 유지하고
# 상한을 라이트 테마의 물리적 하한(≈116%)보다 조금 위인 118%로 잡는다.
EDGE_MAX = 118.0


def report(orig_dir: pathlib.Path, recv_dir: pathlib.Path):
    rows, worst = [], []
    for o in sorted(orig_dir.glob("*.jpg")):
        r = recv_dir / o.name
        if not r.exists():
            r = next((x for x in recv_dir.glob("*.jpg") if o.stem.endswith(x.stem)
                      or x.stem.endswith(o.stem)), None)
        if r is None or not r.exists():
            print(f"  {o.stem:16s} 되받은 파일 없음"); continue
        a, b = Image.open(o), Image.open(r)
        if a.size != b.size:
            b = b.resize(a.size, Image.LANCZOS)
        ya, yb = luma(a), luma(b)
        ea, eb = edge_energy(ya), edge_energy(yb)
        ca, cb = chroma_spread(a), chroma_spread(b)
        # 밝기 차 — 카드가 어두워졌는지
        dl = (yb.mean() - ya.mean()) * 100
        # 최대 오차 (구조적 붕괴 탐지)
        diff = np.abs(ya - yb)
        rows.append(dict(name=o.stem, w=a.size[0], h=a.size[1],
                         edge=eb / ea * 100, chroma=cb / ca * 100,
                         dl=dl, p999=float(np.quantile(diff, 0.999)) * 100,
                         ko=o.stat().st_size // 1024, kr=r.stat().st_size // 1024))
    if not rows:
        print("  비교할 파일이 없습니다."); return
    print(f"{'카드':16s} {'크기':>10s} {'원본KB':>7s} {'수신KB':>7s} "
          f"{'획 보존':>7s} {'색 보존':>7s} {'밝기Δ':>7s} {'최대오차':>8s}")
    print("-" * 82)
    for x in rows:
        flag = ""
        if x["edge"] < 88: flag += " [획손실]"
        elif x["edge"] > EDGE_MAX: flag += " [링잉]"
        if x["chroma"] < 85: flag += " [색손실]"
        if abs(x["dl"]) > 1.5: flag += " [밝기변화]"
        if x["p999"] > 12: flag += " [국소붕괴]"
        print(f'{x["name"]:16s} {x["w"]}x{x["h"]:<5d} {x["ko"]:6d} {x["kr"]:6d} '
              f'{x["edge"]:6.1f}% {x["chroma"]:6.1f}% {x["dl"]:+6.2f} {x["p999"]:7.1f}%{flag}')
        if flag: worst.append((x["name"], flag))
    print()
    print(f"판정 기준 — 획 보존 88~{EDGE_MAX:.0f}% · 색 보존 ≥85% · 밝기 변화 ±1.5 이내 · 최대오차 ≤12%")
    print("  (라이트 테마는 링잉이 커진다. 발송 품질로는 못 줄인다 — 실측 근거는 파일 상단)")
    print("합격" if not worst else f"검토 필요: {worst}")

if __name__ == "__main__":
    o = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "dryrun/orig")
    r = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else "dryrun/received")
    report(o, r)
