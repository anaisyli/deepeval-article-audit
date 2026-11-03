# deepeval-article-audit

当前活动版本为 **v1.10.0**，完整 Skill 位于 [`deepeval-article-audit-v1.10.0/`](deepeval-article-audit-v1.10.0/SKILL.md)。根目录保留本 README、[更新日志](CHANGELOG.md) 和 [`versions/`](versions/README.md) 历史版本入口。

**DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）**。本 Skill 审核已经完成的文章：从写作时实际提供的知识附件中识别可验证主张，逐条判断证据是否支持，并输出可导入的 JSON、Markdown 和 HTML 结果。

本 README 是给人看的使用说明。Codex 审核时按 `SKILL.md` 和 `references/` 执行，不读本文；本文与它们不一致时以 `SKILL.md` 为准。它也不是交接合同：受管运行时，`prepare` 脚本会自己找到当前安装的 `manage-article-knowledge` Skill 的合同文件并校验。

文中的 `SKILL.md`、`references/`、`scripts/` 和 `tests/` 均相对当前版本目录；运行下方命令前先进入 `deepeval-article-audit-v1.10.0/`。本机安装入口 `C:\Users\DELL\.codex\skills\deepeval-article-audit` 已直接链接到 `D:\deepeval-article-audit\deepeval-article-audit-v1.10.0`，不把仓库根目录或历史版本目录作为安装入口。

## 先分清四种版本

| 版本对象 | 当前值 | 含义 |
| --- | --- | --- |
| Faithfulness 审核规则 | `v1.10.0` | 本 Skill 的主张识别、证据判断、复核和输出规则，见 `CHANGELOG.md` |
| 知识库管理 Skill | `manage-article-knowledge v0.6.3` | 负责文章任务、版本、结果导入、Claim 映射和完成状态 |
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

`prepare` 自行定位已安装的 `manage-article-knowledge` 及其 `references/handoff-contract.json`，校验合同版本、执行器、文章身份、文件哈希和版本专属结果目录；审核模型不需要打开知识库 Skill 的任何文件。找不到唯一合同、合同不兼容或身份不一致时 `prepare` 报错停止，只有这时才用 `--manage-skill-root` 指定知识库 Skill 目录；不扫描整块磁盘、不猜字段、不退回未经确认的外部流程。

受管 `prepare` 只接受知识库自己的任务文件：`04_文章任务/30_等待Faithfulness/<任务>/40_最终文章.md` 和同目录的 `30_本篇知识库资料.md`，且 `30` 的字节必须与 `writing_ready.json` 绑定的一致。只差换行（LF、CRLF 或混合）时，`prepare` 按写作许可的 SHA-256 证明并恢复许可字节后继续（输出 `knowledge_restored`），与知识库导入的处理相同；正文、BOM、编码有任何变化，`30` 缺失，或写作许可无法读取、哈希无效、指向别的任务或文章版本不符时，原样拒绝并交回知识库。不得把这两份文件复制到别处或改版本字段来“开新版本”；只想演练或测量审核时，用通用模式对原文件审核并输出到单独目录。

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

本 Skill 回答的是一个限定问题：**文章正文中的可验证事实主张，有多少能被写作时实际提供的当前知识附件明确支撑**。它不是文章质量分、SEO/GEO 评分、发布资格判断，也不是官网或外部资料调研。不能用模型记忆、网页搜索、完整知识库、`35`、Formal Claim、文章自身内容或其他未随本篇提供的文件补证。

审核有两层结果：

1. **内容单元层**：逐句、列表项和表格数据行判断是可验证主张还是非主张/结构内容。标题、表格表头和分隔行、纯 Markdown 图片语法、网址、元数据和管理说明不进入事实分母；表格数据行照常审核。非主张单元只能归入固定类别（`heading`、`question`、`transition`、`cta`、`subjective`、`hypothetical`、`pure_recommendation`，以及通常已被结构排除的 `title`、`table_header`），不能以“没有证据”为理由。
2. **原子主张层**：对可验证内容拆出独立、可分别得到结论的事实命题。

