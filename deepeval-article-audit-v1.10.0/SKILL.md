---
name: deepeval-article-audit
description: Audit finished articles against the exact local fact inputs supplied to the writer by reproducing DeepEval Faithfulness claim extraction and evidence judgment with the current Codex model, without an evaluator API. Use for knowledge-attachment support review, unknown or contradicted claim review, claim-to-source mapping, import-ready manage-article-knowledge v0.6 or v0.5 artifacts, Markdown details, batch summaries, or highlighted HTML. Always label results as a Codex reproduction, not an official DeepEval run.
---

# DeepEval-style Article Audit v1.10.0

Evaluate finished article bodies against only the knowledge files supplied by the user, with the current Codex model as judge and no evaluator API key. All judging rules, schemas and examples live in [references/method.md](references/method.md); compact file formats in [references/audit-preflight.md](references/audit-preflight.md); the full rules behind each step below in [references/run-rules.md](references/run-rules.md). This file is the run order: open a run-rules section only when its step needs it or a command refuses.

## Required label

Write this label near every score and in every deliverable:

`DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）`

Never call the result an official DeepEval score or claim that the DeepEval package or its default evaluator model ran.

## Inputs

- Generic use: one or more finished articles plus their knowledge files, or a folder of article task subfolders. Several explicit knowledge files are allowed.
- `manage-article-knowledge` v0.5: pair `40_最终文章.md` with exactly one fact input, the current `30_本篇知识库资料.md`. Article-specific sources must first be consolidated by that Skill into the current `30/35`; never add them as parallel knowledge files.
- `manage-article-knowledge` v0.6 controlled run: read [the v0.6 adapter](references/manage-article-knowledge-v06-integration.md) and accept the request below. `prepare` locates and validates the installed knowledge Skill's handoff contract itself; do not open the knowledge Skill's SKILL.md, contract or references for an audit.

```text
handoff_event: faithfulness_request
handoff_contract_version, article_file, knowledge_file, project_id,
article_id, article_version, result_root, result_dir
```

Project paths belong to the knowledge Skill; do not search for or invent them. `result_dir` is the one version-specific directory for prepared, judgments, adversarial review and summary; process files stay in its `_work/` subdirectory. Reject a missing or incompatible contract version; never fall back to another local contract. Run without waiting for a second human instruction, then return `handoff_event: faithfulness_completed` with the same contract version. That event only means the artifacts exist and validated; the knowledge Skill still imports, migrates the task to `40_已完成` and issues `article_completed`. If it returns `writing_retry_required`, do not start: the writing Skill must first deliver a changed body.

Confirm pairing from filenames and content; ask one concise question only if several plausible pairings would change the result. Only the supplied knowledge files are `retrieval_context`: no web search, model memory, unrelated project files or the article itself.

## Independent context

Run the audit in a context that did not write the article: a subagent or a new session that receives only the request fields above. Keep the article-writing conversation out of it. The second evidence judgment (step 6) should likewise run in a separate subagent or fresh session that receives only `recheck-list` output plus the allowed knowledge, never the first pass's verdicts, evidence or reasons. If no separate context is available, perform a fresh pass without consulting the first conclusion.

## Review policy

New managed runs declare `review_policy: "decisive_recheck_v1"` in both inventories, `judgments.json` and `adversarial-review.json`:

- every frozen claim gets one primary evidence judgment;
- only claims the first pass judged `entailed` or `contradicted` get a second, blind evidence judgment;
- a first-pass `unknown` is final (`review_depth: first_pass_unknown`) and is never rechecked or promoted;
- a required recheck that is missing, disagrees or fails its checks becomes `unknown`.

Historical `two_pass_consensus` (every claim rechecked) and `risk_weighted_v1` (high-risk tags) results stay valid under their own rules and remain importable; do not add risk tags to new runs.

## Hard rules

Full wording in run-rules.md, sections named in brackets.

- **All-unknown gate** [Article-wide all-unknown gate]: after primary judgments, an article whose frozen claims are all `unknown` pauses before `recheck-list`. Check the evidence view for a missed direct match; if one was missed or the view was not used, `audit_preflight.py reset` and restart from the first pass; otherwise keep every unknown and continue. Never repair verdicts or promote unknowns.
- **Inputs are locked** [Input snapshot and managed prepare]: on `input_snapshot_changed` stop and hand the article back to the knowledge base. Never delete and regenerate `prepared`, refreeze under another name, copy managed files elsewhere or edit their version fields.
- **Scripts write every record** [Script refusals and record writers]: a refusal is a normal step; fix exactly the rows it names in the pass, verdict or review file and rerun the same command. Never edit `judgments.json`, `adversarial-review.json`, frozen files or a judged extraction pass, never delete evidence rows, never rename a record to rerun a step. Extraction and judging are the model's own reading: never generate claims, verdicts or reasons with a script or template. Evidence is a verbatim span of this article's own knowledge files.
- **Resumed or redone passes** [Resumed attempts and pass independence]: claim IDs change on every freeze; never migrate old verdicts by text match or write placeholder reviews.
- **Warnings are not failures** [Warnings versus failures]: a render that exits successfully with `audit_quality: warning` has completed; stop only on a non-zero exit, an explicit failed gate or a missing output.
- **Grouping for the knowledge base** [Grouping for manage-article-knowledge]: a real gap is one stable knowledge question answerable by the same evidence; same industry or "general packaging methods" alone is not a group. Grouping never changes the denominator, judgments or three states.

