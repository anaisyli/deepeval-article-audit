# manage-article-knowledge v0.6 受管审核适配说明

本文件只保留 Faithfulness 一侧的执行说明，不复制知识库的完整交接合同。

## 唯一合同来源

受管运行时先定位当前已安装的 `manage-article-knowledge` Skill，并读取：

```text
references/handoff-contract.json
references/skill-integration-handoff.md 中的 Faithfulness 交接部分
```

不得把某台电脑上的绝对安装路径写进本 Skill。脚本按以下顺序发现知识库 Skill：显式
`--manage-skill-root`、环境变量 `MANAGE_ARTICLE_KNOWLEDGE_SKILL`、当前用户的标准 Skill
目录、当前调用方明确提供并已校验的Skill目录。不得扫描整块磁盘或假定固定盘符。无法找到唯一合同、合同无效或合同未把执行器固定为
`deepeval-article-audit` 时停止，不退回未经确认的“外部流程”。

## Faithfulness 本地职责

收到知识库发出的 `faithfulness_request` 后，本 Skill：

1. 校验 `handoff_contract_version` 与当前唯一合同兼容；
2. 校验 `project_id`、文章ID、文章版本、当前 `40`、当前 `30` 和版本专属结果目录；
3. 要求当前`40`包含唯一的正文开始/结束标记，只审核标记之间的正文文字、列表和表格；
4. 只用当前 `40_最终文章.md` 与当前 `30_本篇知识库资料.md` 执行审核；
5. 生成 prepared、judgments、`faithfulness_summary.md` 及人类可读报告；
6. 返回同一合同版本的 `faithfulness_completed`。

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
```

渲染完成后的结构化回执必须包含：

```text
handoff_event: faithfulness_completed
handoff_contract_version: <与请求相同的版本>
result_dir: <当前文章版本专属目录绝对路径>
article_id: <文章ID>
article_version: <文章版本>
```

`faithfulness_completed` 只是交给知识库导入的中间事件。知识库再次校验合同版本、当前
`40/30`、身份、哈希和结果 schema 后执行导入；只有知识库返回 `article_completed` 才表示整条
文章流程结束。

## 不兼容处理

缺少合同版本、版本不兼容、唯一合同找不到、执行器名称不符、身份/哈希/目录不匹配时，停止当前
审核并返回具体原因。不得猜测字段、扫描宽泛目录、改用另一套本地合同或自行宣布文章完成。
