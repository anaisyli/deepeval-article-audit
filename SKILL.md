---
name: deepeval-article-audit
description: Audit finished articles against the exact local fact inputs supplied to the writer by reproducing DeepEval Faithfulness claim extraction and evidence judgment with the current Codex model, without an evaluator API. Use for knowledge-attachment support review, unknown or contradicted claim review, claim-to-source mapping, import-ready manage-article-knowledge v0.6 or v0.5 artifacts, Markdown details, batch summaries, or highlighted HTML. Always label results as a Codex reproduction, not an official DeepEval run.
---

# DeepEval-style Article Audit v1.6.4

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

The project-specific paths are owned by the knowledge-base Skill's integration configuration. Do not search for or invent them here. `result_dir` is the single version-specific directory for this article; prepared, judgments, and summary must be written there, and protocol 2.0 also requires the adjacent `<article-id>-adversarial-review.json`.

When this Skill is invoked as the post-writing step described by the v0.6 handoff, do not wait for a second human instruction. Reject a missing or incompatible `handoff_contract_version`; do not guess or fall back to another local contract. Once the controlled request and current `40`/`30` pair pass the canonical contract, run the audit, write every artifact required by the current protocol to `result_dir`, and return `handoff_event: faithfulness_completed` with the same contract version. For protocol 2.0, do not synthesize an empty adversarial review when the real blind second pass is missing. This event only means that Faithfulness artifacts were generated and validated; it is not the end of the article workflow. The knowledge-base Skill must continue with `import_faithfulness.py`, governance updates and migration to `40_已完成`, then issue its own `article_completed` event. Human-facing reporting is only needed for a validation failure or a business decision outside Faithfulness.

If the knowledge-base Skill returns `writing_retry_required`, do not start Faithfulness. That event belongs to the writing handoff and means the original writing Skill must produce a changed article body first. This audit Skill does not compare rewrite similarity or alter article wording.

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

   The managed commands reject identity mismatches, extra factual inputs, missing version metadata, malformed `[result root]/[project-id]/[article-id]/v[version]/` directories, and attempts to overwrite an existing core result. Test fixtures must use temporary directories; never repair a test result by manually editing a real project’s prepared, judgments, review, or summary JSON. A managed `40_最终文章.md` must contain exactly one `<!-- ARTICLE_BODY_START -->` / `<!-- ARTICLE_BODY_END -->` pair. Only text, list items, and table rows inside that pair are auditable article content; metadata, TDK, image plans, delivery notes, and audit administration stay outside it.

   For generic or legacy use, run:

   `python scripts/prepare_article_audit.py --article <article.md> --knowledge <knowledge1.md> [--knowledge <knowledge2.md> ...] --output <article-id>-prepared.json`

   If `python` is unavailable, locate and use the Python runtime provided by the current Codex workspace.

   The preparer reads `文章ID` or `Article ID` metadata before falling back to the filename. In v0.6 it also requires and validates `文章版本`. If `--article-id` conflicts with file metadata, stop and correct the pairing.

