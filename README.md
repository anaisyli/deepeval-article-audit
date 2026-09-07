# deepeval-article-audit

用于审核已经完成的文章，判断文章中的原子事实主张是否得到写作时实际提供的知识附件支持。

结果统一标注为：

> DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）

本 Skill 不调用 DeepEval 官方 API，不声称生成官方 DeepEval 分数。

## 在整条流程中的位置

```text
任意写作 Skill
→ writing_request
→ manage-article-knowledge
→ 15 检索、正式 Claim、20 审核、30/35
→ writing_ready
→ 写作 Skill 生成终稿
→ writing_completed
→ 知识库规范化 40_最终文章.md
→ faithfulness_request
→ deepeval-article-audit
→ faithfulness_completed
→ 知识库导入并进入 40_已完成
```

Faithfulness 只负责中间审核事件。`faithfulness_completed` 表示审核结果已经生成并校验，不表示文章流程完成；知识库导入成功、更新治理记录并迁移到 `40_已完成` 后，才返回 `article_completed`。

## 与知识库和任意写作 Skill 的交接

知识库 Skill 是唯一调用方和结果接收方。任意写作 Skill 不直接修改 Faithfulness 结果，也不把完整知识库交给审核器。写作 Skill 只负责：

1. 保存本次写作需求记录；
2. 按知识库合同提交 `writing_request`；
3. 收到 `writing_ready` 和当前 `30_本篇知识库资料.md` 后写作；
4. 返回终稿正文绝对路径，形成 `writing_completed`。

知识库随后规范化终稿并发出：

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

本 Skill 运行时定位当前已安装的 `manage-article-knowledge` Skill，读取其唯一的 `references/handoff-contract.json`，并校验合同版本、项目 ID、文章 ID、文章版本和路径。不得把某台电脑的绝对安装路径或第二份完整合同写入本 Skill。

审核成功后返回：

```text
handoff_event: faithfulness_completed
handoff_contract_version: 与请求相同
result_dir: 当前文章版本专属结果目录
article_id: 当前文章ID
article_version: 当前文章版本
```

知识库收到该事件后负责导入结果、更新 `50_文章知识使用与Faithfulness记录.md`、Claim 支撑、知识缺口和文章状态。不要直接写入知识库的 Faithfulness CSV、`50` 或 Claim 支撑表。

## 找不到审核 Skill 时

如果知识库无法检测到本 Skill，必须在对话中明确说明“未检测到 `deepeval-article-audit` Skill”，并告诉内容运营安装它或提供 Skill 目录。任务保留在 `30_等待Faithfulness`，不能只返回一个机器字段，也不能静默切换成未确认的外部审核流程。

合同缺失、版本不兼容、执行器名称不符、身份不一致、哈希不一致或结果目录不正确时，停止当前审核并说明具体原因；不得猜测、扫描宽泛目录或覆盖旧结果。

## 固定输入边界

在 `manage-article-knowledge v0.6` 受管模式下，只读取两份当前文件：

1. `40_最终文章.md`：知识库规范化后的终稿文字和表格；
2. `30_本篇知识库资料.md`：写作时交给 Writer 的唯一知识库事实附件。

不得把 `35_写作素材来源索引.md`、正式 Claim、完整项目知识库、原始随文资料、写作规则、SEO/GEO 说明或文章配图加入审核上下文。`35`只在结果回到知识库后，用于把 `30`证据正文映射到正式 Claim。

## 结果目录和输出

`result_dir` 必须是当前文章版本专属目录；缺失的项目、文章和版本子目录由本 Skill 自动创建：

```text
[结果根目录]/[项目ID]/[文章ID]/v[文章版本]/
├── [文章ID]-prepared.json
├── [文章ID]-judgments.json
└── faithfulness_summary.md
```

已有当前版本核心结果时拒绝覆盖；文章修订必须由知识库递增文章版本后重新审核。

另外生成两类便于人工复查的报告：

- `<文章ID>_faithfulness_details.md`：逐条事实主张、判断、证据和行号；
- `faithfulness_highlight.html`：按文章单元展示事实/非事实、支持状态和证据。

受管模式下，三个核心文件保留在 `result_dir`，可由知识库导入；详细 Markdown 和 HTML 是复查报告，不直接写入知识库台账。

## 计算口径

```text
Faithfulness = 支持的原子事实主张数 ÷ 全部原子事实主张数
```

- 标题、小标题、网址、元数据、导航、纯修辞、纯建议和管理文字不进入分母。
- 复合句拆成可分别判断的原子事实；不使用部分分。
- 改写、同义替换和合理直接推导可以算支持，但每条支持主张必须有知识文件连续原文证据。
- `30`只有第一至第三节中明确标记为“证据正文（供Faithfulness核验）”的内容可以作为正向证据。
- 第四至第六节、英文表达表、没有证据正文的数据展示表和写作控制文字不能作为正向证据。
- 不支持表示当前知识附件没有依据，不等于事实必然错误，也不等于文章整体不合格。

报告同时显示两层信息：

1. 内容单元诊断：事实单元、非事实单元，以及全部/部分/完全支持；
2. 原子事实主张支持率：唯一进入 Faithfulness 主指标的统计。

“事实内容占比”不是 Faithfulness、引用率、SEO/GEO 分数或文章质量评分。

## 独立或旧版使用

可以直接提供一篇或多篇文章及其对应知识文件。非受管模式允许显式提供多个知识文件，但每篇文章必须能明确配对；无法配对时先询问，不自行混用。

示例：

```text
使用 $deepeval-article-audit。
文章文件：<article.md>
知识文件：<knowledge.md>
输出目录：<new-output-directory>
```

在 v0.5/v0.6 项目中，不要用通用入口绕过当前 `30`、文章身份、版本和结果目录合同。

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
- `references/manage-article-knowledge-v06-integration.md`：v0.6 受管适配说明，不复制完整合同；
- `scripts/prepare_article_audit.py`：建立文章单元、事实主张和候选证据；
- `scripts/render_article_audit.py`：校验判断并生成明细、汇总和网页；
- `CHANGELOG.md`：版本、schema、输出和兼容性变化。

修改事实拆分、证据范围、JSON schema、输出合同或知识库兼容性时，必须同步更新 `CHANGELOG.md`，并运行现有测试。

## 限制

- 这是 Codex 对 DeepEval Faithfulness 规则的复现，不是官方 DeepEval 运行。
- 评审由当前 Codex 模型完成，少数语义边界可能需要人工复核。
- 知识附件缺少事实会降低支持率；这不能单独证明文章事实错误。
- 不同 Skill 版本、文章拆分口径或知识附件下的分数不能直接比较。
