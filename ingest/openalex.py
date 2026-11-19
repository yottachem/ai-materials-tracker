"""OpenAlex: new papers by tracked orgs, journal versions of preprint materials, and critiques of tracked papers.

Three checks, all to the review queue (never to a grade directly):
  papers     works by each org's OpenAlex institutions, raw-affiliation phrases or authors (reference/signals.yaml),
             published since --since. Every work is stored in data/papers/works.csv; a work is queued only when its
             title or abstract names an AI/ML method, a material and experimental work (synthesized, fabricated,
             measured, ...): the site's bar is a real material, so computation-only, methods and biology papers are
             stored but not queued. Watch-list orgs' papers are queued as promotion candidates.
  versions   for each preprint material: a journal version of the same work (OpenAlex locations, then a title
             search) is an evidence-change candidate (preprint -> peer-reviewed).
  critiques  works that cite a tracked publication and read as a comment, reply, reanalysis or critique.

Startups mostly have no OpenAlex institution id (checked 2026-11-19), so they match on the raw affiliation text,
checked again client-side with the org's `require` words against same-name collisions.

Key: OPENALEX_API_KEY or Keychain item openalex-api-key. The key travels in the URL, so errors never print the URL.

Usage: uv run python -m ingest.openalex [--since YYYY-MM-DD] [--checks papers,versions,critiques] [org-id ...]
"""
import argparse
import csv
import json
import re
import time
from datetime import date, timedelta

import requests

from ingest.common import ROOT, contact, materials, orgs, read_queue, secret, signals, write_queue, write_snapshot

URL = "https://api.openalex.org/works"
TYPES = "article|review|preprint|conference-paper|letter|editorial|erratum|retraction|book-chapter|dissertation"
SELECT = ("id,doi,title,publication_date,type,primary_location,locations,authorships,abstract_inverted_index,"
          "referenced_works_count")
WORKS = ROOT / "data" / "papers" / "works.csv"
WORK_COLUMNS = ["wid", "org", "date", "type", "venue", "doi", "title", "relevant", "experimental", "found"]

AI = re.compile(r"machine[- ]learn\w*|deep learning|neural network|artificial intelligence|\bAI\b|active learning|"
                r"Bayesian optimi[sz]\w*|generative|autonomous|self-driving|robotic|high-throughput|foundation model|"
                r"language model|\bLLMs?\b|graph neural|reinforcement learning|closed[- ]loop|data-driven", re.I)
MATERIAL = re.compile(r"material|catalys\w*|alloy|electrolyte|polymer|cathode|anode|batter\w*|crystal|perovskite|"
                      r"oxide|concrete|cement|sorbent|\bMOFs?\b|metal[- ]organic|coating|emitter|\bOLED|magnet\w*|"
                      r"ceramic|composite|semiconductor|nanoparticle|thin film", re.I)
BIO = re.compile(r"enzyme|protein|collagen|biomaterial", re.I)
# Lab work, not "validated against experimental data": simulation papers use those words too
EXPERIMENT = re.compile(r"synthesi[sz]ed|synthesis of|as-synthesized|fabricat(?:ed|ion)|experimentally|prototype|"
                        r"\bXRD\b|X-ray diffraction|characteri[sz]ation|coin cells?|pouch cells?|full cells?|"
                        r"tensile|annealed|sintered|printed|self-driving lab\w*|autonomous lab\w*|robotic platform|"
                        r"electrochemical (?:testing|measurements?)|in the lab", re.I)
CRITIQUE = re.compile(r"\b(comment on|reply to|response to|matters arising|reanalysis|re-analysis|critique|"
                      r"rebuttal|correction to|erratum|retraction|expression of concern)\b", re.I)
BOILERPLATE = re.compile(r"materials and methods|supplementary materials?", re.I)
PREPRINT_DOI = re.compile(r"10\.48550/|10\.26434/|10\.21203/|10\.2139/ssrn", re.I)


def get(params, what):
    """GET /works with contact and key; backs off on the burst limit. Errors name `what`, never the URL."""
    params = dict(params)
    url = params.pop("_url", URL)
    if email := contact():
        params["mailto"] = email
    if key := secret("OPENALEX_API_KEY", "openalex-api-key"):
        params["api_key"] = key
    for attempt in range(6):
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f"OpenAlex request failed ({type(e).__name__}) for {what}") from None
        if r.status_code != 429:
            break
        if int(r.headers.get("Retry-After", 0)) > 60:
            raise RuntimeError(f"OpenAlex daily budget spent ({what})")
        time.sleep(2 ** attempt)
    if r.status_code == 404:
        return None
    if not r.ok:
        raise RuntimeError(f"OpenAlex {r.status_code} for {what}")
    return r.json()


def search(filt, what, limit=200):
    out, cursor = [], "*"
    while cursor and len(out) < limit:
        d = get({"filter": filt, "select": SELECT, "per_page": 100, "cursor": cursor}, what)
        out += d["results"]
        cursor = d["meta"].get("next_cursor") if d["results"] else None
        time.sleep(0.1)
    return out