4. Perform two independent full-article claim-identification passes before judging evidence. Each pass reviews every `articleUnit` in article order and extracts every atomic, independently checkable claim without seeing the other pass's inventory. Think in terms of `verifiable claim / non-claim content`, not prose shape: table data rows, operational recommendations, comparisons, process instructions, and statements of effect or expected outcome remain auditable when they assert a concrete relationship or method. Do not sample or treat a low number of supported claims as evidence that the rest of the article is non-claim content. Source-structured `unit_type=title/heading/table_header` units are deterministically excluded in both passes and cannot be restored with `heading_override_reason`; pure Markdown image syntax and image alt text are also structurally excluded and never become article units or claims. Only unmarked plain-text heading candidates remain reviewer-classified. The final claim inventory is the union, not the intersection, of both passes. A content unit or proposition identified as a claim by either pass enters the denominator; disagreement remains in the denominator and starts as `unknown`. Each atomic claim must contain exactly one proposition that can receive one verdict independently. Before retaining a claim, inspect conjunctions, contrasts, conditions, causes, effects, lists, and multiple predicates; split every independent predicate, condition, causal/effect relation, or contrast clause that can receive a different verdict. Do not split homogeneous list members merely because they are separately enumerable: when they share the same subject, predicate or operation, scope, condition, time, and modality, retain one set-valued claim and explain that boundary in `atomicity_note`. This exception applies to homogeneous enumerations only; it never permits merging distinct fact relationships. Split when members can reasonably receive different evidence or verdicts because any of those semantic dimensions differ. Decide this boundary before viewing evidence and never merge distinct relationships to reduce the denominator. For a list item that is only a noun phrase or fragment, inherit the minimum preceding lead-in context needed to create a checkable proposition; retain the item itself as `article_quote`, and record the lead-in unit ID and inherited predicate in `derivation_note`. Do not audit an orphan fragment such as `logo position and clear space` as if it were a proposition. Each claim must be a direct extraction or minimal normalization of its own `article_quote`; never attach a claim from another sentence or FAQ unit. If a faithful normalization has no shared wording with the quote, add a concrete `derivation_note` explaining the exact transformation so the validator can distinguish it from a swapped claim.
5. Judge each claim against the candidate evidence. Search the supplied knowledge files directly when the candidates are insufficient. In a current manage-article writing-material file (v0.5/v0.6), only content under a `证据正文（供Faithfulness核验）` heading in sections 1-3 is positive evidence. Direct writing facts, English-expression tables, data display tables without an evidence body, outline guidance, controls and gap-handling sections are not citable evidence.
6. Write `<article-id>-judgments.json` using the schema in `references/method.md`. New managed runs use protocol 2.0/schema 1.1 and record both the official-style `yes/no/idk` result and a strict semantic status. Copy article and knowledge quotations as exact raw substrings, including Markdown markers when they occur inside the quoted span, and preserve line numbers.
7. In every managed v0.6 audit, complete first-pass `coverage_review` and a blind second-pass `<article-id>-adversarial-review.json`. The second pass first reads the full article and writes its own `coverage_review` and atomic `claim_inventory` without seeing the first-pass inventory, verdicts or reasons. After both inventories exist, record matches and take their union; freeze that union as the denominator before checking evidence. Then independently judge each frozen claim using the article quotation and allowed evidence, checking subject/object, predicate/relationship, scope/condition, quantity/time/version and causal/effect content using the schema in `references/method.md`. If a separate isolated reviewer is available, use it for this pass; otherwise perform a fresh pass without consulting the first conclusion. Do not copy or paraphrase the first-pass reason.

   Before adding a second-pass inventory item to the union, apply both passes' `coverage_review.unit_classifications`. Deterministically exclude source-structured `title`, `heading`, and `table_header` units. For other units, exclude the item only when both passes explicitly mark it `non_factual`, its first-pass category is `question`, `transition`, or `cta`, and it has no forced review signal. This excludes a pure FAQ question without excluding factual content in the answer; table data rows, recommendations, methods, comparisons, causal/effect statements, and any signaled unit remain eligible for the union. Record excluded inventory IDs and make this step idempotent so rerunning reconciliation cannot recreate the same Claim.
