"""Crossref: corrections, retractions and other update notices on tracked DOIs -> review queue (high priority).

For every DOI in a material's publication, Crossref is asked two ways:
  - the work record's `updated-by` list (publisher notices and Retraction Watch entries, which Crossref carries)
  - works whose `update-to` points at the DOI (filter `updates:<doi>`)
An update already cited in materials.yaml (e.g. the A-Lab Author Correction) is skipped. Nothing changes a grade
or dispute status directly; the reviewer edits materials.yaml and records a dispute row in the ledger.

Polite pool: mailto from the contact email. No key.

Usage: uv run python -m ingest.crossref
"""
import json
import time
from datetime import date

import requests

from ingest.common import contact, materials, read_queue, write_queue, write_snapshot
from ingest.openalex import tracked_dois

API = "https://api.crossref.org/works"


def get(url, params=None):
    p = dict(params or {})
    if email := contact():
        p["mailto"] = email
    for attempt in range(5):
        r = requests.get(url, params=p, timeout=60, headers={"User-Agent": "ai-materials-tracker (crossref polite pool)"})
        if r.status_code != 429:
            break
        time.sleep(max(int(r.headers.get("Retry-After", 0) or 0), 2 ** (attempt + 1)))
    time.sleep(1)
    if r.status_code == 404:
        return None
    if not r.ok:  # never echo the URL: it carries the contact email
        raise RuntimeError(f"Crossref {r.status_code}")
    return r.json()["message"]


def parts(d):
    dp = (d or {}).get("date-parts", [[None]])[0]
    return "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(dp) if x) if dp and dp[0] else ""


def updates(doi):
    """[(update_doi, type, date, label, source)] from both directions."""
    out = {}
    rec = get(f"{API}/{doi}")
    for u in (rec or {}).get("updated-by", []) or []:
        out[u["DOI"].lower()] = (u["DOI"].lower(), u.get("type", ""), parts(u.get("updated")), u.get("label", ""),
                                 u.get("source", "publisher"))
    notices = get(API, {"filter": f"updates:{doi}", "rows": 50}) or {}
    for n in notices.get("items", []):
        for u in n.get("update-to", []):
            if u.get("DOI", "").lower() == doi.lower():
                out[n["DOI"].lower()] = (n["DOI"].lower(), u.get("type", ""), parts(u.get("updated")),
                                         u.get("label", ""), "publisher")
    return list(out.values())


def main():
    queue = read_queue()
    known = {q["qid"] for q in queue}
    cited = tracked_dois()
    snap, errors, added = {}, {}, 0
    for m in materials().values():
        pid = (m.get("publication") or {}).get("id", "")
        if not pid.startswith("doi:"):
            continue
        doi = pid.removeprefix("doi:")
        try:
            ups = updates(doi)
        except (requests.RequestException, RuntimeError, KeyError, ValueError) as e:
            errors[m["id"]] = (str(e) if isinstance(e, RuntimeError) else type(e).__name__)[:200]
            continue
        snap[m["id"]] = ups
        for udoi, kind, when, label, source in ups:
            qid = f"crossref:{udoi}"
            if qid in known or udoi in cited:
                continue
            queue.append({"qid": qid, "found": date.today().isoformat(), "source": "crossref", "org": m["orgs"][0],
                          "material": m["id"], "date": when, "kind": kind or "update",
                          "suggested_type": "correction", "title": f"{label or kind} to {doi} ({source})",
                          "url": f"https://doi.org/{udoi}",
                          "parsed": json.dumps({"reasons": ["HIGH PRIORITY: Crossref update notice on the material's "
                                                            "paper"], "source": source}, sort_keys=True),
                          "status": "open"})
            known.add(qid)
            added += 1
        print(f"{m['id']}: {len(ups)} updates", flush=True)
    write_snapshot("crossref", {"updates": snap, "errors": errors})
    write_queue(queue)
    print(f"{added} queued; {len(errors)} errors")


if __name__ == "__main__":
    main()