def abstract(w):
    inv = w.get("abstract_inverted_index") or {}
    pos = sorted((i, word) for word, idx in inv.items() for i in idx)
    return " ".join(word for _, word in pos)


def venue(w):
    src = (w.get("primary_location") or {}).get("source") or {}
    return src.get("display_name") or ""


def affiliations(w):
    return [a for au in w.get("authorships") or [] for a in au.get("raw_affiliation_strings") or []]


def affiliation_ok(w, cfg):
    """For affiliation matches: the phrase is really in an affiliation string, with a `require` word if set."""
    phrases = [p.lower() for p in cfg.get("affiliations", [])]
    req = [r.lower() for r in cfg.get("require", [])]
    for a in affiliations(w):
        al = a.lower()
        if any(p in al for p in phrases) and (not req or any(r in al for r in req)):
            return True
    return False


def relevant(w, bio=False):
    text = BOILERPLATE.sub(" ", f"{w.get('title') or ''} {abstract(w)}")
    return bool(AI.search(text) and (MATERIAL.search(text) or (bio and BIO.search(text))))


def org_works(oid, cfg, since):
    """Works for one org since `since`, each tagged with how it matched."""
    base = f"type:{TYPES},from_publication_date:{since}"
    if q := cfg.get("query"):
        base += f",title_and_abstract.search:{q}"
    found = {}
    if ids := cfg.get("institutions"):
        for w in search(f"{base},institutions.id:{'|'.join(ids)}", f"{oid} institutions"):
            found[w["id"]] = w
    if ids := cfg.get("authors"):
        for w in search(f"{base},authorships.author.id:{'|'.join(ids)}", f"{oid} authors"):
            found[w["id"]] = w
    for phrase in cfg.get("affiliations", []):
        for w in search(f'{base},raw_affiliation_strings.search:"{phrase}"', f"{oid} affiliation"):
            if affiliation_ok(w, cfg):
                found[w["id"]] = w
    return list(found.values())


def read_works():
    if not WORKS.exists():
        return []
    with WORKS.open(newline="") as fh:
        return list(csv.DictReader(fh))


def write_works(rows):
    WORKS.parent.mkdir(parents=True, exist_ok=True)
    with WORKS.open("w", newline="") as fh:
        w = csv.DictWriter(fh, WORK_COLUMNS)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["org"], r["wid"]), reverse=True))


