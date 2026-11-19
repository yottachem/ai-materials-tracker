"""Build the site into site/dist from the registries, ledger and data files. No JavaScript is needed to read it; a
small inline script adds the materials filter.

Pages: home (evidence ladder, comparison leaderboard, funding vs evidence, recent grade changes, checked statements in
analysis/claims.yaml), materials (filterable table), one page per material (comparison block first), one page per
organization with a material, watch list, methods, status.

Rules the pages follow:
  funding     tier-1 disclosed equity only (orgs.yaml); aggregator-only figures are listed under the chart, never plotted
  comparison  every material shows its benchmark and comparison basis; internal baselines and design targets are
              labeled as such, and "none stated" is shown as a finding
  grades      evidence and stage come from materials.yaml, which the ledger's latest grade rows must match (tests)

Usage: uv run python site/build.py [--preview]
--preview marks every page as an internal preview (noindex, banner) and shows each claim's condition.
"""
import csv
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import charts  # noqa: E402  (site/charts.py)
from analysis import claims  # noqa: E402
from ingest.common import domain_of, load_ledger, read_queue  # noqa: E402
from tracker import registry  # noqa: E402

DIST = HERE / "dist"
REPO = "https://github.com/yottachem/ai-materials-tracker"
SITE_URL = "https://yottachem.github.io/ai-materials-tracker/"
ANALYTICS_TOKEN = "ee99837aed054d67b79a567044baec28"  # Cloudflare Web Analytics (shared yottachem.github.io token)

EVIDENCE = {"peer-reviewed": "Peer-reviewed", "preprint": "Preprint", "company-reported": "Company-reported", "claim": "Claim"}
STAGE = {"deployed": "Deployed", "customer-tested": "Customer-tested", "lab": "Lab"}
BASIS = {"head-to-head": "Head-to-head", "literature": "Literature", "internal-baseline": "Internal baseline",
         "design-target": "Design target", "none-stated": "None stated", "not-applicable": "Not applicable"}
BASIS_NOTE = {"head-to-head": "Same test, same lab, against the field's best or the commercial incumbent",
              "literature": "Against a best-in-class value from a different study",
              "internal-baseline": "Against the starting recipe or the authors' own control, not the field's best",
              "design-target": "Against the value the model was asked to hit",
              "none-stated": "No quantitative comparison found",
              "not-applicable": "Bulk discovery result, not a single material"}
DISPUTE = {"disputed": "Disputed", "corrected": "Corrected", "retracted": "Retracted"}
CLASS = {"catalyst": "Catalyst", "electrolyte": "Electrolyte", "battery-cathode": "Battery cathode", "alloy": "Alloy",
         "polymer": "Polymer", "cement-concrete": "Cement and concrete", "oled": "OLED", "optical": "Optical",
         "inorganic-crystal": "Inorganic crystal", "sorbent": "Sorbent", "magnet": "Magnet", "fluid": "Fluid", "bio": "Bio-derived"}
METHOD = {"optimization": "Bayesian optimization or active learning", "screening": "ML screening",
          "generative": "Generative model", "llm-agentic": "LLM or agent", "not-stated": "Method not stated"}
METHOD_NOTE = {"optimization": "A model chooses the next experiment from the results so far (Bayesian optimization, active "
                               "learning, closed-loop or self-driving labs)",
               "screening": "A model predicts properties to rank a candidate set (graph neural networks, ML potentials, "
                            "regression, virtual screening)",
               "generative": "A generative model proposes new structures",
               "llm-agentic": "A large language model or agent drives the search",
               "not-stated": "The source says AI or ML but not which method"}
ORG_TYPE = {"company": "Company", "corporate-lab": "Corporate lab", "academic": "Academic or national lab", "incumbent": "Incumbent"}
WATCH = {
    "no-result": ("No material yet", "No material made with AI or ML published or announced",
                  "A named material with a measured property, from the organization itself"),
    "platform": ("Platform or software", "Sells AI/ML software or services; no material of its own",
                 "A named customer material with a measured property and the platform's role stated"),
    "incumbent-no-product": ("Incumbent, no AI or ML product", "Runs AI or ML programs but names no product made with them",
                             "A product the company attributes to AI or ML"),
    "no-ml": ("Computational design, no ML", "Computationally designed materials, but no machine-learning role stated",
              "A primary source stating what the model did"),
    "founder-work": ("Founders' prior work", "The published result predates the company",
                     "Company-authored work on a material"),
}
GRADE_TYPES = ("evidence", "stage", "dispute")
TYPE_LABELS = {"funding": "Funding", "valuation": "Valuation", "acquisition": "Acquisition", "listing": "Listing",
               "status": "Status", "launch": "Launch", "deployment": "Deployment", "license": "License", "pilot": "Pilot",
               "result": "Result", "partnership": "Partnership", "contract": "Contract", "award": "Federal award",
               "evidence": "Evidence", "stage": "Stage", "dispute": "Dispute"}
