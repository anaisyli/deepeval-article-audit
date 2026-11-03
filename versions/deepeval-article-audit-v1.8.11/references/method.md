# 评审口径与判断文件格式（协议 2.0，审核 Skill v1.8.11）

## 0. 与官方 DeepEval 的区别

官方 Faithfulness 先抽 claims，再按 retrieval context 给 `yes/no/idk`；默认 `penalize_ambiguous_claims=False` 时 `idk` 计入兼容分子，含义是“未被矛盾”而非“得到支持”。本 Skill 只在机器 JSON/CSV 中保留 `deepeval_verdict` 兼容诊断；HTML、Markdown、50记录、月报和交接页都不展示兼容百分比，不与 RAGSEO 原文引用率或字词覆盖率混算。

## 1. 唯一公式与证据范围

```text
知识附件明确支撑率 = entailed / (entailed + unknown + contradicted)
```

- 计数单位是原子主张，不是词或句。百分比两位小数；分母为0显示 `N/A`。不设部分分：部分支持就继续拆分。
- `retrieval_context` 只含实际交给写作方、由用户明确提供的知识文件。v0.5/v0.6 固定且只能是当前 `30_本篇知识库资料.md`；不得追加 `35`、Formal Claim、整个知识库或原始随文事实文件。
- 指标没有目标值，不判定文章质量、发布资格或是否重写；0% 或很低都可以是正常结果。不得漏提、合并可分别判断的主张、扩大证据含义或把建议归为非主张来抬高比例。

## 2. 分母：两轮盲抽取、并集冻结

可验证主张是能单独判断真假的最小陈述。内部字段仍为 `factual/non_factual`，人读为“可验证主张/非主张内容”。