def tracked_dois():
    """DOIs already in materials.yaml (publications, corrections, critiques), lowercased."""
    text = (ROOT / "reference" / "materials.yaml").read_text().lower()
    dois = set(re.findall(r"10\.\d{4,9}/[^\s\"',}\]]+", text))
    dois |= {f"10.1038/{x}" for x in re.findall(r"nature\.com/articles/([^\s\"',}\]?#]+)", text)}
    dois |= {f"10.48550/arxiv.{x}" for x in re.findall(r"arxiv[:.](?:org/abs/)?(\d{4}\.\d{4,5})", text)}
    dois |= {f"10.48550/arxiv.{x}" for x in re.findall(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", text)}
    return dois


def qrow(qid, source, org, material, d, kind, stype, title, url, parsed):
    return {"qid": qid, "found": date.today().isoformat(), "source": source, "org": org, "material": material,
            "date": d or "", "kind": kind, "suggested_type": stype, "title": (title or "").strip()[:300],
            "url": url or "", "parsed": json.dumps(parsed, sort_keys=True), "status": "open"}


def doi_url(w):
    return w.get("doi") or (w.get("primary_location") or {}).get("landing_page_url") or w["id"]


def papers(ids, since, queue, known):
    cfg_all, all_orgs = signals(), orgs()
    store = read_works()
    seen = {(r["wid"], r["org"]) for r in store}
    pubs = tracked_dois()
    new_rows, queued, errors, snap = [], 0, {}, {}
    for oid in ids or cfg_all:
        cfg = cfg_all[oid].get("openalex")
        if not cfg:
            continue
        try:
            ws = org_works(oid, cfg, since)
        except RuntimeError as e:
            errors[oid] = str(e)
            print(f"{oid}: ERROR {e}", flush=True)
            continue
        org = all_orgs[oid]
        bio = bool(org.get("bio")) or any(m["material_class"] == "bio" for m in materials().values() if oid in m["orgs"])
        snap[oid] = len(ws)
        n_rel = 0
        for w in ws:
            wid = w["id"].rsplit("/", 1)[-1]
            rel, exp = relevant(w, bio), bool(EXPERIMENT.search(f"{w.get('title') or ''} {abstract(w)}"))
            if (wid, oid) not in seen:
                new_rows.append({"wid": wid, "org": oid, "date": w.get("publication_date") or "", "type": w.get("type"),
                                 "venue": venue(w), "doi": w.get("doi") or "", "title": (w.get("title") or "")[:300],
                                 "relevant": int(rel), "experimental": int(exp), "found": date.today().isoformat()})
                seen.add((wid, oid))
            doi = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
            qid = f"paper:{wid}:{oid}"
            if not (rel and exp) or qid in known or (doi and doi in pubs):
                continue  # computation-only and off-topic works stay in works.csv, out of the queue
            n_rel += 1
            watch = org.get("watch")
            reasons = ["new paper by a tracked org"] + ([f"watch-list org ({watch}): promotion candidate"] if watch else [])
            queue.append(qrow(qid, "paper", oid, "", w.get("publication_date"), w.get("type") or "work",
                              "promotion" if watch else "result", w.get("title"), doi_url(w),
                              {"venue": venue(w), "experimental": exp, "reasons": reasons}))
            known.add(qid)
            queued += 1
        print(f"{oid}: {len(ws)} works since {since}, {n_rel} queued", flush=True)
    write_works(store + new_rows)
    return {"works": snap, "errors": errors, "queued": queued}


def work_by_doi(doi):
    return get({"_url": f"{URL}/doi:{doi}", "select": SELECT}, f"doi {doi}")


def versions(queue, known):
    """Journal versions of preprint materials."""
    out = {}
    for m in materials().values():
        if m["evidence"] != "preprint":
            continue
        pub = m.get("publication") or {}
        pid = pub.get("id", "")
        doi = f"10.48550/arxiv.{pid.split(':', 1)[1]}" if pid.lower().startswith("arxiv:") else pid.removeprefix("doi:")
        cands = []
        w = work_by_doi(doi) if doi else None
        for loc in (w or {}).get("locations") or []:
            src = loc.get("source") or {}
            if src.get("type") == "journal" and not loc.get("version") == "submittedVersion":
                cands.append({"title": (w or {}).get("title"), "url": loc.get("landing_page_url") or "",
                              "venue": src.get("display_name"), "how": "OpenAlex location on the preprint record"})
        title = pub.get("title") or ""
        if len(title.split()) >= 4:
            words = re.sub(r"[^\w\s-]", " ", title).split()[:12]
            res = get({"filter": f"type:article|review,title.search:{' '.join(words)}", "select": SELECT,
                       "per_page": 5}, f"title {m['id']}")
            for r in (res or {}).get("results", []):
                if r.get("doi") and not PREPRINT_DOI.search(r["doi"]):
                    cands.append({"title": r.get("title"), "url": r["doi"], "venue": venue(r),
                                  "how": "title search"})
        out[m["id"]] = cands
        for c in cands:
            qid = f"version:{m['id']}:{c['url']}"
            if c["url"] and qid not in known:
                queue.append(qrow(qid, "paper", m["orgs"][0], m["id"], date.today().isoformat(), "journal version",
                                  "evidence", c["title"], c["url"],
                                  {"venue": c["venue"], "how": c["how"],
                                   "reasons": ["possible journal version of a preprint material: check it is the same "
                                               "work, then preprint -> peer-reviewed"]}))
                known.add(qid)
        time.sleep(0.1)
    return out


def critiques(queue, known, since):
    """Comments, replies, reanalyses and corrections that cite a tracked publication."""
    out = {}
    for m in materials().values():
        pid = (m.get("publication") or {}).get("id", "")
        if not pid.startswith("doi:"):
            continue
        w = work_by_doi(pid.removeprefix("doi:"))
        if not w:
            out[m["id"]] = "not in OpenAlex"
            continue
        wid = w["id"].rsplit("/", 1)[-1]
        hits = search(f"cites:{wid},from_publication_date:{since}", f"citers {m['id']}", limit=1000)
        flagged = [h for h in hits if CRITIQUE.search(h.get("title") or "")
                   or h.get("type") in ("erratum", "retraction", "letter")]
        out[m["id"]] = {"citers": len(hits), "flagged": len(flagged)}
        for h in flagged:
            hid = h["id"].rsplit("/", 1)[-1]
            qid = f"critique:{m['id']}:{hid}"
            if qid in known:
                continue
            queue.append(qrow(qid, "paper", m["orgs"][0], m["id"], h.get("publication_date"), h.get("type") or "work",
                              "correction", h.get("title"), doi_url(h),
                              {"venue": venue(h), "reasons": ["HIGH PRIORITY: cites the material's paper and reads as "
                                                              "a comment, reply, reanalysis or correction"]}))
            known.add(qid)
    return out


def main(ids, since, checks):
    queue = read_queue()
    known = {q["qid"] for q in queue}
    snap = {}
    if "papers" in checks:
        snap["papers"] = papers(ids, since, queue, known)
    if "versions" in checks:
        snap["versions"] = versions(queue, known)
    if "critiques" in checks:
        snap["critiques"] = critiques(queue, known, since)
    write_queue(queue)
    write_snapshot("openalex", snap)
    new = sum(1 for q in queue if q["found"] == date.today().isoformat() and q["source"] == "paper")
    print(f"{new} paper items queued today; {sum(q['status'] == 'open' for q in queue)} open")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=(date.today() - timedelta(days=21)).isoformat())
    ap.add_argument("--checks", default="papers,versions,critiques")
    ap.add_argument("ids", nargs="*")
    a = ap.parse_args()
    main(a.ids, a.since, a.checks.split(","))
