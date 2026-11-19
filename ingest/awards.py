"""US federal awards per company, from USAspending.gov: contracts (types A-D) and grants (types 02-05), SBIR/STTR included.

Weekly (plan 2026-11-19). Ported from lifesci-tools-tracker ingest/usaspending.py, which searched keywords; this
searches recipients. Companies only: federal awards to universities, national labs and incumbents fund the institution,
not the AI-made material.

Matching: names come from `awards` in reference/signals.yaml, else from `patents` (legal names such as Mitra Future
Technologies serve both), else the org name without its parenthetical:
  query  names sent as recipient_search_text (USAspending matches recipient name and UEI, loosely)
  match  whole-word phrases, case-insensitive; the recipient name must contain one (keeps same-name recipients out)
`awards: false` skips a company; a company with `patents: false` is skipped too unless it has its own `awards` entry.

Output: data/awards/awards.csv, every matched award (a re-fetch replaces the company's rows). New awards go to the
review queue as suggested type award (non-dilutive, never summed with equity rounds; a product purchase is better
recorded as contract), with the USAspending award page as the source. classify.accept never auto-accepts them. The first run queues only organizations with a material; watch-list awards are
recorded but not queued until they are new. The SBIR.gov API is not used: SBIR and STTR awards reach USAspending as
grants or contracts, and `sbir` flags them from the description.

Run: uv run python -m ingest.awards [org-id ...]
     uv run python -m ingest.awards --discover [org-id ...]   (print recipient names per company; writes nothing)
"""
import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import date

import requests

from ingest.common import (QUEUE, ROOT, load_ledger, materials, names_match, orgs, read_queue, signals, write_queue,
                           write_snapshot)

URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
OUT = ROOT / "data" / "awards" / "awards.csv"
GROUPS = {"contract": ["A", "B", "C", "D"], "grant": ["02", "03", "04", "05"]}
FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Awarding Sub Agency", "Description",
          "Start Date", "End Date", "generated_internal_id"]
COLUMNS = ["org", "internal_id", "award_id", "category", "recipient", "agency", "sub_agency", "amount_usd", "start",
           "end", "sbir", "description", "url"]
START = "2007-10-01"  # earliest date the search accepts
PAGE, MAX_PAGES, PACE = 100, 10, 0.5
SBIR = re.compile(r"\b(SBIR|STTR)\b", re.I)


def config(oid):
    """(query names, match phrases), or None to skip."""
    s = signals().get(oid, {})
    if s.get("awards") is False or (s.get("patents") is False and "awards" not in s):
        return None
    p = s.get("awards") or s.get("patents") or {}
    names = p.get("query") or [re.sub(r"\s*\(.*?\)", "", orgs()[oid]["name"])]
    return names, [m.lower() for m in p.get("match") or names]


def companies():
    return [o for o, v in orgs().items() if v["type"] == "company" and config(o)]


def search(name, codes):
    rows, page = [], 1
    while page <= MAX_PAGES:
        body = {"filters": {"recipient_search_text": [name], "award_type_codes": codes,
                            "time_period": [{"start_date": START, "end_date": date.today().isoformat()}]},
                "fields": FIELDS, "limit": PAGE, "page": page, "sort": "Start Date", "order": "desc"}
        for attempt in range(4):
            r = requests.post(URL, json=body, timeout=90)
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()
        d = r.json()
        rows += d["results"]
        time.sleep(PACE)
        if not d.get("page_metadata", {}).get("hasNext"):
            return rows
        page += 1
    print(f"  note: {name!r} stopped at {MAX_PAGES} pages")
    return rows


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def row(oid, a, category):
    desc = clean(a["Description"])
    return {"org": oid, "internal_id": a["generated_internal_id"], "award_id": a["Award ID"] or "", "category": category,
            "recipient": a["Recipient Name"] or "", "agency": a["Awarding Agency"] or "",
            "sub_agency": a["Awarding Sub Agency"] or "", "amount_usd": f"{a['Award Amount']:.0f}" if a["Award Amount"] else "",
            "start": a["Start Date"] or "", "end": a["End Date"] or "", "sbir": "1" if SBIR.search(desc) else "",
            "description": desc[:300], "url": f"https://www.usaspending.gov/award/{a['generated_internal_id']}"}