- **两轮独立全文识别**：每轮逐一覆盖全部 `articleUnit`，写自己的 `coverage_review` 和原子 `claim_inventory`，第二轮看不到第一轮清单、结论和理由。最终取**并集**：任一轮识别为主张即进入分母；识别或拆分分歧仍进入分母，来源（`inventory_origin`）只表示待判断，不是结论。两轮措辞不同的同一命题由 `freeze` 按实词配对合并为一条，不因措辞重复计数；否定词或数字不同时绝不合并。第二轮的 `coverage_review` 就是第二次独立覆盖复查，不做第三次全文扫描；它要专门挑战非主张标签，找建议、表格、比较、流程和因果解释里的事实前提。
- **分母在查证据前冻结**（`audit_preflight.py freeze`）。只有两轮都确认属于允许的非主张类别时才排除。缺候选证据、没找到依据或附件不支持，永远不是“非主张”理由：先判事实性，再把缺证主张判为 unknown。
- **结构排除**：文章标题、各级小标题、表头、网址、目录、元数据、提示词和审核管理文字不计入。prepared 中 `unit_type=title/heading/table_header`（Markdown 标题、标准表头、桥接器识别的旧式连续 pipe 表头、DOCX 明确结构）确定性排除，不检索候选证据，不能用 `heading_override_reason` 恢复，只在明细和 Highlight 中灰显；表格数据行照常审核。纯 Markdown 图片语法、路径和 alt 文本在生成 articleUnit 前删除，同行正文保留。受管模式下与记录标题相同的首个正文行在准备阶段排除。只有无任何结构标记的孤立小标题候选交给两轮判断，确属事实正文时须写至少20字符的具体 `heading_override_reason`。
- **问题**：纯问题（以 `?`/`？` 结尾的 claim）永远不是主张；问题中断言的事实前提改写成陈述句单独提取并说明推导。FAQ 回答照常审核。结构化标题即使以问号结尾，分类仍是 `heading`；`question` 只用于非结构单元。
- **建议与方法**：建议、行动指令、操作方法、比较和表格数据行只要表达具体做法、关系、效果、条件或预期结果就审核，祈使语气不是排除理由。建议只有事实理由可核验时提取理由；建议本身是可验证操作方法时也提取该方法。忽略纯偏好、CTA、修辞句和没有可验证关系的条件句。
- **话语框架**：审查信号（视图中单元后的“信号:”）只提示两轮重点检查，不把空的列表引入句或过渡语变成事实。两轮都独立判为 `non_factual` 且类别合规时即排除，有信号的单元记入 `signaled_nonclaim_unit_ids` 供人工抽查，不再整单元兜底；后续列表项各自独立审核。缺第二轮分类或两轮分歧时留在分母。覆盖兜底之后再次执行纯问题与结构排除，兜底不能让它们复活。
- **每个事实只写一次**：同一单元里不要既写概括句又写逐项句（如“流程包括出设计稿”和“第一步出设计稿”），重复的事实会在分母和分子里各算两次。
- **原子性**：对 `and / but / because / so / while / whereas / if / therefore`、分号、中文对应连接词、冒号/列表和多个谓词逐一复核——它们是信号，不是机械拆分点；标准是各部分能否得到不同 verdict。独立谓词、条件、因果/效果关系或转折分句必须拆开，不能用“同一操作序列”合并。
- **阅读抽取**：不按句号或逗号机械切分，也不把多个不同事实合成一个主张；每条主张是带主语和谓词、可独立判断真假的完整命题。抽取清单和判断理由都由模型逐条阅读写出，不能用脚本或模板生成。紧凑格式只省机械字段，不能为省 Token 把整句当一条主张。某一轮（≥20条）超过10%的主张是短语碎片（如 `size`、`For wholesale buyers`）时 `freeze` 拒绝该轮。
- **同质枚举**：成员共享同一主体、谓词/操作、范围、条件、时间和语气时保留为一条集合值主张，不因可逐项枚举而拆；这些维度不同就拆。边界在看证据前决定，不能因证据只覆盖部分成员临时拆分，也不能为降分母合并不同关系。保留复合外观或同质列表为一条时，可写至少20字符的 `atomicity_note` 说明共享维度；收敛时只要第二次判断的 `atomicity_review` 确认 `atomic` 且 `independent_proposition_count` 为1（理由至少12字符），或写了该说明，“支持”即可保留，否则降为 unknown。
- **列表片段**：只是名词/宾语短语的列表项要继承紧邻前导句的最小主体和谓词。例：前导 `A useful family brief keeps a few rules constant:`，项 `opening direction and main logo zone` → `A useful family brief keeps opening direction and main logo zone constant.`。`article_quote` 保留列表项原文，`derivation_note` 写明前导 `unit_id`、继承的主体/谓词以及为何没有加入新事实。孤立片段不得直接作为 claim。
- **兜底**：规则信号只是漏检后的最后保险。只能把复合单元整体恢复为一条 unknown 时必须记录 coverage warning，说明分母可能低估，且不得签发100%。

```text
Model A uses a xenon lamp and covers 190–900 nm. → ① uses a xenon lamp ② covers 190–900 nm
JERL offers paper, wood, velvet and leather. → 一条集合值主张
JERL offers paper packaging and has a 7-day lead time. → ① offers paper packaging ② has a 7-day lead time
A boutique display may need ..., while a gifting program may need ... → 按 while 两侧拆成两条
```

### 2.1 绿色单元完整性复核

每轮抽取写完一个单元时，反向读一遍原文，确认每个独立事实谓词都有自己的原子主张（`A, but B` 中 B 可核验时必须有单独的 B），否则该单元即使全部主张都 entailed 也会显示为“全部明确支持”而漏掉 B。这是抽取时的自查，不是第三次全文扫描，也不要求拆开同质枚举。冻结后不再补拆：漏掉的谓词只能在新一次尝试（reset 后两轮重新抽取）中补上。

## 3. 三态判断

| 状态 | 人读 | 颜色 | 含义 |
|---|---|---|---|
| `entailed` | 明确支持 | 绿 | 原文直接陈述，或不增加实质信息即可推出 |
| `unknown` | 尚未确认（证据不足） | 黄 | 无依据、依据不足、只覆盖一部分、范围扩大、版本/条件无法对齐、复核分歧或缺失 |
| `contradicted` | 明确冲突 | 红 | 同一对象、范围、条件和时间/版本下允许证据明确陈述不相容事实，且复核确认 |

