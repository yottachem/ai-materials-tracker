---
name: review-events
description: Work through the AI-Made Materials tracker's review queue (data/review/queue.csv) - news headlines, new papers, journal versions, critiques, Crossref corrections and SEC filings that were not auto-accepted. Proposes accept, grade, reject or duplicate for each item after checking the source, and records only what the owner approves. Run only when the owner asks (/review-events).
---

# Review queued events

Adapted from physical-ai-tracker's skill. The difference that matters: **a material's evidence, stage or dispute
status changes only here, through `classify.review grade`, after the owner approves.** Signals create candidates;
nothing promotes itself. The site's bar is a published result for a real material made with AI/ML.

## Steps

1. **Refresh and list.** `uv run python -m classify.accept`, then `uv run python -m classify.review list --limit 40`.
   High-priority items (Crossref corrections, critiques of tracked papers, dispute headlines) come first; work them
   first. Then one org at a time (`--org ID`), largest first. Say how many items and groups are open and how you
   will batch them.

2. **Check each item before proposing anything.** Open the source (WebFetch; for SEC, the filing; for papers, the
   abstract and, when open access, the paper). Confirm the org, the material, the figures and the date from the
   source, not the headline.
   - **Paper (result / promotion):** is it a real material, made or tested in a lab, with AI/ML doing real work
     (proposing, ranking or optimizing the candidates)? Is the tracked org an author affiliation on it? If yes and
     the material is new to the registry, propose a new `materials.yaml` entry (all fields, including the benchmark:
     the field's best on the key property, sourced separately where possible, with its comparison basis). If it is
     a tracked material, propose `grade` where it changes the evidence. Founder or pre-company work does not count
     for the company (Aionics rule).
   - **Journal version (evidence):** confirm it is the same work as the preprint (authors, material, numbers), then
     propose `grade --field evidence --to peer-reviewed` citing the journal DOI.
   - **Critique / correction (high priority):** read it. Say in two lines what it claims and whether it narrows or
     removes the material's result. Propose `grade --field dispute --to disputed|corrected|retracted` and the edit to
     the material's `dispute` block (status, critique or correction URL, one-line summary). A retraction also means
     proposing the evidence grade the material can still claim.
   - **Launch / deployment / license / pilot news:** does the source say the material itself shipped, was licensed,
     sampled to a named customer or used in production? Propose `accept --type launch|deployment|license|pilot` for
     the event, and `grade --field stage` only when the source supports the new stage (deployed = sold or in use;
     customer-tested = samples, pilots, scale-up partner, license). A press release of intent is not a stage change.
   - **Funding:** the physical-ai rules apply (tier 1, closed round, no double counting). Euro and pound figures go
     in `--detail`; `--amount` is USD only and needs a source that states USD. Form D `amount_sold` is cumulative per
     offering, not a round size.
   - **SEC 8-K / 10-K / 10-Q (SES AI, Schrodinger):** one line on what it reports; propose reject unless it changes
     what the site says about a material (product revenue tied to an AI-derived material, a license, a shutdown).
   - **Watch-list org:** a promotion needs the same bar as any material. Say what is still missing if it falls short.

3. **Propose in a table**, one row per group: qid, org, material, proposal (accept / grade / reject / dup), type or
   field -> value, figures, date, sources, one-line reason. Group obvious rejects (wrong company, off-topic
   paper, routine filing) in one row.

4. **Apply only what the owner approves**, with the script, never by editing CSVs:
   ```
   uv run python -m classify.review accept QID --type funding --round "Series B" --amount 50000000 --source https://...
   uv run python -m classify.review accept QID --type license --material copernic-neptune
   uv run python -m classify.review grade --material lila-inmnpdox --field evidence --to peer-reviewed \
       --source https://doi.org/10.xxxx/... --qid QID
   uv run python -m classify.review reject QID --reason "different company named Cambium (wood marketplace)"
   uv run python -m classify.review dup QID E0042
   ```
   New materials and benchmark changes are edits to `reference/materials.yaml` (and `orgs.yaml` /
   `signals.yaml` for a new org), followed by a `grade`-style seed row: run `grade` for the starting evidence and
   stage so the ledger and registry agree (tests check this).

5. **Verify and wait.** `uv run pytest -q` must pass. Show `git diff --stat` and the new ledger rows. Do not commit
   or push until the owner says to; then commit as "Review queue: N accepted, N graded, N rejected, N duplicates".

## Rules

- Same-name collisions are the most common error: Cambium (wood marketplace, Cambium Networks), Chemix (Daito
  Chemix), Dunia, Altrove, Polaron, Polymerize, Uncountable, Radical AI (the podcast). Add an `exclude` phrase in
  `reference/signals.yaml` when one keeps appearing.
- Never change an existing ledger row. If one is wrong, report it to the owner with both sources.
- A benchmark that flatters the material is a finding to report, not to soften: keep the comparison basis honest.
- Keep `detail` short and factual; no em dashes.
