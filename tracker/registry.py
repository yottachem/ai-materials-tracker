"""Load and check reference/materials.yaml and reference/orgs.yaml.

The material is the unit; organizations are secondary. `problems()` returns every rule violation as a string so a
test can show them all at once. `launch_problems()` adds the launch gate: no open `verify` fields, and every
organization plotted on the funding chart sourced from tier 1.
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "reference"

EVIDENCE = ("peer-reviewed", "preprint", "company-reported", "claim")
STAGES = ("deployed", "customer-tested", "lab")
BASES = ("head-to-head", "literature", "internal-baseline", "design-target", "none-stated", "not-applicable")
ORG_TYPES = ("company", "corporate-lab", "academic", "incumbent")
WATCH = ("no-result", "platform", "incumbent-no-product", "no-ml", "founder-work")
DISPUTE = ("disputed", "corrected", "retracted")
FUNDING_TIERS = ("tier1", "aggregator")
MATERIAL_CLASSES = ("catalyst", "electrolyte", "battery-cathode", "alloy", "polymer", "cement-concrete", "oled",
                    "optical", "inorganic-crystal", "sorbent", "magnet", "fluid", "bio")
# What the AI or ML did, by family. Most proven results use established ML that predates LLMs.
#   optimization  Bayesian optimization, active learning or a closed-loop / self-driving lab choosing the next experiment
#   screening     ML property prediction ranking a candidate set (GNNs, ML potentials, regression, virtual screening)
#   generative    a generative model proposing new structures
#   llm-agentic   an LLM or agent driving the search
#   not-stated    the source says AI or ML but not which method
METHODS = ("optimization", "screening", "generative", "llm-agentic", "not-stated")
COMPARED = ("head-to-head", "literature", "internal-baseline", "design-target")


def load(name: str) -> list[dict]:
    data = yaml.safe_load((REFERENCE / f"{name}.yaml").read_text())
    return data[name]


def materials() -> list[dict]:
    return load("materials")


def orgs() -> list[dict]:
    return load("orgs")


def _get(entry: dict, path: str):
    node = entry
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _urls(entry, found=None):
    found = [] if found is None else found
    if isinstance(entry, dict):
        for v in entry.values():
            _urls(v, found)
    elif isinstance(entry, list):
        for v in entry:
            _urls(v, found)
    elif isinstance(entry, str) and entry.startswith("http"):
        found.append(entry)
    return found


def problems(mats: list[dict] | None = None, org_list: list[dict] | None = None) -> list[str]:
    mats = materials() if mats is None else mats
    org_list = orgs() if org_list is None else org_list
    out: list[str] = []
    org_ids = [o["id"] for o in org_list]
    by_org = {o["id"]: o for o in org_list}
    for dup in {i for i in org_ids if org_ids.count(i) > 1}:
        out.append(f"orgs: duplicate id {dup}")
    mat_ids = [m["id"] for m in mats]
    for dup in {i for i in mat_ids if mat_ids.count(i) > 1}:
        out.append(f"materials: duplicate id {dup}")

    used: set[str] = set()
    for m in mats:
        mid = m["id"]
        for field in ("name", "orgs", "material_class", "application", "what_is_new", "benchmark", "ai_role",
                      "evidence", "stage", "verified"):
            if not m.get(field):
                out.append(f"{mid}: missing {field}")
        if m.get("evidence") not in EVIDENCE:
            out.append(f"{mid}: evidence {m.get('evidence')!r} not in {EVIDENCE}")
        if m.get("stage") not in STAGES:
            out.append(f"{mid}: stage {m.get('stage')!r} not in {STAGES}")
        if m.get("method") not in METHODS:
            out.append(f"{mid}: method {m.get('method')!r} not in {METHODS}")
        if m.get("material_class") not in MATERIAL_CLASSES:
            out.append(f"{mid}: material_class {m.get('material_class')!r} not in {MATERIAL_CLASSES}")
        for oid in m.get("orgs") or []:
            used.add(oid)
            if oid not in by_org:
                out.append(f"{mid}: org {oid} not in orgs.yaml")
            elif by_org[oid].get("watch"):
                out.append(f"{mid}: org {oid} is on the watch list but has a material")
        if m.get("evidence") in ("peer-reviewed", "preprint"):
            pub = m.get("publication") or {}
            if not pub.get("id") or not pub.get("url"):
                out.append(f"{mid}: {m['evidence']} needs publication id and url")
        elif not (m.get("sources") or _get(m, "key_property.source")):
            out.append(f"{mid}: company-reported or claim needs a source")
        b = m.get("benchmark") or {}
        if b.get("basis") not in BASES:
            out.append(f"{mid}: benchmark.basis {b.get('basis')!r} not in {BASES}")
        if b.get("basis") in COMPARED:
            if not b.get("gap"):
                out.append(f"{mid}: benchmark basis {b['basis']} needs a gap")
            if not b.get("material"):
                out.append(f"{mid}: benchmark basis {b['basis']} needs a benchmark material")
        if b.get("basis") in ("head-to-head", "literature") and not b.get("source"):
            out.append(f"{mid}: {b['basis']} benchmark needs its own source")
        if b.get("basis") in ("none-stated", "not-applicable") and not b.get("gap"):
            out.append(f"{mid}: say why there is no comparison (benchmark.gap)")
        if m.get("dispute") and m["dispute"].get("status") not in DISPUTE:
            out.append(f"{mid}: dispute.status not in {DISPUTE}")
        for path in m.get("verify") or []:
            if _get(m, path) is None and path not in ("orgs",):
                out.append(f"{mid}: verify field {path} does not exist")
        for url in _urls(m):
            if not url.startswith("https://"):
                out.append(f"{mid}: non-https url {url}")

    for o in org_list:
        oid = o["id"]
        if o.get("type") not in ORG_TYPES:
            out.append(f"{oid}: type {o.get('type')!r} not in {ORG_TYPES}")
        if o.get("watch") and o["watch"] not in WATCH:
            out.append(f"{oid}: watch {o['watch']!r} not in {WATCH}")
        if not o.get("watch") and oid not in used:
            out.append(f"{oid}: not on the watch list and has no material")
        f = o.get("funding")
        if f:
            if f.get("tier") not in FUNDING_TIERS:
                out.append(f"{oid}: funding.tier not in {FUNDING_TIERS}")
            if f.get("low", 0) > f.get("high", 0):
                out.append(f"{oid}: funding low > high")
    return out


def chart_orgs(org_list: list[dict] | None = None, mats: list[dict] | None = None) -> list[dict]:
    """Venture-backed companies with tier-1 sourced funding: the funding vs evidence chart's points."""
    org_list = orgs() if org_list is None else org_list
    return [o for o in org_list if o.get("type") == "company" and (o.get("funding") or {}).get("tier") == "tier1"]


def unconfirmed_funding_orgs(org_list: list[dict] | None = None) -> list[dict]:
    """Companies whose funding is aggregator-only: listed under the chart with figure and source, never plotted
    (decision 2026-11-19, option A)."""
    org_list = orgs() if org_list is None else org_list
    return [o for o in org_list if o.get("type") == "company" and o.get("funding") and o["funding"].get("tier") != "tier1"]


def launch_problems() -> list[str]:
    out = problems()
    for m in materials():
        if m.get("verify"):
            out.append(f"{m['id']}: open verify {m['verify']}")
    for o in orgs():
        if o.get("verify"):
            out.append(f"{o['id']}: open verify {o['verify']}")
    for o in chart_orgs():
        if o["funding"]["tier"] != "tier1":
            out.append(f"{o['id']}: chart funding not tier-1 sourced")
    return out


if __name__ == "__main__":
    import sys

    found = launch_problems() if "--launch" in sys.argv else problems()
    print("\n".join(found) or "ok")
    sys.exit(1 if found else 0)
