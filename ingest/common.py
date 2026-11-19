"""Shared helpers: registry access, signal config, source tiers, secrets, raw snapshots, the ledger and the queue.

Ported from physical-ai-tracker (ingest/common.py, ingest/filings.py queue helpers). Differences: organizations come
from reference/orgs.yaml (tracker.registry) and their signal config (domains, news terms, newsroom feed, CIK, OpenAlex
IDs) from reference/signals.yaml, so the hand-curated registry stays free of fetch settings.
"""
import csv
import json
import os
import re
import subprocess
from datetime import date
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

from tracker import registry

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "reference"
RAW = ROOT / "data" / "raw"
SIGNALS = REF / "signals.yaml"
LEDGER = REF / "ledger.csv"
LEDGER_COLUMNS = ["id", "org", "material", "date", "type", "round", "amount_usd", "valuation_usd", "qualifier", "status",
                  "grade", "detail", "sources", "accept", "added"]
QUEUE = ROOT / "data" / "review" / "queue.csv"
QUEUE_COLUMNS = ["qid", "found", "source", "org", "material", "date", "kind", "suggested_type", "title", "url",
                 "parsed", "status"]


@cache
def orgs():
    return {o["id"]: o for o in registry.orgs()}


@cache
def materials():
    return {m["id"]: m for m in registry.materials()}


@cache
def signals():
    """Org id -> signal config. Every key must be an org (tests/test_signals.py)."""
    return yaml.safe_load(SIGNALS.read_text())["signals"]


def news_orgs():
    """Orgs with news terms, merged with their registry entry (name, type, watch)."""
    return {k: dict(orgs()[k], **v) for k, v in signals().items() if v.get("news")}


def load_ledger():
    if not LEDGER.exists():
        return []
    with LEDGER.open(newline="") as f:
        return list(csv.DictReader(f))


def append_ledger(rows):
    new = not LEDGER.exists()
    with LEDGER.open("a", newline="") as f:
        w = csv.DictWriter(f, LEDGER_COLUMNS)
        if new:
            w.writeheader()
        w.writerows(rows)


def next_ledger_id(ledger):
    return f"E{max([int(e['id'][1:]) for e in ledger] or [0]) + 1:04d}"


def read_queue():
    if not QUEUE.exists():
        return []
    with QUEUE.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_queue(rows):
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, QUEUE_COLUMNS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda q: (q["status"] != "open", q["date"], q["org"], q["qid"])))


def names_match(text, phrases):
    """True when a whole-word phrase occurs in text, case-insensitive ("radical ai" is not in "Radical Air Heating")."""
    t = (text or "").lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", t) for p in phrases)


def domain_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def _matches(host, domain):
    return host == domain or host.endswith("." + domain)


@cache
def _tiers():
    t = yaml.safe_load((REF / "source_tiers.yaml").read_text())
    tier1 = [d for group in t["tier1"].values() for d in group]
    evidence = [d for group in t["evidence"].values() for d in group]
    org_domains = [d for s in signals().values() for d in s.get("domains", [])]
    return {"org": org_domains, "tier1": tier1, "evidence": evidence, "tier2": t["tier2"], "secondary": t["secondary"]}


def tier_of(url):
    """'tier1' (org domain, tier-1 list or evidence source), 'tier2', 'secondary', or None for an unclassified domain."""
    host = domain_of(url)
    t = _tiers()
    if any(_matches(host, d) for d in t["org"] + t["tier1"] + t["evidence"]):
        return "tier1"
    for name in ("tier2", "secondary"):
        if any(_matches(host, d) for d in t[name]):
            return name
    return None


def is_evidence(url):
    """A journal, preprint server or publication record: the only sources an evidence grade can rest on."""
    host = domain_of(url)
    return any(_matches(host, d) for d in _tiers()["evidence"])


@cache
def secret(name, keychain_service):
    """Env var first, then macOS Keychain. Never log the return value."""
    if v := os.environ.get(name):
        return v
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", keychain_service, "-w"],
                           capture_output=True, text=True)
    except FileNotFoundError:  # not macOS (CI uses env vars)
        return None
    return r.stdout.strip() or None


def contact():
    """Contact email for SEC, OpenAlex and Crossref polite pools: CONTACT_EMAIL, else Keychain item
    ai-materials-tracker-contact, else the lifesci-tracker-contact item. Kept out of the repo."""
    return (secret("CONTACT_EMAIL", "ai-materials-tracker-contact")
            or secret("LIFESCI_CONTACT_EMAIL", "lifesci-tracker-contact"))


def user_agent():
    """SEC wants "name email"; URLs in the UA get 403."""
    if not (email := contact()):
        raise RuntimeError("SEC needs a contact email: set CONTACT_EMAIL or Keychain item ai-materials-tracker-contact")
    return f"ai-materials-tracker {email}"


def write_snapshot(source, payload):
    """Raw snapshot per run: data/raw/<source>/<YYYY-MM-DD>.json (gitignored)."""
    d = RAW / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{date.today().isoformat()}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    return path
