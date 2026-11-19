"""Signal config, ledger, acceptance rules and review helpers. A failure here means a grade could change without
review, or a figure would publish unattributed."""
import json
import re
import shutil
from datetime import date

import pytest

from classify import accept, events, review
from ingest import awards, common, financials, news, openalex, patents
from tracker import registry

SIGNALS = common.signals()
LEDGER = common.load_ledger()
ORGS = common.orgs()
MATS = common.materials()
LEDGER_TYPES = {"funding", "valuation", "listing", "acquisition", "status", "launch", "deployment", "license", "pilot",
                "result", "partnership", "contract", "award", "evidence", "stage", "dispute"}


def test_signals_cover_every_org():
    assert set(SIGNALS) == set(ORGS)


@pytest.mark.parametrize("oid", [k for k, v in SIGNALS.items() if v.get("news")])
def test_news_terms_are_quoted_phrases(oid):
    for t in SIGNALS[oid]["news"]["terms"]:
        assert re.fullmatch(r'("[^"]+"\s*)+', t), t


@pytest.mark.parametrize("oid", list(SIGNALS))
def test_signal_fields(oid):
    s = SIGNALS[oid]
    assert set(s) <= {"domains", "news", "newsroom_feed", "cik", "openalex", "patents", "awards", "verify"}, set(s)
    for key in ("patents", "awards"):
        if key in s and s[key] is not False:
            assert set(s[key]) <= {"query", "match"} and s[key].get("query"), (key, s[key])
            assert ORGS[oid]["type"] == "company", f"{key} are fetched for companies only"
    if "cik" in s:
        assert isinstance(s["cik"], str) and s["cik"].isdigit()
    if feed := s.get("newsroom_feed"):
        assert feed.startswith("https://")
    for d in s.get("domains", []):
        assert "/" not in d and "." in d
    if oa := s.get("openalex"):
        assert set(oa) <= {"institutions", "affiliations", "require", "authors", "query"}
        assert all(re.fullmatch(r"I\d+", i) for i in oa.get("institutions", []))
        assert all(re.fullmatch(r"A\d+", i) for i in oa.get("authors", []))
        if oa.get("institutions") and not oa.get("affiliations"):
            big = ORGS[oid]["type"] in ("academic", "corporate-lab")
            assert not big or oa.get("query"), "a whole big institution needs a query"


def test_ledger_shape():
    assert list(LEDGER[0]) == common.LEDGER_COLUMNS
    assert [r["id"] for r in LEDGER] == [f"E{n:04d}" for n in range(1, len(LEDGER) + 1)], "ids sequential, never reused"


@pytest.mark.parametrize("row", LEDGER, ids=[r["id"] for r in LEDGER])
def test_ledger_row(row):
    assert row["org"] in ORGS
    assert row["material"] == "" or row["material"] in MATS
    assert row["type"] in LEDGER_TYPES
    assert row["status"] in {"closed", "reported", "announced", "superseded"}
    assert row["accept"] in {"seed", "auto", "reviewed"}
    assert row["date"] == "" or re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", row["date"])
    assert row["sources"] and all(s.startswith("https://") for s in row["sources"].split())
    for k in ("amount_usd", "valuation_usd"):
        assert row[k] == "" or row[k].isdigit()
    date.fromisoformat(row["added"])
    if row["type"] in ("evidence", "stage", "dispute"):
        assert row["material"] and row["grade"], "grade rows name the material and the new grade"
        assert row["accept"] != "auto", "grades never change automatically"


def test_grades_match_the_ledger():
    """The latest evidence, stage and dispute row per material is what materials.yaml says."""
    latest = {}
    for r in LEDGER:
        if r["type"] in ("evidence", "stage", "dispute"):
            latest[(r["material"], r["type"])] = r["grade"]
    for mid, m in MATS.items():
        assert latest.get((mid, "evidence")) == m["evidence"], mid
        assert latest.get((mid, "stage")) == m["stage"], mid
        assert latest.get((mid, "dispute"), "none") in ((m.get("dispute") or {}).get("status", "none"),), mid


