# 冻结、复核清单与预检模板

受管审核在第一轮抽取和覆盖复查后使用 `scripts/audit_preflight.py`，它从不判断蕴含。

## 紧凑文件（模型只写语义内容）

抽取轮读 `_work/<article-id>-extract-view.md`（抽取写法与正文单元，不含证据），主判断读 `_work/claims.md`（`freeze` 生成，每行一条冻结主张）和 `_work/<article-id>-evidence-view.md`（允许证据），复核读 `_work/recheck.json` 和证据视图；`audit-view.md` 是完整视图，留作记录。模型只写下面三类紧凑 JSON，全部放在结果目录的 `_work/` 下；身份字段、结构单元分类、计数、`matched_claim_id`、`deepeval_verdict/verdict`、证据行号和机器头字段全部由脚本补齐。文件可以是数组，也可以是 `{"verdicts": [...]}` / `{"reviews": [...]}`。

第一轮抽取（`_work/first.json`；v1.8 续跑的旧尝试另有互不可见的 `_work/blind.json`）：

```json
{
  "coverage_review": {"unit_classifications": [
    {"unit_id": "U009", "classification": "non_factual", "category": "transition", "reason": "只承上启下，没有可独立判断的关系。"}]},
  "claim_inventory": [
    {"unit_id": "U002", "article_quote": "A rigid tray keeps the bottle upright."},
    {"unit_id": "U004", "article_quote": "Use a sealed tray to reduce exposure", "claim": "A sealed tray reduces exposure."}]
}
```

视图中每个未标“结构排除”的单元，要么在 `claim_inventory` 里有主张（自动计为可验证，不写分类行），要么写一条 `non_factual` 分类行；两者都没有时 `freeze` 报出该单元。`claim` 与引文（去掉行内 Markdown 后）相同时省略。`derivation_note` 只在推导补全时写；`atomicity_note` 可选，只在复合外观仍保留为一条时写；都不给每条主张写。可选 `reconsidered_candidate_unit_ids`；有候选证据却无主张的单元由脚本自动列入。

主判断（`_work/verdicts-N.json`，可按 `claim_id` 区间分片并行）：

```json
[{"claim_id": "C001", "semantic_status": "entailed", "evidence": [{"quote": "The tray holds the bottle upright"}]},
 {"claim_id": "C002", "semantic_status": "unknown", "gap_type": "effect_or_purpose"},
 {"claim_id": "C003", "semantic_status": "unknown", "gap_type": "no_evidence"},
 {"claim_id": "C004", "semantic_status": "contradicted", "conflict": "证据写的是14天而非7天",
  "evidence": [{"quote": "closes within 14 days"}]}]
```

理由只写在能改变结果的地方（v1.10）：

- unknown 写 `gap_type`：`no_evidence`、`partial_coverage`、`scope_broader`、`effect_or_purpose`、`subject_unclear`、`version_mismatch`，都不合适时写 `other` 并加短语 `gap`（至少6字符）。其他类型可选加一个短语，没有字数要求。组装器生成“允许证据未覆盖：<类型说明>（<短语>）”，在 judgments 中保留 `gap_type`。许多主张共用同一类型是正常的。
- entailed 不写理由，只写 `evidence`；组装器生成“证据正文逐字支持该主张：“<第一条引文>””。想补充时可写短语 `support` 或完整 `reason`。
- contradicted 写短语 `conflict`（至少6字符）或完整 `reason`（至少12字符）。
- 组装器生成的理由标 `reason_source: assembled`。`source_file` 单知识文件时省略，多文件写视图中的别名 `F1`…，也可写视图证据块编号 `K0001`…（引文须在该块内）；写了不属于本篇知识文件的路径，或 `quote` 不是所引文件中的逐字连续片段（例如从另一篇文章的资料里抄来），组装直接报错，被点名的主张须重新判断。行号不写，由组装填写；判为 entailed 的引文必须位于允许证据块内。不写 `coreference_evidence`：引文含 we/our/this 等指代且所在证据块有 EC-1 归属板块时，组装自动附加并当场校验；`check` 并入复核行补引的证据时同样附加；受管渲染遇到支持主张仍缺这类记录时按 prepared 补齐并随 judgments 保存，不再拒绝。每条冻结主张恰好一行，缺行、重复或多余编号都会报错。模型自己写的同一句理由（展开后，并去掉各自的主张ID、主张和引文文本，所以“未能确认主张 C00X：<主张>”这类模板算同一句）用在超过3条 entailed/contradicted 主张上时，组装直接报错，要求逐条判断；复核者自己写的 `challenge_reason` 同样适用。unknown 和组装器生成的理由不做这项检查。

