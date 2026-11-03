# 完整审核记录格式（参考，不手写）

新运行只写 `audit-preflight.md` 中的紧凑文件，由 `assemble-judgments`/`assemble-review`/`reconcile_article_audit.py` 生成下面的完整记录。本文件只在阅读、排查历史结果或手写旧式完整 JSON 时使用；常规审核不需要读取。

## 1. 完整 `<article-id>-judgments.json`（`assemble-judgments` 生成）

```json
{
  "schema_version": "1.1",
  "evaluation_protocol_version": "2.0",
  "evaluation_mode": "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）",
  "primary_metric": "strict_support",
  "penalize_ambiguous_claims": false,
  "review_policy": "decisive_recheck_v1",
  "article_id": "ART-001",
  "coverage_review": {
    "reviewed_unit_count": 79, "factual_unit_count": 18, "non_factual_unit_count": 61,
    "unit_classifications": [
      {"unit_id": "U001", "classification": "non_factual", "category": "heading", "reason": "该单元是章节标题，只命名后续主题，不陈述产品事实。"},
      {"unit_id": "U002", "classification": "factual", "reason": "包含关于运输和陈列关系的可核验断言。"}
    ],
    "reconsidered_candidate_unit_ids": ["U002"]
  },
  "claims": [{
    "claim_id": "C001", "unit_id": "U019", "article_line": 19,
    "article_quote": "A double-beam instrument divides the source",
    "claim": "A double-beam instrument divides the source.",
    "deepeval_verdict": "yes", "semantic_status": "entailed", "verdict": "supported",
    "reason": "知识库明确描述了光源被分成样品光路和参比光路。",
    "evidence": [{"source_file": "D:/knowledge/input.md", "line_start": 82, "line_end": 82,
                  "quote": "The light beam is split into a sample beam and a reference beam."}],
    "coreference_evidence": []
  }, {
    "claim_id": "C002", "unit_id": "U021", "article_line": 21,
    "article_quote": "The beam split improves accuracy.", "claim": "The beam split improves accuracy.",
    "deepeval_verdict": "idk", "semantic_status": "unknown", "verdict": "unsupported",
    "reason": "允许证据未覆盖：证据没有支持主张中的效果、目的或好处", "reason_source": "assembled",
    "gap_type": "effect_or_purpose", "evidence": []
  }]
}
```

v1.10 起，组装器生成的理由带 `reason_source: assembled`（entailed 由第一条引文生成，unknown 由 `gap_type` 生成），unknown 另存 `gap_type`。复核行中由片段生成的 `challenge_reason` 带 `challenge_reason_source: assembled`，组装器补写的 `all_supported_challenge` 带 `source: assembled`。这些标记只用于跳过重复模板检查；旧结果没有这些字段，照原规则读取。

## 2. 完整 `<article-id>-adversarial-review.json`（`assemble-review` 生成）

```json
{
  "schema_version": "1.0", "evaluation_protocol_version": "2.0", "article_id": "ART-001",
  "review_mode": "independent_adversarial", "review_policy": "decisive_recheck_v1",
  "coverage_review": {"reviewed_unit_count": 2, "unit_classifications": [
    {"unit_id": "U001", "classification": "factual", "reason": "包含可核验的操作关系。"},
    {"unit_id": "U002", "classification": "non_factual", "category": "question", "reason": "只提出问题，没有断言关系。"}]},
  "claim_inventory": [
    {"inventory_id": "P2-C001", "unit_id": "U001", "article_quote": "Use a sealed tray to reduce exposure.", "claim": "Use a sealed tray.", "matched_claim_id": "C003"},
    {"inventory_id": "P2-C002", "unit_id": "U001", "article_quote": "Use a sealed tray to reduce exposure.", "claim": "A sealed tray reduces exposure.", "matched_claim_id": ""}],
  "claim_reviews": [{
    "claim_id": "C001", "unit_id": "U001", "article_line": 12,
    "article_quote": "Another construction changes the filling sequence.",
    "claim": "Another construction changes the filling sequence.",
    "claim_fragment": "changes the filling sequence", "evidence_fragment": "",
    "independent_semantic_status": "unknown", "semantic_status": "unknown",
    "atomicity_review": {"status": "atomic", "independent_proposition_count": 1, "reason": "该主张只断言结构变化会改变填充顺序。"},
    "entailment_checks": {
      "subject_object": {"status": "covered", "claim_fragment": "another construction", "evidence_fragment": "another construction", "reason": "证据覆盖同一结构对象。"},
      "predicate_relation": {"status": "missing", "claim_fragment": "changes", "evidence_fragment": "", "reason": "证据没有说明结构会产生该变化。"},
      "scope_condition": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张没有额外适用条件。"},
      "quantity_time_version": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张不含数量、时间或版本限定。"},
      "causal_effect": {"status": "missing", "claim_fragment": "changes the filling sequence", "evidence_fragment": "", "reason": "证据没有陈述该效果关系。"}},
    "challenge_reason": "虽然证据涉及包装结构，但没有说明结构会改变填充顺序，因此只能判为unknown。"
  }]
}
```

## 3. 收敛写入的记录与历史复核口径

收敛后 judgments 与 adversarial review 都写入：

```json
{"reconciliation": {"gate_version": "1.0", "coverage_gate_version": "1.1", "policy": "decisive_recheck_v1",
  "completed": true, "no_human_queue": true,
  "rechecked_claim_ids": ["C001"], "first_pass_unknown_claim_ids": ["C002"], "missing_recheck_claim_ids": []}}
```

每条 claim 的 `reconciliation` 记录 `review_depth`、`first_pass_semantic_status`、`independent_semantic_status`、`final_semantic_status`、`action`（`retained`/`downgraded_to_unknown`）和 `reasons`。`claim_reviews` 只保留需要复核的主张。这些是审计记录，不改变 `yes/no/idk`、三态和 `supported/unsupported` 契约。

**历史口径**：`two_pass_consensus` 要求每条主张一行复核；`risk_weighted_v1` 要求按 `review_risk_level/review_risk_categories`（类别 `company_or_customer_capability`、`product_scope_or_compatibility`、`quantity_time_or_specification`、`guarantee_or_certification`、`performance_or_outcome`、`safety_or_environment`；高风险至少一类，标准风险为空列表；晚期兜底的未分类主张标 `high` 加 `unclassified` 并保持 unknown）只复核高风险，元数据为 `reviewed_high_risk_claim_ids/single_pass_standard_claim_ids/missing_high_risk_review_ids`。两者的旧结果继续按原规则校验和导入，新运行不再使用。

## 4. 旧协议 1.0 的 `quality_review`

仅读取旧 protocol 1.0 受管结果时使用：`gate_version: "1.0"`，`review_mode: "independent_adversarial"`，`reviewed_claim_count` 等于主张数，`claim_reviews` 与全部 `claim_id` 一一对应，`confirmed_verdict` 与主判断一致，`claim_fragment` 来自该主张，supported 的 `evidence_fragment` 来自已引用证据，`challenge_reason` 具体且不复制原理由；全部 supported 时附 `all_supported_challenge`。理由重复只记 `audit_warnings`，不改分数。缺当前完整 `coverage_review`/`quality_review` 的旧结果不能凭旧100%视为通过，需以新版本重审；只读复核时标 `audit_quality: warning`。协议2.0 的内嵌 `quality_review` 不算独立复核。
