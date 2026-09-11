---
name: deepeval-article-audit
description: Audit finished articles against the exact local fact inputs supplied to the writer by reproducing DeepEval Faithfulness claim extraction, evidence judgment, and supported-claims/total-claims calculation with the current Codex model, without an evaluator API. Use for article support-rate review, unsupported-claim review, claim-to-source mapping, import-ready manage-article-knowledge v0.6 or v0.5 artifacts, Markdown details, batch summaries, or highlighted HTML. Always label results as a Codex reproduction, not an official DeepEval run.
---

# DeepEval-style Article Audit v1.4.1

Evaluate finished article bodies against only the knowledge files supplied by the user. Use the current Codex model as the judge and require no evaluator API key.

## Required label

Write this label near every score and in every deliverable:

`DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）`

Never call the result an official DeepEval score. Do not claim that the DeepEval package or its default evaluator model ran.

## Inputs

Accept either:

- one or more finished article files plus their corresponding knowledge files; or
- a folder containing article task subfolders, writing-input Markdown, and finished-article Markdown.

For a `manage-article-knowledge v0.5` task folder, pair exactly:

- `40_最终文章.md` as the article;
- `30_本篇知识库资料.md` as the default fact context.

In `manage-article-knowledge v0.5` mode, accept exactly one factual input: the current `30_本篇知识库资料.md`. Article-specific source files must first be consolidated through that Skill into source records, Formal Claims and the current `30/35`; do not add them as parallel knowledge files. Generic non-manage audits may still accept multiple explicitly supplied knowledge files.

For a `manage-article-knowledge v0.6` controlled run, read [the v0.6 managed-audit adapter](references/manage-article-knowledge-v06-integration.md), locate the installed knowledge-base Skill, and read its canonical `references/handoff-contract.json`. Accept the controlled request fields:

```text
handoff_event: faithfulness_request
handoff_contract_version, article_file, knowledge_file, project_id,
article_id, article_version, result_root, result_dir
```

The project-specific paths are owned by the knowledge-base Skill's integration configuration. Do not search for or invent them here. `result_dir` is the single version-specific directory for this article; all three importable core artifacts must be written there.

When this Skill is invoked as the post-writing step described by the v0.6 handoff, do not wait for a second human instruction. Reject a missing or incompatible `handoff_contract_version`; do not guess or fall back to another local contract. Once the controlled request and current `40`/`30` pair pass the canonical contract, run the audit, write the three core results to `result_dir`, and return `handoff_event: faithfulness_completed` with the same contract version. This event only means that Faithfulness artifacts were generated and validated; it is not the end of the article workflow. The knowledge-base Skill must continue with `import_faithfulness.py`, governance updates and migration to `40_已完成`, then issue its own `article_completed` event. Human-facing reporting is only needed for a validation failure or a business decision outside Faithfulness.

Confirm the file pairing from filenames and content. If more than one plausible article or knowledge file remains and the pairing would change the result, ask one concise question before judging.

Treat only user-supplied knowledge files as `retrieval_context`. Do not use web search, model memory, unrelated project files, or the finished article itself as evidence.

## Workflow