SECTIONS = [("published", "Published", "Peer-reviewed or preprint: the material and AI's role are in a paper"),
            ("company", "Company-reported", "Delivered or tested materials the company describes, without a paper"),
            ("claim", "Lab claims", "Announced results with no paper and no customer")]
CURATED = [("reference/materials.yaml", "Material registry: grades, key property, benchmark, comparison basis, sources"),
           ("reference/orgs.yaml", "Organizations: type, HQ, watch-list reason, tier-1 funding totals"),
           ("reference/ledger.csv", "Dated events and every grade change, each with its source"),
           ("reference/source_tiers.yaml", "Source tiers"),
           ("reference/signals.yaml", "What each signal searches for, per organization")]


def read(path):
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def links(urls):
    return [{"url": u, "host": domain_of(u)} for u in urls if u]


def section(m):
    return "published" if m["evidence"] in ("peer-reviewed", "preprint") else "company" if m["evidence"] == "company-reported" else "claim"


ACRONYMS = {r"\bsbir\b": "SBIR", r"\bsttr\b": "STTR", r"\bphase i\b": "Phase I", r"\bphase ii\b": "Phase II",
            r"\bpmma\b": "PMMA", r"\bmek\b": "MEK", r"\bpem\b": "PEM", r"\blow-ir\b": "low-Ir", r"\bghg\b": "GHG",
            r"\bai\b": "AI", r"\bdoe\b": "DOE", r"\bdod\b": "DoD", r"\blfp\b": "LFP", r"\bev\b": "EV",
            r"\(pn\)": "(PN)", r"\bu\.s\.": "U.S.", r"\bnavy\b": "Navy", r"\br and d\b": "R&D", r"\bp-100\b": "P-100",
            r"\bamerican\b": "American", r"\bstoicheia\b": "Stoicheia", r"\bco2\b": "CO2", r"\bpfas\b": "PFAS"}


