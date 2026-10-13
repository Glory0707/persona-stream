# FOLD_RULES — 折叠规则（通用模板）

> 夜间蒸馏 LLM 的语义裁决手册。本文件是**模板**：用它承载你自己的折叠规则，
> 但各处的"硬约束"来自代码（foldlib / audit / schema 契约），不是风格建议。
> 规则变更时先问一句：这属于语义规则（改本文件）还是契约（改 schema/README）？

## 0. 三条哲学

1. **减法优先**：折叠是把事件流压进有限的档案，不是把档案养肥。当天新增
   内容若只是把既有条目换句话再说，正确动作是 `update` 或不折，而不是新开条目。
2. **噪声与信号由语义裁决**：采集层全量保真（寒暄/测试只打 `noise_like`
   等 hint 标签照记）。折叠时对 `tags` 只作参考——一句"继续"本身无信号，
   但它出现的时间与上下文可能是信号（某个线头当天动过）。系统回显
   （自动化任务提示词、system-reminder 块）不算用户行为。
3. **可回溯可证伪**：任何写进档案的结论都必须带 `evidence: [12-hex 事件id]`。
   给不出事件 id 的结论不配入库——去 `python eval/search_events.py "关键词"`
   找到原始事件再折。
   **复述同理**：向用户复述、或把上游文本（会话摘要、计划稿、提问措辞）
   折入档案前同样核验锚点——无 evidence 的数字/时间线不得以档案名义传播；
   发现无源条目当场核销（更正 + 账本 + 向用户交代）。
4. **指针优先于快照**：易变事实（路径/数值/结果类）入档写**指针三件套——去哪找 +
   哪个权威源 + 截至何时**，不抄快照正文；同一事实多口径并存时登记口径与适用场景，
   不裁决唯一值。档案价值 = 让任何会话总能找到最新值，而不是记住某次的旧值。
   注入层只承载入口与检索路线（inject.py FOOTER 的【渐进披露】），细节按任务逐层
   展开（分片 → episodes → 事件流 → 问用户本人），无关领域不随注入扩散。
   可选惯例：为"涉及用户信息的任务"建一张方法地图（本部署是 docs/PERSONAL_INFO.md）——
   只记方法与入口，不堆事实。

## 1. 各档案类型的折法

### policies/<域>.yaml（情境 → 倾向）
- 条目：`when`（可观察的情境）+ `tendency`（该情境下的倾向做法）+
  `confidence`(0~1) + `evidence[]`。
- 同域同 when 已有条目时：新证据强化 → 提高 confidence 并补 evidence；
  矛盾 → 走 `foldlib.invalidate_rule`（软失效，保留原文）后写新条目。
  **禁止原地改写 tendency 语义**——倾向的演变必须可追溯。
- 首次出现且只有孤证的，confidence ≤0.5，进 `低置信` 池（日记任务的
  记忆对账问题类会定期求证它们）。

### beliefs/<话题>.yaml（思想版本链）
- 只追加版本（v+1），**从不改写旧版本**；`current` 指向最新版本。
- 言行矛盾是珍贵信号：记 `tension`，不仲裁——人格的复杂度由此生长。

### threads/*.yaml（开放线头）
- 线头 = 一件进行中的事（项目/决定/悬念），有 `paths:`（真实存在的目录）
  才能被 cwd 定向注入。
- 正文块一律 `【MM-DD：标题】正文` 形态，走 `foldlib.append_detail`
  （写前查重、32KB 上限、自动记账）。结论被推翻的块走
  `foldlib.invalidate_block`（`〔已失效…〕`标记，不删原文）。
- `digest:` ≤32 字、无日期后缀（日期由 regen_digest 统一加）；
  每次动过线头就 `foldlib.touch_thread`。
- 生命周期（机械执行，勿手工代替）：open 21 天未动 → sweep 置 dormant；
  dormant 60 天无活动 → 自动闭合；闭合线头写 `closure`（一句话结局）。
- open 线头软上限 14：超限就把最弱的闭合/归档。

### people.yaml / assets.yaml
- 只注入 name+brief / path+brief 索引（brief ≤150/≤80 字单行），
  完整 relation/证据原话按需读文件。新人物至少两处独立证据再建档。

### protocols.yaml（口令协议）
- 用户用固定短语指代一套既定流程，且复现 ≥3 次才沉淀为协议
  （`when` = 口令原文，`spec` = 流程要点，`evidence[]` = 三次复现的事件）。
  上限 12 条——满了说明流程该合并或退场。

### episodes/ 与 journal/
- 情绪强度 ≥4 的挫折/高光折事件卡（`emotion` 0-5 标量）；**卡尾带 `evidence:`**
  事件 id 列表（与 metrics 同口径——账本 id 必须落在 evidence/trigger 字段，
  2026-10-08 起；只在散文里提 id 等于没回链，backlink 会掉）。
  journal 的 `## 待折叠` 小节消化后**清空**。

## 2. 机械上限速查（audit 会抓）

