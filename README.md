# deepeval-article-audit

**DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）**。本 Skill 审核已经完成的文章：从写作时实际提供的知识附件中识别可验证主张，逐条判断证据是否支持，并输出可导入的 JSON、Markdown 和 HTML 结果。

本 README 是使用说明，不是第二份交接合同。受管运行时始终读取当前安装的 `manage-article-knowledge` Skill 的唯一合同文件。

## 先分清四种版本

| 版本对象 | 当前值 | 含义 |
| --- | --- | --- |
| Faithfulness 审核规则 | `v1.6.8` | 本 Skill 的主张识别、证据判断、双轮盲审和输出规则 |
| 知识库管理 Skill | `manage-article-knowledge v0.6.1` | 负责文章任务、版本、结果导入、Claim 映射和完成状态 |
| 三方交接合同 | 运行时读取当前安装合同 | 知识库、写作方和本 Skill 之间的请求/回执字段约定，不以本文示例替代机器合同 |
| 文章版本 | `v1`、`v2`、`v3`…… | 同一文章的不同正文版本；每个版本有独立审核结果目录 |

文章重做后，知识库必须递增 `article_version`，并在新版本目录重新审核。旧版本结果保留为历史记录，不能被新版本覆盖或当作新版本结论。

## 它在文章流程中的位置

```text
知识库准备写作事实
  → 写作 Skill 生成终稿
  → 知识库生成当前 40_最终文章.md 和 30_本篇知识库资料.md
  → 知识库发送 faithfulness_request
  → 本 Skill 审核并回传 faithfulness_completed
  → 知识库导入结果、更新 Claim/治理记录
  → 知识库返回 article_completed
```

`faithfulness_completed` 只是审核结果已生成并通过本 Skill 校验的中间事件。它不等于整篇文章完成，也不负责把结果写入知识库的 CSV、`50`、Claim、知识缺口或任务目录；这些动作由 `manage-article-knowledge` 完成。

收敛器会在渲染前统一排除两轮中误提的 FAQ 纯问题（包括第一轮已进入 `judgments.claims` 的问题），并保留 FAQ 回答中的事实主张。若问题含有事实前提，主张清单应把前提改写成陈述句后再审核；不能用问题句本身充当 Claim。

## 受管 v0.6 模式

### 请求字段

知识库发来的 `faithfulness_request` 至少要包含：

```text
handoff_event: faithfulness_request
handoff_contract_version: <当前安装合同中声明的版本>
project_id
article_id
article_version
article_file: 当前 40_最终文章.md
knowledge_file: 当前 30_本篇知识库资料.md
result_root
result_dir: <result_root>/<project_id>/<article_id>/v<article_version>/
```

本 Skill 会定位已安装的 `manage-article-knowledge`，读取其 `references/handoff-contract.json`，校验合同版本、执行器、文章身份、文件哈希和版本专属结果目录。找不到唯一合同、合同不兼容或身份不一致时停止；不扫描整块磁盘、不猜字段、不退回未经确认的外部流程。

### 允许读取的输入

受管审核只读取两份当前文件：

1. `40_最终文章.md`：文章正文；
2. `30_本篇知识库资料.md`：写作时实际提供给文章的事实附件。

`40` 必须恰好包含一对 `<!-- ARTICLE_BODY_START -->` 和 `<!-- ARTICLE_BODY_END -->`。只审核标记之间的正文文字、列表和表格；身份、关键词、TDK、图片清单、交付说明和管理字段在边界外。标记缺失、重复或倒置时停止，不能根据附近标题猜正文。

`30` 中只有第 1 至第 3 节里明确标记为“证据正文（供Faithfulness核验）”的连续内容可以作为正向证据。不得把 `35`、Formal Claim、完整知识库、原始随文资料、写作规则、SEO/GEO 说明、英文表达表、无证据正文的数据表或图片加入审核上下文。

如果知识库返回 `writing_retry_required`，说明正文重做尚未形成变化，本 Skill 不启动审核、不建立结果目录，也不把该事件当作审核失败；等知识库重新发出新的 `faithfulness_request`。

## 通用和旧版模式

