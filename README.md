# AI-Made Materials: Proof Tracker

Which materials made with AI/ML are real, is there published proof, and are they better than the existing best? Each entry is a material: what is new about it, its key property against the current best-in-class (with how fair that comparison is), the evidence behind it, and how far it has gone (lab, customer-tested, deployed). Organizations are secondary.

**Status: Phase 1, local only.** Registry seeded 2026-11-19 from the Tools Companies vault screen: 28 materials (12 published, 10 delivered with company-reported evidence, 6 lab claims) and 77 organizations including the watch list. No ingest or site yet. Plan: EA `projects/ai-materials-tracker/inception-plan.md`.

## Grading

| Field | Values |
|-------|--------|
| Evidence | peer-reviewed, preprint, company-reported, claim |
| Stage | deployed, customer-tested, lab |
| Comparison basis | head-to-head, literature, internal-baseline, design-target, none-stated, not-applicable |

Grades change only through review. Funding on the public chart is tier-1 sourced only.

## Run

```sh
uv sync
uv run pytest                          # registry rules
LAUNCH_GATE=1 uv run pytest            # also fails on open verify fields and non-tier-1 chart funding
uv run python -m tracker.registry      # list rule violations
uv run python -m tracker.registry --launch
```

## Layout

| Path | Purpose |
|------|---------|
| `reference/materials.yaml` | One entry per material: what is new, key property, benchmark and comparison basis, evidence, stage, publication, dispute |
| `reference/orgs.yaml` | Organizations, funding with source tier, watch list with reasons |
| `reference/source_tiers.yaml` | News tiers (from physical-ai-tracker) plus the evidence tier (journals, preprint servers) |
| `sources.yaml` | Planned data sources and their status |
| `tracker/registry.py` | Schema and rule checks; launch gate |
| `tests/` | Registry tests |

## License

Code: MIT. Derived data: CC-BY-4.0, subject to upstream terms listed in `sources.yaml`.
