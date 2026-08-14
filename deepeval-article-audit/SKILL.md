---
name: deepeval-article-audit
description: Audit finished articles against one or more local knowledge-base or writing-input files by reproducing the original DeepEval Faithfulness claim-extraction, evidence-judgment, and truthful-claims/total-claims calculation with the current Codex model, without an evaluator API. Use when users ask for article knowledge-base support rate, Faithfulness-style citation rate, unsupported-claim review, claim-to-source evidence mapping, a per-claim Markdown table, one-row-per-article summary, or an interactive highlighted HTML report. Always label results as a Codex reproduction, not an official DeepEval run.
---

# DeepEval-style Article Audit

Evaluate finished article bodies against only the knowledge files supplied by the user. Use the current Codex model as the judge and require no evaluator API key.

## Required label

Write this label near every score and in every deliverable:

`DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）`

Never call the result an official DeepEval score. Do not claim that the DeepEval package or its default evaluator model ran.

## Inputs

Accept either:

- one or more finished article files plus their corresponding knowledge files; or
- a folder containing article task subfolders, writing-input Markdown, and finished-article Markdown.

Confirm the file pairing from filenames and content. If more than one plausible article or knowledge file remains and the pairing would change the result, ask one concise question before judging.

Treat only user-supplied knowledge files as `retrieval_context`. Do not use web search, model memory, unrelated project files, or the finished article itself as evidence.

## Workflow

1. Read `references/method.md` completely.
2. Create a work output directory that does not overwrite source files.
3. For each article, run:

   `python scripts/prepare_article_audit.py --article <article.md> --knowledge <knowledge1.md> [--knowledge <knowledge2.md> ...] --output <article-id>-prepared.json`

   If `python` is unavailable, locate and use the Python runtime provided by the current Codex workspace.

4. Review every `articleUnit` in the prepared JSON in article order. Extract every atomic, independently checkable factual claim. Do not sample.
5. Judge each claim against the candidate evidence. Search the supplied knowledge files directly when the candidates are insufficient.
6. Write `<article-id>-judgments.json` using the schema in `references/method.md`. Copy article evidence and knowledge evidence verbatim and preserve line numbers.
7. Run the renderer once for the whole batch:

   `python scripts/render_article_audit.py --input-dir <work-output-directory> --output-dir <final-output-directory>`

8. Inspect the generated Markdown and HTML. Fix missing claims, invalid evidence, incorrect pairings, or rendering errors, then rerun.

## Mandatory outputs

Produce all of the following:

1. `<article-id>_faithfulness_details.md`: one row per factual claim.
2. `faithfulness_summary.md`: one article per row, with numerator, denominator, unsupported count, and percentage.
3. `faithfulness_highlight.html`: all articles in one interactive page. Keep each article claim, verdict, reason, and source quotation together in the same expandable card. Do not use a detached source panel fixed at the top or upper-right.
4. Preserve the prepared and judgment JSON files as the audit trail.

## Non-negotiable rules

- Exclude the article title, headings, URLs, navigation, metadata, prompts, and administrative audit text from the denominator.
- Use factual claims, not words or sentences, as numerator and denominator.
- Split compound statements when their parts can receive different verdicts.
- Count a supported paraphrase as supported; verbatim overlap is not required.
- Mark a real-world fact unsupported when the supplied knowledge context does not support it.
- Require at least one exact knowledge quotation for every supported claim.
- Do not use partial credit. Split the claim, then use only `supported` or `unsupported`.
- Compute the score only as `supported claims / all factual claims`. Exclude non-factual language before creating claim rows.
- Do not silently omit difficult, ambiguous, or weakly supported claims.
- Do not blend this score with RAGSEO exact-text citation rate or any word-coverage rate.

## Handoff

Tell the user which files were treated as articles and knowledge context, how many claims were counted, the resulting score, and where the three human-readable outputs were saved.
