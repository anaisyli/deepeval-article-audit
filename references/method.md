# 评审口径与判断文件格式（协议 2.0）

## 0. 官方 DeepEval 与本 Skill 的区别

GitHub 版 DeepEval Faithfulness 是 LLM-as-a-judge：先抽取 claims，再依据 retrieval context 生成 `yes`、`no` 或 `idk`。默认 `penalize_ambiguous_claims=False` 时，计分实现会把 `idk` 计入兼容分子；它表达的是“没有被上下文直接矛盾”，不是“得到支持”。本 Skill 仅在机器 JSON/CSV 中保留该结果作为 `deepeval_verdict` 兼容诊断；HTML、Markdown、单篇记录、月报和交接页均不展示该兼容百分比。知识库正式指标使用更严格的语义支撑结果。

## 0.1 严格语义协议

新受管结果使用 `evaluation_protocol_version: "2.0"` 和 judgments `schema_version: "1.1"`。每条 claim 必须记录：

```json
{"deepeval_verdict":"idk","semantic_status":"unknown","verdict":"unsupported"}
```

`semantic_status` 只能是 `entailed`、`contradicted` 或 `unknown`；只有 `entailed` 才能映射为 `verdict: supported`。人读状态固定为：灰色非主张不计入、绿色明确支持、黄色尚未确认（证据不足）、红色明确冲突。证据与主张仅主题相关，或只列出材料、尺寸、分类、流程而没有文章所说的因果、效果、体验、规模化或适用关系时，一律为 `unknown`。`contradicted`只用于两轮都确认允许证据对同一对象、范围、条件和时间/版本明确给出不相容事实；完全没有证据、证据只覆盖一部分、版本无法对齐或两轮有分歧仍是`unknown`，不能因为“没有找到”而判为冲突。

受管协议 2.0 必须另有 `<article-id>-adversarial-review.json`，由盲审复核阶段逐条检查对象、断言、范围、数量、条件、时间/版本及因果/效果关系。第二轮只能看到主张、文章引文和允许证据，不读取第一轮 verdict、semantic_status 或 reason；缺失、分歧或覆盖不全时不得签发严格支持结果。

两轮完成后必须运行 `scripts/reconcile_article_audit.py`。收敛器只允许“保留或降级”，绝不把第一轮 unknown/contradicted 升级为 entailed：

- 两轮均为 entailed，且原子性与全部适用语义组件检查通过：保留 entailed；
- 两轮均为 contradicted，且有明确冲突证据片段：保留 contradicted；
- 其他所有情况：自动写为 idk / unknown / unsupported，继续生成报告，不进入人工队列。

## 1. 唯一计算公式

复现 DeepEval Faithfulness 的核心步骤：先从文章正文提取事实主张，再用同一个评审者逐条判断其是否得到 `retrieval_context` 支持。

```text
知识附件明确支撑率 = entailed / (entailed + unknown + contradicted)
```

本 Skill 中的 `retrieval_context` 仅指实际交给写作Codex的、由用户明确提供的事实知识文件。对`manage-article-knowledge v0.5/v0.6`，固定且只能是当前文章任务中的`30_本篇知识库资料.md`；不能追加`35`、Formal Claim、整个项目知识库或原始随文事实文件。随文事实文件应先由知识库流程合并进当前`30/35`。

该指标没有目标值，也不判定文章质量、发布资格或是否需要重写。0%、低分或只有一条明确支持都可以是正常审核结果。不得通过漏提主张、合并可分别判断的主张、扩大证据含义或把建议归为非主张来抬高比例。

## 2. 分母：可验证主张总数

可验证主张是可以单独判断真假的最小陈述。内部字段为兼容既有结果仍使用`factual/non_factual`，人读时理解为“可验证主张/非主张内容”，不是只审核陈述语气的客观事实。

