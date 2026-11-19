"""Review-queue actions for the /review-events skill. Every decision is written by this script, never by hand.

  uv run python -m classify.review list [--org ID] [--source news|sec|paper|crossref] [--limit N]
  uv run python -m classify.review accept QID --type funding [--date D] [--round R] [--amount USD] [--valuation USD]
                                          [--qualifier approx|at_least|up_to] [--status closed|reported|announced]
                                          [--material ID] [--detail TEXT] [--source URL ...] [--org ID] [--group]
  uv run python -m classify.review grade --material ID --field evidence|stage|dispute --to VALUE --source URL
                                          [--qid QID] [--date D] [--detail TEXT]
  uv run python -m classify.review reject QID --reason TEXT [--group]
  uv run python -m classify.review dup QID EXXXX [--group]

list shows open items, high-priority (corrections, critiques) first, then grouped news by org.
accept appends a ledger event (funding, launch, license, ...) citing the item's URL plus any --source URLs.
grade is the only way a material's evidence, stage or dispute status changes: it appends a ledger row (type =
the field, grade = the new value) and, for evidence and stage, rewrites that one line of reference/materials.yaml.
A dispute change also needs the material's `dispute` block edited by hand (tests check they agree). With --qid the
queue item closes as accepted. Evidence can only rest on a journal, preprint server or publication record.
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date

from classify.events import LEDGER as ORG_LEVEL
from classify.group import group_of, groups
from ingest.common import (LEDGER_COLUMNS, REF, append_ledger, is_evidence, load_ledger, materials, next_ledger_id, orgs,
                           read_queue, tier_of, write_queue)
from tracker.registry import DISPUTE, EVIDENCE, STAGES

TYPES = ["funding", "valuation", "listing", "acquisition", "status", "launch", "deployment", "license", "pilot",
         "result", "partnership", "contract", "award"]
GRADES = {"evidence": EVIDENCE, "stage": STAGES, "dispute": (*DISPUTE, "none")}


def find(queue, qid):
    for q in queue:
        if q["qid"] == qid:
            if q["status"] != "open":
                sys.exit(f"{qid} is already {q['status']}")
            return q
    sys.exit(f"{qid} not in queue")


def high(q):
    return "HIGH PRIORITY" in q["parsed"]


def show(q):
    p = json.loads(q["parsed"] or "{}")
    why = "; ".join(p.get("reasons", [])) or p.get("items", "")
    mat = f" [{q['material']}]" if q["material"] else ""
    return (f"- {q['qid']} | {q['date']} | {q['source']}:{q['kind']} | {q['suggested_type']}{mat} | {q['title'][:110]}\n"
            f"  {q['url'] or '(no link)'}" + (f"\n  why: {why}" if why else ""))


def cmd_list(a):
    rows = [q for q in read_queue() if q["status"] == "open" and (not a.org or q["org"] == a.org)
            and (not a.source or q["source"] == a.source)]
    top = [q for q in rows if high(q)]
    if top:
        print(f"## High priority ({len(top)})")
        for q in sorted(top, key=lambda q: q["date"], reverse=True):
            print(show(q))
    rest = [q for q in rows if not high(q)]
    gs = groups(rest)
    by = defaultdict(list)
    for g in gs:
        by[g[0]["org"]].append(g)
    shown = 0
    for oid in sorted(by, key=lambda k: -sum(len(g) for g in by[k])):
        print(f"\n## {oid} ({sum(len(g) for g in by[oid])} open, {len(by[oid])} groups)")
        for g in sorted(by[oid], key=lambda g: max(q["date"] for q in g), reverse=True):
            if shown >= a.limit:
                print(f"\n... {len(gs) - shown} more groups (raise --limit)")
                return
            size = f" [group of {len(g)}]" if len(g) > 1 else ""
            print(show(g[0]) + size)
            for r in g[1:a.members + 1]:
                print(f"    + {r['qid']} | {r['date']} | {r['title'][:90]}")
            shown += 1
    print(f"\n{len(rows)} open ({len(top)} high priority) in {len(gs) + len(top)} groups")


def close_row(q, status, note):
    q.update(status=status, parsed=json.dumps(dict(json.loads(q["parsed"] or "{}"), **note), sort_keys=True))


def close_group(a, status, note):
    members = [r for r in a.members_of if r["qid"] != a.qid]
    for r in members:
        close_row(r, status, note)
    return len(members)


def check_sources(sources):
    unknown = [s for s in sources if tier_of(s) is None]
    if unknown:
        print(f"note: unclassified source domain(s), add to reference/source_tiers.yaml: {unknown}")


def cmd_accept(a):
    queue = read_queue()
    q = find(queue, a.qid)
    oid = a.org or q["org"]
    if oid not in orgs():
        sys.exit(f"{oid} not in reference/orgs.yaml")
    # Org-level events (funding, valuation, listing, acquisition, status) carry no material unless one is named.
    mat = a.material if a.material is not None else ("" if a.type in ORG_LEVEL else q["material"])
    if mat and mat not in materials():
        sys.exit(f"{mat} not in reference/materials.yaml")
    a.members_of = (group_of(queue, a.qid) or []) if a.group else []
    sources = [s for s in [q["url"], *a.source] if s]
    if not sources:
        sys.exit("no source URL: pass --source")
    check_sources(sources)
    ledger = load_ledger()
    row = {k: "" for k in LEDGER_COLUMNS}
    row.update(id=next_ledger_id(ledger), org=oid, material=mat, date=a.date or q["date"], type=a.type,
               round=a.round or "", amount_usd=a.amount or "", valuation_usd=a.valuation or "",
               qualifier=a.qualifier or "", status=a.status, detail=a.detail or q["title"], sources=" ".join(sources),
               accept="reviewed", added=date.today().isoformat())
    append_ledger([row])
    close_row(q, "accepted", {"ledger": row["id"]})
    rest = close_group(a, "dup", {"duplicate_of": row["id"]}) if a.group else 0
    write_queue(queue)
    print(f"{a.qid} -> {row['id']}: {row['type']} {row['status']} {row['amount_usd']}"
          + (f"; {rest} group members -> dup {row['id']}" if rest else ""))
    if a.type in ("launch", "deployment", "license", "pilot", "result"):
        print("note: an event is not a grade change. If it moves the material's stage or evidence, run `grade` too.")


def set_line(mid, field, value):
    """Rewrite `  <field>: ...` inside material `mid`'s block, keeping comments and layout."""
    path = REF / "materials.yaml"
    text = path.read_text()
    start = text.index(f"\n- id: {mid}\n")
    end = text.find("\n- id: ", start + 1)
    end = len(text) if end == -1 else end
    block = text[start:end]
    new, n = re.subn(rf"(?m)^  {field}: [^\n#]*?(\s*#.*)?$", lambda m: f"  {field}: {value}{m.group(1) or ''}", block,
                     count=1)
    if n != 1:
        sys.exit(f"{mid}: no `{field}:` line to rewrite")
    path.write_text(text[:start] + new + text[end:])


