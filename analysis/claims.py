"""Checked statements: every interpretive sentence on the home page is a claim in analysis/claims.yaml, stored with
the condition it rests on. Each build re-checks every claim:

- condition holds: the text is shown, numbers filled in from this run
- condition fails: the neutral fallback is shown (or nothing, if it's ""), marked under review
- a value the claim uses is outside its plausibility range: treated as a possible data problem, shown the same way,
  and reported separately as a data check

Statements describe the evidence on the tracker. An organization is named only as the holder of a figure, never judged.
Rewriting a claim is a reviewed, manual step: change `text` and `holds` together so the condition states exactly what
the text asserts.

Values (all under `m`): counts of materials by evidence, stage and comparison basis, the chart's funding totals, and
fractions (0.31, shown with :pct as 31%). See them all with --values.

Usage: uv run python -m analysis.claims            # print claims under review, write analysis/results/review.json
       uv run python -m analysis.claims --values   # also print every value claims can use
       uv run python -m analysis.claims --markdown # claims under review as an issue body
"""
import html
import importlib.util
import json
import re
import sys
from types import SimpleNamespace

import yaml

from ingest.common import ROOT

SPEC = ROOT / "analysis" / "claims.yaml"
REVIEW = ROOT / "analysis" / "results" / "review.json"
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*\.[a-z0-9_]+)(?::([a-z0-9_]+))?\}")
NAME = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\b")
SAFE = {"abs": abs, "min": min, "max": max, "round": round, "None": None, "True": True, "False": False}
PUBLISHED = ("peer-reviewed", "preprint")
COMPARED = ("head-to-head", "literature")


def load_site():
    """site/build.py as a module (it isn't a package)."""
    spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def values(d):
    """{'market': {name: value}} from site/build.py's load()."""
    from tracker import registry
    mats = d["mats"]
    n = len(mats)

    def count(f):
        return sum(1 for m in mats if f(m))

    basis = {b: count(lambda m, b=b: m["benchmark"]["basis"] == b) for b in
             ("head-to-head", "literature", "internal-baseline", "design-target", "none-stated", "not-applicable")}
    single = n - basis["not-applicable"]  # single materials: bulk discovery results have no benchmark by design
    chart = registry.chart_orgs(d["orgs"])
    with_mat = {o for m in mats for o in m["orgs"]}
    raised = sum((o["funding"]["low"] + o["funding"]["high"]) / 2 for o in chart)
    none = [o for o in chart if o["id"] not in with_mat]
    none_raised = sum((o["funding"]["low"] + o["funding"]["high"]) / 2 for o in none)
    v = {
        "n": n, "single": single,
        "published": count(lambda m: m["evidence"] in PUBLISHED),
        "peer_reviewed": count(lambda m: m["evidence"] == "peer-reviewed"),
        "company_reported": count(lambda m: m["evidence"] == "company-reported"),
        "claim": count(lambda m: m["evidence"] == "claim"),
        "deployed": count(lambda m: m["stage"] == "deployed"),
        "customer_tested": count(lambda m: m["stage"] == "customer-tested"),
        "lab": count(lambda m: m["stage"] == "lab"),
        "beyond_lab": count(lambda m: m["stage"] != "lab"),
        "published_deployed": count(lambda m: m["evidence"] in PUBLISHED and m["stage"] == "deployed"),
        "published_beyond_lab": count(lambda m: m["evidence"] in PUBLISHED and m["stage"] != "lab"),
        "deployed_unpublished": count(lambda m: m["evidence"] not in PUBLISHED and m["stage"] == "deployed"),
        "disputed": count(lambda m: (m.get("dispute") or {}).get("status") in ("disputed", "corrected", "retracted")),
        **{b.replace("-", "_"): x for b, x in basis.items()},
        "compared": basis["head-to-head"] + basis["literature"],
        "own_baseline": basis["internal-baseline"] + basis["design-target"],
        "chart_n": len(chart), "chart_raised": raised,
        "chart_none_n": len(none), "chart_none_raised": none_raised,
        "unconfirmed_n": len(registry.unconfirmed_funding_orgs(d["orgs"])),
    }
    pub = [m for m in mats if m["evidence"] in PUBLISHED]
    for k in registry.METHODS:
        v[f"{k.replace('-', '_')}_n"] = count(lambda m, k=k: m["method"] == k)
        v[f"published_{k.replace('-', '_')}"] = sum(m["method"] == k for m in pub)
    v["published_established"] = v["published_optimization"] + v["published_screening"]
    v["published_pre2022"] = sum(str((m.get("publication") or {}).get("date", ""))[:4] < "2022" for m in pub)
    v["published_share"] = v["published"] / n if n else None
    v["compared_share"] = v["compared"] / single if single else None
    v["none_stated_share"] = v["none_stated"] / single if single else None
    v["beyond_lab_share"] = v["beyond_lab"] / n if n else None
    v["chart_none_share"] = none_raised / raised if raised else None
    return {"market": {"name": "All tracked materials", **v}}