1. Read `references/method.md` completely.
2. Create a work output directory that does not overwrite source files.
3. For each article, run one of the following paths:

   In v0.6 controlled mode, use the version-specific result directory supplied by the knowledge-base Skill:

   `python scripts/prepare_article_audit.py --article <40_最终文章.md> --knowledge <30_本篇知识库资料.md> --project-id <project-id> --article-id <article-id> --article-version <article-version> --handoff-contract-version <contract-version> --result-dir <result-dir>`

   After judgments are written as `<article-id>-judgments.json` in that same directory, render it with:

   `python scripts/render_article_audit.py --result-dir <result-dir> --article-id <article-id> --article-version <article-version>`

   If the knowledge-base Skill has already moved the task from `30_等待Faithfulness` to `40_已完成`, revalidate the same artifacts without rewriting them by supplying the moved current files and a separate report directory:

   `python scripts/render_article_audit.py --result-dir <result-dir> --article <moved-40_最终文章.md> --knowledge <moved-30_本篇知识库资料.md> --output-dir <review-output-dir>`

   The managed commands reject identity mismatches, extra factual inputs, missing version metadata, malformed `[result root]/[project-id]/[article-id]/v[version]/` directories, and attempts to overwrite an existing core result. A managed `40_最终文章.md` must contain exactly one `<!-- ARTICLE_BODY_START -->` / `<!-- ARTICLE_BODY_END -->` pair. Only text, list items, and table rows inside that pair are auditable article content; metadata, TDK, image plans, delivery notes, and audit administration stay outside it.

   For generic or legacy use, run:

   `python scripts/prepare_article_audit.py --article <article.md> --knowledge <knowledge1.md> [--knowledge <knowledge2.md> ...] --output <article-id>-prepared.json`

   If `python` is unavailable, locate and use the Python runtime provided by the current Codex workspace.

   The preparer reads `文章ID` or `Article ID` metadata before falling back to the filename. In v0.6 it also requires and validates `文章版本`. If `--article-id` conflicts with file metadata, stop and correct the pairing.

4. Review every `articleUnit` in the prepared JSON in article order. Extract every atomic, independently checkable factual claim. Do not sample or treat a low number of supported claims as evidence that the rest of the article is non-factual. Treat statements about real-world entities, product capabilities, materials, dimensions, mechanisms, effects, quantities, process steps, quality controls, typicality, causal relationships, or constraints as factual even when phrased as advice (`should`, `can`, `may`, `use`, `compare`) if the sentence asserts a reason or expected result. A recommendation with no factual premise may remain non-factual. Only headings, pure transitions, rhetorical/subjective language, pure questions, CTAs, and explicitly hypothetical planning language without an asserted fact may be excluded. Each atomic claim must be a direct extraction or minimal normalization of its own `article_quote`; never attach a claim from another sentence or FAQ unit. If a faithful normalization has no shared wording with the quote, add a concrete `derivation_note` explaining the exact transformation so the validator can distinguish it from a swapped claim.
5. Judge each claim against the candidate evidence. Search the supplied knowledge files directly when the candidates are insufficient. In a current manage-article writing-material file (v0.5/v0.6), only content under a `证据正文（供Faithfulness核验）` heading in sections 1-3 is positive evidence. Direct writing facts, English-expression tables, data display tables without an evidence body, outline guidance, controls and gap-handling sections are not citable evidence.
6. Write `<article-id>-judgments.json` using the schema in `references/method.md`. Copy article and knowledge quotations as exact raw substrings, including Markdown markers when they occur inside the quoted span, and preserve line numbers.
7. Run the renderer once for the whole batch:

   `python scripts/render_article_audit.py --input-dir <work-output-directory> --output-dir <final-output-directory>`

8. Inspect the generated Markdown and HTML. The renderer also verifies current source files, schema versions, exact quotations, v0.5 evidence boundaries, and import-sensitive summary formatting. Fix any failure and rerun.

### Coverage completeness gate

Before writing judgments, perform a second, independent coverage pass over every prepared article unit. The pass must challenge every proposed `non-factual` classification and identify factual premises hidden in advice, tables, comparisons, process descriptions, and cause/effect explanations. If a managed article has at least 20 content units and fewer than 20% are classified as factual, do not treat the score as complete by default. Add a `coverage_review` object to judgments containing `reviewed_unit_count`, `factual_unit_count`, `non_factual_unit_count`, and a `unit_classifications` entry for every `unit_id`; each excluded unit needs a concrete reason, and every excluded unit with candidate evidence must be explicitly reconsidered. The renderer rejects a managed result that falls below this coverage threshold without a complete coverage review. This is a completeness safeguard, not a replacement for semantic judgment or the claim-level score.

