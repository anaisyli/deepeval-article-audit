# manage-article-knowledge v0.6 受管审核适配说明

本文件只保留 Faithfulness 一侧的执行说明，不复制知识库的完整交接合同。

## 唯一合同来源

合同由 `prepare_article_audit.py` 自行定位、读取和校验；审核时不需要打开知识库 Skill 的 SKILL.md、`handoff-contract.json` 或 `skill-integration-handoff.md`。

不得把某台电脑上的绝对安装路径写进本 Skill。脚本按以下顺序发现知识库 Skill：显式
`--manage-skill-root`、环境变量 `MANAGE_ARTICLE_KNOWLEDGE_SKILL`、当前用户的标准 Skill
目录、当前调用方明确提供并已校验的Skill目录。不得扫描整块磁盘或假定固定盘符。无法找到唯一合同、合同无效或合同未把执行器固定为
`deepeval-article-audit` 时 prepare 报错并停止，不退回未经确认的“外部流程”；只有这时才需要用 `--manage-skill-root` 指定知识库 Skill 目录。

## Faithfulness 本地职责

收到知识库发出的 `faithfulness_request` 后，本 Skill：

1. 校验 `handoff_contract_version` 与当前唯一合同兼容；
2. 校验 `project_id`、文章ID、文章版本、当前 `40`、当前 `30` 和版本专属结果目录；
3. 要求当前`40`包含唯一的正文开始/结束标记，只审核标记之间的正文文字、列表和表格；
4. 只用当前 `40_最终文章.md` 与当前 `30_本篇知识库资料.md` 执行审核；
5. 对每个正文单元完成 `coverage_review`；新协议2.0为每条可验证主张记录DeepEval `yes/no/idk`、严格语义状态和最终导入verdict，并按`review_policy: decisive_recheck_v1`只对第一轮判为明确支持或明确冲突的主张，在独立`<文章ID>-adversarial-review.json`中做看不到第一轮结论的第二次证据判断（最好在独立子代理或新会话中）；明确支持需逐项检查原子性、对象、关系、范围/条件、数量/时间/版本和因果/效果，明确冲突需核对冲突证据与对象/范围/时间对齐；第一轮尚未确认的主张不复核，保持unknown；
6. 运行`reconcile_article_audit.py`自动收敛。只有双轮一致且全部适用语义组件有原文证据覆盖时保留entailed，冲突同理；应复核却缺复核、分歧、缺项、模板化复核或未解决复合主张自动降为unknown。不得中途停止或建立人工逐条队列，也不得升级第一轮结论；
7. 先由两轮独立阅读全文识别原子主张并取并集，冻结分母后再完成标题、问题、原子性、非主张分类、三态语义判断和证据片段校验，随后生成 prepared、judgments、独立adversarial review、`faithfulness_summary.md`及人类可读报告。知识附件明确支撑率是唯一正式指标；DeepEval默认兼容率只保留在机器JSON/CSV，不进入人读报告。
8. 返回同一合同版本的 `faithfulness_completed`。

若知识库返回的是`writing_retry_required`，说明重做正文与基线完全相同，控制权仍在写作流程。本Skill不得启动审核、生成结果目录或把该事件解释为失败；待写作Skill交付发生变化的正文并由知识库重新发出`faithfulness_request`后再运行。

受管命令固定为：

```text
python scripts/prepare_article_audit.py \
  --article <40_最终文章.md> \
  --knowledge <30_本篇知识库资料.md> \
  --project-id <项目ID> \
  --article-id <文章ID> \
  --article-version <文章版本> \
  --handoff-contract-version <知识库交接合同版本> \
  --result-dir <结果根目录>/<项目ID>/<文章ID>/v<文章版本>/

python scripts/reconcile_article_audit.py \
  --result-dir <结果根目录>/<项目ID>/<文章ID>/v<文章版本>/ \
  --article-id <文章ID>
```

渲染完成后的结构化回执必须包含：

```text
handoff_event: faithfulness_completed
handoff_contract_version: <与请求相同的版本>
result_dir: <当前文章版本专属目录绝对路径>
article_id: <文章ID>
article_version: <文章版本>
task_dir: <知识库文章任务目录，即 40_最终文章.md 所在目录；可选>
```

`task_dir` 是可选字段，由 prepared 中的 `article_file` 推出；知识库用它直接运行
`import_faithfulness.py --task-dir <task_dir>`，不必再查布局文档拼路径。

`faithfulness_completed` 只是交给知识库导入的中间事件。知识库再次校验合同版本、当前
`40/30`、身份、哈希和结果 schema 后执行导入；只有知识库返回 `article_completed` 才表示整条
文章流程结束。

知识库导入器接受 `decisive_recheck_v1`，并继续接受历史 `two_pass_consensus`、`risk_weighted_v1` 结果；它把 `reconciliation.policy` 记入 `10_文章Faithfulness明细.csv` 的 `review_policy`、50记录“复核口径”和月报，月内口径混用时月报给出提示。交接合同版本不变。

缺少当前完整`coverage_review`的旧受管结果可在只读复核报告中标记`audit_quality: warning`并保留结果，不创建人工逐条队列。协议2.0当前受管运行若缺少独立adversarial-review，不得生成空壳文件或返回`faithfulness_completed`；必须完成真实第二轮后再继续。旧协议1.0的内嵌`quality_review`只用于兼容读取，不视为独立复核。需要重新确认旧结果有效性时，知识库应递增文章版本或使用独立复核目录发起当前审核；本 Skill 不覆盖旧审核文件，也不直接修改知识库中的current状态。

## 不兼容处理

缺少合同版本、版本不兼容、唯一合同找不到、执行器名称不符、身份/哈希/目录不匹配时，停止当前
审核并返回具体原因。不得猜测字段、扫描宽泛目录、改用另一套本地合同或自行宣布文章完成。