分母必须在查证据和判绿黄红之前冻结。执行两轮相互独立的全文识别，每轮都逐一覆盖全部`articleUnit`并形成自己的原子主张清单；最终取两轮并集，不取交集。任一轮识别为可验证主张就进入分母；两轮对事实性或原子拆分有分歧时仍进入分母并先标为`unknown`。只有两轮都确认属于允许的非主张类别时才排除。

- 忽略标题、各级小标题、表格表头、网址、目录、元数据和操作说明；表格数据行仍按事实关系正常审核。
- 纯Markdown图片标记、图片路径和图片alt文本在articleUnit生成前确定性排除；同一行另有正文时，只保留移除图片语法后的正文。
- 忽略纯问题、纯偏好、CTA、修辞句和没有可验证关系的条件句。
- 建议、行动指令、操作方法、比较和表格数据行只要表达了具体做法、关系、效果、条件或预期结果，就进入审核；不能因为不是完整陈述句或使用祈使语气而排除。
- 建议句若只有事实理由可核验，提取事实理由；若建议本身是一项可验证的具体操作方法，也提取该方法主张。
- 一个句子含多个可分别判断的事实时，拆成多个原子主张。并列名词是否拆分取决于其是否共享同一主体、谓词和语义边界；独立谓词、条件、因果/效果关系或转折分句只要可能获得不同 verdict，就必须拆分，不能用“同一操作序列”合并。
- 不要仅因为成员可以逐项枚举，就把同质列表机械拆成多条。成员共享同一主体、谓词/操作关系、范围、条件、时间和语气时，默认保留为一条“集合值主张”；只有这些语义维度不同、因而可能由不同证据支持或得到不同 verdict 时才拆分。
- 集合边界必须在查看证据前确定，不能因为当前证据只覆盖部分成员而临时拆分，也不能为了降低分母把不同对象、关系、条件、数量/时间、效果或因果合并。
- 不按句号机械计数，也不把多个不同事实合成一个主张。
- 对`and / but / because / so / while / if / therefore`、中文对应连接词、冒号/列表和多个谓词进行原子性复核；它们是必须检查的信号，不是机械拆分符。判断标准是各部分能否得到不同 verdict。
- 列表项若只是名词短语、宾语短语或其他不完整片段，必须继承紧邻前导句提供的最小主体和谓词，形成可独立核验的命题后再审核。例如前导句为`A useful family brief keeps a few rules constant:`时，`opening direction and main logo zone`应审核为`A useful family brief keeps opening direction and main logo zone constant.`。`article_quote`仍保留列表项原文，并在`derivation_note`写明前导单元、继承的主体/谓词和最小补全方式。
- 规则信号只能做漏检后的最后保险，不能替代第二轮全文识别。若兜底只能把一个复合单元整体恢复为一条`unknown`，必须记录coverage warning，明确分母仍可能被低估；不得因此签发100%或把该单元排除。

示例：

```text
原句：Model A uses a xenon lamp and covers 190–900 nm.
主张1：Model A uses a xenon lamp.
主张2：Model A covers 190–900 nm.

原句：JERL offers paper, wood, velvet and leather.
主张1：JERL offers paper, wood, velvet and leather.（同一供应关系下的集合值）

原句：JERL offers paper packaging and has a 7-day lead time.
主张1：JERL offers paper packaging.
主张2：JERL has a 7-day lead time.

原句：A boutique display may need a clean opening sequence and strong logo visibility, while a gifting program may need space for a warranty card, strap, or care leaflet.
主张1：A boutique display may need a clean opening sequence and strong logo visibility.
主张2：A gifting program may need space for a warranty card, strap, or care leaflet.
```

集合值主张只有在证据覆盖全部实质成员时才是`entailed`；只覆盖部分成员为`unknown`。允许证据若在同一对象、范围、条件和时间/版本下明确否定任一被断言成员，整条集合主张可为`contradicted`，理由必须定位发生冲突的成员。

### 2.1 绿色内容单元完整性复核