def fmt(x, spec=None):
    if x is None:
        return "n/a"
    match spec:
        case "pct":
            return f"{x:.0%}"
        case "musd":  # USD millions
            return f"${x / 1000:,.1f} billion" if x >= 1000 else f"${x:,.0f} million"
        case "int":
            return f"{x:,.0f}"
        case "word":  # small counts spelled out
            return ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][x] if 0 <= x < 10 else f"{x:,}"
        case "cap":
            s = fmt(x, "word")
            return s[:1].upper() + s[1:]
        case _:
            return str(x)


def namespace(v, tid):
    env = {k: SimpleNamespace(**d) for k, d in v.items()}
    env["t"], env["m"] = env[tid], env["market"]
    return env


def fill(text, env, as_html=True):
    def sub(m):
        obj, attr = m.group(1).split(".")
        return fmt(getattr(env[obj], attr), m.group(2))
    s = PLACEHOLDER.sub(sub, (text or "").strip())
    if as_html:
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(s, quote=False))
    return s


def holds(expr, env):
    return bool(eval(str(expr), {"__builtins__": {}}, {**SAFE, **env}))  # noqa: S307  expressions are repo-authored


def names_in(claim):
    found = set()
    for s in (claim.get("text", ""), claim.get("fallback", "")):
        found |= {tuple(m.group(1).split(".")) for m in PLACEHOLDER.finditer(s or "")}
    found |= set(NAME.findall(re.sub(r"(['\"]).*?\1", "", str(claim.get("holds", "True")))))
    return found


def resolve(o, tid):
    return {"t": tid, "m": "market"}.get(o, o)


def evaluate(v, spec=None):
    """({entry: [claim rows]}, [claims under review])."""
    spec = spec or yaml.safe_load(SPEC.read_text())
    checks = spec.get("checks", {})
    out, review = {}, []
    for tid, claims in spec["claims"].items():
        env = namespace(v, tid)
        rows = []
        for c in claims:
            bad = sorted(f"{resolve(o, tid)}.{a}" for o, a in names_in(c)
                         if a in checks and (x := v[resolve(o, tid)].get(a)) is not None
                         and not checks[a][0] <= x <= checks[a][1])
            ok = not bad and holds(c.get("holds", True), env)
            status = "ok" if ok else "data_check" if bad else "review"
            rows.append({"id": c["id"], "status": status, "holds": str(c.get("holds", True)),
                         "html": fill(c["text"], env) if ok else fill(c.get("fallback", ""), env)})
            if not ok:
                review.append({"entry": tid, "id": c["id"], "status": status, "holds": str(c.get("holds")),
                               "text": fill(c["text"], env, as_html=False), "out_of_range": bad})
        out[tid] = rows
    return out, review


def current():
    return values(load_site().load())


def run(v=None):
    out, review = evaluate(v or current())
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    REVIEW.write_text(json.dumps(review, indent=1))
    n = sum(len(r) for r in out.values())
    print(f"{n} claims, {n - len(review)} hold, {len(review)} under review")
    for r in review:
        print(f"  {r['status']:10} {r['entry']}.{r['id']}: {r['holds']}  {r['out_of_range'] or ''}")
    return out


def markdown():
    rows = json.loads(REVIEW.read_text()) if REVIEW.exists() else []
    return "".join(f"- **{r['entry']}.{r['id']}** ({r['status']}): {r['text']}\n  holds: `{r['holds']}`"
                   f"{'  out of range: ' + ', '.join(r['out_of_range']) if r['out_of_range'] else ''}\n" for r in rows)


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        sys.exit(print(markdown(), end=""))
    v = current()
    if "--values" in sys.argv:
        for tid, d in v.items():
            print(f"== {tid}")
            for k, x in d.items():
                print(f"  {k:26} {x if not isinstance(x, float) else round(x, 4)}")
    run(v) if SPEC.exists() else None
