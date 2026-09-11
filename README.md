# deepeval-article-audit

DeepEval-style Article Audit v1.4.1。用于审核已经完成的文章，按写作时实际提供的知识附件提取原子事实主张、判断证据支持情况，并计算支持主张数 / 全部事实主张数。

本 Skill 使用当前 Codex 模型完成规则复现，不调用 DeepEval 官方 API，也不声称生成官方 DeepEval 分数。所有分数和交付文件都必须标注：

> DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）

## 先看这些

- [v0.6 受管审核适配说明](references/manage-article-knowledge-v06-integration.md)：从知识库接收审核请求、校验身份和回传结果时阅读。
- [审核方法与结果结构](references/method.md)：需要判断事实主张、证据范围或 JSON 字段时阅读。

交接合同由当前安装的 `manage-article-knowledge` Skill 提供；本 Skill 运行时定位并校验该 Skill 的 `references/handoff-contract.json`，不在此处维护第二份合同。

## 在文章流程中的位置

```text
写作需求
→ 知识库检索、Claim 和文章前审核
→ 30_本篇知识库资料.md
→ 写作 Skill 生成终稿
→ 知识库规范化 40_最终文章.md
→ faithfulness_request
→ 本 Skill 审核并返回 faithfulness_completed
→ 知识库导入结果、更新治理记录并返回 article_completed
```

`faithfulness_completed` 只是审核结果已经生成并校验的中间事件。知识库完成导入、更新 `50_文章知识使用与Faithfulness记录.md`、Claim 支撑和任务状态后，文章流程才结束。

## 两种使用方式

### v0.6 受管模式

知识库是唯一调用方和结果接收方。请求必须包含：

```text
handoff_event: faithfulness_request
handoff_contract_version
project_id
article_id
article_version
article_file: 当前 40_最终文章.md
knowledge_file: 当前 30_本篇知识库资料.md
result_root
result_dir
```

审核器定位当前安装的知识库 Skill，读取其规范合同并校验版本、身份、路径和结果目录。通过校验后立即审核，不等待第二次人工指令；成功时在同一 `result_dir` 写入三个可导入核心结果，并返回相同合同版本的 `faithfulness_completed`。

受管模式的输入只有两份当前文件：

1. `40_最终文章.md`；
2. `30_本篇知识库资料.md`。

`40` 必须恰好包含一对 `<!-- ARTICLE_BODY_START -->` / `<!-- ARTICLE_BODY_END -->` 标记。只审核标记内的正文文字、列表和表格；身份、关键词、TDK、图片清单、交付说明和管理文字放在边界外。标记缺失、重复或倒置时停止，由知识库重新生成 `40`，审核器不得猜测正文范围。

在当前 `30` 中，只有第 1 至第 3 节明确标记为“证据正文（供Faithfulness核验）”的连续内容可作为正向证据。不得把 `35`、正式 Claim、完整知识库、原始随文资料、写作规则、SEO/GEO 说明、英文表达表、无证据正文的数据展示表或图片加入审核上下文。

### 通用或旧版模式

可以显式提供一篇或多篇文章及其对应知识文件。非受管模式允许多个知识文件，但每篇文章必须能明确配对，不能因配对不清而混用证据。v0.5 项目同样以当前 `40_最终文章.md` 和唯一 `30_本篇知识库资料.md` 为事实输入。

示例：

```text
使用 $deepeval-article-audit。
文章文件：<article.md>
知识文件：<knowledge.md>
输出目录：<new-output-directory>
```

## 审核口径

```text
Faithfulness = 支持的原子事实主张数 ÷ 全部原子事实主张数
```