第二次判断（`_work/reviews-N.json`，只覆盖 `recheck-list` 中的主张）：

```json
[{"claim_id": "C001", "independent_semantic_status": "entailed", "atomicity_review": {"status": "atomic"},
  "entailment_checks": {
    "subject_object": {"status": "covered", "claim_fragment": "A rigid tray", "evidence_fragment": "The tray"},
    "predicate_relation": {"status": "covered", "claim_fragment": "keeps the bottle upright", "evidence_fragment": "holds the bottle upright"}}},
 {"claim_id": "C004", "independent_semantic_status": "contradicted", "contradiction_evidence_fragment": "closes within 14 days",
  "contradiction_alignment": {"subject_object": "aligned", "scope_condition": "aligned", "time_version": "aligned"}}]
```

复核看全部允许证据（证据视图），不只看第一轮引用的那一句：用到第一轮没有引用的证据句时，在该行加 `"evidence": [{"quote": "…"}]`（逐字片段，`source_file` 可省略或写 `F1`/`K0001`），`check` 会把它并入主张证据。标为 `covered` 的检查必须有从主张原文复制的 `claim_fragment` 和从本篇知识文件逐字复制的 `evidence_fragment`。只写主张实际含有的维度；主张措辞要求的维度（范围/条件、数量/时间/版本、因果/效果信号）必须写出：covered、missing，或复核认为主张实际不含该维度时写 `not_applicable` 并给出自己的理由（至少8字符，收敛时以复核判断为准，不降级）；省略的其他维度补为 `not_applicable`。`challenge_reason`、`atomicity_review.reason` 和各检查的 `reason` 都可省略，组装按状态和片段补写（entailed 用 covered 片段对，contradicted 用冲突片段）；`atomic` 的 `independent_proposition_count` 可省略（记为1）。只有两处要写自己的理由：主张措辞像复合命题时判 `atomic`，`atomicity_review.reason` 至少12字符；见上文，判措辞要求的维度 `not_applicable`，理由至少8字符。缺任何一项时 `assemble-review` 一次列出全部缺项并拒绝写入；复核结论本身（`split_required`、某维度 missing）不是缺项，收敛时降为 unknown。全部冻结主张都在复核清单里且全部判 entailed 时，组装器自动补 `all_supported_challenge`；自己写在文件对象顶层的优先。

```text
python scripts/audit_preflight.py assemble-judgments --prepared P --frozen-inventory F --first-inventory first.json --blind-inventory blind.json --verdicts v1.json [--verdicts v2.json] --output <id>-judgments.json
python scripts/audit_preflight.py assemble-review --prepared P --frozen-inventory F --blind-inventory blind.json --recheck recheck.json --reviews r1.json [--reviews r2.json] --output <id>-adversarial-review.json
python scripts/audit_preflight.py view --prepared P --output _work/<id>-audit-view.md   # 同时写 extract/evidence 两个分视图
```

收敛之前，改正紧凑文件后可用同一 assemble 命令重新组装：旧输出及由它派生的文件（重组 judgments 时还有 adversarial-review、recheck 和 check 回执）移到 `_work/superseded-N/`；收敛后拒绝重新组装，只能 reset。`judgments` 与 `adversarial-review` 输出到结果目录根部，其余（frozen、replacements、recheck、receipt）都写在 `_work/`；受管渲染成功后整个 `_work/` 被删除。

## status（续跑先看进度）

`python scripts/audit_preflight.py status --result-dir <result-dir>`

