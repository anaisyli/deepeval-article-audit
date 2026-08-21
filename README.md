# 文章知识库支持率评审 Skill

`deepeval-article-audit` 用来检查已完成文章中的事实主张，有多少能够由写作时实际提供的知识文件支持。它复现 DeepEval Faithfulness 的核心拆分、判断和计算逻辑，但由当前 Codex 评审，不调用 DeepEval 官方程序、默认评审模型或额外 evaluator API。

当前版本：`v1.1.0`。已与 `manage-article-knowledge v0.5` 的输入边界和结果导入契约完成端到端兼容验证。

所有分数和交付物必须标注：

> DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）

## 它回答什么问题

```text
Faithfulness = 有知识文件支持的原子事实主张数 ÷ 文章全部原子事实主张数
```

它衡量的是“文章事实能否由指定写作上下文支持”，不是原文复制率、连续词覆盖率或文章整体质量。文章可以改写、换序或使用同义表达；只要没有增加实质信息，仍可判为支持。

本 Skill 不替代：

- RAGSEO 逐字引用率、词组覆盖率或查重率；
- SEO/GEO、文风、合规、可发布性或文章整体质量评价；
- 事实真伪的开放网络调查；
- DeepEval 官方运行结果。

## 当前报告采用双层口径

### 1. 原子事实主张支持率

这是唯一的 Faithfulness 主指标。复合陈述会拆成能够分别判断真假的最小主张，每条只使用 `supported` 或 `unsupported`，不计部分分。

例如：

```text
Model A uses a xenon lamp and covers 190–900 nm.
```

会拆为两条事实主张，因此分母增加 2。

### 2. 内容单元诊断

预处理器会把正文确定性切分为可追踪的 `articleUnit`，通常是一句话、一个列表项或一行表格数据。报告标明每个单元：

- 是否包含事实主张；
- 若包含事实，是全部支持、部分支持还是完全不支持；
- 对应的 `unit_id`、原始行号和原子主张数量。

```text
事实内容占比 = 含事实主张的文章单元数 ÷ 全部文章单元数
```

事实内容占比只是正文结构诊断，不是 Faithfulness，也不表示相同比例的文字来自知识库。`v1.1.0` 的高亮网页直接按最小内容单元呈现；同一 Markdown 行内的多个句子会拆成独立卡片，不再合并成一个颜色块。

## 输入要求

最少提供：

1. 一篇或多篇已完成文章；
2. 每篇文章写作时实际允许使用的知识文件；
3. 一个不会覆盖源文件的结果目录。

通用审核可以接收多个明确提供的知识文件。若文章和知识文件存在多种合理配对，且选择会改变结果，Skill 会先询问一个简短问题，不会自行混用。

### `manage-article-knowledge v0.5` 严格配对

| 角色 | 唯一文件 |
|---|---|
| 待审核文章 | `40_最终文章.md` |
| 事实上下文 | 当前 `30_本篇知识库资料.md` |

在 v0.5 模式中只能使用当前 `30` 作为事实输入。不要追加：

- `35_写作素材来源索引.md`；
- Formal Claim 或整个项目知识库；
- 原始随文事实文件；
- SEO/GEO 规则、写作提示或评价说明；
- 网页搜索结果、模型记忆或最终文章本身。

随文事实资料应先由 `manage-article-knowledge` 纳入来源记录、Formal Claim 和当前 `30/35`，再进行审核。

## 在 Codex 中使用

单篇审核示例：

```text
使用 $deepeval-article-audit。
文章文件：D:\项目\04_文章任务\30_等待Faithfulness\ART-001_文章标题\40_最终文章.md
知识文件：D:\项目\04_文章任务\30_等待Faithfulness\ART-001_文章标题\30_本篇知识库资料.md
输出目录：D:\项目外部审核结果
```

批量审核示例：

```text
使用 $deepeval-article-audit，审核这个目录内全部待评审文章。
每个任务目录中的 40_最终文章.md 是文章，
30_本篇知识库资料.md 是唯一事实上下文。
把结果写到新的外部审核目录，不要覆盖源文件。
```

不需要提供额外 API Key；运行消耗当前 Codex 的正常使用额度。

## 审核流程

```text
确认文章与知识文件配对
→ 预处理正文和可用知识块，生成 prepared JSON
→ 逐个检查全部 articleUnit，不抽样
→ 提取每条可独立核验的原子事实主张
→ 只用已提供的知识上下文判断 supported / unsupported
→ 写入 judgments JSON
→ 校验证据原文、行号、schema、文件哈希和 v0.5 证据边界
→ 批量渲染 Markdown 明细、稳定汇总表和交互式高亮网页
```

预处理命令：

```text
python scripts/prepare_article_audit.py \
  --article <article.md> \
  --knowledge <knowledge.md> \
  --output <article-id>-prepared.json
```

批量渲染命令：

```text
python scripts/render_article_audit.py \
  --input-dir <work-output-directory> \
  --output-dir <final-output-directory>
```

实际执行时，评审者必须读取 [method.md](references/method.md) 中的完整判断口径和 judgments JSON 契约。

## 证据判断规则

进入分母的是正文中全部可以独立判断真假的事实主张。标题、小标题、URL、目录、元数据、纯问题、纯指令，以及不含事实理由的建议或修辞不进入分母。

