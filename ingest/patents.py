"""US patent applications and grants per company, from the USPTO Open Data Portal (ODP) application search.

Quarterly (plan 2026-11-19). The live ODP search is used instead of the PatentsView bulk files: the bulk files stop at
2025-12-31 and need a ~6 GB download, while the search returns current filings for one applicant in a few calls.

Scope: companies only (type company). Patents of corporate labs, universities and incumbents describe the parent
institution, not the AI-made material, so they are not fetched.

Matching: each company has `patents` in reference/signals.yaml:
  query  names sent to ODP as quoted phrases, against the first-applicant field and the assignee field
         (default: the org name without its parenthetical). ODP matches the words in order ("Kyulux" finds "KYULUX, INC.").
  match  whole-word phrases, case-insensitive; a record counts only if its first applicant or an assignee contains one
         (default: the query names). Same-name companies are kept out here.
`patents: false` skips a company (no US filings expected, or a name too generic to search).
Assignee matching catches reassignments (Stoicheia filings now owned by Mattiq).

Only public records are kept: a pre-grant publication or a grant. Unpublished filings returned by the API are dropped.
`in_scope` marks records with a CPC class in the materials scope (MATERIAL_CPC); every record is kept so org pages can
show total activity. Patents never change a grade: they are data for the organization pages, not queue candidates.

Key: USPTO_ODP_API_KEY or Keychain item uspto-odp-api-key (X-API-Key header). Never logged.

Run: uv run python -m ingest.patents [org-id ...]        (all companies, or the ones named)
     uv run python -m ingest.patents --if-due             (skip unless the last full run is 85+ days old)
     uv run python -m ingest.patents --discover           (print applicant names per company; writes nothing)
"""
import argparse
import csv
import re
import time
from collections import Counter
from datetime import date

import requests

from ingest.common import ROOT, names_match, orgs, secret, signals, write_snapshot

API = "https://api.uspto.gov/api/v1/patent/applications/search"
OUT = ROOT / "data" / "patents" / "patents.csv"
STAMP = ROOT / "data" / "patents" / "last_full_run.txt"
COLUMNS = ["org", "application", "filing_date", "publication", "publication_date", "patent", "grant_date", "title",
           "status", "applicant", "assignees", "cpc", "in_scope", "matched"]
PAGE, MAX_RECORDS, PACE = 100, 1000, 1.0
DUE_DAYS = 85

# Materials scope: catalysts and sorbents, inorganic compounds, cement and ceramics, polymers, alloys and metal powders,
# batteries and electrolysis, coatings, luminescent materials and OLED, and the bio-derived classes (enzymes, proteins).
MATERIAL_CPC = ("B01J", "B01D53", "C01", "C04B", "C08", "C09D", "C09K", "C22C", "C22F", "B22F", "C25B", "H01M",
                "H10K", "C07D", "C07F", "C12N", "C12P", "C07K")


def headers():
    key = secret("USPTO_ODP_API_KEY", "uspto-odp-api-key")
    if not key:
        raise RuntimeError("USPTO key missing: set USPTO_ODP_API_KEY or Keychain item uspto-odp-api-key")
    return {"X-API-Key": key}


def config(oid):
    """(query names, match phrases) for a company."""
    p = signals().get(oid, {}).get("patents") or {}
    names = p.get("query") or [re.sub(r"\s*\(.*?\)", "", orgs()[oid]["name"])]
    return names, [m.lower() for m in p.get("match") or names]


def queries(names):
    for field in ("applicationMetaData.firstApplicantName", "assignmentBag.assigneeBag.assigneeNameText"):
        for n in names:
            yield f'{field}:"{n}"'


def companies():
    return [o for o, v in orgs().items() if v["type"] == "company" and signals().get(o, {}).get("patents") is not False]


