"""Site build smoke tests: every page renders, no internal labels leak, every claim holds."""
import importlib.util
import os
import re

import pytest

from analysis import claims
from ingest.common import ROOT
from tracker import registry


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DIST = tmp_path_factory.mktemp("dist")
    mod.main(preview=True)
    return mod.DIST


def test_pages_exist(site):
    for p in ("index", "materials", "watch", "methods", "status"):
        assert (site / f"{p}.html").exists(), p
    for m in registry.materials():
        assert (site / "material" / f"{m['id']}.html").exists(), m["id"]


def test_internal_links_resolve(site):
    bad = []
    for f in site.rglob("*.html"):
        for h in re.findall(r'href="([^"#]+)"', f.read_text()):
            if not h.startswith(("http", "mailto")) and not (f.parent / h).resolve().exists():
                bad.append((f.name, h))
    assert bad == []


def test_no_internal_text_leaks(site):
    """Review-step labels, verify flags and private notes never reach a page."""
    leaks = re.compile(r"launch check:|evidence at entry|stage at entry|Weakest entry|Weak entry|verify:|>None<")
    for f in site.rglob("*.html"):
        hits = leaks.findall(f.read_text())
        assert not hits, (f.name, hits)


def test_chart_points_are_tier1(site):
    svg = (site / "index.html").read_text()
    for o in registry.unconfirmed_funding_orgs():
        assert f'href="org/{o["id"]}.html"><circle' not in svg, o["id"]


def test_claims_evaluate():
    out, review = claims.evaluate(claims.current())
    assert out["market"], "no claims"


@pytest.mark.skipif(not os.environ.get("LAUNCH_GATE"), reason="launch gate only: a claim under review is a normal state")
def test_claims_hold_at_launch():
    _, review = claims.evaluate(claims.current())
    assert review == [], review