正式指标只有知识附件明确支撑率：

```text
明确支撑率 = entailed ÷ (entailed + unknown + contradicted)
```

- `entailed`：知识原文在同一对象、关系、范围、条件和时间/版本下直接陈述，或不增加实质信息即可推出文章主张。资料说得比主张强或更具体可以推出（“必须提供”推出“可以包含”），说得比主张弱不能推出（“可以/允许”不能变成“应该/优先”）；判断看全部允许证据，不只看某一句。
- `unknown`：没有证据、证据只覆盖一部分、范围/条件/版本无法对齐、主张未能确认是单一命题，或复核没有确认。缺少证据不能变成 `contradicted`。
- `contradicted`：允许证据对同一对象、范围、条件和时间/版本明确给出不相容事实，且复核确认。

人读页面固定显示：**绿色 = 明确支持**，**黄色 = 尚未确认**，**红色 = 明确冲突**，**灰色 = 非主张，不计入**。机器兼容字段中的 DeepEval `yes/no/idk` 和 `supported/unsupported` 只留在 JSON/CSV 里，人读报告不展示 DeepEval 兼容百分比。

没有部分分：集合值主张只有全部实质成员都被证据覆盖才是 `entailed`，部分覆盖仍为 `unknown`。分母为 0 时结果为 `N/A`。这个比率没有目标值；知识附件缺少事实会降低比率，但不能单独证明文章事实错误，后续是否补知识、改写或建立治理事项由 `manage-article-knowledge` 处理。

## 主张怎么定：第一轮抽取、覆盖复查、冻结分母

### 第一轮抽取 + 覆盖复查（v1.9 起）

第一轮通读全文，写出主张清单和非主张分类。随后另一个上下文（子代理或新会话）做覆盖复查：读阅读视图和第一轮结果，对第一轮判为非主张的每个单元给出结论（同意就写自己的非主张理由，不同意就写出主张），并补上第一轮在已有主张的单元里漏掉的独立事实；它不重抽全文，也不改写或取消第一轮的主张。分母是第一轮主张加复查补充的主张；只有第一轮和复查都判为非主张的单元才排除。FAQ 的问句本身不是主张，问句里断言的事实前提要改写成陈述句；FAQ 回答照常审核。

v1.8 及以前是两个互不可见的上下文各自完整抽取、再按措辞配对合并；这套做法占审核用量约四成，配对与分歧判断也是冻结阶段不稳定和重复计数的主要来源。P-005 两篇对照中，只以第一轮为基础的支撑率与两轮并集几乎相同，第二轮独有的主张多是换说法的同一事实。两轮模式只保留用于续跑旧尝试，冻结副本见 `versions/deepeval-article-audit-v1.8.11/`。

分母在看证据之前冻结。冻结后主张不能再拆、并或补；要改抽取，只能 `reset` 后重新做第一轮和覆盖复查。

### 原子性规则

- 独立谓词、转折、条件、因果/效果关系或可能得到不同结论的部分必须拆开。
- 同一主体、同一关系、同一范围/条件/时间和同一语气下的同质枚举，保留为一条集合值主张；不能因为成员可枚举就机械拆分。
- 每条主张是带主语和谓词、能单独判断真假的完整命题；不按句号或逗号机械切分，也不能为了降低分母把不同关系合并。
- 每轮写完一个单元时反向读一遍原文，确认每个独立事实谓词都有自己的主张（`A, but B` 中 B 可核验时必须单独成条）。这是抽取时的自查，冻结后不再补拆。

例如：

```text
JERL describes customized internal structures and external dimensions around the product, but the actual fit still needs a physical sample.
```

至少拆成“描述定制内部结构和外部尺寸”与“实际适配仍需实体样品”两个主张，因为两者可能得到不同结论。

### 列表继承

只有名词短语的列表项不是独立完整命题。列表前有前导句时，补入最小主体和谓词，并在 `derivation_note` 记录继承关系；`article_quote` 仍必须保留文章原文。例如：