一条主张判为 `supported` 时，必须至少提供一处：

- 知识文件绝对路径；
- 原始行号范围；
- 从知识文件逐字复制的连续原文；
- 原文如何支持主张的简短理由。

文章引用和知识证据都必须是原始 Markdown 的连续子串。若引用范围含有 `**`、反引号、链接语法或引用符号，也必须原样保留。

以下情况判为 `unsupported`：

- 知识上下文完全没有依据；
- 只支持复合主张的一部分且无法进一步拆分；
- 文章扩大了适用范围或增加了新结论；
- 文章与知识上下文矛盾；
- 说法可能符合常识，但本次知识上下文没有证明。

### v0.5 的证据正文边界

审核当前 `30_本篇知识库资料.md` 时，只有第 1 至第 3 节内明确位于“证据正文（供Faithfulness核验）”标题下的内容可以作为正向证据，且引用起止行必须完整落在同一个证据正文块中。

以下内容不能作为支持证据：直接写作事实、英文表达表、没有配套证据正文的数据展示表、按大纲使用建议、生成控制和缺口处理。审核过程不读取 `35`；后续由 `manage-article-knowledge` 使用 `35` 将证据行范围映射回 Formal Claim。

## 输出文件

### 人工可读结果

| 文件 | 内容 |
|---|---|
| `<文章编号>_faithfulness_details.md` | 内容单元诊断，以及一行一条原子事实主张的证据明细 |
| `faithfulness_summary.md` | 每篇文章的分子、分母、不支持数和 Faithfulness；主表后追加内容单元诊断 |
| `faithfulness_highlight.html` | 全文内容单元卡片、状态着色、筛选和同卡片证据展开 |

高亮网页提供“全部、事实、全部支持、不支持、非事实”筛选。非事实单元灰显，事实单元按支持聚合状态着色；证据、判断和理由与对应文章单元保存在同一可展开卡片中，不使用脱离正文的固定证据面板。

### 审计轨迹

```text
<文章编号>-prepared.json
<文章编号>-judgments.json
```

prepared JSON 保存文章单元、知识块、候选证据和输入文件顺序；judgments JSON 保存原子主张、判断、文章原文和证据原文。渲染器会重新读取当前源文件，拒绝已经失效的行号、引文、schema 或证据边界。

### 交给 `manage-article-knowledge v0.5` 的文件

导入必需文件固定为：

1. `<文章编号>-prepared.json`；
2. `<文章编号>-judgments.json`；
3. 原始 `faithfulness_summary.md`。

还要按 prepared JSON 中 `knowledge_files` 的原始顺序提供文章和知识文件。不要手工修改、重排列或美化这三个导入产物；明细 Markdown 和高亮 HTML 是人工审核输出，不是导入必需文件。本 Skill 不直接写入知识项目的 Faithfulness CSV，导入事务由接收方负责。

## 如何理解结果

```text
支持主张数：38
全部事实主张数：56
Faithfulness：38 ÷ 56 = 67.86%
```

这表示 56 条事实主张中有 38 条能由指定知识上下文支持。它不表示文章有 67.86% 的文字复制自知识库，也不表示剩余内容必然错误。

不同 Skill 版本、知识文件、文章版本或拆分规则可能产生不同分母。只有同一版本、同一输入和同一口径的结果适合直接比较；规则升级后应保留旧结果并重新建立基线。

复查时优先关注：

1. 不支持主张是否确实缺少本次知识上下文；
2. 复合事实是否拆分得合理；
3. 证据原文是否真正支持文章主张；
4. 产品参数、公司事实、认证、案例和数据是否有明确证据；
5. 是否漏交了写作时真实使用的知识文件。

若输入文件漏交或发生变化，应重新运行审核，不要直接把 judgments 中的结论改成支持。

## 目录

```text
deepeval-article-audit/
├── CHANGELOG.md
├── README.md
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   └── method.md
├── scripts/
│   ├── audit_common.py
│   ├── prepare_article_audit.py
│   └── render_article_audit.py
└── tests/
    └── test_audit_pipeline.py
```

- `SKILL.md`：执行流程、输入边界和强制输出；
- `references/method.md`：原子主张、证据判断、JSON schema 和报告口径；
- `CHANGELOG.md`：行为、兼容性和呈现层更新；
- `audit_common.py`：预处理与渲染共用的确定性解析；
- `prepare_article_audit.py`：生成文章单元、知识块和候选证据；
- `render_article_audit.py`：验证输入与判断并生成全部报告；
- `test_audit_pipeline.py`：端到端管线和 v0.5 兼容性回归。

## 当前限制与维护

- 语义拆分和支持判断由当前 Codex 模型完成，边界主张仍可能需要人工复核；
- 知识上下文缺失会直接降低支持率；
- 输入应是能够稳定读取并保留行号的本地文本或 Markdown；PDF、Word 等应先转换；
- prepared 与 judgments 当前使用 `schema_version: 1.0`；
- `faithfulness_summary.md` 的主表列顺序和数值必须保持稳定，以兼容 v0.5 导入；内容单元诊断只能追加在主表之后。

凡是修改事实拆分、证据规则、schema、验证逻辑、报告格式或外部 Skill 兼容性，都必须同步更新 [CHANGELOG.md](CHANGELOG.md)，并说明用户可见影响和兼容性边界。