| 项 | 上限 |
|---|---|
| DIGEST 条目 / 单条长度 | 12 条 / 40 字（含日期后缀） |
| 线头 digest（写入值） | 32 字 |
| 线头正文 | 32 KB |
| open / dormant 线头 | 14 / 8 |
| protocols / assets | 12 / 12 |
| people brief / assets brief | ≤160 / ≤80 字符 |

## 3. 写路径纪律（硬约束）

- 档案变更**只准**经 `distiller/foldlib.py`（append_detail / set_digest /
  touch_thread / add_related / add_paths / invalidate_block / invalidate_rule /
  regen_digest / sweep_threads / upsert_belief_version / ledger_append）。它负责写前查重、YAML 预检、
  原子落盘、侧车锁与账本。**禁止临时脚本字符串切片**——历史上两次档案
  YAML 损坏都由此而来。
- 长文本字段一律 YAML 块标量（`|-`）：裸标量里的英文引号/冒号会炸整个文件，
  audit 的 `check_yaml` 会抓到，但别让它抓到。
- 折叠账本 `data/fold_ledger.jsonl` 由 foldlib 自动记（add/update/prune/
  invalidate + event_ids），幂等判重与 backlink 指标都吃它——账本行不许手工造。

## 4. 收尾顺序（每夜固定）

1. 读游标并收集新事件；**收集完成后立即** `python distiller/foldlib.py collect`
   把行数快照记入 `data/_collect_cursor.json`——这是收尾推进游标的唯一依据
2. `foldlib.pending` 幂等守卫（游标后事件 vs 账本；pending==0 且 total>0
   → 上夜已折完，直接复核闸门收工）
3. 语义折叠（本文件规则）
4. `python eval/missed_sessions.py` 零引用裁决（补折或写排除行，见 §6）
5. `python distiller/foldlib.py sweep`（机械遗忘）＋
   `foldlib.regen_digest`（DIGEST 确定性再生）
6. 三闸门：`check_events` → `audit_persona` → `check_injection`；
   `python eval/metrics.py` 把 coverage/backlink 抄进 KPI 行
7. **最后** `python distiller/foldlib.py advance` 按快照推进游标——
   运行期新到的事件不在快照里，留给明夜（游标竞态，2026-10-11：按结束时刻
   重算行数，并发会话的收尾事件会被"消费但从未读过"）。
   之前任何失败都只是幂等重跑

三闸门任何 error 当夜必须处置：修档案或修规则，不修就停推游标
（注入读链断= 用户侧看不见你写的字，等于没写）。

## 5. 思想日常化（月度挖掘，2026-10 起）

夜间折叠管"事件→档案"的归档；模式级的自我理解需要横向重读。模板提供两层节奏：

- **夜间语言样本**：折叠时顺手指认当日至多 5 条最能代表本人语言/思维习惯的
  未折原话（`- 日期 | "原话≤80字" | ev:<12hex id> | 为什么`）追加到
  `data/style_pool.md`，只增不清。原话是证据不是结论，入池不等于立档；
  成熟模式（当日重复 ≥3 次）照常直接立档，不等月度。
- **月度思想挖掘**（任务书 `MINING_PROMPT.md`，建议每月 1 日跑，KPI `kind: mining`）：
  横向重读上月 prompt/stop 语料 + style_pool，四问卷产出——语言习惯、工作习惯、
  思维模式与价值张力各开 beliefs 新版本（`思维模式.yaml`，版本链惯例）；本月新折叠
  与既有 beliefs/policies 矛盾时走 `invalidate_block`/`invalidate_rule` 软失效。
  挖掘读的是已消费历史，**不碰游标**；档案写入走 §3 写路径纪律。

## 6. 排除与零引用裁决（2026-10-11 起，漏折事故的完整性网）

- **折叠排除**：宿主目录内的系统操作事件（hook 自身执行、git、跑测试、
  编辑本仓库文件）不折；但同目录下的**个人对话照折**——判定标准是内容是否
  关于档案主，判断不了默认折（漏了比多了代价大）。任务自身的标记词事件、
  `/clear` 类会话管理命令、重复 id 不折。因 cwd 被排除但确属个人对话的，
  可从宿主 CLI 的 rollout 目录补读（`collector/backfill_rollout.py` 出聚合
  报告，只打印不落盘）。
- **会话级显式排除**：整段会话确认无可折内容（纯寒暄/测试噪音/话题已被
  其他会话承载）时，写一行 `data/fold_exclusions.jsonl`
  （date/session/reason）——排除必须留痕，**禁止静默跳过**；进行中的活跃
  会话按日增量折叠，不用排除行搪塞。
- **零引用裁决**：`python eval/missed_sessions.py` 把"已消费但整个会话零引用
  （账本 ∪ evidence 都不含其任何事件 id）"的会话按纪元列出，夜间任务逐个
  二选一：补折（变为被引用，自动从报告消失）或写排除行。audit_persona 以
  WARN 接力，裁决完应清零。历史教训：一整段 12 事件的会话在夜间任务自己
  打印的清单里仍被丢掉，而三道闸门无一报警——完整性必须有自己的网。