```text
A useful family brief keeps a few rules constant:
opening direction and main logo zone
```

应按“该 brief 将 opening direction and main logo zone 保持为常量”形成可核验主张，而不是直接把名词碎片当主张。

## 证据判断与复核（`decisive_recheck_v1`）

1. **主判断**：每条冻结主张判一次 `entailed / unknown / contradicted`，证据必须是本篇知识文件里逐字连续的原文。
2. **整篇全 unknown 自查**：某篇有主张且主判断全部为 unknown 时，先暂停，对照阅读视图检查是否漏用了直接、完整的证据。这只是流程诊断，不改任何结论：发现漏用或无法确认时 `reset` 重做；没有漏用就保留全部 unknown 继续。
3. **第二次判断**：只对第一轮判为 `entailed` 或 `contradicted` 的主张，由看不到第一轮结论、证据和理由的独立上下文再判一次，并逐项核对原子性、对象、关系、范围/条件、数量/时间/版本、因果/效果。第一轮 `unknown` 直接定为 unknown，不复核、也不会被升级。
4. **收敛**（`reconcile_article_audit.py`）：只保留或降级，不升级，不建立人工队列。
   - 两次都判支持、复核确认是单一命题、且每个适用维度都有原文片段覆盖，才保留 `entailed`；
   - 两次都判冲突且冲突证据与对象/范围/时间对齐，才保留 `contradicted`；
   - 缺复核、两次分歧、检查缺项、复核者自己套用模板的理由、复核判为 `split_required`（含多个命题）的主张，一律降为 `unknown`，继续出报告。

同一主张的绑定键是 `unit_id + 逐字 article_quote + 完整规范化 claim`。`claim_id` 只用于显示，`claim_fragment` 只说明复核检查的局部语义，不能用来绑定两轮结果。

## 防编造门禁

所有审核记录都由脚本写入，模型只写语义内容（分类、主张、结论、逐字引文和片段对齐）。理由只写在能改变结果的地方（v1.10）：`unknown` 选一个缺口类型 `gap_type`，`entailed` 只给逐字证据，`contradicted` 写简短冲突说明；第二次判断不写说明文字，只在推翻机器信号时（复合外观的主张判 `atomic`、措辞要求的维度判 `not_applicable`）写自己的理由。其余理由由组装器按引文、类型和片段生成。以下情况由脚本直接拦下：

| 门禁 | 拦什么 | 为什么 |
| --- | --- | --- |
| `freeze`：覆盖复查不完整 | 第一轮判为非主张的单元复查没给结论、复查给已有主张的单元写分类行、同一单元既写主张又判非主张 | 排除一个单元需要两方都同意；复查不取消第一轮主张 |
| `freeze`：两轮相同（仅两轮模式） | 两轮清单逐条相同（≥5 条） | 说明盲抽取不是独立结果 |
| `freeze`：碎片主张 | 某轮 ≥20 条主张且超过 10% 是短语碎片（如 `size`、`For wholesale buyers`） | 用脚本按逗号切句得到的不是可判断的命题 |
| `freeze`：两轮分歧（仅两轮模式） | 配对后仍有超过 3 个且超过五分之一的单元两轮抽出不同命题 | 并集会把同一事实的不同拆分算两次 |
| `freeze`：分类不合规 | 不存在的非主张类别、以缺证据为理由、问句/标题类别用错 | 这些行在渲染时必然失败 |
| `freeze`：已开始判断 | 判断开始后再改抽取轮重新冻结 | 结论会挂在已经变了的主张上 |
| `assemble-judgments`：证据位置 | 支持主张的引文不在允许证据块内，或不在所写的 K 块内 | 只有“证据正文”可以支持主张 |
| `assemble-*`：套用理由 | 模型自己写的理由，去掉各条自己的主张 ID、主张和引文后，同一句用于 3 条以上 entailed/contradicted 主张（unknown 的缺口类型和组装器生成的理由不查） | 模板理由不是逐条判断 |
| `assemble-*`：外来证据 | 证据文件不属于本篇，或引文不是所引文件的逐字片段 | 证据只能来自本篇知识附件 |
| `assemble-review`：检查片段 | 标为 covered 的检查缺少主张原文片段或证据逐字片段 | 没有原文对齐就不算覆盖 |
| `assemble-review`：复核缺项 | 主张措辞要求的维度没写（可写 covered、missing，或写 not_applicable 并说明理由）；复合外观的主张判 `atomic` 却没写不少于12字的原子性理由 | 这些缺项到收敛时只会悄悄降级，组装时就要补上；信号词只提示检查，复核的明确判断优先 |
| `check`/`render`/`status`：输入快照 | 准备之后文章单元、证据块、行锚或允许范围变了 | 旧结果不再对应当前输入，交回知识库 |