def test_tiers():
    assert common.tier_of("https://www.businesswire.com/news/x") == "tier1"
    assert common.tier_of("https://www.lila.ai/news") == "tier1"          # org domain from signals.yaml
    assert common.tier_of("https://doi.org/10.1038/x") == "tier1"         # evidence source
    assert common.tier_of("https://cen.acs.org/business/x") == "tier2"
    assert common.is_evidence("https://arxiv.org/abs/2609.30133")
    assert not common.is_evidence("https://cen.acs.org/business/x")


ORG_FIXTURE = {"acme": {"name": "Acme", "news": {"terms": ['"Acme Materials"']}},
               "watcher": {"name": "Watcher", "watch": "no-result", "news": {"terms": ['"Watcher Labs"']}}}


def art(title, org="acme", tier="tier1", url="https://www.businesswire.com/x"):
    return {"title": title, "org": org, "tier": tier, "url": url, "date": "2026-11-19"}


def test_funding_auto_accepts_from_tier1():
    d, row = accept.decide(art("Acme Materials raises $20M Series A"), ORG_FIXTURE, [])
    assert d == "auto" and row["amount_usd"] == "20000000" and row["round"] == "Series A"


def test_stage_news_never_auto_accepts():
    for t in ("Acme Materials launches AI-designed alloy", "Acme Materials begins shipping new catalyst",
              "Acme Materials licenses electrolyte to cell maker", "Acme Materials paper published in Nature"):
        d, p = accept.decide(art(t), ORG_FIXTURE, [])
        assert d == "queue", t
    d, p = accept.decide(art("Study disputes Acme Materials claims"), ORG_FIXTURE, [])
    assert d == "queue" and p["type"] == "correction" and "HIGH PRIORITY" in p["reasons"][0]


def test_watch_org_news_is_a_promotion_candidate():
    d, p = accept.decide(art("Watcher Labs unveils first AI-discovered magnet", org="watcher"), ORG_FIXTURE, [])
    assert d == "queue" and any("promotion candidate" in r for r in p["reasons"])


def test_non_usd_funding_goes_to_review():
    d, p = accept.decide(art("Acme Materials raises €11M seed round"), ORG_FIXTURE, [])
    assert d == "queue" and "not in US dollars" in p["reasons"]


def test_events_money():
    assert events.money("X raises $1.5 billion") == [1_500_000_000]
    assert events.round_of("Copernic closes $8M seed prime") == "Seed Prime"


def test_openalex_relevance():
    w = {"title": "Machine learning discovery of a durable acid OER catalyst", "abstract_inverted_index": None}
    assert openalex.relevant(w)
    assert not openalex.relevant({"title": "Learning gene co-regulation across cells", "abstract_inverted_index": None})
    assert not openalex.relevant({"title": "A new crystal structure of protein FeSII", "abstract_inverted_index": None})
    assert openalex.relevant({"title": "Generative design of collagen-like proteins", "abstract_inverted_index": None},
                             bio=True)


def test_lab_work_wording():
    assert openalex.EXPERIMENT.search("catalysts were synthesized and tested in a self-driving lab")
    assert not openalex.EXPERIMENT.search("force field validated against experimental data")
    assert not openalex.EXPERIMENT.search("We demonstrate a generative model for electrolytes")


def test_affiliation_require():
    cfg = {"affiliations": ["Cambium"], "require": ["El Segundo"]}
    w = {"authorships": [{"raw_affiliation_strings": ["Cambium, El Segundo, CA, USA"]}]}
    assert openalex.affiliation_ok(w, cfg)
    w2 = {"authorships": [{"raw_affiliation_strings": ["Inria, project-team Cambium, Paris"]}]}
    assert not openalex.affiliation_ok(w2, cfg)


def test_set_line_keeps_comments(tmp_path, monkeypatch):
    shutil.copytree(common.REF, tmp_path / "reference")
    monkeypatch.setattr(review, "REF", tmp_path / "reference")
    review.set_line("lila-inmnpdox", "stage", "customer-tested")
    text = (tmp_path / "reference" / "materials.yaml").read_text()
    block = text[text.index("- id: lila-inmnpdox"):text.index("- id: atinary-cuzncezro2")]
    assert "  stage: customer-tested" in block
    assert text.count("\n  stage: ") == (common.REF / "materials.yaml").read_text().count("\n  stage: ")


def test_queue_columns():
    q = common.read_queue()
    if q:
        assert list(q[0]) == common.QUEUE_COLUMNS
        for r in q:
            json.loads(r["parsed"] or "{}")
            assert r["status"] in {"open", "accepted", "rejected", "dup", "auto"}