没有 v0.6 交接合同的旧项目可以显式提供文章文件、对应知识文件和输出目录。每篇文章必须能和唯一知识文件明确配对，不能混用证据。v0.5 项目同样只把当前 `40_最终文章.md` 与当前 `30_本篇知识库资料.md` 作为事实输入；旧模式不要求项目 ID、文章版本或三方合同。

```text
使用 $deepeval-article-audit。
文章文件：<article.md>
知识文件：<knowledge.md>
输出目录：<new-output-directory>
```

## 审核口径

本 Skill 回答的是一个限定问题：**文章正文中的可验证事实主张，有多少能被写作时实际提供的当前知识附件明确支撑**。它不是文章质量分、SEO/GEO 评分、发布资格判断，也不是官网或外部资料调研。

审核只使用两份输入：`40_最终文章.md` 正文边界内的文章内容，以及当前 `30_本篇知识库资料.md` 第 1 至第 3 节中标记为“证据正文（供Faithfulness核验）”的连续证据。不能用模型记忆、网页搜索、完整知识库、`35`、Formal Claim、文章自身内容或其他未随本篇提供的文件补证。

审核有两层结果：

1. **内容单元层**：逐句、列表项和表格数据行判断是可验证主张还是非主张/结构内容。标题、明确的 Heading、表格表头和分隔行、纯 Markdown 图片语法、网址、元数据、管理说明和纯问题不进入事实分母；表格数据行仍然审核。表头在报告中保留为灰色结构单元，不能通过 `heading_override_reason` 恢复为 Claim。
2. **原子 Claim 层**：对可验证内容拆出独立、可分别得到 verdict 的事实命题。两轮独立识别结果取并集后冻结分母，再开始证据判断；任一轮认为是可验证主张，原则上都不能因另一轮漏识别而排除。只有两轮都确认属于允许的非主张类别，才可以排除。

正式指标只有知识附件明确支撑率：

```text
明确支撑率 = entailed ÷ (entailed + unknown + contradicted)
```

- `entailed`：知识原文在同一对象、关系、范围、条件和时间/版本下直接陈述，或不增加实质信息即可推出文章主张。
- `unknown`：没有证据、证据只覆盖一部分、范围/条件/版本无法对齐、主张未能稳定拆分，或两轮审核有分歧。缺少证据不能变成 `contradicted`。
- `contradicted`：允许证据对同一对象、范围、条件和时间/版本明确给出不相容事实，且两轮都确认冲突。

人读页面固定显示：**绿色 = 明确支持**，**黄色 = 尚未确认**，**红色 = 明确冲突**，**灰色 = 非主张，不计入**。机器兼容字段中的 `unsupported` 不替代这三个含义。

没有部分分。一个 Claim 只有在其全部实质语义组件、同质集合成员和必要条件都被证据覆盖时才是 `entailed`；部分覆盖仍为 `unknown`。建议、方法、比较、效果、因果和条件表达只要包含可核验关系就要审核，不能因为它们是“建议”或“操作步骤”而自动排除。独立谓词、转折、因果/效果、条件或可能得到不同 verdict 的部分必须拆开；同一主体、关系、范围、条件、时间和语气下的同质枚举可以保留为一条集合值 Claim，不能为了降低分母合并不同关系，也不能仅因成员可枚举而机械拆分。

协议 2.0 要求真实的第二轮独立盲审和 `coverage_review`。只有两轮都确认支持、原子性完整且语义组件均有证据时才保留绿色；明确冲突必须是同对象、同范围、同条件和同时间/版本的不相容证据，并经两轮确认。其他分歧、缺失、歧义和未覆盖内容都降为黄色。机器兼容字段仍可记录 DeepEval 的 `yes/no/idk` 和 `supported/unsupported`，但人读报告只解释严格三态；DeepEval 默认兼容率不作为知识库主指标。

分母为 0 时结果为 `N/A`。这个比率没有目标值，也不是文章质量、SEO/GEO、引用率或发布资格评分。知识附件缺少事实会降低比率，但不能单独证明文章事实错误；后续是否补知识、改写或建立治理事项由 `manage-article-knowledge` 按其合同处理。

## 双轮主张识别与证据判断