def cmd_grade(a):
    m = materials().get(a.material) or sys.exit(f"{a.material} not in reference/materials.yaml")
    if a.to not in GRADES[a.field]:
        sys.exit(f"--to must be one of {GRADES[a.field]}")
    current = (m.get("dispute") or {}).get("status", "none") if a.field == "dispute" else m[a.field]
    if current == a.to:
        sys.exit(f"{a.material} {a.field} is already {a.to}")
    if a.field == "evidence" and a.to in ("peer-reviewed", "preprint") and not any(is_evidence(s) for s in a.source):
        sys.exit("evidence peer-reviewed or preprint needs a journal, preprint server or publication record source")
    queue = read_queue()
    q = find(queue, a.qid) if a.qid else None
    sources = [s for s in [*(a.source or []), q["url"] if q else ""] if s]
    if not sources:
        sys.exit("no source URL: pass --source")
    check_sources(sources)
    ledger = load_ledger()
    row = {k: "" for k in LEDGER_COLUMNS}
    row.update(id=next_ledger_id(ledger), org=m["orgs"][0], material=a.material,
               date=a.date or (q["date"] if q else date.today().isoformat()), type=a.field, grade=a.to,
               status="closed", detail=a.detail or f"{a.field}: {current} -> {a.to}", sources=" ".join(dict.fromkeys(sources)),
               accept="reviewed", added=date.today().isoformat())
    append_ledger([row])
    if a.field in ("evidence", "stage"):
        set_line(a.material, a.field, a.to)
    if q:
        close_row(q, "accepted", {"ledger": row["id"]})
        write_queue(queue)
    print(f"{a.material}: {a.field} {current} -> {a.to} ({row['id']})")
    if a.field == "dispute":
        print("now edit the material's dispute block in reference/materials.yaml to match (status, critique or "
              "correction URL, summary); tests fail until it agrees")


def cmd_close(a, status, note):
    queue = read_queue()
    q = find(queue, a.qid)
    a.members_of = (group_of(queue, a.qid) or []) if a.group else []
    close_row(q, status, note)
    rest = close_group(a, status, note) if a.group else 0
    write_queue(queue)
    print(f"{a.qid} -> {status}" + (f"; {rest} group members -> {status}" if rest else ""))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--org")
    p.add_argument("--source", choices=["news", "sec", "paper", "crossref"])
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--members", type=int, default=3)
    p = sub.add_parser("accept")
    p.add_argument("qid")
    p.add_argument("--type", required=True, choices=TYPES)
    p.add_argument("--date")
    p.add_argument("--round")
    p.add_argument("--amount")
    p.add_argument("--valuation")
    p.add_argument("--qualifier", choices=["approx", "at_least", "up_to"])
    p.add_argument("--status", default="closed", choices=["closed", "reported", "announced"])
    p.add_argument("--material")
    p.add_argument("--detail")
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--org", help="record under this org instead of the matched one")
    p.add_argument("--group", action="store_true")
    p = sub.add_parser("grade")
    p.add_argument("--material", required=True)
    p.add_argument("--field", required=True, choices=list(GRADES))
    p.add_argument("--to", required=True)
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--qid")
    p.add_argument("--date")
    p.add_argument("--detail")
    p = sub.add_parser("reject")
    p.add_argument("qid")
    p.add_argument("--reason", required=True)
    p.add_argument("--group", action="store_true")
    p = sub.add_parser("dup")
    p.add_argument("qid")
    p.add_argument("eid")
    p.add_argument("--group", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list(a)
    elif a.cmd == "accept":
        cmd_accept(a)
    elif a.cmd == "grade":
        cmd_grade(a)
    elif a.cmd == "reject":
        cmd_close(a, "rejected", {"reason": a.reason})
    else:
        if a.eid not in {e["id"] for e in load_ledger()}:
            sys.exit(f"{a.eid} not in ledger")
        cmd_close(a, "dup", {"duplicate_of": a.eid})


if __name__ == "__main__":
    main()