## Mandatory outputs

Produce all of the following:

1. `<article-id>_faithfulness_details.md`: one row per factual claim.
2. `faithfulness_summary.md`: one article per row, with numerator, denominator, unsupported count, and percentage. Append a separate content-unit diagnostic table showing total article units, factual units, factual-content share, fully/partially/unsupported units, and non-factual units; this diagnostic does not replace or alter the claim-level metric.
3. `faithfulness_highlight.html`: all articles in one interactive page. Keep each article claim, verdict, reason, and source quotation together in the same expandable card. Do not use a detached source panel fixed at the top or upper-right.
4. Preserve the prepared and judgment JSON files as the audit trail.

## Non-negotiable rules

- Exclude the article title, headings, URLs, navigation, metadata, prompts, and administrative audit text from the denominator.
- In managed v0.6 mode, missing, repeated, or reversed article-body markers are a hard stop. Do not infer a body from nearby headings; require the current knowledge bridge to regenerate `40_最终文章.md`.
- Use factual claims, not words or sentences, as numerator and denominator.
- Split compound statements when their parts can receive different verdicts.
- Count a supported paraphrase as supported; verbatim overlap is not required.
- Mark a real-world fact unsupported when the supplied knowledge context does not support it.
- Require at least one exact knowledge quotation for every supported claim.
- For current `30_本篇知识库资料.md` in v0.5/v0.6, cite only a complete line range inside an explicit evidence body in sections 1-3. Do not cite metadata, direct writing facts, English-expression tables, outline guidance, controls or gap-handling text.
- Do not search for or cite Formal Claim IDs in `30`. The receiving Skill maps evidence-body line ranges through sibling `35_写作素材来源索引.md`, which is not part of retrieval context.
- Do not use partial credit. Split the claim, then use only `supported` or `unsupported`.
- Compute the score only as `supported claims / all factual claims`. Exclude non-factual language before creating claim rows.
- Keep two layers visible in human-readable reports: (a) article content-unit classification, where every prepared `articleUnit` is labeled factual or non-factual and factual units are labeled fully supported, partially supported, or unsupported; and (b) the atomic claim-level score above. Do not confuse factual-content share with Faithfulness or source-text coverage.
- In the highlighted HTML, render each prepared article unit (sentence, list item, or table data row) as its own visible card. If several units originate from one Markdown line, do not merge them back into one visual block. Show the unit's source line, `unit_id`, factuality/support status and atomic-claim count; keep non-factual units visibly muted, factual units color-coded by support status, and filters for all, factual, fully supported, unsupported, and non-factual content. Table fields may remain atomic claims, but each surrounding content-unit card must show its own aggregate status.
- Preserve the original summary table columns and values because `manage-article-knowledge v0.5` validates them. Add diagnostics after the stable table rather than replacing or reordering its columns.
- Do not silently omit difficult, ambiguous, or weakly supported claims.
- Do not blend this score with RAGSEO exact-text citation rate or any word-coverage rate.

## Handoff

Tell the user which files were treated as articles and knowledge context, how many claims were counted, the resulting score, and where the three human-readable outputs were saved.

For `manage-article-knowledge v0.6` or v0.5, hand off these files without editing them:

- `<article-id>-prepared.json`;
- `<article-id>-judgments.json`;
- `faithfulness_summary.md`.

In v0.6, keep all three core files in the supplied `result_dir`; the receiving Skill reads that directory and performs the import. Also report the article file and every knowledge file in the exact order stored in `prepared.json`. The importer must receive repeated `--knowledge` arguments in that same order. Detailed Markdown and HTML remain audit outputs and are not required for import. Do not write directly to the knowledge project's Faithfulness CSV files; the receiving Skill owns the import.

## Maintenance

Update `CHANGELOG.md` whenever behavior, schemas, validation rules, output contracts, or compatibility claims change. Keep the newest released entry first and record the date, user-visible effect, and relevant compatibility impact.
