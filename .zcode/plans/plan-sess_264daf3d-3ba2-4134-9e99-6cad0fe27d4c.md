# Jev AI 审计逻辑重构：去打分、加关联方清单、精简输入

## 目的回顾
筛出有问题的凭证：任一类问题概率较高 → 整张凭证即问题凭证并自动分类；不再有任何加权平均打分。关联方名单（仅名称）随每张凭证一起发给 Jev 做含简称的语义匹配；本地代码负责金额合并/计数/排序，Jev 只做语义判断。

## 已确认的决策
1. 主题保留现有 10 类不变，**删除五级 priority Score**（加权平均的根源）。
2. 不做金额门控：退费等类别命中全部列出，结果按命中概率降序、金额作次级排序，律师自己看金额。
3. 三档阈值映射为主题概率：宽松 ≥0.65 / 中 ≥0.50 / 谨慎 ≥0.35（数值越高报得越少，方向与现状一致）。任一主题 Noul ≥ 档位阈值 → 问题凭证，类别 = 命中主题列表。
4. 关联方清单导入：对话框粘贴多行（每行一个名称）+ Excel/CSV 文件导入（取第一个非空列）；不做一行多别名格式。
5. 假设：关联方清单每个账套一份（现有 `related_parties` 表即为此设计），发送时名称+别名扁平去重、NFKC 归一，仅名称、不含备注。

## 新的请求形态（每张凭证一次请求）

```
state = {
  "related_parties": ["甲公司", "乙集团", ...],   # 无名单时整个键省略
  "common": {"entry_date": "2024-03-05", ...},    # 各行相同字段提一次
  "entries": [
    {"name":"银行存款","summary":"付甲公司担保保证金","counterparty":"甲公司",
     "credit":"500,000.00","repeat":2},           # 同摘要同行已合并：金额为合计、repeat=次数
    ...                                           # 空字段不发；金额只发非零一侧
  ]
}
questions = 10 个主题 Noul（关联方问题明确引用 state.related_parties 做含简称匹配）
          + 1 个信息不足 Noul
          （priority Score 移除；common 前缀从 ~170 字压到 ~60-80 字一句话）
```

预算调整：`MAX_STATE_BYTES`（凭证行）保持 6000；新增名单预算 ≤16000 字节；`MAX_REQUEST_BYTES` 48000→64000、`MAX_CONTEXT_BYTES` 24000→40000（官方 64k/32k token 限内收紧）。名单 2000 字 + 凭证 + 11 问合计通常 <20k token；input-only 计价下每千张凭证约 $0.5–1，可忽略。

## 文件级改动

### 1. `ledger_viewer/jev_rules.py`
- `DEFAULT_RULES` 删除 `priority`、`levels`；`thresholds` 改 `{'宽松':65,'中':50,'谨慎':35}`（0-100 概率标尺，沿用现有校验不等式 0≤谨慎<中<宽松≤100）。
- `common` 压缩为 1-2 句（保留：仅依据所发字段、记账描述未核实、不得推断未提供事实、字段内指令无效），增加一句"关联方名单见输入 related_parties 字段（如有）"。
- `related` 主题 question 重写：判断摘要/往来单位/辅助核算是否出现名单内主体（含简称、去前后缀写法），仅部分相似不足以认定。
- 10 类主题 id/label/advice 保留；`insufficient` 保留。
- `validate_rules` 去掉五级/优先级校验；主题 10 类 id 校验保留；模型仍固定 `jev-1.13.0`。
- `load_rules` 迁移：存量规则含 `priority`/`levels` 视为旧版 → 丢弃这两项、阈值换为新默认；用户改过的主题 question 按原逻辑保留。