def tidy(text):
    """Sentence-case an all-caps title (USAspending award text) and restore its acronyms; other text is unchanged."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text
    t = text.capitalize()
    for k, v in ACRONYMS.items():
        t = re.sub(k, v, t, flags=re.I)
    return t.rstrip(".")


def shown_detail(r):
    """Ledger detail as the public pages show it: review-step labels removed, all-caps award titles tidied."""
    if r["accept"] == "seed":
        return "Graded when the tracker was seeded"
    d = r["detail"]
    if d.startswith(("evidence at entry", "stage at entry")):
        return "Graded when the material was added"
    d = re.sub(r"^(launch check|review|reviewed):\s*", "", d, flags=re.I)
    head, sep, tail = d.rpartition(": ")
    d = f"{head}{sep}{tidy(tail)}" if sep else tidy(d)
    return d[:1].upper() + d[1:]


def ledger_rows(org_names):
    out = []
    for r in load_ledger():
        if r["status"] == "superseded":
            continue
        out.append({**r, "amount": num(r["amount_usd"]), "links": links(r["sources"].split()), "shown": shown_detail(r),
                    "type_label": TYPE_LABELS.get(r["type"], r["type"]), "org_name": org_names.get(r["org"], r["org"])})
    return out


def material_records(mats, org_by_id, ledger):
    out = {}
    for m in mats:
        b = m["benchmark"]
        rows = sorted((r for r in ledger if r["material"] == m["id"]), key=lambda r: (r["date"], r["id"]))
        trail = []
        pub = m.get("publication")
        if pub:
            trail.append({"what": "Paper" if m["evidence"] == "peer-reviewed" else "Preprint" if m["evidence"] == "preprint"
                          else "Publication", "label": pub.get("title") or pub["id"], "url": pub["url"],
                          "note": f'{pub["id"]}, {pub.get("date", "")}'.strip(", ")})
        d = m.get("dispute")
        if d:  # the summary describes the correction when there is one, otherwise the critique
            c = d.get("critique")
            if c:
                url = c if c.startswith("http") else ""
                label = d.get("summary") if url and not d.get("correction") else (domain_of(c) if url else c)
                trail.append({"what": "Critique", "label": label, "url": url, "note": ""})
            if d.get("correction"):
                trail.append({"what": "Correction", "label": d.get("summary") or domain_of(d["correction"]), "url": d["correction"], "note": ""})
        seen = {pub["url"]} if pub else set()
        for u in [m["key_property"].get("source"), *(m.get("sources") or [])]:
            if u and u not in seen:
                seen.add(u)
                trail.append({"what": "Source", "label": domain_of(u), "url": u, "note": ""})
        out[m["id"]] = {
            "id": m["id"], "m": m, "b": b, "section": section(m),
            "orgs": [org_by_id[o] for o in m["orgs"]],
            "evidence": EVIDENCE[m["evidence"]], "stage": STAGE[m["stage"]], "basis": BASIS[b["basis"]],
            "basis_note": BASIS_NOTE[b["basis"]], "cls": CLASS[m["material_class"]], "method": METHOD[m["method"]],
            "dispute": DISPUTE.get((d or {}).get("status")), "trail": trail,
            "grades": [r for r in rows if r["type"] in GRADE_TYPES],
            "events": [r for r in rows if r["type"] not in GRADE_TYPES],
            "org_types": sorted({org_by_id[o]["type"] for o in m["orgs"]}),
        }
    return out


def org_records(org_list, mrecs, ledger):
    papers = read(ROOT / "data" / "papers" / "works.csv")
    patents = read(ROOT / "data" / "patents" / "patents.csv")
    awards = read(ROOT / "data" / "awards" / "awards.csv")
    news = read(ROOT / "data" / "news" / "articles.csv")
    fin = read(ROOT / "data" / "public" / "financials.csv")
    out = {}
    for o in org_list:
        oid = o["id"]
        mine = [r for r in mrecs.values() if oid in r["m"]["orgs"]]
        rows = sorted((r for r in ledger if r["org"] == oid and r["type"] not in GRADE_TYPES + ("award", "contract")),
                      key=lambda r: (r["date"], r["id"]), reverse=True)
        pats = [p for p in patents if p["org"] == oid]
        by_year = Counter(p["filing_date"][:4] for p in pats if p["filing_date"])
        aw = sorted(({**a, "title": tidy(a["description"])} for a in awards if a["org"] == oid), key=lambda a: a["start"], reverse=True)
        f = [x for x in fin if x["company"] == oid]
        annual = sorted((x for x in f if x["metric"] == "revenue" and len(x["frame"]) == 6), key=lambda x: x["frame"])
        cash = sorted((x for x in f if x["metric"] == "cash"), key=lambda x: x["end"])
        fund = o.get("funding")
        out[oid] = {
            "id": oid, "o": o, "type": ORG_TYPE[o["type"]], "materials": mine, "rows": rows,
            "funding": fund, "funding_sources": links(o.get("sources") or []),
            "papers": sorted((p for p in papers if p["org"] == oid and p["relevant"] == "1"), key=lambda p: p["date"], reverse=True),
            "patents_n": len(pats), "patents_scope": sum(p["in_scope"] == "1" for p in pats),
            "patents_granted": sum(bool(p["patent"]) for p in pats),
            "patent_years": sorted(by_year.items()), "patents_recent": sorted((p for p in pats if p["in_scope"] == "1"),
                                                                              key=lambda p: p["filing_date"], reverse=True)[:8],
            "awards": aw, "awards_total": sum(num(a["amount_usd"]) or 0 for a in aw),
            "news": sorted((a for a in news if a["org"] == oid and a["url"] and (a["tier"] == "tier1" or a["events"])),
                           key=lambda a: a["date"], reverse=True)[:12],
            "revenue": annual[-1] if annual else None, "cash": cash[-1] if cash else None,
            "watch": WATCH.get(o.get("watch")), "page": bool(mine or fund),
        }
    return out


def best(mats):
    """(evidence row index, stage level) of an org's strongest material: published beats company-reported beats claim."""
    if not mats:
        return None
    order = list(EVIDENCE)
    m = min(mats, key=lambda r: (order.index(r["m"]["evidence"]), list(STAGE).index(r["m"]["stage"])))
    return order.index(m["m"]["evidence"]), 2 - list(STAGE).index(m["m"]["stage"]), m