脚本拒绝是流程里的正常一步：按报错在出错的那一步改正后继续（冻结前只改被点名的行再重跑；判断后重判或重审被点名的主张）。只有输入快照变化或须由知识库修复的受管输入错误才停止并交回知识库。任何时候都不手改 `judgments.json`、`adversarial-review.json` 或冻结文件，不删证据行过关，不把记录改名后重跑，也不把旧尝试的结论按文本搬到新编号上。

## 运行顺序

```bash
# 1. 准备：prepared.json（机读）+ _work/ 下三个视图：extract-view（抽取读，含“抽取轮写法”，不含证据）、
#    evidence-view（判断与复核读）、audit-view（完整记录）；freeze 另写 _work/claims.md 供主判断读
python scripts/prepare_article_audit.py \
  --article <40_最终文章.md> --knowledge <30_本篇知识库资料.md> \
  --project-id <project-id> --article-id <article-id> --article-version <article-version> \
  --handoff-contract-version <contract-version> --result-dir <result-dir>

# 2. 第一轮抽取写 _work/first.json；另一个上下文做覆盖复查写 _work/coverage.json；然后冻结
#    （freeze 同时写出 _work/blind.json：第一轮主张加复查补充，后续命令照常读它）
python scripts/audit_preflight.py freeze --prepared <P> --first-inventory _work/first.json \
  --coverage-review _work/coverage.json --output _work/frozen.json

# 3. 主判断（可分片并行）写 _work/verdicts-*.json，然后组装
python scripts/audit_preflight.py assemble-judgments --prepared <P> --frozen-inventory _work/frozen.json \
  --first-inventory _work/first.json --blind-inventory _work/blind.json \
  --verdicts _work/verdicts-1.json --output <result-dir>/<article-id>-judgments.json

# 4. 复核清单 → 独立复核写 _work/reviews-*.json → 组装
python scripts/audit_preflight.py recheck-list --judgments <article-id>-judgments.json --output _work/recheck.json
python scripts/audit_preflight.py assemble-review --prepared <P> --frozen-inventory _work/frozen.json \
  --blind-inventory _work/blind.json --recheck _work/recheck.json --reviews _work/reviews-1.json \
  --output <result-dir>/<article-id>-adversarial-review.json

# 5. 预检、收敛、渲染
python scripts/audit_preflight.py check --prepared <P> --judgments <article-id>-judgments.json \
  --adversarial-review <article-id>-adversarial-review.json --frozen-inventory _work/frozen.json \
  --receipt _work/receipt.json [--attempt 1]
python scripts/reconcile_article_audit.py --result-dir <result-dir> --article-id <article-id>
python scripts/render_article_audit.py --result-dir <result-dir> --article-id <article-id> \
  --article-version <article-version>
```

中断或换会话后先看进度，从返回的 `next_stage` 继续，不为确认进度重读已完成阶段的文件：

```bash
python scripts/audit_preflight.py status --result-dir <result-dir>
```

导入前要作废本次尝试、从第一轮抽取重新做时（例如冻结后才暴露的问题，或全 unknown 自查发现漏用证据）：

```bash
python scripts/audit_preflight.py reset --result-dir <result-dir>
```

