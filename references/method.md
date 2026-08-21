# 评审口径与判断文件格式

## 1. 唯一计算公式

复现 DeepEval Faithfulness 的核心步骤：先从文章正文提取事实主张，再用同一个评审者逐条判断其是否得到 `retrieval_context` 支持。

```text
Faithfulness = supported factual claims / total factual claims
```

本 Skill 中的 `retrieval_context` 仅指实际交给写作Codex的、由用户明确提供的事实知识文件。对`manage-article-knowledge v0.5`，固定且只能是当前文章任务中的`30_本篇知识库资料.md`；不能追加`35`、Formal Claim、整个项目知识库或原始随文事实文件。随文事实文件应先由知识库流程合并进当前`30/35`。

## 2. 分母：事实主张总数

事实主张是可以单独判断真假的最小陈述。

- 忽略标题、各级小标题、网址、目录、元数据和操作说明。
- 忽略纯问题、纯建议、行动指令、修辞句和没有事实断言的条件句。
- 建议句中如果包含事实理由，只提取其中的事实理由。
- 一个句子含多个可分别判断的事实时，拆成多个原子主张。
- 不按句号机械计数，也不把多个不同事实合成一个主张。

示例：

```text
原句：Model A uses a xenon lamp and covers 190–900 nm.
主张1：Model A uses a xenon lamp.
主张2：Model A covers 190–900 nm.
```

## 3. 分子：得到支持的事实主张数

只使用两种计分结论：

- `supported`：知识库原文直接陈述该事实，或在不增加实质信息的情况下能够推出该事实。
- `unsupported`：知识库未提供依据、依据不足、只支持一部分、文章扩大了范围，或知识库与文章矛盾。

不要设置部分分。遇到部分支持的复合陈述，继续拆分原子主张。

改写、同义替换和语序变化可以判为 `supported`。与知识库逐字相同不是条件。反过来，一条说法即使符合常识，只要提供的知识库没有支持，也判为 `unsupported`。

## 4. 证据要求

每个 `supported` 主张必须提供：

- 知识文件绝对路径；
- 原始行号范围；
- 从知识文件逐字复制的连续原文；
- 一句简短理由，说明原文如何支持文章主张。

`unsupported` 主张可以记录最接近但不足以支持的原文；如果完全没有相关原文，`evidence` 使用空数组，并明确写“提供的知识文件中未找到支持证据”。

对当前`30_本篇知识库资料.md`：

- 只把第1至第3节中明确标题为`证据正文（供Faithfulness核验）`的内容作为正向证据；证据引用的起止行必须完整位于同一个证据正文块内。
- 直接写作事实、英文表达表、数据展示表本身、按大纲使用、生成控制和缺口处理都不能作为证据；表格事实必须引用其配套证据正文。
- `30`不显示Formal Claim ID。审核Skill不读取`35`，接收方在导入时按证据引用区间与`35`映射区间的重叠关系确定Formal Claim。

文章定位字段 `article_quote` 也必须从文章正文逐字复制，并尽量使用包含该原子主张的最短连续片段。不得把改写后的主张文本冒充文章原句。这里的“逐字”指原始Markdown文件中的连续子串；若引用范围包含`**`、反引号、链接语法或引用符号，必须原样保留这些字符。知识证据`quote`遵循同一规则。

## 5. 判断 JSON 格式

为每篇文章创建 `<article-id>-judgments.json`：

```json
{
  "schema_version": "1.0",
  "evaluation_mode": "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）",
  "article_id": "ART-001",
  "claims": [
    {
      "claim_id": "C001",
      "unit_id": "U001",
      "article_line": 19,
      "article_quote": "A double-beam instrument divides the source",
      "claim": "A double-beam instrument divides the source.",
      "verdict": "supported",
      "reason": "知识库明确描述了光源被分成样品光路和参比光路。",
      "evidence": [
        {
          "source_file": "D:/knowledge/input.md",
          "line_start": 82,
          "line_end": 82,
          "quote": "The light beam is split into a sample beam and a reference beam."
        }
      ]
    }
  ]
}
```

约束：

- `claim_id` 按文章顺序使用 `C001`、`C002`……且不得重复。
- `unit_id` 必须对应 prepared JSON 中的文章单元。
- `article_line` 必须等于该单元的原始行号。
- `article_quote` 必须是该文章单元的连续原文子串。
- `verdict` 只能是 `supported` 或 `unsupported`。
- `supported` 的 `evidence` 不得为空。
- 证据 `quote` 必须逐字存在于标注的知识文件行号范围内。
- prepared与judgments的`schema_version`当前固定为`1.0`。
- v0.5终稿中的`文章ID`优先于通用文件名；显式传入的ID不得与元数据冲突。

## 6. 报告字段

明细表固定包含：主张编号、文章行号、文章原文、拆分后的事实主张、判断、知识库原文、来源文件与行号、判断理由。

整体统计表固定为一篇文章一行，包含：文章编号、文章标题、支持主张数（分子）、全部事实主张数（分母）、不支持主张数、Faithfulness 百分比、评审模式。

百分比保留两位小数。分母为零时显示 `N/A`，不得显示 0% 或 100%。

人类可读报告同时提供一层内容单元诊断，但不改变上述 claim-level 计算：以 prepared JSON 的每个 `articleUnit` 为一个内容单元；有一条或多条原子主张的单元标为“事实”，没有主张的单元标为“非事实，不计入”。事实单元再按其下主张标为“全部支持”“部分支持”或“完全不支持”。“事实内容占比”=事实单元数÷全部内容单元数，仅用于解释正文中哪些内容进入事实审核，不等于 Faithfulness，也不等于原文引用率。表格字段仍可在原子主张层逐项核验，表格所在内容单元同时显示聚合状态。

HTML 应展示完整文章内容，并直接按 prepared JSON 的最小内容单元逐条渲染：句子、列表项或表格数据行各自成为独立卡片；同一原始Markdown行中的多个单元不得重新合并。每个卡片显示原始行号、`unit_id`、事实/非事实状态、支持聚合状态和原子主张数量。非事实单元灰显，事实单元按支持状态着色，并提供“全部/事实/全部支持/不支持/非事实”筛选。Markdown 汇总保留原有导入主表列顺序，在其后追加内容单元诊断表；不得替换或重排 `manage-article-knowledge v0.5` 校验的主表。

汇总表单元格不得包含会被导入器误拆列的原始英文竖线`|`；渲染器会把正文中的竖线转成HTML实体。交接时保留原始`faithfulness_summary.md`，不要手工重排表格。

## 7. manage-article-knowledge v0.5交接

v0.5导入必需文件是：

1. `<article-id>-prepared.json`；
2. `<article-id>-judgments.json`；
3. `faithfulness_summary.md`。

导入时还需要当前`40_最终文章.md`、实际审核过的全部知识文件、文章版本和完成日期。知识文件参数顺序必须与prepared中的`knowledge_files`一致。终稿或任一知识文件改变后，旧结果失效，应重新审核而不是修改旧JSON。