在渲染某个内容单元为`fully_supported`之前，必须反向阅读该单元的原文，并逐一核对其中每个独立、可验证的事实谓词是否都有对应的原子主张。该检查只针对拟标绿色的单元，目的在于防止一条已支持的前半句掩盖同句后半句。

- 若原单元为`A, but B`、`A and B`、`A, while B`等结构，而`B`是可独立核验的事实关系，则必须存在分别审核的`A`和`B`主张；不能只保留`A`并给整单元标绿。
- 若发现遗漏谓词，先拆出遗漏主张并完成两轮核验；无法稳定拆分时保留整个单元为`unknown`并写入覆盖警告。
- 只有原单元所有独立事实谓词均已进入冻结分母且均为`entailed`，才允许显示`fully_supported`。该检查不要求把同一谓词下的同质枚举机械拆开。

## 3. 分子：得到支持的事实主张数

正式人读结论使用三种语义状态：

- `entailed`（明确支持）：知识库原文直接陈述该事实，或在不增加实质信息的情况下能够推出该事实。
- `unknown`（尚未确认/证据不足）：知识库未提供依据、依据不足、只支持一部分、文章扩大了范围、版本或适用条件无法对齐，或者两轮有分歧。
- `contradicted`（明确冲突）：同一对象、范围、条件和时间/版本下，允许证据明确陈述不相容事实，且两轮都确认该冲突。

机器兼容字段仍把`entailed`映射为`supported`，把`unknown`和`contradicted`映射为`unsupported`；该合并字段不能出现在人读页面中替代三态含义。

不要设置部分分。遇到部分支持的复合陈述，继续拆分原子主张。

改写、同义替换和语序变化可以判为 `supported`。与知识库逐字相同不是条件。反过来，一条说法即使符合常识，只要提供的知识库没有支持，也判为 `unsupported`。

## 4. 证据要求

每个 `supported` 主张必须提供：

- 知识文件绝对路径；
- 原始行号范围；
- 从知识文件逐字复制的连续原文；
- 一句简短理由，说明原文如何支持文章主张。

`unknown`或`contradicted`主张可以记录最接近的原文；如果完全没有相关原文，只能是`unknown`，`evidence` 使用空数组，并明确写“提供的知识文件中未找到可确认该主张的证据”。

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

### 5.1 受管事实抽取完整性复核

每个受管 v0.6 结果都必须在 `judgments.json` 附带完整的 `coverage_review`，不能只在事实单元占比低时补做：

```json
{
  "coverage_review": {
    "reviewed_unit_count": 79,
    "factual_unit_count": 18,
    "non_factual_unit_count": 61,
    "unit_classifications": [
      {"unit_id": "U001", "classification": "non_factual", "category": "heading", "reason": "该单元是章节标题，只命名后续主题，不陈述产品事实。"},
      {"unit_id": "U002", "classification": "factual", "reason": "包含关于运输和陈列关系的可核验断言。"}
    ],
    "reconsidered_candidate_unit_ids": ["U002"]
  }
}
```

`unit_classifications` 必须覆盖 prepared 中的每个 `unit_id` 且不得重复；`factual` 单元必须至少对应一条原子主张，`non_factual` 单元不得对应主张，并必须提供固定 `category` 与逐单元具体理由。允许的非事实分类只有：`title`、`heading`、`table_header`、`question`、`transition`、`cta`、`subjective`、`hypothetical`、`pure_recommendation`。prepared 已标记为`unit_type=title/heading/table_header`的结构单元确定性归入对应非事实分类，不检索候选证据、不得创建主张，也不能用`heading_override_reason`恢复；Markdown标题、标准Markdown表头、桥接器兼容识别的旧式连续pipe表头，以及由知识库桥接器从DOCX明确结构转换的标题和表头都适用。孤立且无任何结构标记的小标题候选才交给两轮复核，并在确属事实正文时使用override。缺少候选证据、没有找到依据或知识附件未提供支持，均不能作为“非事实”的理由；应先独立判断事实性，再把无支持证据的事实主张判为 `unsupported`。带有候选证据但被排除的内容单元必须出现在 `reconsidered_candidate_unit_ids`。排除理由不得整篇复用同一模板。该复核只防止漏提主张，不改变 `supported / total factual claims` 公式。