只读。按运行顺序报告 prepare、view、第一轮抽取、覆盖复查（v1.8 旧尝试为第二轮盲抽取）、freeze、主判断分片、assemble-judgments、recheck-list、复核分片、assemble-review、check、收敛、渲染各阶段是否完成，并列出已有分片和仍缺主判断/复核的主张ID区间（如`C004, C006-C007`）、check回执的`next_action`。不输出任何文件正文。中断、暂停或换会话后先运行它，从`next_stage`继续，只读取该步需要的文件；已完成阶段的产物不为确认进度而重读。根目录出现的其他文件列在`other_files`。渲染前若当前文章单元、30证据块/行锚/允许范围/身份上下文与prepared不同，或`_work`中的文件来自另一套输入，`next_stage`为`input_snapshot_changed`并在`snapshot_conflicts`列出原因：停止审核并交回知识库，不删除或重建prepared，也不改名重新冻结。仅原始SHA-256不同但上述解析输入相同的序列化差异可继续审核；原始SHA仍保留在prepared用于追溯。

## reset（渲染前重做）

`python scripts/audit_preflight.py reset --result-dir <result-dir>`

导入前，某次审核因冻结后才暴露的问题无法正确完成时（门禁点名已冻结记录），用它把根目录除prepared外的文件（含未导入的报告）及`_work`中除阅读视图外的过程文件移到`_work/attempt-N/`，保留prepared并按需重建阅读视图（输入保持锁定），然后从第一轮抽取和覆盖复查重新开始；归档内容不得复制或迁移到新一轮。已经主判断的尝试不得改抽取轮，`freeze`会拒绝并提示reset。已导入（有`article_completed.json`）的结果拒绝重置，须由知识库建立新文章版本。

## freeze

v1.9 默认流程：`freeze --prepared P --first-inventory _work/first.json --coverage-review _work/coverage.json --output _work/frozen.json`。覆盖复查文件格式同上，但 `unit_classifications` 只写第一轮判为非主张、复查也同意排除的单元（自己的类别与理由），`claim_inventory` 只写第一轮漏掉的主张；第一轮判为非主张的单元，复查必须二选一（写分类行或写主张），否则 `freeze` 列出这些单元并拒绝；复查给第一轮已有主张的单元写分类行，或对同一单元既写主张又写分类行，也被拒绝。`freeze` 据此写出 `_work/blind.json`（第一轮主张加复查补充，`derived_from: coverage_review`），后续 `assemble-*` 照常用 `--blind-inventory _work/blind.json`，不要手改它。一个单元只有第一轮和复查都判为非主张才排除；第一轮主张记为 `both`，复查补充记为 `blind_only`。覆盖复查模式不做下面的两轮相同检查、措辞配对和分歧门禁。