- 机器字段：`entailed→yes/supported`，`unknown→idk/unsupported`，`contradicted→no/unsupported`。合并的 unsupported 不得在人读页面替代三态。
- 同主题不是蕴含。必须同时覆盖主体、对象、关系/谓词、范围、条件、数量、时间/版本和效果/因果。只列材料、尺寸、类别、流程，或同属一个产品/业务概念，不能补足客户能力、效果或适用范围。符合常识但附件未支持仍是 unknown。改写、同义替换和语序变化可以 entailed，不要求逐字。
- **按允许证据整体判断**：读本篇全部允许证据正文（证据视图），不只看某一句或第一轮引用的那一句；支持可以来自几句的组合，只要每句都在允许证据内。证据正文之外的标题、归属字段和写作说明仍不能作为证据。
- **看推理方向**：资料的说法比主张强或更具体时，可以推出主张——“必须/应当提供”推出“可以包含”，“按某标准认证的设备”推出“受控条件”，具体实例推出不更宽的概括，意思不变的改写、缩写和语序变化都算 entailed。资料的说法比主张弱时不能推出——“允许/可以/在某条件下”不能变成“应该/优先/总是”，“部分/某些”不能变成“全部”，资料没写的目的、好处、效果、范围或适用对象不能补上；这些是 unknown，不是 contradicted。
- 集合值主张须覆盖全部实质成员才 entailed；部分覆盖为 unknown；任一被断言成员在同范围下被明确否定可整条 contradicted，理由点名该成员。
- 缺证、部分覆盖、歧义或版本不同永远不是 contradicted。
- 范围、数量、因果等维度由主张的实际含义决定，不由某个词决定：措辞信号（如 when、all、helps、made、support、数字）只提示复核检查该维度。名称或名词里的效果词（某指南的名称、“benefit claims”这类类别名）、材料构成（made from）、条件性的可行性说明（when time allows）都可能不断言效果，此时 `causal_effect` 判 `not_applicable` 并写明理由；真正断言效果的（“benefits customers”“improves trust”）仍需证据覆盖。
- **主体归属**：`we/our/this` 只有在允许证据正文与紧邻绑定的已核验 EC-1 归属块共同唯一确定所指时才可解析，此时允许最小指代补全和忠实改写；否则 `subject_object=missing` 并保持 unknown。不得从产品列表、材料列表、来源标题或主题相似推断客户能力。

## 4. 证据要求

每条 entailed 主张至少一条证据：知识文件绝对路径、行号范围、逐字连续原文、一句具体理由。unknown/contradicted 可记录最接近的原文；完全无关时 `evidence` 为空数组，并写“提供的知识文件中未找到可确认该主张的证据”。

- `article_quote` 与证据 `quote` 都是原始 Markdown 的连续子串，范围内的 `**`、反引号、链接语法或引用符号原样保留。`article_quote` 尽量取包含该主张的最短片段，不得用改写后的主张冒充原句。
- 行号留空，由 `assemble-judgments` 填写：判为 entailed 的引文必须位于允许证据块内（写了视图块编号 `K0001`… 时须在该块内），否则组装报错；其他状态的引文按其实际所在行填写。
- 当前 v0.5/v0.6 的 `30`：只有第1–3节中标题为 `证据正文（供Faithfulness核验）` 的内容是正向证据，引用起止行必须完整位于同一个证据正文块。直接写作事实、英文表达表、数据展示表本身、按大纲使用、生成控制、缺口处理和元数据都不能引用；表格事实必须引用配套证据正文。
- `30` 不显示 Formal Claim ID，本 Skill 不读同目录的 `35_写作素材来源索引.md`（它不属于 retrieval context）；接收方按证据区间与 `35` 映射区间的重叠确定 Formal Claim。
- **EC-1**：新包头部含 `证据模板版本：EC-1`；每个事实块按固定顺序有四级标题“证据归属（仅用于指代解析）”（内容提供方、对应对象、指代关系、核验状态=已核验）并紧邻四级“证据正文（供Faithfulness核验）”，不得跨块或跨章节绑定，不得含 URL、Claim ID 或新事实；缺块、跨块或状态非“已核验”时渲染器拒绝受管包。归属块是管理元数据，不能作为普通 `evidence`，不能单独支持能力、效果、认证或范围，也不进入 `35` 映射。支持判断依赖它时，该 claim 需要 `coreference_evidence`（`source_file/line_start/line_end/quote` 逐字对应同一块的归属板块，`reason` 说明解析关系，并同时保留同一事实块的普通 `evidence`；多个含指代的证据块各自记录）。紧凑流程中由 `assemble-judgments` 按证据引文自动附加并当场校验，模型不写这个字段。主体仍不清时保留引文行号，由知识库归入 `evidence_context_gap`；当前 `30` 没有对应证据时按 MAT、ANM、CUS 或公共知识缺口处理。未标 EC-1 的历史资料按原证据边界读取，不推定归属。