### 5.2 受管语义质量反向复核

每个受管 v0.6 结果还必须附带 `quality_review`，逐条反向挑战第一次判断：

```json
{
  "quality_review": {
    "gate_version": "1.0",
    "review_mode": "independent_adversarial",
    "reviewed_claim_count": 1,
    "claim_reviews": [
      {
        "claim_id": "C001",
        "confirmed_verdict": "supported",
        "claim_fragment": "uses a xenon lamp",
        "evidence_fragment": "uses a xenon lamp",
        "challenge_reason": "反向检查后，引用原文直接说明同一型号使用氙灯，没有把其他型号或更宽范围带入主张。"
      }
    ]
  }
}
```

- `gate_version` 固定为 `1.0`，`review_mode` 固定为 `independent_adversarial`。
- `claim_reviews` 必须与主判断中的全部 `claim_id` 一一对应；`confirmed_verdict` 必须与主判断一致。如反向复核不一致，先修正主判断，不能在两处保留冲突。
- `claim_fragment` 必须来自当前原子主张；`supported` 的 `evidence_fragment` 必须逐字来自该主张已经引用的证据。`unsupported` 可以把 `evidence_fragment` 留空。
- `challenge_reason` 应具体说明范围、对象、机制、因果或缺口的核对结果，不能复制第一次 `reason`。如果批量结果中理由不可避免地重复，渲染器记录`audit_warnings`并继续生成报告；这不改变`semantic_status`或知识附件明确支撑率，也不转成需要人工逐条处理。只有输入、身份、引文或字段结构不可解析时才停止。

如果全部主张都为 `supported`，`quality_review` 还必须包含：

```json
{
  "all_supported_challenge": {
    "performed": true,
    "challenged_claim_ids": ["C001", "C002"],
    "conclusion": "已按文章顺序反向检查每条主张与引用证据的对象、范围和因果关系，未发现仅主题相关或扩大表述。"
  }
}
```

`challenged_claim_ids` 必须按主判断顺序列出全部主张，结论必须说明实际检查内容。旧受管结果如果没有当前完整 `coverage_review` 和 `quality_review`，不能仅凭旧的 100% 分数视为已通过当前 Skill；应以不覆盖旧文件的新版本结果重新审核后再决定是否导入或保留为 current。

### 5.3 协议2.0盲审、分母并集与自动收敛

协议2.0的独立文件先写第二轮全文`coverage_review`和`claim_inventory`。第二轮形成这两项时不得读取第一轮清单、结论或理由。两轮清单完成后才填写`matched_claim_id`：能确认是同一原子命题时指向第一轮ID；第二轮独有主张留空，由收敛器加入分母并标为`unknown`。第一轮独有主张也保留。

`claim_id`和`inventory_id`只用于显示与追踪，不能作为两轮匹配依据。收敛时先在各轮内部按`unit_id + 规范化命题`去重，再按完整命题身份对齐两轮清单：正文`unit_id`、逐字`article_quote`和完整规范化`claim`必须同时一致。协议2.0的每条盲审行都重复保存这三个字段。`claim_fragment`只说明本次检查涉及完整主张中的哪个词语或语义成分，可以很短，也可以在多条主张中重复；它不参与匹配和重绑。若`claim_id`或`matched_claim_id`指错，但能找到唯一相同的完整命题身份，自动重绑。历史盲审行缺少完整身份字段时，仅当`claim_fragment`等于一条完整且唯一的claim，才允许一次性迁移；普通短片段不得作为迁移依据。若没有唯一对应项，隔离该盲审行或清单行，不把其证据与verdict转移给别的主张，并把真实正文命题保守记为`unknown`后继续生成报告。只有正文来源本身不可恢复时才属于输入身份错误；普通编号错位不得让整篇文章停止。示意：

