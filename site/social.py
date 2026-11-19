"""Render the social preview card (site/static/social.png, 1200x630): materials by evidence grade, split by stage.

Run locally when grades move; CI doesn't have Chrome, so the PNG is committed.
Run: uv run python site/social.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import build  # noqa: E402
from analysis import claims  # noqa: E402
from tracker import registry  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROWS = [("Peer-reviewed", ("peer-reviewed",)), ("Preprint", ("preprint",)), ("Company-reported", ("company-reported",)),
        ("Claim", ("claim",))]
STAGES = ("lab", "customer-tested", "deployed")


def bars(mats, width=640, row=50, label_w=190):
    top = max(sum(m["evidence"] in ev for m in mats) for _, ev in ROWS) or 1
    unit = (width - label_w - 50) / top
    out = [f'<svg class="chart" viewBox="0 0 {width} {row * len(ROWS)}" role="img" aria-label="Materials by evidence grade, split by stage">']
    for i, (label, ev) in enumerate(ROWS):
        y, x = i * row, label_w
        out.append(f'<text class="lab" x="{label_w - 14}" y="{y + row / 2 + 6}" text-anchor="end">{label}</text>')
        n = 0
        for k, st in enumerate(STAGES):
            c = sum(m["evidence"] in ev and m["stage"] == st for m in mats)
            if c:
                out.append(f'<rect x="{x:.1f}" y="{y + 8}" width="{c * unit:.1f}" height="{row - 16}" class="k{k}"/>')
                x += c * unit
                n += c
        out.append(f'<text class="val" x="{x + 10:.1f}" y="{y + row / 2 + 6}">{n}</text>')
    out.append("</svg>")
    return "".join(out)


def main():
    mats = registry.materials()
    m = claims.current()["market"]
    note = (f"{m['published_established']} of {m['published']} materials with a paper came from Bayesian optimization, active "
            f"learning or ML screening. {claims.fmt(m['published_deployed'], 'cap')} is both published and deployed.")
    html = Environment(loader=FileSystemLoader(HERE / "templates")).get_template("social.html.j2").render(
        chart=bars(mats), n=len(mats), note=note, site_url=build.SITE_URL)
    out = HERE / "static" / "social.png"
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "social.html"
        page.write_text(html)
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        "--window-size=1200,630", "--virtual-time-budget=4000", f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True)
    print(out)


if __name__ == "__main__":
    main()