以下针对 v1.8 两轮盲抽取（`--blind-inventory`，只用于续跑旧尝试）：保存正文单元、完整命题身份、两轮来源和显式语义替换；不保存 verdict、证据或第一轮结论。两轮清单的命题（单元、引文、主张）完全相同且不少于5条时拒绝冻结：这说明 blind 轮不是独立结果，应在新子代理或新会话中重做该轮。重新冻结后主张ID会变，所有主张都要重新做主判断和复核，不能把旧结论按文本迁移到新ID上。两轮措辞不同的同一命题由 `freeze` 自动配对合并：同一单元内，一条主张的实词（去掉冠词、介词等虚词，英文按词干、中文按相邻两字）至少75%出现在另一轮的某条主张里，且两条的否定词和数字完全一致，就一对一配对（英文所有格的 's 等单个字母不算实词；一个单元里某一轮有3条以上主张时，该轮在其中至少3条、且至少一半里重复出现的词视为框架词，如公司名、“流程”。两条主张在框架词之外各有实词却一个也不相同时不配对；能配多条时优先选框架词之外重合最多的），保留实词更多（更完整）的措辞，来源记为两轮，另一条写入 `replacements`（`pairing: content_word_overlap`），`assemble-review` 不再把它列入复核清单。命令行输出的 `reworded_pairs` 是配对数。配对之后，两轮仍在超过3个且超过五分之一的有主张单元里各自给出对方没有的命题时拒绝冻结：并集会把同一事实的不同拆分算两次。P-005 v4 两篇按原文措辞比对为 41/54、42/48 个单元，配对后为 5/54、2/48。仍超限时应在新子代理中重做一轮，或用 `--replacements` 把复合句映射到保留的命题上。不需要为了配对统一两轮的措辞。一轮省略 `review_policy` 时按当前默认口径处理，只与声明了历史口径的另一轮冲突。某一轮有至少20条主张且超过10%是短语碎片（不足6个汉字且不超过3个英文词，例如按逗号切出的 `size`）时拒绝该轮：主张须是可独立判断的完整命题，抽取是阅读任务，不能用脚本切句。原子边界由抽取轮在看证据前决定；复合外观的主张不在冻结时拦截，由第二次判断的 `atomicity_review` 确认是否只含一个命题。同一轮既给某单元写了主张、又把它分类为 `non_factual` 时，`freeze` 点名该单元并拒绝（两轮都判非主张的单元会被排除，这样的主张以前会被悄悄丢掉）：保留主张就删掉那条分类行，确无事实就删掉主张，再用同一命令重新冻结。两轮可省略结构单元分类与 `inventory_id`，由 `freeze` 补齐；受管运行同时按渲染器口径检查非结构单元的分类类别（允许类别见阅读视图的“抽取轮写法”）、问句/标题类别和理由，问题一次列全；此时尚未判断，只改正被点名的行，再用同一命令重新冻结（覆盖 `_work/frozen.json`；开始判断后 `freeze` 拒绝并提示 reset）。脚本拒绝不是停止审核的理由。两轮清单必须声明同一个 `review_policy`（或都不声明），冻结结果原样记录该口径。清单项缺 `article_line` 时按 `unit_id` 自动填写；已填但与单元行号不符仍报错。每条冻结命题都必须完成一次主判断。历史 `risk_weighted_v1` 清单的风险标签按并集合并。

## recheck-list

`python scripts/audit_preflight.py recheck-list --judgments <judgments.json> --output <recheck.json>`

仅用于 `decisive_recheck_v1`：列出第一轮判为 `entailed` 或 `contradicted` 的主张，只含 `claim_id/unit_id/article_line/article_quote/claim`，不含状态、证据或理由。把它连同允许的知识文件交给独立子代理或新会话做第二次判断。已完成收敛的结果不能再生成清单。

## check

读取同版本的 prepared、冻结清单、主判断和复核文件，只做确定性检查：文章/知识哈希、逐字引用、行号、允许证据范围、身份唯一性、组件记录是否有对应证据。

- 应复核集合由口径决定：`decisive_recheck_v1` 为第一轮 entailed/contradicted；第一轮 unknown 不需要复核行，出现复核行即报错。历史 `risk_weighted_v1` 为高风险主张；`two_pass_consensus` 为全部主张。应复核却缺行时进入 `review_tasks`（`missing_independent_review`）。
- 证据 `line_start/line_end` 缺失或无效时，若引文在该知识文件的允许证据块内唯一出现，自动填写并记录 `citation_line_located`；在允许证据内出现多次或只在块外出现时不补。
- 能从复核记录找到同一允许正文、仅是 evidence 列表遗漏时自动补齐并记录 `citation_list_omission`。`review_tasks` 只列记录问题（缺复核行、引文不在允许证据内、手写记录缺字段），不列复核结论；复核结论在收敛时降为 unknown。

回执固定字段：`terminal=false`、`next_owner`、`automatic_repairs`、`review_tasks`、`next_action`、`resume_condition`、`ready_for_reconciliation`。`review_records_once` 是审核方内部恢复动作，不是人工阻塞：改正紧凑文件、重新组装后再运行 check；仍有未解决项时用 `--attempt 1` 收敛，这些项以 unknown 输出。已完成的历史结果不可原地修复，必须新建文章版本。

语义替换模板：

```json
{
  "source_ref": "first:I001",
  "replacement_refs": ["blind:I014", "blind:I015"],
  "all_predicates_preserved": true,
  "coverage_reason": "逐项核对原始单元中的对象、谓词、范围、条件和时间，确认替代清单完整覆盖原关系。"
}
```

不允许按行号匹配、以 `inventory_origin` 预置 unknown、把复合主张结论复制给拆分项，或用预检脚本生成语义证据。