8. Reconcile both passes before rendering. Claim IDs are display labels, never matching keys. First deduplicate each pass by source `unit_id` plus normalized proposition, then align the two inventories by the full proposition identity: source `unit_id`, exact `article_quote`, and the complete normalized `claim`. Every protocol-2 blind-review row must repeat those identity fields; only after alignment may the final list be renumbered `C001...`. `claim_fragment` is explanatory only: it may identify a checked word or semantic component, may repeat across claims, and must never select or bind a review row. A stale or wrong `claim_id` or `matched_claim_id` is automatically rebound when exactly one full proposition identity exists. A legacy row without identity fields may be migrated only when its `claim_fragment` equals one complete, unique claim; a short substring is never a migration key. If no unique full identity exists, quarantine that review or inventory row, keep the real source proposition as `unknown`, record an alignment warning and continue the article; never transfer a verdict or evidence by row number or shared wording alone. This step never upgrades a claim and never opens a human-review queue. Before an article unit may be labeled `fully_supported`, perform a supported-unit completeness check against its original text: identify every independent factual predicate in the unit and confirm that each has a corresponding atomic claim. An uncovered predicate means the unit cannot be green; split and judge it, or retain the unresolved unit as `unknown` with a coverage warning. It adds second-pass-only claims to the frozen denominator as `unknown`, retains `entailed` only when both evidence judgments independently agree and every material semantic component is evidenced, and retains `contradicted` only when both evidence judgments independently identify an explicit incompatible statement for the same object, scope, condition and time/version. Any inventory disagreement, missing component, templated challenge or unresolved compound claim becomes `idk / unknown / unsupported`:

   Before renumbering and before rendering, the reconciler applies the same structural guard to both passes' claims, review rows, and inventory: a proposed atomic `claim` that is only a question ending in `?` or `？` is removed from the denominator and recorded in `reconciliation.pure_question_claim_ids_excluded` / `pure_question_inventory_ids_excluded`. This catches a pure FAQ question mistakenly emitted by the first pass as well as one added by the second pass. A question containing a factual premise must instead be rewritten by the claim pass as a declarative premise; factual claims from the FAQ answer remain eligible.

   `python scripts/reconcile_article_audit.py --result-dir <result-dir> --article-id <article-id>`

   If the second pass marks `split_required`, automatically split and renumber the claim set, rebuild the coverage review and adversarial review, and rerun reconciliation. Do not ask a human to decide ordinary atomicity or entailment. If an automatic second attempt still cannot produce an atomic claim, retain the unresolved content as `unknown`, record the warning, and continue.
9. Run the renderer once for the whole batch:

   `python scripts/render_article_audit.py --input-dir <work-output-directory> --output-dir <final-output-directory>`

10. Inspect the generated Markdown and HTML. The renderer also verifies current source files, schema versions, exact quotations, v0.5 evidence boundaries, managed quality gates, and import-sensitive summary formatting. On a first managed render into the new result directory, the renderer may perform and persist reconciliation if the explicit command was missed, but only when the real independent adversarial-review artifact already exists. It must never manufacture that review from an empty shell. If an older result says reconciliation is complete but still contains a pure FAQ question in `claims`, `claim_reviews`, or `claim_inventory`, the renderer automatically reruns the structural cleanup and persists the repaired JSON before validation; clean reconciled results remain idempotent. Rendering an existing result to a separate review directory remains read-only. Fix any identity, quotation or structure failure and rerun. Semantic uncertainty is resolved to `unknown`; it is not a reason to suppress the reports.

### Coverage completeness gate

Claim coverage is not a score-optimization step. Faithfulness has no target value: 0%, a low percentage, or only one supported claim can all be valid outcomes. Never omit, reclassify, or merge semantically distinct claims to increase the percentage; applying the pre-evidence homogeneous-enumeration rule is correct boundary selection, not score optimization. Never treat the percentage as article quality, publication eligibility or a writing-retry trigger.

The two independent unit classifications and atomic inventories are mandatory. The union is frozen before evidence retrieval or semantic verdicts. If either pass identifies a verifiable relationship, the unit enters the denominator. Only a two-pass agreement on a permitted non-claim category can exclude it. Rule signals in the preparer and reconciliation script are a final safety net, not a substitute for the second full-article pass. If the safety net must preserve a whole unit because stable atomic splitting is unavailable, keep it yellow and emit a coverage warning that the denominator may still be conservative.