def funding_chart(orecs):
    rows = [EVIDENCE[e] for e in EVIDENCE] + ["No material yet"]
    pts = []
    for o in registry.chart_orgs():
        r = orecs[o["id"]]
        bst = best(r["materials"])
        f = o["funding"]
        x = (f["low"] + f["high"]) / 2
        tip = f'{o["name"]}: {charts.fmt(x, "musd")} disclosed equity. '
        tip += (f'Strongest material: {bst[2]["m"]["name"]} ({bst[2]["evidence"]}, {bst[2]["stage"]})' if bst
                else "No material made with AI or ML on the tracker yet")
        pts.append({"label": o["name"], "href": f'org/{o["id"]}.html', "x": x, "row": bst[0] if bst else len(rows) - 1,
                    "level": bst[1] if bst else "n", "tip": tip, "none": not bst})
    svg = charts.funding_strip(pts, rows, desc="Organizations by tier-1 disclosed equity and the evidence grade of their strongest material")
    table = sorted(({"name": p["label"], "href": p["href"], "x": p["x"], "row": rows[p["row"]], "tip": p["tip"]} for p in pts),
                   key=lambda p: -p["x"])
    unconfirmed = [{"o": o, "r": orecs[o["id"]]} for o in registry.unconfirmed_funding_orgs()]
    return {"funding_svg": svg, "funding_table": table, "unconfirmed": unconfirmed,
            "funding_total": sum(p["x"] for p in pts), "funding_none": [p for p in pts if p["none"]]}


def home_context(mrecs, orecs, ledger):
    ladder = []
    for e, elabel in EVIDENCE.items():
        ladder.append({"label": elabel, "cells": [[r for r in mrecs.values() if r["m"]["evidence"] == e and r["m"]["stage"] == s]
                                                  for s in STAGE]})
    order = list(BASIS)
    board = sorted(mrecs.values(), key=lambda r: (order.index(r["b"]["basis"]), list(EVIDENCE).index(r["m"]["evidence"]),
                                                  r["m"]["name"]))
    changes = sorted((r for r in ledger if r["type"] in GRADE_TYPES and r["accept"] != "seed"),
                     key=lambda r: (r["date"], r["id"]), reverse=True)[:12]
    for c in changes:
        c["mrec"] = mrecs.get(c["material"])
    figs = {"materials": len(mrecs), "published": sum(r["section"] == "published" for r in mrecs.values()),
            "deployed": sum(r["m"]["stage"] == "deployed" for r in mrecs.values()),
            "compared": sum(r["b"]["basis"] in ("head-to-head", "literature") for r in mrecs.values()),
            "orgs": len({o for r in mrecs.values() for o in r["m"]["orgs"]})}
    methods = [{"key": k, "label": v, "note": METHOD_NOTE[k],
                "cells": [[r for r in mrecs.values() if r["m"]["method"] == k and r["section"] == sec] for sec, _, _ in SECTIONS]}
               for k, v in METHOD.items()]
    return {"ladder": ladder, "stages": list(STAGE.values()), "board": board, "methods": methods, "changes": changes, "figs": figs,
            **funding_chart(orecs)}


def watch_context(orecs):
    groups = []
    for key, (label, why, promote) in WATCH.items():
        rows = sorted((r for r in orecs.values() if r["o"].get("watch") == key), key=lambda r: r["o"]["name"].lower())
        groups.append({"key": key, "label": label, "why": why, "promote": promote, "rows": rows})
    return {"groups": groups, "n": sum(len(g["rows"]) for g in groups)}


def last_changed(path):
    r = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() or None


