# AI-Made Materials: Proof Tracker

Which materials made with AI/ML are real, is there published proof, and are they better than the existing best? Each entry is a material: what is new about it, its key property against the current best-in-class (with how fair that comparison is), the evidence behind it, and how far it has gone (lab, customer-tested, deployed). Organizations are secondary.

**Status: Phase 2, local only.** Registry seeded 2026-11-19 from the Tools Companies vault screen: 28 materials (12 published, 10 delivered with company-reported evidence, 6 lab claims) and 80 organizations including the watch list. Weekly ingest built (news, newsroom feeds, SEC filings, OpenAlex papers, journal versions, critiques, Crossref corrections) feeding a review queue; no site yet. Plan: EA `projects/ai-materials-tracker/inception-plan.md`.

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

# weekly ingest (each step writes data/review/queue.csv; run in this order)
uv run python -m ingest.filings --since 2026-11-01
uv run python -m ingest.news --days 8
uv run python -m classify.accept
uv run python -m ingest.openalex --since 2026-10-25
uv run python -m ingest.crossref

# review (or /review-events in Claude Code)
uv run python -m classify.review list
```

Secrets come from env vars or the macOS Keychain: `CONTACT_EMAIL` (or Keychain `ai-materials-tracker-contact`), `OPENALEX_API_KEY` (or `openalex-api-key`).

## Layout

| Path | Purpose |
|------|---------|
| `reference/materials.yaml` | One entry per material: what is new, key property, benchmark and comparison basis, evidence, stage, publication, dispute |
| `reference/orgs.yaml` | Organizations, funding with source tier, watch list with reasons |
| `reference/signals.yaml` | Per org: owned domains, news terms, newsroom feed, SEC CIK, OpenAlex institutions / affiliation phrases / authors |
| `reference/ledger.csv` | Dated events: grade changes (evidence, stage, dispute), funding, launches, licenses; `accept` = seed, auto or reviewed |
| `reference/source_tiers.yaml` | News tiers (from physical-ai-tracker) plus the evidence tier (journals, preprint servers) |
| `ingest/` | `news`, `filings` (ported from physical-ai-tracker), `openalex` (papers, journal versions, critiques), `crossref` (corrections) |
| `classify/` | `events` (headline types), `accept` (rules), `group`, `review` (queue CLI; `grade` is the only way a grade changes) |
| `data/` | `news/articles.csv`, `papers/works.csv`, `review/queue.csv` (committed); `raw/` snapshots (gitignored) |
| `.claude/skills/review-events/` | `/review-events` workflow |
| `.github/workflows/update.yml` | Weekly run (inactive until a remote exists and SCHEDULE_ON=true) |
| `tracker/registry.py` | Schema and rule checks; launch gate |
| `tests/` | Registry, signal, ledger, acceptance and review tests |

## License

Code: MIT. Derived data: CC-BY-4.0, subject to upstream terms listed in `sources.yaml`.
