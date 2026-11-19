import os

import pytest

from tracker import registry


def test_registry_rules():
    assert registry.problems() == []


def test_every_material_has_a_comparison_basis():
    for m in registry.materials():
        assert m["benchmark"]["basis"] in registry.BASES, m["id"]


def test_published_entries_cite_a_paper():
    for m in registry.materials():
        if m["evidence"] in ("peer-reviewed", "preprint"):
            assert m["publication"]["url"].startswith("https://"), m["id"]


def test_watch_orgs_have_no_material():
    watch = {o["id"] for o in registry.orgs() if o.get("watch")}
    for m in registry.materials():
        assert not watch & set(m["orgs"]), m["id"]


def test_rules_catch_errors():
    mats = [{"id": "x", "name": "X", "orgs": ["nobody"], "material_class": "alloy", "application": "a",
             "what_is_new": "w", "ai_role": "r", "evidence": "peer-reviewed", "stage": "lab", "verified": "2026-11-19",
             "benchmark": {"basis": "literature", "material": "Y"}}]
    found = registry.problems(mats, [])
    assert any("not in orgs.yaml" in p for p in found)
    assert any("needs publication" in p for p in found)
    assert any("needs a gap" in p for p in found)
    assert any("needs its own source" in p for p in found)


@pytest.mark.skipif(not os.environ.get("LAUNCH_GATE"), reason="launch gate runs with LAUNCH_GATE=1")
def test_launch_gate():
    assert registry.launch_problems() == []


def test_chart_plots_tier1_only():
    """Option A (2026-11-19): aggregator-only funding is listed under the chart, never plotted."""
    fx = [{"id": "a", "type": "company", "funding": {"low": 1, "high": 1, "tier": "tier1"}},
          {"id": "b", "type": "company", "funding": {"low": 1, "high": 1, "tier": "aggregator"}},
          {"id": "c", "type": "lab", "funding": {"low": 1, "high": 1, "tier": "tier1"}},
          {"id": "d", "type": "company"}]
    assert [o["id"] for o in registry.chart_orgs(fx)] == ["a"]
    assert [o["id"] for o in registry.unconfirmed_funding_orgs(fx)] == ["b"]


ORG_KEYS = {"id", "name", "type", "hq", "watch", "note", "funding", "sources", "ticker", "bio", "former_names", "verify"}


def test_org_keys_are_known():
    """Catches flow-YAML entries split by an unquoted comma (a name like "X (a, b)" becomes a stray key)."""
    for o in registry.orgs():
        assert set(o) <= ORG_KEYS, (o["id"], set(o) - ORG_KEYS)