Before writing judgments, perform a second, independent coverage pass over every prepared article unit. The pass must challenge every proposed `non-factual` classification and identify factual premises hidden in advice, tables, comparisons, process descriptions, and cause/effect explanations. Every managed v0.6 result must include a complete `coverage_review`, regardless of factual-content share. Each excluded unit needs a fixed exclusion category and a unit-specific reason; absence of evidence is never a valid reason to call a statement non-factual. Every excluded unit with candidate evidence must be explicitly reconsidered. This is a completeness safeguard, not a replacement for semantic judgment or the claim-level score.

### Managed adversarial quality gate

Protocol 2.0 stores the blind adversarial challenge in a separate JSON artifact. The same-model `quality_review` field is retained only for legacy protocol 1.0 compatibility and is not sufficient evidence of independent review.

Human-facing colors are fixed: `entailed` / 明确支持 is green, `unknown` / 尚未确认 is yellow, `contradicted` / 明确冲突 is red, and headings or other non-claims are gray and excluded from the denominator. Never use a single “unsupported” color in human reports.

For protocol 2.0, `semantic_status=entailed` is the only state that may produce the import verdict `supported`; `contradicted` and `unknown` remain separate human meanings even though both map to the machine compatibility field `unsupported`. `unknown` means the supplied attachment does not currently confirm or refute the claim. `contradicted` means allowed evidence explicitly states an incompatible fact for the same object, scope, condition and time/version, and both passes confirm that conflict. Absence of evidence, partial coverage, ambiguity, reviewer disagreement or a different version is `unknown`, never `contradicted`. A topic match, common-sense inference, or a source that merely lists materials, sizes, categories, or process steps is not entailment without the claimed relationship, scope, condition, or effect being stated.

Every protocol 2.0 adversarial row must record an independent semantic status, an atomicity review, and structured entailment checks for subject/object, predicate/relationship, scope/condition, quantity/time/version and causal/effect content. `covered` requires exact fragments from both the claim and its cited evidence; `not_applicable` is allowed only when that component is absent from the claim. The reconciliation script keeps identity, quotation and schema defects as hard failures, but converts semantic-quality defects into a final `unknown` result. Templated reasons, short explanations, missing checks, disagreement or an unresolved compound therefore do not stop Markdown/HTML/summary generation and do not create a human-review queue; they also cannot remain `entailed`. When every claim remains supported, add `all_supported_challenge`; its absence is a warning, not a reason to suppress the report. Only unreadable or identity-inconsistent inputs remain hard failures.

## Mandatory outputs

Produce all of the following:

1. `<article-id>_faithfulness_details.md`: one row per factual claim.
2. `faithfulness_summary.md`: one article per row, with explicitly supported, total verifiable, unknown, contradicted and knowledge-attachment support percentage. Preserve the import-sensitive first seven column positions, but use human-readable labels and show the two non-green states separately. Do not display the DeepEval-compatible percentage in this human report.
3. `faithfulness_highlight.html`: all articles in one interactive page. Use four human states only: gray non-claim / not counted, green `entailed` / 明确支持, amber `unknown` / 尚未确认（证据不足）, and red `contradicted` / 明确冲突. Keep machine `verdict=unsupported` and DeepEval `yes/no/idk` inside JSON/CSV compatibility artifacts; do not display them, the DeepEval-compatible percentage, or a combined unsupported filter in the human page. Provide separate filters for unknown and contradicted. Keep each article claim, semantic result, reason, and source quotation together in the same expandable card. Do not use a detached source panel fixed at the top or upper-right.
4. Preserve the prepared and judgment JSON files as the audit trail.

## Non-negotiable rules