@pytest.mark.parametrize("oid, title, keep", [
    # Name collisions found in the first review (2026-11-19)
    ("google-deepmind", "GNOME 48 Linux desktop launches with performance boosts", False),
    ("google-deepmind", "DeepMind's GNoME finds 2.2 million new crystals", True),
    ("argonne", "Polybot receives validation order for AI-supported agriculture", False),
    ("argonne", "Argonne's Polybot makes high-conductivity polymer films", True),
    ("polaron", "Groundbreaking study reveals small polaron effect in 2D perovskites", False),
    ("polaron", "Polaron raises $8M for AI materials design", True),
])
def test_news_collisions(oid, title, keep):
    assert news.relevant(common.news_orgs()[oid], title) is keep


def test_org_level_accept_drops_material():
    assert set(events.LEDGER) == {"funding", "valuation", "listing", "acquisition", "status"}
    assert review.ORG_LEVEL is events.LEDGER


# ---------------------------------------------------------------- Phase 3: patents, federal awards, financials

def _patent(applicant, assignees=(), pub="US20260012345A1", grant=None, cpc=("H01M 10/0567",)):
    return {"applicationNumberText": "18000001",
            "applicationMetaData": {"firstApplicantName": applicant, "earliestPublicationNumber": pub, "patentNumber": grant,
                                    "cpcClassificationBag": list(cpc), "filingDate": "2025-01-02"},
            "assignmentBag": [{"assigneeBag": [{"assigneeNameText": a} for a in assignees]}]}


def test_patent_config_defaults_and_overrides():
    assert patents.config("kyulux") == (["Kyulux"], ["kyulux"])
    assert patents.config("cambium") == (["Cambium Biomaterials"], ["cambium biomaterials"])
    names, phrases = patents.config("chemix")
    assert names == ["Chemix Inc"] and phrases == ["chemix inc"]
    assert "polaron" not in patents.companies() and "dp-technology" not in patents.companies()
    assert all(ORGS[o]["type"] == "company" for o in patents.companies())


@pytest.mark.parametrize("rec,how", [
    (_patent("KYULUX, INC."), "applicant"),
    (_patent("Kyushu University", ["KYULUX NORTH AMERICA, INC."]), "assignee"),
    (_patent("USHIO CHEMIX CORP"), None),
])
def test_patent_matching(rec, how):
    phrases = ["kyulux"] if how else ["chemix inc"]
    assert not common.names_match("Radical Air Heating LLC", ["radical ai"])
    assert common.names_match("SCHRODINGER, INC.", ["schrodinger, inc"])
    assert patents.matches(rec, phrases) == how


def test_patent_public_and_scope():
    assert patents.public(_patent("X")) and patents.public(_patent("X", pub=None, grant="12000000"))
    assert not patents.public(_patent("X", pub=None))
    assert patents.in_scope(["H01M10/0567"]) and patents.in_scope(["C08L67/04"])
    assert not patents.in_scope(["G06N20/00"])
    r = patents.row("kyulux", _patent("KYULUX, INC.", cpc=("G06N 20/00", "C09K 11/06")), "applicant")
    assert r["in_scope"] == "1" and r["cpc"] == "G06N20/00 C09K11/06" and set(r) == set(patents.COLUMNS)


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self.ok, self._body = status, status < 400, body or {}

    def json(self):
        return self._body


def test_patent_search_pages_and_shrinks_on_413(monkeypatch):
    calls = []
    pages = iter([_Resp(413), _Resp(200, {"count": 3, "patentFileWrapperDataBag": [1, 2]}),
                  _Resp(200, {"count": 3, "patentFileWrapperDataBag": [3]})])

    def get(url, headers, params, timeout):
        calls.append(params["limit"])
        return next(pages)
    monkeypatch.setattr(patents.requests, "get", get)
    monkeypatch.setattr(patents, "headers", lambda: {})
    monkeypatch.setattr(patents, "PACE", 0)
    assert patents.search("q") == [1, 2, 3]
    assert calls == [100, 25, 25]