```markdown
### [事实标题]

#### 证据归属（仅用于指代解析）
- 内容提供方：[来源已核验主体]
- 对应对象：[当前事实产品或服务对象]
- 指代关系：[we/our/this的唯一指向；无则写“无”]
- 核验状态：已核验

#### 证据正文（供Faithfulness核验）
> [当前30中逐字来源证据或已核验严格翻译]
```

## 5. 主判断记录的约束

紧凑写法见 `audit-preflight.md`；`assemble-judgments` 生成的完整 `judgments.json` 结构见 `record-format.md`，不手写。约束：

- `coverage_review.unit_classifications` 覆盖 prepared 中每个 `unit_id` 且不重复；`factual` 单元至少一条主张，`non_factual` 单元不得有主张，并给固定 `category` 和逐单元具体理由。允许类别只有 `title`、`heading`、`table_header`、`question`、`transition`、`cta`、`subjective`、`hypothetical`、`pure_recommendation`。有候选证据却被排除的单元列入 `reconsidered_candidate_unit_ids`。每个受管结果都必须带完整 `coverage_review`，不是只在占比低时补做。
- `claim_id` 按文章顺序 `C001`、`C002`… 不重复，只是显示标签。同一 `unit_id + 规范化命题` 不得出现两次；重复项收敛前合并，verdict 不一致则保留一条并降为 unknown。
- `unit_id` 对应 prepared 单元；`article_line` 等于该单元原始行号（`freeze` 会按 `unit_id` 自动填写）；`article_quote` 是该单元的连续子串。
- `claim` 直接来自同一 `article_quote`，只做原子化所需的最小规范化，不得挂其他句子或其他 FAQ 单元的主张。忠实规范化后与引文没有共享词时，必须加具体 `derivation_note`。
- `reason` 具体且至少12字符；受管文章不得整篇复用同一判断、排除或复核理由，每条理由点明该主张与证据的关系或精确缺口。
- entailed 的 `evidence` 不得为空，证据 `quote` 必须逐字存在于所标行号范围。
- v0.5 终稿的 `文章ID` 优先于文件名，显式 ID 不得与元数据冲突。
- 旧协议1.0/schema 1.0 结果仍可读取；其内嵌 `quality_review` 见 `record-format.md`。

## 6. 第二次证据判断（`decisive_recheck_v1`）

**整篇全 unknown 的流程门禁**：主判断组装后，逐篇检查；有冻结主张且全部为 `unknown` 时，先暂停并对照阅读视图检查是否漏用了直接、完整的允许证据。这个过程诊断不是第二次证据判断，不增加复核行，也不能改变任何 verdict。若发现漏用或不能确认，按审核 Skill 的 reset 规则丢弃本次尝试并从两轮新抽取开始重判；不得在后续复核中把 unknown 升为支持。若未发现漏用，保留全体 unknown 并继续正常流程。