- Exclude the article title, headings, table headers, URLs, navigation, metadata, prompts, and administrative audit text from the denominator. A prepared `unit_type=title/heading/table_header` is a structural exclusion, receives no evidence candidates, cannot own a claim even with `heading_override_reason`, and remains visible only as a gray structural unit in details and Highlight. Table data rows remain auditable.
- Exclude pure Markdown image syntax, image paths and image alt text before article-unit creation. Surrounding prose on the same line remains auditable after the image syntax is removed.
- In managed mode, a plain first body line identical to the recorded article title is excluded during preparation. Isolated unmarked heading-like lines cannot be claimed as facts without a concrete `heading_override_reason`; this override applies only when the source supplied no structural heading marker.
- A pure question can never be a claim. If a question contains an asserted factual premise, extract only that premise as a declarative atomic claim and explain the derivation.
- In managed v0.6 mode, missing, repeated, or reversed article-body markers are a hard stop. Do not infer a body from nearby headings; require the current knowledge bridge to regenerate `40_最终文章.md`.
- Use factual claims, not words or sentences, as numerator and denominator.
- Split compound statements whenever their parts can receive different verdicts. Explicitly inspect `and / but / because / so / while / if / therefore`, Chinese equivalents, colon/list structures and multiple predicates; these are review signals, not automatic split points.
- Treat a homogeneous enumeration as one set-valued claim when all members share subject, predicate/operation, scope, condition, time and modality. Separate enumerability alone is not a split reason. Split different objects, relationships, conditions, quantities/times, effects or causal assertions that can receive different evidence or verdicts.
- Decide enumeration boundaries before retrieving evidence. Do not merge truly different claims to lower the denominator, and do not split a shared list merely because it contains many members.
- Do not retain a compound-looking or homogeneous-list statement as one claim unless `atomicity_note` explains why it contains exactly one independently judgeable set-valued proposition and the blind atomicity review confirms a proposition count of one.
- For every unit proposed as `fully_supported`, reverse-check the original unit for an independent factual predicate without a matching claim. If one exists, split it before evidence judgment; if it cannot be stably split, the unit is `unknown` and carries a coverage warning. A green unit means all of its independently checkable factual predicates entered the frozen inventory and were independently entailed.
- A list item that lacks its own predicate must inherit the minimum semantic context from its lead-in sentence before it can enter the denominator. Preserve the list item as the article quote, state the inherited context in `derivation_note`, and do not manufacture an unsupported predicate that the lead-in never supplied.
- Judge a retained set-valued claim as `entailed` only when the evidence covers every material asserted member. Partial member coverage is `unknown`; an explicit same-scope conflict for an asserted member may make the whole set claim `contradicted`, with the reason naming that member.
- Count a supported paraphrase as supported; verbatim overlap is not required.
- Mark a real-world claim `unknown` when the supplied knowledge context does not confirm or refute it; use `contradicted` only for an explicit same-scope conflict confirmed by both passes.
- Require at least one exact knowledge quotation for every supported claim.
- For current `30_本篇知识库资料.md` in v0.5/v0.6, cite only a complete line range inside an explicit evidence body in sections 1-3. Do not cite metadata, direct writing facts, English-expression tables, outline guidance, controls or gap-handling text.
- Do not search for or cite Formal Claim IDs in `30`. The receiving Skill maps evidence-body line ranges through sibling `35_写作素材来源索引.md`, which is not part of retrieval context.
- Do not use partial credit. Split the claim, then use only `supported` or `unsupported`.
- Compute the score only as `supported claims / all factual claims`. Exclude non-factual language before creating claim rows.
- Keep two layers visible in human-readable reports: (a) article content-unit classification, where every prepared `articleUnit` is labeled verifiable or non-claim and verifiable units distinguish all entailed, entailed plus unknown, all unknown, and any contradicted; and (b) the atomic claim-level knowledge-attachment support rate above. Do not call an all-unknown unit “不支持” or visually equate it with a contradiction. Do not confuse verifiable-content share with Faithfulness or source-text coverage.
- In the highlighted HTML, render each prepared article unit (sentence, list item, or table data row) as its own visible card. If several units originate from one Markdown line, do not merge them back into one visual block. Show the unit's source line, `unit_id`, semantic aggregate and atomic-claim count; keep non-claim units visibly muted, use amber for unknown and red only when a contradiction is present, and provide filters for all, verifiable, entailed, unknown, contradicted, and non-claim content. Table fields may remain atomic claims, but each surrounding content-unit card must show its own aggregate status.
- Preserve the original summary table columns and values because `manage-article-knowledge v0.5` validates them. Add diagnostics after the stable table rather than replacing or reordering its columns.
- Do not silently omit difficult, ambiguous, or weakly supported claims.
- Same topic is never semantic entailment. Evidence must cover the claim's key object, asserted relationship/action, material scope/condition, quantity/time/version and claimed causal/effect relationship. A process name, category list or nearby business concept cannot support details that the evidence does not state.
- Protocol 2.0 final artifacts must contain `reconciliation.gate_version: "1.0"`. Reconciliation may retain or downgrade a first-pass result but must never upgrade it.
- Protocol 2.0 handoff must contain one deduplicated proposition per source unit/proposition key. Every adversarial row must repeat the corresponding final proposition's `unit_id`, exact `article_quote`, and complete `claim`; these fields, not `claim_id` or `claim_fragment`, establish identity. `claim_fragment` must belong to that claim but may be short or repeated because it only explains the checked component. Every nonempty `matched_claim_id` must resolve to the same full proposition identity. The reconciler should auto-rebind a unique identity and quarantine an ambiguous or unrelated row as `unknown`; the final handoff must never preserve a cross-proposition mapping.
- Do not classify a factual statement as non-factual because retrieval returned no candidate or the supplied knowledge lacks support; classify the statement first, then mark it unsupported when evidence is absent.
- Do not reuse one generic verdict, non-factual exclusion reason, or challenge reason across an article. Each reason must identify the specific unit classification, the relationship between that claim and its evidence, or the precise lack of support.
- Do not blend this score with RAGSEO exact-text citation rate or any word-coverage rate.