def test_patent_search_404_is_empty_and_other_errors_raise(monkeypatch):
    monkeypatch.setattr(patents, "headers", lambda: {})
    monkeypatch.setattr(patents, "PACE", 0)
    monkeypatch.setattr(patents.requests, "get", lambda *a, **k: _Resp(404))
    assert patents.search("q") == []
    monkeypatch.setattr(patents.requests, "get", lambda *a, **k: _Resp(500))
    with pytest.raises(RuntimeError, match="USPTO ODP 500"):
        patents.search("q")


def test_award_config():
    assert awards.config("mitra-chem") == (["Mitra Future Technologies"], ["mitra future technologies"])
    assert awards.config("schrodinger") is None          # awards: false
    assert awards.config("polaron") is None              # patents: false and no awards entry
    assert awards.config("radical-ai") == (["Radical AI"], ["radical ai"])
    assert "schrodinger" not in awards.companies()


def _award(**kw):
    a = {"generated_internal_id": "CONT_AWD_X_9700", "Award ID": "X", "Recipient Name": "RADICAL AI, INC",
         "Award Amount": 1197902.0, "Awarding Agency": "Department of Defense", "Awarding Sub Agency": "Department of the Air Force",
         "Description": "SBIR PHASE I:  AUTONOMOUS\nEXPERIMENTATION", "Start Date": "2025-05-23", "End Date": "2026-05-23"}
    return a | kw


def test_award_row_and_queue_item():
    r = awards.row("radical-ai", _award(), "contract")
    assert set(r) == set(awards.COLUMNS) and r["sbir"] == "1" and r["amount_usd"] == "1197902"
    assert r["description"] == "SBIR PHASE I: AUTONOMOUS EXPERIMENTATION"
    assert r["url"] == "https://www.usaspending.gov/award/CONT_AWD_X_9700"
    q = awards.queue_item(r)
    assert set(q) == set(common.QUEUE_COLUMNS) and q["qid"] == "award:CONT_AWD_X_9700"
    assert q["suggested_type"] == "award" and q["material"] == "" and q["status"] == "open"
    assert "SBIR/STTR contract $1.2M" in q["title"]
    assert json.loads(q["parsed"])["amounts"] == [1197902]
    assert common.tier_of(q["url"]) == "tier1"


def test_award_fetch_keeps_only_matching_recipients(monkeypatch):
    monkeypatch.setattr(awards, "search", lambda name, codes: [_award(), _award(**{
        "generated_internal_id": "Y", "Recipient Name": "RADICAL AIR HEATING LLC"})])
    got = awards.fetch("radical-ai")
    assert [r["internal_id"] for r in got] == ["CONT_AWD_X_9700"] and got[0]["category"] == "contract"


def test_awards_first_run_queues_material_orgs_only(tmp_path, monkeypatch):
    monkeypatch.setattr(awards, "OUT", tmp_path / "awards.csv")
    monkeypatch.setattr(awards, "write_snapshot", lambda *a: None)
    store = {"queue": []}
    monkeypatch.setattr(awards, "read_queue", lambda: list(store["queue"]))
    monkeypatch.setattr(awards, "write_queue", lambda rows: store.update(queue=rows))
    monkeypatch.setattr(awards, "fetch", lambda oid: [awards.row(oid, _award(generated_internal_id=f"A-{oid}"), "grant")])
    awards.main(["radical-ai", "questek"])
    assert [q["org"] for q in store["queue"]] == ["radical-ai"]   # questek is watch list: recorded, not queued
    assert {r["org"] for r in awards.read()} == {"radical-ai", "questek"}
    monkeypatch.setattr(awards, "fetch", lambda oid: [awards.row(oid, _award(generated_internal_id=f"B-{oid}"), "grant")])
    awards.main(["questek"])                                    # later runs queue new awards for every company
    assert [q["qid"] for q in store["queue"]] == ["award:A-radical-ai", "award:B-questek"]


def test_financials_scope():
    c = financials.load_companies()
    assert set(c) == {"ses-ai", "schrodinger"}
    assert c["ses-ai"]["cik"] == 1819142 and all(financials.in_scope(v) for v in c.values())
    assert financials.shell_cutoff({"name": "SES AI Corp", "formerNames": [
        {"name": "Ivanhoe Capital Acquisition Corp.", "to": "2022-02-04T00:00:00.000Z"}]}) == "2022-02-04"
    assert financials.shell_cutoff({"name": "Ivanhoe Capital Acquisition Corp."}) is None