**范围**：只对第一轮 `semantic_status` 为 `entailed` 或 `contradicted` 的冻结主张做第二次独立证据判断。第一轮 unknown 不复核、不进入 `claim_reviews`，最终保持 unknown（`review_depth: first_pass_unknown`，`independent_semantic_status: not_reviewed`）。用 `audit_preflight.py recheck-list` 生成复核清单：只含 `claim_id/unit_id/article_line/article_quote/claim`，不含第一轮结论、证据或理由。

**独立性**：第二轮最好在独立子代理或新会话中进行，只拿到复核清单和证据视图；没有独立上下文时也必须重新判断，不读取、不复述第一轮结论与理由。

**判断方式**：与主判断用同一标准（§3 的“按允许证据整体判断”和“看推理方向”），对照全部允许证据，不只核对第一轮可能引用的那一句；严格不等于只认字面一致。需要第一轮没有引用的证据句时，在复核行写 `evidence`（`quote` 为允许证据中的逐字片段，`source_file` 可省略或写 `F1`/`K0001`），`evidence_fragment` 可取自这些句子，`check` 会把它们并入该主张的证据（`citation_list_omission`）。

- `claim_inventory` 是第二轮盲抽取结果，`matched_claim_id` 由 `assemble-review` 按冻结来源填写；第二轮独有主张留空，由 `freeze` 纳入并集。新运行不写 `review_risk_level/review_risk_categories`。
- 每条复核行重复最终主张的 `unit_id`、逐字 `article_quote` 和完整 `claim`，三者共同构成命题身份；`claim_fragment` 只说明本次检查的成分，可短可重复，必须逐字属于该 claim，不参与匹配。
- **判为 entailed 的行**须有 `atomicity_review` 和五项 `entailment_checks`（紧凑复核行可省略主张不含的维度，`assemble-review` 只为未被措辞信号要求的维度补 `not_applicable`；被要求的维度缺失即按缺项降为 unknown）：`subject_object`、`predicate_relation` 必须 `covered`；主张措辞含范围/条件、数量/时间/版本、因果/效果信号时对应项必须写出：`covered`，或复核认为主张实际不含该维度时写 `not_applicable` 并给出自己的理由（至少8字符）；信号词只提示检查，复核的明确判断优先，脚本补写的理由不算。`covered` 同时给出主张片段和已引用证据片段，不能用总结代替原文对齐；任一项 `missing` 即 unknown。`evidence_fragment` 必须出现在该主张已引用证据中。
- **判为 contradicted 的行**不需要 `entailment_checks`，须给 `contradiction_evidence_fragment`（在已引用证据中）和 `contradiction_alignment` 三项 `subject_object/scope_condition/time_version` 均为 `aligned`；任一无法确认即 unknown。
- `atomicity_review.status` 只能 `atomic` 或 `split_required`。`split_required` 表示冻结的主张含多个命题：收敛时该主张降为 unknown，不自动拆分，也不改冻结分母；不转人工。同质枚举可记为一个 `atomic` 集合命题并说明共享维度。
- `challenge_reason` 至少20字符，指出本条特有的覆盖或缺口，不复制第一轮 `reason`；同一模板用于多条拟支持主张时，收敛器把它们降为 unknown。
- 若全部主张最终 entailed，adversarial review 另附 `all_supported_challenge`：`performed: true`，`challenged_claim_ids` 按顺序列出全部复核主张，`conclusion` 至少30字符说明实际检查内容。

## 7. 收敛（`reconcile_article_audit.py`）

收敛只允许保留或降级，绝不把第一轮 unknown/contradicted 升级为 entailed，也不建立人工队列：

- 第一轮 unknown：最终 unknown，`first_pass_unknown`；
- 两轮均 entailed 且原子性与全部适用检查通过：保留 entailed（`dual_pass`）；
- 两轮均 contradicted 且冲突证据与对齐完整：保留 contradicted（`dual_pass`）；
- 应复核却缺复核行：保留一条 `review_status: missing` 的占位行，最终 unknown（`missing_second_pass_review`）；
- 两轮分歧、检查缺项、模板化理由或未解决复合主张：降为 `idk / unknown / unsupported`，继续出报告。

