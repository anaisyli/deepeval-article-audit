---
name: deepeval-article-audit
description: Audit finished articles against the exact local fact inputs supplied to the writer by reproducing DeepEval Faithfulness claim extraction, evidence judgment, and supported-claims/total-claims calculation with the current Codex model, without an evaluator API. Use for article support-rate review, unsupported-claim review, claim-to-source mapping, import-ready manage-article-knowledge v0.5 artifacts, Markdown details, batch summaries, or highlighted HTML. Always label results as a Codex reproduction, not an official DeepEval run.
---

# DeepEval-style Article Audit v1.1

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

Confirm the file pairing from filenames and content. If more than one plausible article or knowledge file remains and the pairing would change the result, ask one concise question before judging.

Treat only user-supplied knowledge files as `retrieval_context`. Do not use web search, model memory, unrelated project files, or the finished article itself as evidence.

## Workflow

1. Read `references/method.md` completely.
2. Create a work output directory that does not overwrite source files.
3. For each article, run:

   `python scripts/prepare_article_audit.py --article <article.md> --knowledge <knowledge1.md> [--knowledge <knowledge2.md> ...] --output <article-id>-prepared.json`

   If `python` is unavailable, locate and use the Python runtime provided by the current Codex workspace.

   The preparer reads `文章ID` or `Article ID` metadata before falling back to the filename. If `--article-id` conflicts with file metadata, stop and correct the pairing.

4. Review every `articleUnit` in the prepared JSON in article order. Extract every atomic, independently checkable factual claim. Do not sample.
5. Judge each claim against the candidate evidence. Search the supplied knowledge files directly when the candidates are insufficient. In a current v0.5 writing-material file, only content under a `证据正文（供Faithfulness核验）` heading in sections 1-3 is positive evidence. Direct writing facts, English-expression tables, data display tables without an evidence body, outline guidance, controls and gap-handling sections are not citable evidence.
6. Write `<article-id>-judgments.json` using the schema in `references/method.md`. Copy article and knowledge quotations as exact raw substrings, including Markdown markers when they occur inside the quoted span, and preserve line numbers.
7. Run the renderer once for the whole batch:

   `python scripts/render_article_audit.py --input-dir <work-output-directory> --output-dir <final-output-directory>`

8. Inspect the generated Markdown and HTML. The renderer also verifies current source files, schema versions, exact quotations, v0.5 evidence boundaries, and import-sensitive summary formatting. Fix any failure and rerun.

## Mandatory outputs

Produce all of the following:

1. `<article-id>_faithfulness_details.md`: one row per factual claim.
2. `faithfulness_summary.md`: one article per row, with numerator, denominator, unsupported count, and percentage. Append a separate content-unit diagnostic table showing total article units, factual units, factual-content share, fully/partially/unsupported units, and non-factual units; this diagnostic does not replace or alter the claim-level metric.
3. `faithfulness_highlight.html`: all articles in one interactive page. Keep each article claim, verdict, reason, and source quotation together in the same expandable card. Do not use a detached source panel fixed at the top or upper-right.
4. Preserve the prepared and judgment JSON files as the audit trail.

## Non-negotiable rules

- Exclude the article title, headings, URLs, navigation, metadata, prompts, and administrative audit text from the denominator.
- Use factual claims, not words or sentences, as numerator and denominator.
- Split compound statements when their parts can receive different verdicts.
- Count a supported paraphrase as supported; verbatim overlap is not required.
- Mark a real-world fact unsupported when the supplied knowledge context does not support it.
- Require at least one exact knowledge quotation for every supported claim.
- For current `30_本篇知识库资料.md`, cite only a complete line range inside an explicit evidence body in sections 1-3. Do not cite metadata, direct writing facts, English-expression tables, outline guidance, controls or gap-handling text.
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

For `manage-article-knowledge v0.5`, hand off these files without editing them:

- `<article-id>-prepared.json`;
- `<article-id>-judgments.json`;
- `faithfulness_summary.md`.

Also report the article file and every knowledge file in the exact order stored in `prepared.json`. The v0.5 importer must receive repeated `--knowledge` arguments in that same order. Detailed Markdown and HTML remain audit outputs and are not required for import. Do not write directly to the knowledge project's Faithfulness CSV files; the receiving Skill owns the import.

## Maintenance

Update `CHANGELOG.md` whenever behavior, schemas, validation rules, output contracts, or compatibility claims change. Keep the newest released entry first and record the date, user-visible effect, and relevant compatibility impact.