def search(q):
    """All records for one query, paged. ODP answers 404 when nothing matches and 413 when a page's response is too
    large (records with long assignment histories); the page size then halves and the same offset is retried."""
    out, limit = [], PAGE
    while len(out) < MAX_RECORDS:
        for attempt in range(4):
            r = requests.get(API, headers=headers(), params={"q": q, "limit": limit, "offset": len(out)}, timeout=60)
            if r.status_code != 429:
                break
            time.sleep(5 * (attempt + 1))
        time.sleep(PACE)
        if r.status_code == 404:
            return out
        if r.status_code == 413 and limit > 1:
            limit = max(1, limit // 4)
            continue
        if not r.ok:
            raise RuntimeError(f"USPTO ODP {r.status_code}")
        d = r.json()
        bag = d.get("patentFileWrapperDataBag", [])
        out += bag
        if not bag or len(out) >= d.get("count", 0):
            return out
    print(f"  note: {q!r} stopped at {MAX_RECORDS} records")
    return out


def assignees(rec):
    return sorted({a.get("assigneeNameText", "") for x in rec.get("assignmentBag") or [] for a in x.get("assigneeBag") or []}
                  - {""})


def cpc_groups(meta):
    return [re.sub(r"\s+", "", c) for c in meta.get("cpcClassificationBag") or []]


def in_scope(groups):
    return any(g.startswith(MATERIAL_CPC) for g in groups)


def row(oid, rec, how):
    m = rec["applicationMetaData"]
    groups = cpc_groups(m)
    return {"org": oid, "application": rec["applicationNumberText"], "filing_date": m.get("filingDate", ""),
            "publication": m.get("earliestPublicationNumber", ""), "publication_date": m.get("earliestPublicationDate", ""),
            "patent": m.get("patentNumber", ""), "grant_date": m.get("grantDate", ""), "title": m.get("inventionTitle", ""),
            "status": m.get("applicationStatusDescriptionText", ""), "applicant": m.get("firstApplicantName", ""),
            "assignees": "; ".join(assignees(rec)), "cpc": " ".join(groups[:8]), "in_scope": "1" if in_scope(groups) else "",
            "matched": how}


def public(rec):
    m = rec["applicationMetaData"]
    return bool(m.get("earliestPublicationNumber") or m.get("patentNumber"))


def matches(rec, phrases):
    """'applicant', 'assignee', or None."""
    if names_match(rec["applicationMetaData"].get("firstApplicantName"), phrases):
        return "applicant"
    if any(names_match(a, phrases) for a in assignees(rec)):
        return "assignee"
    return None


def fetch(oid):
    names, phrases = config(oid)
    found = {}
    for q in queries(names):
        for rec in search(q):
            how = matches(rec, phrases)
            if how and public(rec):
                found.setdefault(rec["applicationNumberText"], row(oid, rec, how))
    return list(found.values())


def discover(oid):
    qnames, phrases = config(oid)
    names, public_n = Counter(), 0
    for q in queries(qnames):
        for rec in search(q):
            names[rec["applicationMetaData"].get("firstApplicantName", "")] += 1
            names.update(f"[assignee] {a}" for a in assignees(rec))
    kept = {n: c for n, c in names.items() if names_match(n.removeprefix("[assignee] "), phrases)}
    other = [(n, c) for n, c in names.most_common() if n not in kept][:4]
    print(f"{oid}: {qnames} -> kept {kept}; other {other}", flush=True)


def read():
    if not OUT.exists():
        return []
    with OUT.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, COLUMNS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["org"], r["filing_date"], r["application"])))


def due():
    if not STAMP.exists():
        return True
    return (date.today() - date.fromisoformat(STAMP.read_text().strip())).days >= DUE_DAYS


def main(ids, if_due=False, discover_only=False):
    targets = ids or companies()
    if discover_only:
        for oid in targets:
            discover(oid)
        return
    if if_due and not due():
        print(f"patents: last full run {STAMP.read_text().strip()}, not due")
        return
    rows = [r for r in read() if r["org"] not in targets]  # re-fetch replaces each target company's rows
    summary, errors = {}, {}
    for oid in targets:
        try:
            got = fetch(oid)
        except (requests.RequestException, RuntimeError) as e:
            errors[oid] = f"{type(e).__name__}: {e}"[:200]
            rows += [r for r in read() if r["org"] == oid]  # keep the last good rows
            print(f"{oid}: ERROR {errors[oid]}")
            continue
        rows += got
        summary[oid] = {"records": len(got), "granted": sum(bool(r["patent"]) for r in got),
                        "in_scope": sum(bool(r["in_scope"]) for r in got)}
        print(f"{oid}: {len(got)} public records ({summary[oid]['granted']} granted, {summary[oid]['in_scope']} in materials scope)")
    write(rows)
    if not ids and not errors:
        STAMP.write_text(date.today().isoformat() + "\n")
    write_snapshot("patents", {"run": date.today().isoformat(), "orgs": summary, "errors": errors})
    print(f"{len(rows)} records; {len(errors)} errors")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--if-due", action="store_true")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()
    main(a.ids, a.if_due, a.discover)