身份对齐：`claim_id`/`inventory_id` 不是匹配键。先在各轮内部按 `unit_id + 规范化命题` 去重，再要求 `unit_id`、`article_quote`、完整规范化 `claim` 三者一致；编号指错但存在唯一相同身份时自动重绑。历史复核行缺身份字段时，只有 `claim_fragment` 等于一条完整且唯一的 claim 才允许一次性迁移。没有唯一对应时隔离该行，不转移其证据与结论，真实命题记 unknown 并继续；只有正文来源本身不可恢复才是输入错误。仅在收敛时才发现、尚未独立判断的漏项保守记 unknown。

结构清理：第二轮清单并入前复用两轮 `unit_classifications`——结构单元直接排除；其他单元两轮都标 `non_factual` 且类别合规时排除（有复核信号的记入 `signaled_nonclaim_unit_ids`），记录 `second_pass_inventory_ids_excluded` 并按 `inventory_id` 幂等。随后对第一轮 claims、`claim_reviews`、`claim_inventory` 统一移除纯问题，记录 `pure_question_claim_ids_excluded` / `pure_question_inventory_ids_excluded`。旧结果已标完成却仍有纯问题残留时，渲染器在内存中重跑清理；输出到原受管目录才持久化，输出到独立复核目录只读。

收敛写入 judgments 与 adversarial review 的 `reconciliation` 字段、历史 `two_pass_consensus`/`risk_weighted_v1` 口径和旧协议 1.0 的读取规则见 `record-format.md`；新运行用不到。

## 8. 报告

- 明细：主张编号、文章行、文章原文、原子主张、语义状态、复核深度、知识库原文与位置、理由。
- 汇总：一篇一行，前七列位置固定兼容导入器（文章编号、标题、明确支持数、全部可验证主张数、尚未确认与明确冲突合计、支撑率、评审模式），其后追加尚未确认、明确冲突和复核深度。单元格中的英文竖线 `|` 转为 HTML 实体；交接时不手工重排。
- 内容单元诊断（不改变 claim 级计算）：有主张的单元为“可验证主张”，否则“非主张内容，不计入”；可验证单元聚合为全部明确支持 / 部分尚未确认（entailed+unknown）/ 全部尚未确认 / 存在明确冲突。全 unknown 单元不得叫“不支持”或与冲突同色。“可验证内容占比”= 可验证单元 ÷ 全部单元，不等于 Faithfulness 或原文引用率。
- Highlight：完整正文按 prepared 最小单元（句子、列表项、表格数据行）逐卡渲染，同一 Markdown 行的多个单元不合并；每卡显示行号、`unit_id`、状态、聚合结果和主张数；筛选“全部/可验证/明确支持/尚未确认/明确冲突/非主张”。“明确支持”筛选是主张级：混合单元保持可见，只隐藏其中非 entailed 的主张卡。不显示 `yes/no/idk`、兼容率或合并的 unsupported 筛选，不用固定在顶部或右上角的独立来源面板。
- 渲染前断言：汇总分子等于最终 entailed 数，且每条最终主张恰好挂在一个渲染单元上；不符是渲染器错误，不是隐藏卡片的理由。

## 9. manage-article-knowledge v0.5/v0.6 交接

导入必需 `<article-id>-prepared.json`、`<article-id>-judgments.json`、`faithfulness_summary.md`，协议2.0另需同目录 `<article-id>-adversarial-review.json`。v0.6 受管结果位于知识库配置计算出的 `[结果根目录]/[项目ID]/[文章ID]/v[文章版本]/`（目录名不重复版本前缀）。导入还需要当前 `40`、实际审核的全部知识文件（按 prepared 中的顺序）、文章版本和完成日期。文章解析单元/行号、知识证据块/行锚/允许范围或身份上下文改变后旧结果失效，应重审而不是改旧 JSON；只改换行、编码或表格首尾竖线等未改变这些解析输入的序列化可复用，原始 SHA-256 仍保留追踪。受管 prepare 的 writing_ready 字节绑定和知识库导入校验仍独立生效。知识库导入器把 `reconciliation.policy` 记入 Faithfulness 明细 CSV 的 `review_policy`、50记录和月报。