- 按文章顺序检查每个 `articleUnit`，不抽样、不因事实单元少而跳过覆盖。
- 把复合句拆成可分别判断的原子主张；改写、同义替换和合理直接推导可以支持，但每条支持主张都要有至少一段知识文件原文证据。
- 现实实体、产品能力、材料、尺寸、机制、效果、数量、流程、质量控制、典型性、因果关系和限制条件通常属于事实，即使句子使用 `should`、`can`、`may` 或建议语气。
- 标题、小标题、网址、导航、元数据、纯修辞、纯建议、纯问题、CTA 和没有事实前提的假设性规划不进入分母。
- 不使用部分分：拆分后的主张只记 `supported` 或 `unsupported`。不支持表示当前知识附件没有依据，不等于事实必然错误。

报告保留两层信息：内容单元诊断，以及原子事实主张支持率。事实内容占比不是 Faithfulness、引用率、SEO/GEO 分数或文章质量评分。

在受管文章中，如果内容单元达到 20 个而事实单元少于 20%，必须在 judgments 中补充完整的 `coverage_review`，逐个说明单元分类和排除理由；不能把低比例当作审核完成。

## 输出文件

受管模式下，`result_dir` 是当前文章版本唯一的结果目录：

```text
[结果根目录]/[项目ID]/[文章ID]/v[文章版本]/
├── [文章ID]-prepared.json
├── [文章ID]-judgments.json
└── faithfulness_summary.md
```

另外生成两份人工复查报告：

- `<文章ID>_faithfulness_details.md`：逐条事实主张、判断、证据和行号；
- `faithfulness_highlight.html`：按文章单元展示事实性、支持状态和证据。

高亮页中每个句子、列表项或表格数据行都是独立卡片，保留 `unit_id`、原文行、事实性、支持状态和主张数；支持、部分支持、不支持和非事实单元可筛选。详细 Markdown 和 HTML 不直接写入知识库台账。

已有当前版本的核心结果时拒绝覆盖；文章修订必须由知识库递增文章版本后重新审核。

## 运行入口

受管 v0.6 的典型命令由交接请求提供参数：

```bash
python scripts/prepare_article_audit.py \
  --article <40_最终文章.md> \
  --knowledge <30_本篇知识库资料.md> \
  --project-id <project-id> \
  --article-id <article-id> \
  --article-version <article-version> \
  --handoff-contract-version <contract-version> \
  --result-dir <result-dir>

python scripts/render_article_audit.py \
  --result-dir <result-dir> \
  --article-id <article-id> \
  --article-version <article-version>
```

准备器先读取文章元数据中的 `文章ID` / `Article ID` 和 v0.6 的 `文章版本`，再校验命令参数。渲染器会复核 schema、原文引用、证据边界、摘要格式和受管合同；失败时修正判断或输入后重新运行，不覆盖原始文章和知识文件。

## 目录和维护

```text
deepeval-article-audit/
├── SKILL.md
├── README.md
├── CHANGELOG.md
├── references/
│   ├── method.md
│   └── manage-article-knowledge-v06-integration.md
├── scripts/
│   ├── prepare_article_audit.py
│   ├── render_article_audit.py
│   └── manage_handoff_contract.py
└── tests/
```

- `SKILL.md`：执行流程和硬性边界；
- `references/method.md`：事实拆分、证据判断和 JSON 结构；
- `references/manage-article-knowledge-v06-integration.md`：v0.6 受管适配；
- `scripts/prepare_article_audit.py`：建立文章单元、事实主张和候选证据；
- `scripts/render_article_audit.py`：校验判断并生成明细、汇总和网页；
- `CHANGELOG.md`：版本、schema、输出和兼容性变化。

修改事实拆分、证据范围、JSON schema、输出合同或知识库兼容性时，必须同步更新 `CHANGELOG.md` 并运行现有测试。不要直接写入知识库的 Faithfulness CSV、`50` 或 Claim 支撑表，这些由知识库 Skill 导入和维护。

## 限制

- 这是 Codex 对 DeepEval Faithfulness 规则的复现，不是官方 DeepEval 运行。
- 评审由当前 Codex 模型完成，少数语义边界可能需要人工复核。
- 知识附件缺少事实会降低支持率；这不能单独证明文章事实错误。
- 不同 Skill 版本、文章拆分口径或知识附件下的分数不能直接比较。