### 冻结分母

先对全文做两轮相互独立的事实识别，再取两轮主张清单的**并集**冻结分母，之后才判断证据和颜色。任何一轮识别为可验证主张，就不能因为另一轮漏识别而排除；只有两轮都确认是标题、问题、CTA、纯修辞、纯偏好或其他允许的非主张，才可以排除。

标题、网址、元数据、纯图片语法和管理说明不进入分母。建议、操作方法、比较、表格数据行、效果、因果和条件表达，只要包含可核验关系就要进入审核。

### 原子性规则

- 独立谓词、转折、条件、因果/效果关系或可能得到不同 verdict 的部分必须拆开。
- 同一主体、同一关系、同一范围/条件/时间和同一语气下的同质枚举，可以保留为一条集合值主张；不能因为成员可枚举就机械拆分。
- 集合值主张只有全部实质成员都被证据覆盖时才是 `entailed`；部分成员有证据仍为 `unknown`。
- 不能按句号机械计数，也不能为了提高比例把不同对象、关系或条件合并。

例如：

```text
JERL describes customized internal structures and external dimensions around the product, but the actual fit still needs a physical sample.
```

至少拆成“描述定制内部结构和外部尺寸”与“实际适配仍需实体样品”两个主张，因为两者可能得到不同 verdict。

### 列表继承

只有名词短语的列表项不是独立完整命题。列表前有前导句时，审核时补入最小主体和谓词，并在 `derivation_note` 记录继承关系；`article_quote` 仍必须保留文章原文。例如：

```text
A useful family brief keeps a few rules constant:
opening direction and main logo zone
```

应按“该 brief 将 opening direction and main logo zone 保持为常量”形成可核验主张，而不是直接把名词碎片当主张。

### 绿色单元完整性

准备把一个内容单元标为 `fully_supported` 前，必须反向阅读原单元，确认每个独立事实谓词都有对应主张。若一个绿色单元的后半句、附加能力或限制没有进入审核，先拆出遗漏主张并重新完成双轮核验；无法稳定拆分时，整单元降为 `unknown`。同质枚举仍不要求机械拆分。

## 协议 2.0 的盲审与收敛

受管 v0.6 结果必须包含完整 `coverage_review`，并另有独立的 `<article-id>-adversarial-review.json`。第二轮只能看到主张、文章引文和允许的证据，不能读取第一轮的 verdict、semantic_status 或 reason；它要重新检查：

- 主张是否原子；
- 对象和关系是否一致；
- 范围、条件、数量、时间和版本是否对齐；
- 因果/效果是否真的由证据覆盖；
- 集合值主张是否覆盖全部成员；
- 绿色内容单元是否遗漏独立谓词。

随后运行 `reconcile_article_audit.py` 自动收敛：

- 两轮都确认 `entailed`，且所有适用语义组件均有证据覆盖，才保留明确支持；
- 两轮都确认明确冲突且有同范围冲突证据，才保留 `contradicted`；
- 其他情况一律降为 `idk / unknown / unsupported`，继续生成报告，不建立人工逐条队列。

同一主张的绑定键是 `unit_id + 逐字 article_quote + 完整规范化 claim`。`claim_id` 只用于显示，`claim_fragment` 只说明盲审检查的局部语义，不能用来绑定两轮结果。协议 2.0 缺少真实独立盲审时，不得生成空壳文件或返回 `faithfulness_completed`。

收敛器在第二轮清单并入分母前，会同时检查两轮 `coverage_review`：两轮都明确标为非事实、类别属于标题/纯问题/过渡/CTA，且没有强制复核信号的单元才会被排除。FAQ问题本身不计入Claim，但FAQ回答中的可验证事实仍会保留；建议、方法、比较、因果或存在复核信号的内容仍按并集规则进入分母。该过滤按 `inventory_id` 记录并保持幂等，重复收敛不会重新加入同一Claim。

如果旧结果虽然写有 `reconciliation.completed=true`，但三份收敛输入中仍残留纯问题，渲染器会先自动重新收敛并修复 JSON，再进行硬校验；同目录受管渲染会持久化修复，单独复核目录只在内存修复。干净结果不会被重复改写。