第二轮`claim_inventory`并入分母前，收敛器还要复用两轮`coverage_review.unit_classifications`做非主张过滤：结构化`title`、`heading`和`table_header`直接确定性排除；其他单元只有两轮都明确标为`non_factual`，第一轮类别是`question`、`transition`或`cta`，且没有强制复核信号时，才排除该清单项。FAQ纯问题因此不进入分母，但FAQ回答和表格数据行中的事实主张仍按普通清单项保留；建议、方法、比较、因果或带强制复核信号的单元不能借“非事实”分类逃避并集。收敛器记录`second_pass_inventory_ids_excluded`，并按`inventory_id`保持幂等，重复收敛不得再次创建同一主张。

收敛器在此之后还要对第一轮`judgments.claims`、第二轮`claim_reviews`和`claim_inventory`做同一项结构清理：凡`claim`本身只是以`?`或`？`结尾的纯问题，均从Claim分母和盲审行中移除，并记录`pure_question_claim_ids_excluded`与`pure_question_inventory_ids_excluded`。这一步必须覆盖第一轮已误提的问题，不能只处理第二轮新增项，否则问题会在渲染阶段反复触发硬校验。问题中的事实前提应由主张识别阶段改写成陈述句；FAQ回答里的事实主张不受影响。

```json
{
  "coverage_review": {
    "reviewed_unit_count": 2,
    "unit_classifications": [
      {"unit_id":"U001","classification":"factual","reason":"包含可核验的操作关系。"},
      {"unit_id":"U002","classification":"non_factual","category":"question","reason":"只提出问题，没有断言关系。"}
    ]
  },
  "claim_inventory": [
    {"inventory_id":"P2-C001","unit_id":"U001","article_line":12,"article_quote":"Use a sealed tray to reduce exposure.","claim":"Use a sealed tray.","matched_claim_id":"C003"},
    {"inventory_id":"P2-C002","unit_id":"U001","article_line":12,"article_quote":"Use a sealed tray to reduce exposure.","claim":"A sealed tray reduces exposure.","matched_claim_id":""}
  ]
}
```

之后对冻结清单逐条填写`independent_semantic_status`，不得读取或复述第一轮证据结论：

```json
{
  "claim_id": "C001",
  "unit_id": "U001",
  "article_line": 12,
  "article_quote": "Another construction changes the filling sequence.",
  "claim": "Another construction changes the filling sequence.",
  "claim_fragment": "changes the filling sequence",
  "evidence_fragment": "",
  "independent_semantic_status": "unknown",
  "semantic_status": "unknown",
  "atomicity_review": {
    "status": "atomic",
    "independent_proposition_count": 1,
    "reason": "该主张只断言结构变化会改变填充顺序。"
  },
  "entailment_checks": {
    "subject_object": {"status": "covered", "claim_fragment": "another construction", "evidence_fragment": "another construction", "reason": "证据覆盖同一结构对象。"},
    "predicate_relation": {"status": "missing", "claim_fragment": "changes", "evidence_fragment": "", "reason": "证据没有说明结构会产生该变化。"},
    "scope_condition": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张没有额外适用条件。"},
    "quantity_time_version": {"status": "not_applicable", "claim_fragment": "", "evidence_fragment": "", "reason": "主张不含数量、时间或版本限定。"},
    "causal_effect": {"status": "missing", "claim_fragment": "changes the filling sequence", "evidence_fragment": "", "reason": "证据没有陈述该效果关系。"}
  },
  "challenge_reason": "虽然证据涉及包装结构，但没有说明结构会改变填充顺序，因此只能判为unknown。"
}
```

检查规则：

