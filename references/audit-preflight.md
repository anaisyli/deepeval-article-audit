# 冻结与预检模板

受管审核在两轮全文识别后使用 `scripts/audit_preflight.py`。`freeze` 只保存正文单元、完整命题身份、两轮来源和显式语义替换；它不保存 verdict、证据或第一轮结论。每条冻结命题都必须分别完成两次证据判断。

`check` 读取相同版本的 prepared、冻结清单、主判断和盲审文件。它只做确定性检查：文章/知识哈希、逐字引用、行号、允许证据范围、身份唯一性，以及组件记录是否有对应证据。能从盲审记录找到同一允许正文且仅是 evidence 列表遗漏时，自动补齐并记录 `citation_list_omission`；不能证明的组件、数量、时间、范围和因果关系进入 `review_tasks`，由审核模型依据原文补记录或保持 unknown。

预检回执固定字段：`terminal=false`、`next_owner`、`automatic_repairs`、`review_tasks`、`next_action`、`resume_condition`、`ready_for_reconciliation`。`review_records_once` 是审核方内部恢复动作，不是对人工报告的终止；一次有界复核仍无法解决时，使用 `--attempt 1` 收敛，未解决项继续以 unknown 输出。已完成的历史结果不可原地修复，必须新建文章版本。

语义替换模板：

```json
{
  "source_ref": "first:I001",
  "replacement_refs": ["blind:I014", "blind:I015"],
  "all_predicates_preserved": true,
  "coverage_reason": "逐项核对原始单元中的对象、谓词、范围、条件和时间，确认替代清单完整覆盖原关系。"
}
```

不允许按行号匹配、以 `inventory_origin` 预置 unknown、将复合主张结论复制给拆分项，或用预检脚本生成语义证据。
