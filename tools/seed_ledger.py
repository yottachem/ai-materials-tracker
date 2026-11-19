"""One-off (2026-11-19): seed reference/ledger.csv from the registry.

Rows: each material's starting evidence and stage grade (and dispute status where set), dated from the publication
or the verification date, plus funding events with a tier-1 source found in the screen. accept = seed.
Refuses to run when the ledger already exists.
"""
import sys
from datetime import date

from ingest.common import LEDGER, LEDGER_COLUMNS, append_ledger, materials

FUNDING = [  # (org, date, round, amount_usd, detail, source)
    ("mattiq", "2023-02-07", "Seed", 15_000_000, "Mattiq launches with $15M seed",
     "https://www.businesswire.com/news/home/20230207005510/en/Mattiq-Launches-With-Revolutionary-Approach-to-Developing-Sustainable-Materials"),
    ("copernic-catalysts", "2024-11-12", "Seed Prime", 8_000_000, "Copernic Catalysts closes $8M seed prime led by Breakout Ventures",
     "https://www.businesswire.com/news/home/20241112148260/en/Copernic-Catalysts-Closes-$8M-Seed-Prime-Led-by-Breakout-Ventures-to-Scale-Its-Transformational-Catalyst-for-Sustainable-Ammonia-Production"),
    ("orbital-industries", "2026-05-28", "Series B", 50_000_000, "Orbital Industries raises $50M Series B",
     "https://fortune.com/2026/05/28/exclusive-orbital-industries-raises-50-million-series-b-funding-round-ai-to-discover-exotic-new-materials/"),
]


def main():
    if LEDGER.exists():
        sys.exit("ledger exists; seeding is one-off")
    today = date.today().isoformat()
    rows = []

    def add(**kw):
        row = {k: "" for k in LEDGER_COLUMNS}
        row.update(kw, id=f"E{len(rows) + 1:04d}", accept="seed", added=today, status=kw.get("status", "closed"))
        rows.append(row)

    for m in materials().values():
        pub = m.get("publication") or {}
        src = pub.get("url") or (m.get("sources") or [None])[0] or (m.get("key_property") or {}).get("source")
        when = str(pub.get("date") or m["verified"])
        add(org=m["orgs"][0], material=m["id"], date=when, type="evidence", grade=m["evidence"],
            detail=f"evidence at seed: {m['evidence']}", sources=src)
        add(org=m["orgs"][0], material=m["id"], date=str(m["verified"]), type="stage", grade=m["stage"],
            detail=f"stage at seed: {m['stage']}", sources=(m.get("sources") or [src])[0])
        if d := m.get("dispute"):
            dsrc = d.get("correction") or d.get("critique")
            add(org=m["orgs"][0], material=m["id"], date=str(m["verified"]), type="dispute", grade=d["status"],
                detail=d.get("summary", ""), sources=dsrc if str(dsrc).startswith("https://") else src)
    for org, when, rnd, amt, detail, src in FUNDING:
        add(org=org, date=when, type="funding", round=rnd, amount_usd=str(amt), detail=detail, sources=src)
    append_ledger(rows)
    print(f"{len(rows)} rows -> {LEDGER}")


if __name__ == "__main__":
    main()