## Handoff

在向`manage-article-knowledge`交接黄色/红色主张的治理归组时，不改变Faithfulness的分母、证据判断或三态：先逐条比较已有正式Claim与本篇`30`证据范围。已有正式知识确能覆盖但未进`30`的是资料包覆盖遗漏；真正缺证的主张按可由同一组证据回答的稳定知识问题归组。仅同属“通用包装方法”或同一行业不能合为一个缺口；一个问题可含任意数量的相关claim。已解决缺口只覆盖其RES和正式Claim明确回答的范围，不能将新黄色主张直接附到终态ID并沿用“已解决”；同一问题的新边界请知识库Skill明确重开，不同问题另建gap_key。处置JSON由接收方生成，公共缺口组需逐claim_id写明与同一具体问题的关系；本Skill不直接修改知识库治理台账或把后续新证据追溯为本次附件支持。

Tell the user which files were treated as articles and knowledge context, how many claims were counted, the resulting score, and where the three human-readable outputs were saved.

For `manage-article-knowledge v0.6` or v0.5, hand off these files without editing them:

- `<article-id>-prepared.json`;
- `<article-id>-judgments.json`;
- `faithfulness_summary.md`.

In v0.6, keep the three core files in the supplied `result_dir`; protocol 2.0 also requires `<article-id>-adversarial-review.json` beside them. The receiving Skill reads that directory and performs the import. Also report the article file and every knowledge file in the exact order stored in `prepared.json`. The importer must receive repeated `--knowledge` arguments in that same order. Detailed Markdown and HTML remain audit outputs and are not required for import. Do not write directly to the knowledge project's Faithfulness CSV files; the receiving Skill owns the import.

## Maintenance

Faithfulness 只交付当前文章的 prepared/judgments/summary/HTML 产物，不直接编辑知识库治理台账、KMR 维护清单、Claim、RES、GAP、CUS、ANM 或 SKFB，也不重算历史文章结果。黄色/红色逐 Claim 的状态、证据缺口和回链交给 `manage-article-knowledge`，由其按维护入口写入清单并在人工补充后重新整理。

Update `CHANGELOG.md` whenever behavior, schemas, validation rules, output contracts, or compatibility claims change. Keep the newest released entry first and record the date, user-visible effect, and relevant compatibility impact.