## Speed rules

The model writes only semantic content; scripts copy every identity and mechanical field.

- When resuming an interrupted or paused audit, run `python scripts/audit_preflight.py status --result-dir <result-dir>` first. It reports each stage as done or pending, the compact shards present and the claim IDs still missing verdicts or reviews, without printing file contents. Continue at `next_stage` and read only the files that step needs; never reopen prepared, inventories, frozen, judgments or adversarial-review just to find out how far the audit got.
- Read the views, never `prepared.json` (it repeats the evidence per candidate, often 10–30×) or `frozen.json` (identity fields for the scripts). `_work/<article-id>-extract-view.md` holds the extraction guide and the article units without evidence, for the extraction passes; `_work/<article-id>-evidence-view.md` holds the knowledge files and allowed evidence, for judging and rechecking; `_work/claims.md` (written by `freeze`) lists the frozen claims one per line, for the judges. `_work/<article-id>-audit-view.md` is the full view, kept as the record.
- Do not read script sources or `--help` to find out how a command works: the commands below are complete, and a refusal names the rows to fix. Run each validation (`freeze`, `assemble-*`, `check`) once per corrected file, not after every row.
- Keep compact files terse in mechanical fields only; terseness never means fewer or coarser claims. Then: no classification row for a unit that has claims, no `claim` when an atomic claim equals its quote, notes only where method §2 requires them. Write a reason only where it decides something (v1.10): an `unknown` verdict gives a `gap_type` (optionally a short `gap`), an `entailed` verdict gives its verbatim `evidence` and no reason, a `contradicted` verdict keeps a short `conflict`. In the second judgment the fragments and statuses are the review: leave out `challenge_reason`, `atomicity_review.reason` and per-check reasons, and write your own reason only where you overrule a machine signal (`atomic` for a compound-looking claim, `not_applicable` for a dimension the claim's wording signals). The assembler fills the rest from the row.
- Run independent work in parallel when the runtime offers subagents: primary-judgment shards (about 40 claims each, split by `claim_id` range, one compact file per shard); recheck shards; and whole articles in a batch. Each subagent gets only what it reads and writes: the first extraction pass the extract view (its 抽取轮写法 section gives the file format and the allowed `non_factual` categories) and method §2; the coverage review the extract view, `_work/first.json` and method §2; a judgment shard its range of `_work/claims.md`, the evidence view, method §3–§5 and the 主判断 section of audit-preflight.md; a recheck shard `_work/recheck.json`, the evidence view, method §3 and §6 and the 第二次判断 section. Give it the exact file paths and tell it not to load this SKILL.md; it reads nothing else: no knowledge-base folders, no `35`, no other article's files, no result directory of another version, and no other pass's or shard's output. Every audit starts the first pass and the coverage review fresh; neither is copied from another version or attempt. Without subagents run the same steps in sequence.

## Result directory layout

The version directory root holds only result files: `<id>-prepared.json`, `<id>-judgments.json`, `<id>-adversarial-review.json`, `faithfulness_summary.md`, `<id>_faithfulness_details.md`, `faithfulness_highlight.html`, plus the knowledge Skill's `unsupported-disposition.json` and `article_completed.json`. Every process file goes in `<result-dir>/_work/`; prepare creates it and a successful managed render deletes it (`--keep-work` keeps it). Nothing downstream reads `_work`. Move any `stray_files` the render lists into `_work` before rendering next time.

## Workflow

Each step's full rules are under the same step number in run-rules.md (Step details).

1. The orchestrating context needs this file and the commands below; it hands each subagent the method and audit-preflight sections listed under Speed rules instead of reading every reference itself, and opens audit-preflight.md when a command refuses. Without subagents, read method.md once per batch before judging. `references/record-format.md` (full record schemas, historical policies) is not needed for a run.
2. Prepare into an output directory that never overwrites sources (if `python` is unavailable, use the workspace's Python runtime). It writes the extract, evidence and full views into `_work/`; identity mismatches, missing or repeated `ARTICLE_BODY` markers and overwriting a core result are hard stops.
   - v0.6: `python scripts/prepare_article_audit.py --article <40_最终文章.md> --knowledge <30_本篇知识库资料.md> --project-id <project-id> --article-id <article-id> --article-version <version> --handoff-contract-version <contract-version> --result-dir <result-dir>`
   - generic/legacy: `python scripts/prepare_article_audit.py --article <article.md> --knowledge <k1.md> [--knowledge <k2.md> ...] --output <article-id>-prepared.json`
3. First extraction pass into `_work/first.json` from the extract view (method §2, its 抽取轮写法 section); then, in a separate context, the coverage review into `_work/coverage.json` (it reviews only the units the first pass classified non_factual and adds missed independent facts; it never rewrites first-pass claims).
4. `python scripts/audit_preflight.py freeze --prepared ... --first-inventory _work/first.json --coverage-review _work/coverage.json --output _work/frozen.json`. Fix the rows it names and rerun; it refuses once judging has started.
5. Primary judgment of every frozen claim (method §3–§5) into compact verdict files, then `python scripts/audit_preflight.py assemble-judgments --prepared ... --frozen-inventory ... --first-inventory ... --blind-inventory ... --verdicts _work/verdicts-1.json [--verdicts ...] --output <result-dir>/<article-id>-judgments.json`. Apply the all-unknown gate before step 6.
6. `python scripts/audit_preflight.py recheck-list --judgments <article-id>-judgments.json --output _work/recheck.json`; the blind reviewer writes one compact row per listed claim (method §6), then `python scripts/audit_preflight.py assemble-review --prepared ... --frozen-inventory ... --blind-inventory ... --recheck _work/recheck.json --reviews _work/reviews-1.json [--reviews ...] --output <result-dir>/<article-id>-adversarial-review.json`.
7. `python scripts/audit_preflight.py check --prepared ... --judgments ... --adversarial-review ... --frozen-inventory ... --receipt _work/receipt.json [--attempt 1]`. Fix `review_tasks` once in the compact files, assemble again and rerun; anything left after `--attempt 1` stays `unknown`.
8. `python scripts/reconcile_article_audit.py --result-dir <result-dir> --article-id <article-id>` (only retains or downgrades; no human queue). A claim both passes entailed but downgraded only for a record problem is not a reason to `reset`: run `audit_preflight.py reopen-review --result-dir <result-dir> --article-id <id>` once before import, fix only the listed rows, then assemble-review, check, reconcile and render again [Step 8].
9. Render once per batch: `python scripts/render_article_audit.py --input-dir <work> --output-dir <final>`; in v0.6 `--result-dir <result-dir> --article-id <id> --article-version <version>`.
10. Inspect Markdown and HTML. Identity, quotation or structure failures are fixed and rerun; semantic uncertainty is reported as `unknown`, never a reason to suppress reports.

## Mandatory outputs

1. `<article-id>_faithfulness_details.md`: one row per claim, with review depth.
2. `faithfulness_summary.md`: one row per article; keep the import-sensitive first seven columns, then unknown, contradicted and review depth. No DeepEval-compatible percentage.
3. `faithfulness_highlight.html`: every prepared unit as its own card in four human states (gray non-claim, green 明确支持, amber 尚未确认, red 明确冲突) with separate unknown/contradicted filters; claim, result, reason and quote stay in the same expandable card.
4. Prepared, judgments and adversarial-review JSON as the audit trail.

## Handoff

Tell the user which files were treated as article and knowledge, the claim count, the score and where outputs were saved. For v0.5/v0.6 hand off `<article-id>-prepared.json`, `<article-id>-judgments.json`, `faithfulness_summary.md` (plus `<article-id>-adversarial-review.json` for protocol 2.0) unedited in `result_dir`, and list the knowledge files in the exact order stored in `prepared.json`; the importer receives repeated `--knowledge` in that order. Never write the knowledge project's CSVs; the receiving Skill's `import_faithfulness.py` owns the import. When grouping yellow/red claims for `manage-article-knowledge`, follow run-rules.md (Grouping for manage-article-knowledge); never change the denominator, evidence judgments or three states.

## Maintenance

This Skill delivers only the current article's artifacts. It never edits knowledge governance ledgers, KMR lists, Claim, RES, GAP, CUS, ANM or SKFB, never recomputes historical results, and never back-dates later evidence as attachment support. Update the repository-level `CHANGELOG.md` (one level above this version's resolved physical directory) whenever behavior, schemas, validation, output contracts or compatibility change; newest entry first, with date, user-visible effect and compatibility impact.