def fetch(oid):
    names, phrases = config(oid)
    found = {}
    for category, codes in GROUPS.items():
        for n in names:
            for a in search(n, codes):
                if names_match(a["Recipient Name"], phrases):
                    found.setdefault(a["generated_internal_id"], row(oid, a, category))
    return list(found.values())


def discover(oid):
    names, phrases = config(oid)
    seen = Counter()
    for codes in GROUPS.values():
        for n in names:
            seen.update(a["Recipient Name"] or "" for a in search(n, codes))
    kept = {k: v for k, v in seen.items() if names_match(k, phrases)}
    other = [(k, v) for k, v in seen.most_common() if k not in kept][:4]
    print(f"{oid}: {names} -> kept {kept}; other {other}", flush=True)


def money(n):
    n = float(n)
    return f"${n / 1e6:.1f}M" if n >= 1e6 else f"${n / 1e3:.0f}K"


def queue_item(a):
    name = re.sub(r"\s*\(.*?\)", "", orgs()[a["org"]]["name"])
    kind = "SBIR/STTR " + a["category"] if a["sbir"] else a["category"]
    amount = f" {money(a['amount_usd'])}" if a["amount_usd"] else ""
    parsed = {"agency": a["agency"], "sub_agency": a["sub_agency"], "award_id": a["award_id"], "category": a["category"],
              "sbir": bool(a["sbir"]), "end": a["end"], "recipient": a["recipient"], "type": "award",
              "amounts": [int(a["amount_usd"])] if a["amount_usd"] else [],
              "reasons": ["federal award: non-dilutive; contract if it is a product purchase"]}
    return {"qid": f"award:{a['internal_id']}", "found": date.today().isoformat(), "source": "award", "org": a["org"],
            "material": "", "date": a["start"], "kind": a["category"], "suggested_type": "award",
            "title": f"{name}: {a['agency']} {kind}{amount}: {a['description'][:140]}", "url": a["url"],
            "parsed": json.dumps(parsed, sort_keys=True), "status": "open"}


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
        w.writerows(sorted(rows, key=lambda r: (r["org"], r["start"], r["internal_id"])))


def main(ids, discover_only=False):
    targets = ids or companies()
    if discover_only:
        for oid in targets:
            discover(oid)
        return
    first_run = not OUT.exists()
    old = read()
    known_awards = {r["internal_id"] for r in old}
    with_material = {o for m in materials().values() for o in m["orgs"]} if first_run else None
    queue = read_queue()
    queued = {q["qid"] for q in queue}
    cited = " ".join(e["sources"] for e in load_ledger())
    rows = [r for r in old if r["org"] not in targets]
    summary, errors, added = {}, {}, 0
    for oid in targets:
        try:
            got = fetch(oid)
        except (requests.RequestException, KeyError, ValueError) as e:
            errors[oid] = f"{type(e).__name__}: {e}"[:200]
            rows += [r for r in old if r["org"] == oid]
            print(f"{oid}: ERROR {errors[oid]}", flush=True)
            continue
        rows += got
        new = 0
        for a in got:
            if a["internal_id"] in known_awards or f"award:{a['internal_id']}" in queued or a["internal_id"] in cited:
                continue
            if with_material is not None and oid not in with_material:
                continue  # first run: watch-list history is recorded, not queued
            queue.append(queue_item(a))
            queued.add(f"award:{a['internal_id']}")
            new += 1
        added += new
        summary[oid] = {"awards": len(got), "usd": sum(float(a["amount_usd"] or 0) for a in got), "queued": new}
        print(f"{oid}: {len(got)} awards, {new} queued", flush=True)
    write(rows)
    write_queue(queue)
    write_snapshot("awards", {"run": date.today().isoformat(), "orgs": summary, "errors": errors})
    print(f"{len(rows)} awards; {added} queued; {sum(q['status'] == 'open' for q in queue)} open in "
          f"{QUEUE.relative_to(ROOT)}; {len(errors)} errors")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()
    main(a.ids, a.discover)