def status_context(ledger):
    queue = read_queue()
    news = read(ROOT / "data" / "news" / "articles.csv")
    papers = read(ROOT / "data" / "papers" / "works.csv")
    awards = read(ROOT / "data" / "awards" / "awards.csv")
    fin = read(ROOT / "data" / "public" / "financials.csv")
    stamp = ROOT / "data" / "patents" / "last_full_run.txt"
    found = defaultdict(list)
    for q in queue:
        found[q["source"]].append(q["found"])
    sources = [
        {"name": "News: Bing News, Google News, company newsrooms", "what": "Launches, pilots, licenses, funding (to the review queue)",
         "last": max((a["found"] for a in news), default=None), "cadence": "Weekly"},
        {"name": "SEC EDGAR filings", "what": "Form D and 8-K for tracked companies", "last": max(found["sec"], default=None),
         "cadence": "Weekly"},
        {"name": "OpenAlex papers", "what": "New papers by tracked organizations; journal versions of preprints",
         "last": max((p["found"] for p in papers), default=None), "cadence": "Weekly"},
        {"name": "Crossref updates", "what": "Corrections and retractions of tracked papers", "last": max(found["crossref"], default=None),
         "cadence": "Weekly"},
        {"name": "USAspending", "what": "Federal grants and contracts", "last": max(found["award"], default=None), "cadence": "Weekly"},
        {"name": "SEC XBRL financials", "what": "Revenue, R&D, cash for public companies", "last": max((x["filed"] for x in fin), default=None),
         "cadence": "Weekly"},
        {"name": "USPTO Open Data Portal", "what": "Patent applications and grants", "last": stamp.read_text().strip() if stamp.exists() else None,
         "cadence": "Quarterly"},
    ]
    return {"sources": sources, "open": sum(q["status"] == "open" for q in queue), "ledger_n": len(ledger),
            "grade_n": sum(r["type"] in GRADE_TYPES and r["accept"] != "seed" for r in ledger),
            "curated": [{"path": p, "what": w, "changed": last_changed(p)} for p, w in CURATED]}


def load():
    """Everything the pages are built from; analysis/claims.py uses it too."""
    mats = registry.materials()
    org_list = registry.orgs()
    org_by_id = {o["id"]: o for o in org_list}
    ledger = ledger_rows({o["id"]: o["name"] for o in org_list})
    mrecs = material_records(mats, org_by_id, ledger)
    orecs = org_records(org_list, mrecs, ledger)
    return {"mats": mats, "orgs": org_list, "ledger": ledger, "mrecs": mrecs, "orecs": orecs}


def main(preview=False):
    d = load()
    mrecs, orecs, ledger = d["mrecs"], d["orecs"], d["ledger"]
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["fmt"] = charts.fmt
    env.filters["host"] = domain_of
    common = {"repo": REPO, "preview": preview, "site_url": SITE_URL, "built": __import__("datetime").date.today().isoformat(),
              "analytics_token": "" if preview else ANALYTICS_TOKEN, "EVIDENCE": EVIDENCE, "STAGE": STAGE, "BASIS": BASIS,
              "BASIS_NOTE": BASIS_NOTE, "CLASS": CLASS, "METHOD": METHOD, "METHOD_NOTE": METHOD_NOTE, "ORG_TYPE": ORG_TYPE, "SECTIONS": SECTIONS}
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "material").mkdir(parents=True)
    (DIST / "org").mkdir()

    def render(template, path, page, **ctx):
        (DIST / path).write_text(env.get_template(template).render(**common, page=page, **ctx))

    said, _ = claims.evaluate(claims.values(d)) if claims.SPEC.exists() else ({}, [])
    render("index.html.j2", "index.html", "index", **home_context(mrecs, orecs, ledger), claims=said)
    render("materials.html.j2", "materials.html", "materials", mrecs=mrecs)
    for r in mrecs.values():
        render("material.html.j2", f"material/{r['id']}.html", "material", r=r, orecs=orecs, up="../")
    for r in orecs.values():
        if r["page"]:
            render("org.html.j2", f"org/{r['id']}.html", "org", r=r, up="../")
    render("watch.html.j2", "watch.html", "watch", **watch_context(orecs))
    render("methods.html.j2", "methods.html", "methods", n_materials=len(mrecs))
    render("status.html.j2", "status.html", "status", **status_context(ledger))
    for f in (HERE / "static").iterdir():
        shutil.copy(f, DIST / f.name)
    (DIST / ".nojekyll").touch()
    print(f"site/dist: {sum(1 for _ in DIST.rglob('*') if _.is_file())} files{' (preview)' if preview else ''}")


if __name__ == "__main__":
    main(preview="--preview" in sys.argv)