## 输出文件

受管结果目录固定为当前文章版本专属目录：

```text
<result_root>/<project_id>/<article_id>/v<article_version>/
├── <article-id>-prepared.json
├── <article-id>-judgments.json
├── <article-id>-adversarial-review.json
├── <article-id>_faithfulness_details.md
├── faithfulness_highlight.html
└── faithfulness_summary.md
```

`judgments.json` 要保留每条主张的逐字文章引文、严格语义状态、证据文件、行号、连续原文和简短理由；`coverage_review` 必须覆盖 `prepared` 中每个 `unit_id`。明细 Markdown 和 HTML 用灰色表示非主张/结构内容（包括标题和表格表头），绿色表示明确支持，黄色表示尚未确认，红色只表示明确冲突，并支持独立筛选。HTML 的“只看明确支持”按最终 Claim 过滤：混合单元只要含有一条明确支持主张仍保留该单元，并隐藏其中非支持卡片；不能因为单元整体是“部分支持”而隐藏支持主张。渲染器还校验汇总支持数与最终 `entailed` Claim 数及逐单元卡片映射一致。人读报告不展示 DeepEval 兼容百分比。

已有当前版本核心结果时拒绝覆盖。新文章版本必须由知识库递增后写入新目录；历史版本结果保留，不删除、不覆盖。

## 运行顺序

```bash
python scripts/prepare_article_audit.py \
  --article <40_最终文章.md> \
  --knowledge <30_本篇知识库资料.md> \
  --project-id <project-id> \
  --article-id <article-id> \
  --article-version <article-version> \
  --handoff-contract-version <contract-version> \
  --result-dir <result-dir>

python scripts/reconcile_article_audit.py \
  --result-dir <result-dir> \
  --article-id <article-id>

python scripts/render_article_audit.py \
  --result-dir <result-dir> \
  --article-id <article-id> \
  --article-version <article-version>
```

准备器先校验文章元数据、文章版本、正文边界、证据范围和文件哈希；收敛器必须在渲染前完成；渲染器再次校验 schema、引文、证据边界、合同、盲审和语义状态。原始文章和知识文件始终不被覆盖。

## 与知识库的交接边界

本 Skill 只负责：

- 从当前 `40/30` 生成审核输入；
- 识别和判断文章主张；
- 生成 `prepared`、`judgments`、盲审、汇总、明细和高亮页面；
- 回传 `faithfulness_completed`。

知识库 Skill 负责：

- 文章版本递增和历史结果保留；
- 导入 Faithfulness 结果并映射 Formal Claim；
- 更新文章知识使用记录、覆盖/缺口台账和任务状态；
- 在全部步骤完成后返回 `article_completed`。

不要让本 Skill 直接写知识库 CSV、`50_文章知识使用与Faithfulness记录.md`、Claim、CUS、ANM、SKFB 或知识缺口文件。

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
│   ├── reconcile_article_audit.py
│   ├── render_article_audit.py
│   └── manage_handoff_contract.py
└── tests/
```

- `SKILL.md`：执行流程和硬性边界；
- `references/method.md`：事实拆分、三态判断、证据规则和 JSON 结构；
- `references/manage-article-knowledge-v06-integration.md`：v0.6 受管适配；
- `scripts/`：准备、收敛、渲染和合同管理；
- `CHANGELOG.md`：版本、schema、输出和兼容性变化。

修改事实拆分、证据范围、JSON schema、输出合同或知识库兼容性时，必须同步更新 `CHANGELOG.md` 并运行测试。只重写 README 不改变审核算法、schema、输出文件名、交接合同或既有审核结果。

## 限制

- 这是 Codex 对 DeepEval Faithfulness 规则的复现，不是官方 DeepEval 运行。
- 评审由当前 Codex 模型完成；语义无法稳定确认时记为 `unknown`。
- 分数只适用于对应文章版本、对应知识附件和对应审核规则版本；不同版本、不同附件或不同拆分口径不能直接比较。
- Faithfulness 结果不直接决定文章是否发布，也不自动关闭知识缺口；知识库依据正式 Claim 和证据规则处理后续治理。
