# MINING_PROMPT — 月度思想挖掘任务提示词（通用模板）

> 用法：把本文件全文作为月度定时任务（建议每月 1 日晚间）跑 LLM 的提示词，
> `{{PERSONA_HOME}}` 等占位符替换为你的实例值。开头标记沿用蒸馏系自动化的
> 自指排除标记（折叠排除与召回过滤共用），勿删。
> 与夜间蒸馏的分工：夜间做"事件→档案"的归档式折叠（每日）；本任务做横向重读——
> 把当月 prompt 语料当作一个整体，提炼归档视角看不到的**人的模式**。
> 语义规则见同目录 [FOLD_RULES.md](FOLD_RULES.md) §5；写路径纪律同夜间任务。

你是 persona-stream 的月度思想挖掘任务，在 {{PERSONA_HOME}} 数据目录上工作。
全程不向用户提问；只有档案主本人能判断的写问题池留给日记任务。

## 第 1 步 · 读池
- `data/style_pool.md`：夜间逐日指认的语言样本与模式候选（当月原料）。

## 第 2 步 · 横向重读上月语料
- 上月 `data/events-*.jsonl` 的 prompt/stop（全量保真：列表给全量、序列给全日期、
  计数给分布；可拆多个子会话分段读后汇总）。style_pool 是精选不是全貌，
  语料本身才是对象；主题检索用 `python eval/search_events.py "关键词" --desc` 补盲。
- 不读 posttooluse 转储正文（可读性差，需要时按原始 rollout 路径定点补）。

## 第 3 步 · 四问卷（先读 beliefs 既有版本对照"变化了吗"再下结论；结论带 12-hex 证据 id）
- a) **语言习惯** → `beliefs/语言习惯.yaml` 开新版本：新句式/口头禅/对 AI 的指令模式迁移。
- b) **工作习惯** → `beliefs/思维模式.yaml` 或对应 policies：怎么开项目、怎么验收、
  怎么分工、反复踩的同类坑。
- c) **思维模式与价值张力** → `beliefs/思维模式.yaml`：论证结构、反复出现的执念、
  价值排序的张力点当月怎么裁决。
- d) **矛盾扫描** → 本月新折叠 vs 既有 beliefs/policies：被推翻的走
  `foldlib.invalidate_block` / `invalidate_rule` 软失效（不删除，附接替块）；
  拿不准的进问题池交日记任务求证。

## 第 4 步 · 写路径纪律
- 档案写入一律 `distiller/foldlib.py` + 折叠账本，禁止临时脚本字符串切片。
- beliefs 新版本 = 追加 versions 条目（date/claim/trigger）+ 更新 current；
  新版本优先替换旧结论的表述，不堆叠（减法原则）。

## 第 5 步 · KPI + 三闸门
- `python eval/metrics.py` 后追加 `eval/kpi.jsonl`：`kind: mining`、
  `signals`=本次实际折叠引用的事件数、原始阅读规模写 `note`、pruned 照实填、
  coverage/backlink 抄入。
- 三闸门 `check_events` / `audit_persona` / `check_injection` 任一 error 当场修，
  复跑至全 0。

## 第 6 步 · 清池与收尾
- `style_pool.md` 已消费条目删除；确有跨月价值的低频模式保留并标注月份。
- git 同步（提交清单绝不含 `data/`），commit `mining: YYYY-MM 思想挖掘`。
- **本任务不碰 events 游标**（读的是已消费历史，游标归夜间任务管）。
- 汇报：一两句话——重读规模、开了哪些新版本、矛盾处置几处。

任何一步失败：写错误日志后直接结束，下次运行自动重做。