- `subject_object`和`predicate_relation`始终必须为`covered`才可能保留entailed。
- 主张出现条件、范围、数量、时间、版本或因果/效果内容时，对应字段也必须为`covered`；主张不包含该维度时才可写`not_applicable`。
- `covered`必须同时给出主张原文片段和已引用证据原文片段；不能用总结性理由代替原文对齐。
- 任一字段为`missing`，最终即为unknown。证据“沾边”、同属一个产品或提到同一步骤不构成覆盖。
- `contradicted`除提供`contradiction_evidence_fragment`外，还必须说明对象、范围、条件和时间/版本如何对齐；任一项无法确认即回到`unknown`。
- `atomicity_review.status`只能为`atomic`或`split_required`。若为`split_required`，先自动拆分并重建两轮判断；第二次仍无法稳定拆分时保留该内容为unknown并继续，不转人工。
- 同质枚举可在`atomicity_review`中记为一个`atomic`集合值命题，`independent_proposition_count`为1；理由必须说明成员共享的对象、关系、范围、条件、时间和语气。不同关系或可能获得不同 verdict 的部分仍应标记`split_required`。
- `challenge_reason`必须指出本条特有的覆盖或缺口；同一段模板用于多条拟支持主张时，收敛器将这些主张降为unknown。

若旧结果已经写入`reconciliation.completed=true`，渲染器仍会检查三份收敛输入中的纯问题残留；发现第一轮`claims`、第二轮`claim_reviews`或`claim_inventory`仍有纯问题时，先重新运行上述结构清理，再做硬校验。输出到同一受管结果目录时持久化修复后的JSON；输出到单独复核目录时只在内存修复，不改原结果。没有残留的结果保持幂等，不重复改写。

收敛后，judgments与adversarial-review都必须写入：

```json
{
  "reconciliation": {
    "gate_version": "1.0",
    "policy": "two_pass_consensus",
    "completed": true,
    "no_human_queue": true
  }
}
```

每条claim还记录第一轮、第二轮、最终状态、动作和原因。该记录用于审计，不改变现有`yes/no/idk`、`semantic_status`和`supported/unsupported`字段契约。

约束：

- `claim_id` 按文章顺序使用 `C001`、`C002`……且不得重复。
- 同一`unit_id + 规范化命题`不得出现两次；重复项在收敛前合并，若重复项 verdict 不一致，保留一条并降为`unknown`。
- `unit_id` 必须对应 prepared JSON 中的文章单元。
- `article_line` 必须等于该单元的原始行号。
- `article_quote` 必须是该文章单元的连续原文子串。
- `claim`必须直接来自同一`article_quote`，只允许为原子化而做最小规范化；不得把其他正文句或FAQ单元的主张挂到当前引文。若忠实规范化后与引文没有共享词语，必须增加`derivation_note`，具体说明引文到原子主张的转换；空泛说明不能替代对应关系。
- 每条协议2.0盲审行必须重复保存最终主张的`unit_id`、逐字`article_quote`和完整`claim`，三者共同构成身份；每个非空`matched_claim_id`也必须指向同一完整身份。`claim_fragment`只需逐字属于该完整`claim`，允许与其他主张重复，不得用于身份匹配。只要完整身份任一项不一致，就不能沿用该行的绿色或红色结论。
- 与文章标题相同的行和纯问题不能创建事实主张。prepared中的结构化`title/heading/table_header`永远不能创建主张；孤立的无 Markdown 标记小标题候选若确实是事实正文，才可增加至少20字符的具体 `heading_override_reason`。
- 含分号、`and`、`while`、`whereas`或列表等复合信号的候选若整句作为一个 claim 保留，必须增加至少20字符的 `atomicity_note`，说明为何它仍是一个可独立判断的命题；同质枚举应明确写出共享语义维度及其集合值边界。
- 由列表前导句补全的 claim 必须增加`derivation_note`，其中写明前导`unit_id`、继承的主体和谓词，以及为何补全没有加入列表项原文之外的新事实。孤立名词短语不得直接作为 claim。
- `verdict` 只能是 `supported` 或 `unsupported`。
- 每条 `reason` 必须具体且至少12字符；受管文章不得整篇复用同一模板理由。
- `supported` 的 `evidence` 不得为空。
- 证据 `quote` 必须逐字存在于标注的知识文件行号范围内。
- prepared与judgments的`schema_version`当前固定为`1.0`。
- v0.5终稿中的`文章ID`优先于通用文件名；显式传入的ID不得与元数据冲突。

