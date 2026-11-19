"""Server-rendered SVG charts: no JavaScript, readable without CSS, colors from the page's tokens.

fmt and nice_ticks match physical-ai-tracker and lifesci-tools-tracker so the sites read alike. The funding chart is a
strip chart: one row per evidence level, organizations placed by tier-1 funding on a log scale, fill by stage.
"""
import math
from html import escape


def fmt(v, kind="n"):
    if v is None:
        return "–"
    if kind == "usd":
        return f"${v / 1e9:,.1f}B" if abs(v) >= 1e9 else f"${v / 1e6:,.0f}M" if abs(v) >= 1e6 else f"${v / 1e3:,.0f}K"
    if kind == "musd":  # value already in USD millions
        return f"${v / 1000:,.1f}B" if v >= 1000 else f"${v:,.0f}M" if v >= 10 else f"${v:,.1f}M"
    if kind == "pct":
        return f"{v:.0%}"
    return f"{v:,.0f}"


def scale(d0, d1, r0, r1):
    k = (r1 - r0) / (d1 - d0) if d1 != d0 else 0
    return lambda v: r0 + (v - d0) * k


def funding_strip(points, rows, *, width=960, height=None, desc=""):
    """points: [{"label", "href", "x" (USD millions, > 0), "row" (index into rows), "level" (0 lab .. 2 deployed),
    "tip"}]. rows: row labels, top to bottom. Labels are placed right of the dot, or left when that collides."""
    row_h, top, bottom, left, right = 54, 18, 40, 190, 40
    height = height or top + bottom + row_h * len(rows)
    w = width - left - right
    xs = [p["x"] for p in points]
    lx0, lx1 = math.floor(math.log10(min(xs))), math.ceil(math.log10(max(xs)))
    lin = scale(lx0, lx1, left, left + w)

    def x(v):
        return lin(math.log10(v))

    def y(i):
        return top + row_h * i + row_h / 2

    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(desc)}" class="chart strip">']
    for i, name in enumerate(rows):
        out.append(f'<line x1="{left}" x2="{left + w}" y1="{y(i):.1f}" y2="{y(i):.1f}" class="grid"/>'
                   f'<text x="{left - 12}" y="{y(i) + 4:.1f}" text-anchor="end" class="axis">{escape(name)}</text>')
    for e in range(lx0, lx1 + 1):
        v = 10 ** e
        out.append(f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="{top}" y2="{top + row_h * len(rows)}" class="grid"/>'
                   f'<text x="{x(v):.1f}" y="{top + row_h * len(rows) + 16}" text-anchor="middle" class="tick">'
                   f'{fmt(v, "musd")}</text>')
    out.append(f'<text x="{left + w}" y="{height - 4}" text-anchor="end" class="axis">'
               f'Disclosed equity, tier-1 sources (log scale) →</text>')
    # Place dots first: within a row, a dot closer than 16px to one already placed moves up or down (a small beeswarm),
    # then every label is placed against all dots and earlier labels.
    placed = []
    for p in sorted(points, key=lambda p: (p["row"], p["x"])):
        cx = x(p["x"])
        for dy in (0, -14, 14):
            cy = y(p["row"]) + dy
            if all((cx - qx) ** 2 + (cy - qy) ** 2 >= 16 ** 2 for qx, qy, _ in placed):
                break
        placed.append((cx, cy, p))
    boxes = [(cx - 8, cy - 8, cx + 8, cy + 8) for cx, cy, _ in placed]
    dots, labels = [], []
    for cx, cy, p in placed:
        dots.append(f'<a href="{p["href"]}"><circle cx="{cx:.1f}" cy="{cy:.1f}" r="7" class="st{p["level"]}">'
                    f'<title>{escape(p["tip"])}</title></circle></a>')
        tw = 7.2 * len(p["label"])
        cands = [(cx + 11, cy + 4, "start"), (cx - 11, cy + 4, "end"), (cx + 6, cy - 12, "start"), (cx - 6, cy - 12, "end"),
                 (cx + 6, cy + 21, "start"), (cx - 6, cy + 21, "end")]

        def box(c):
            tx, ty, a = c
            x0 = tx if a == "start" else tx - tw
            return (x0, ty - 11, x0 + tw, ty + 3)

        def bad(bx):
            return sum(max(0, min(bx[2], o[2]) - max(bx[0], o[0])) * max(0, min(bx[3], o[3]) - max(bx[1], o[1])) for o in boxes) \
                + (1e6 if bx[2] > width or bx[0] < left - 4 else 0)

        best = min(cands, key=lambda c: bad(box(c)))
        boxes.append(box(best))
        tx, ty, a = best
        labels.append(f'<a href="{p["href"]}"><text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{a}" class="lab">'
                      f'{escape(p["label"])}</text></a>')
    out += dots + labels
    out.append("</svg>")
    return "".join(out)