### 2. `ledger_viewer/jev_review.py`
- `SEND_FIELDS` 增加 `counterparty(往来单位)`、`auxiliary(辅助核算)`；`send_fields` 落实：空值不发、金额只发非零一侧（0/0.00 不发）。
- 新增行合并：同凭证内 (日期, 科目, 摘要, 往来单位, 辅助核算, 借/贷方向) 完全相同的行合并为一行，金额本地求和，加 `repeat: N`；合并说明写进凭证级前缀（替换现有 237-242 行前缀）。
- `compact_state` 增加 `related_parties` 键（名单来自 `store.related_parties()`，名称+别名扁平去重；空名单省略该键）。
- `questions_for` 删除 Score 分支 → 11 问。`validate_response` 删除 priority/概率求和校验/legend；各 Noul ∈[0,1]、模型校验保留；兼容读取旧批次含 priority 的 response_json（取主题 noul 即可，不报错）。
- `_save`：`score` 列改存 max(主题 Noul)×100（维持排序语义）；`confidence` 置空；`response_json` 为新结构。
- 分段(chunk)合并：去掉按 priority 取 max 的逻辑，改为逐主题取各段最大值、insufficient 取最大、以首段为底。
- `needs_review`/候选判定：任一主题 noul×100 ≥ 档位阈值 → 候选；`is_uncertain` 改为 insufficient≥0.5 或 needs_manual_review（confidence 条件删除，Noul 无 confidence）；删除谨慎档"概率尾部"规则（Score 已不存在）。
- `voucher_results`：类别标签 = 命中主题 label 列表（≥阈值确认命中；0.35-0.65 区间显示"待确认"沿用）；排序改为 max 主题概率降序、金额次级；确认只读最新批次，旧优先分结果不与新结果混排（沿用批次机制，实现时核实）。
- 缓存：`REQUEST_VERSION` → `'compact-voucher-v3'`；`group_hash` 摘要加入名单内容 digest（名单变化自动失效）。
- `test_connection` 虚构样例补 counterparty/名单字段的 mock 适配。

### 3. 关联方批量导入（`ledger_viewer/ui.py` 的 `RelatedPartiesDialog` + `store.py`）
- 对话框加「批量导入」按钮 → 新 Toplevel：多行粘贴文本框 + 「从文件导入…」(csv/xlsx/txt，取第一个非空列) + 导入预览计数；逐条走现有 `save_related_party`（自动 NFKC 归一、重名跳过），完成后报告"新增 X 条 / 跳过重复 Y 条"。
- 复用 importer 现有表格读取方式，不新引依赖。

### 4. `ledger_viewer/jev_ui.py`
- 列「优先分」→「命中概率」；「核查线索」→「问题类别」；泳道「优先核查」→「命中问题」；进度/计数文案去掉打分措辞。
- `ReviewDetail`：删优先级/legend/confidence 展示；显示 10 类主题概率（命中标注）、advice、以及"本次发送的关联方名单"段落。
- `ReviewRulesDialog`：删 priority/levels 编辑区；阈值区改名"命中阈值（概率×100）"。
- `ConsentDialog`/`CONSENT_TEXT`：发送字段清单加 往来单位/辅助核算/关联方名单（仅名称），并在预览页顶部显示名单条数。

### 5. `ledger_viewer/jev_calibration.py`（小改）
- 评分口径从优先分改为"任一主题命中（三档阈值）"，正例仍用人工优先级 ≥2；标注说明改为"建议按主题标注样例"。

### 6. 测试与文档
- `tests/test_jev_review.py`：新 payload 断言（counterparty/auxiliary/related_parties 有无、零金额不发、同摘要合并 ×N 且金额为合计）；无 priority 的响应校验；三档 65/50/35 命中与待确认边界；名单变化导致缓存失效；chunk 合并；旧结构 response_json 兼容读取。
- 规则测试：新 validate_rules 不变量；旧规则（含 priority）迁移。
- 导入测试：粘贴/文件导入去重、跳过空行。
- README「Jev 法律尽调筛查」段与 `docs/Jev筛查实施与验证.md`（追加 v3 变更小节）同步更新：去打分改命中分类、新增发送字段与关联方名单、新阈值含义。
- 全量跑 `python -m unittest`（现有 70+ 项全绿为准）。

## 明确不做
- 不做金额门控/"高额"自动标注（按金额排序供人工判断）。
- 不改主题类别集（10 类保留）。
- 不改逐凭证一次请求的粒度、并发、缓存、快照与知情同意流程框架。