## 6. 报告字段

明细表固定包含：主张编号、文章行号、文章原文、拆分后的事实主张、判断、知识库原文、来源文件与行号、判断理由。

整体统计表固定为一篇文章一行，前七列位置继续兼容导入器：文章编号、文章标题、明确支持主张数（分子）、全部可验证主张数（分母）、尚未确认与明确冲突合计、知识附件明确支撑率、评审模式；其后分别追加尚未确认和明确冲突数量。人读报告不显示DeepEval默认兼容百分比。

百分比保留两位小数。分母为零时显示 `N/A`，不得显示 0% 或 100%。

人类可读报告同时提供一层内容单元诊断，但不改变上述 claim-level 计算：以 prepared JSON 的每个 `articleUnit` 为一个内容单元；有一条或多条原子主张的单元标为“可验证主张”，没有主张的单元标为“非主张内容，不计入”。内部JSON为兼容仍保留`factual/non_factual`。可验证单元按语义状态聚合：全部`entailed`为“全部明确支持”；`entailed + unknown`为“部分尚未确认”；全部`unknown`为“全部尚未确认”；出现任何`contradicted`为“存在明确冲突”。旧式“完全不支持”不得同时指代全unknown和明确冲突。“可验证内容占比”=可验证内容单元数÷全部内容单元数，仅用于解释正文中哪些内容进入审核，不等于 Faithfulness，也不等于原文引用率。表格字段仍可在原子主张层逐项核验，表格所在内容单元同时显示聚合状态。

HTML 应展示完整文章内容，并直接按 prepared JSON 的最小内容单元逐条渲染：句子、列表项或表格数据行各自成为独立卡片；同一原始Markdown行中的多个单元不得重新合并。每个卡片显示原始行号、`unit_id`、可验证/非主张状态、语义聚合状态和原子主张数量。非主张单元灰显，明确支持为绿色，尚未确认为黄色，明确冲突为红色，并提供“全部/可验证/明确支持/尚未确认/明确冲突/非主张”筛选。HTML和Markdown不显示`yes/no/idk`或DeepEval默认兼容率。Markdown 汇总保留原有导入主表列位置，在其后追加三态和内容单元诊断；不得重排 `manage-article-knowledge v0.5` 校验所依赖的前七列。

汇总表单元格不得包含会被导入器误拆列的原始英文竖线`|`；渲染器会把正文中的竖线转成HTML实体。交接时保留原始`faithfulness_summary.md`，不要手工重排表格。

## 7. manage-article-knowledge v0.5/v0.6交接

v0.5/v0.6导入必需文件是：

1. `<article-id>-prepared.json`；
2. `<article-id>-judgments.json`；
3. `faithfulness_summary.md`。

v0.6受管运行时，三个文件必须位于知识库配置计算出的`[结果根目录]/[项目ID]/[文章ID]/v[文章版本]/`，且目录名不能重复版本前缀；由知识库Skill传入当前`40`、唯一`30`、项目ID、文章ID和文章版本。导入时还需要当前`40_最终文章.md`、实际审核过的全部知识文件、文章版本和完成日期。知识文件或终稿解析后的正文内容改变后，旧结果失效，应重新审核而不是修改旧JSON。仅换行、编码、Markdown表格首尾竖线等不改变解析后正文的序列化变化可以复用结果，但仍保留原始文件SHA-256作为追踪信息。文章目录移动后如需复核，使用当前`40/30`覆盖路径并输出到新报告目录，不改写原审计文件。