它把除 `prepared` 以外的结果文件和过程文件（包括已渲染但未导入的报告）归档到 `_work/attempt-N/`，并从 `prepared` 重建阅读视图。已导入（有 `article_completed.json`）的结果拒绝重置，须由知识库建立新文章版本。

命令行输出只给下一步需要的内容（路径、计数或回执），统一为 UTF-8；原始文章和知识文件始终不被改写。

## 输出文件

受管结果目录固定为当前文章版本专属目录：

```text
<result_root>/<project_id>/<article_id>/v<article_version>/
├── <article-id>-prepared.json
├── <article-id>-judgments.json
├── <article-id>-adversarial-review.json
├── <article-id>_faithfulness_details.md
├── faithfulness_highlight.html
├── faithfulness_summary.md
└── _work/                      # 过程文件；受管渲染成功后自动删除（--keep-work 保留）
```

知识库导入时还会在同一目录写 `unsupported-disposition.json` 和 `article_completed.json`。导入、项目校验和月报只读取根目录文件，不读 `_work`。

- 明细 Markdown：每条主张一行，含文章原文、原子主张、语义状态、复核深度、证据位置和理由。
- 汇总 Markdown：每篇一行，前七列位置固定供知识库导入。
- 高亮 HTML：每个正文单元一张卡片，灰色非主张、绿色明确支持、黄色尚未确认、红色明确冲突，可分别筛选；“只看明确支持”按主张过滤，混合单元保留其中的支持主张。渲染器校验汇总支持数与最终 `entailed` 主张数及逐单元卡片映射一致。

同一版本已有核心结果时，`prepare` 拒绝覆盖。收敛之前，改正紧凑文件后可用同一 assemble 命令重新组装（旧记录移到 `_work/superseded-N/`）；收敛后、导入前用 `reset` 归档后重做；已导入的结果只能由知识库递增文章版本后在新目录重审。

EC-1 指代归属记录（`coreference_evidence`）和证据行号由 `assemble-judgments` 按证据引文自动填写并当场校验，模型不手写。

## 与知识库的交接边界

本 Skill 只负责：

- 从当前 `40/30` 生成审核输入；
- 识别和判断文章主张；
- 生成 `prepared`、`judgments`、复核记录、汇总、明细和高亮页面；
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
├── deepeval-article-audit-v1.10.0/ # 当前活动版本，也是安装入口指向的位置
│   ├── SKILL.md                  # 执行顺序和硬性边界（Codex 按它运行）
│   ├── agents/                   # Skill 元数据
│   ├── references/               # 审核口径、预检和交接规范
│   ├── scripts/                  # 准备、冻结、复核、收敛与渲染脚本
│   └── tests/                    # 当前版本回归测试
├── README.md                     # 本说明（给人看）
├── CHANGELOG.md                  # 版本、行为、schema 和兼容性变化
└── versions/                     # 历史冻结版本，仅供对照和回溯
    ├── README.md
    ├── deepeval-article-audit-v1.8.11/
    └── deepeval-article-audit-v1.9.0/
```

升级时，旧活动版本归档到 `versions/`，根目录只保留一个活动版本文件夹，并将安装链接切换到新版本文件夹。Git 元数据（`.git/`、`.gitignore`）保留在仓库根目录；测试临时目录与 Python 缓存不属于版本交付内容。

修改主张拆分、证据范围、门禁、JSON schema、输出合同或知识库兼容性时，必须同步更新 `CHANGELOG.md` 并运行测试。只改 README 不改变审核算法、schema、输出文件名、交接合同或既有审核结果。

## 限制

- 这是 Codex 对 DeepEval Faithfulness 规则的复现，不是官方 DeepEval 运行。
- 评审由当前 Codex 模型完成；语义无法稳定确认时记为 `unknown`。
- 分数只适用于对应文章版本、对应知识附件和对应审核规则版本；不同版本、不同附件或不同拆分口径不能直接比较。
- Faithfulness 结果不直接决定文章是否发布，也不自动关闭知识缺口；知识库依据正式 Claim 和证据规则处理后续治理。
