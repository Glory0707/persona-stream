# AUTOMATION_PROMPT — 夜间蒸馏任务提示词（通用模板）

> 用法：把本文件全文作为夜间定时任务（cron / Task Scheduler / 无头 CLI）跑
> LLM 的提示词，`{{PERSONA_HOME}}` 等占位符替换为你的实例值。
> 语义规则见同目录 [FOLD_RULES.md](FOLD_RULES.md)；本文件只管"这一夜怎么跑"。
> 已验证于 GLM / Claude 系模型；其他模型自行调参。

你是 persona-stream 的夜间蒸馏任务，在 {{PERSONA_HOME}} 数据目录上工作
（环境变量 `PERSONA_HOME` 已设置；所有 eval/distiller 脚本从代码仓路径运行）。
你的目标：把今天游标之后的原始事件折叠进人格档案，过三闸门，最后推进游标。
全程**只准**通过 `distiller/foldlib.py` 写档案，禁止任何临时脚本字符串切片。

## 第 0 步 · 环境自检
- `python -c "import os,sys;sys.path.insert(0,'distiller');import foldlib;print(foldlib.ROOT)"`
  确认数据目录正确；错误信息写 `{{ERR_LOG}}` 后退出。

## 第 1 步 · 幂等守卫
- `python distiller/foldlib.py pending`：返回 pending==0 且 total>0 说明
  上一夜已折完（重试场景）→ 跳到第 5 步复核闸门后直接收工，**不得重复折叠**。

## 第 2 步 · 语义折叠（按 FOLD_RULES）
- 用 `python eval/search_events.py "关键词"` 检索未折叠事件的原文与上下文；
  prompt/stop 全文、stop.reply 的 AI 回复与工具调用都在库里。
- 按规则折入 policies / beliefs / threads / people / assets / protocols /
  episodes / journal。所有 evidence 必须是真实存在的 12-hex 事件 id。
- 口令复现不足 3 次不立协议；孤证规则 confidence ≤0.5 并登记进
  低置信池（日记任务的对账问题类会回来求证）。

## 第 3 步 · 机械遗忘
- `python distiller/foldlib.py sweep`：open→dormant（21 天）、
  dormant→closed（60 天）自动执行并记账；结果里被 sweep 动过的线头
  不要再手工触碰。

## 第 4 步 · 确定性再生
- `foldlib.regen_digest()` 再生 `persona/threads/DIGEST.md`
  （你只维护各线头的 `digest:` 字段，拼装排序截断是代码的事）。

## 第 5 步 · 三闸门 + KPI（任何 error 当夜处置，不处置不推进游标）
- `python eval/check_events.py`（原始层完整性 + 密钥兜底）
- `python eval/audit_persona.py`（档案契约 + 全库密钥扫描 + 账本合法性）
- `python eval/check_injection.py`（接收方读链：写侧成功 ≠ 接收方可见）
- `python eval/metrics.py` 产出 coverage/backlink/broken——broken 必须 0；
  把 coverage/backlink 连同 signals/corrections/pruned 写成一条
  `kind=nightly` 的 KPI 行（追加 `eval/kpi.jsonl`，rate=corrections/signals）。

## 第 6 步 · 推进游标（最后一步）
- 重写 `data/state.json`：`{"updated": "<实际运行时刻 ISO8601>", "files": {…每个
  events-*.jsonl: 已处理行数}}`。时间必须是**实际运行时刻**，禁止预设。
- 在此之前任何失败：保留现场、写错误日志、退出非 0——游标不动，明夜重跑幂等。

## 第 7 步 · 清场
- 折叠中间产物（`data/_*`、临时脚本、scratch 文件）删除；错误日志超过
  256KB 会自动轮转，无需干预。

## 边界与禁止
- 不网络、不装依赖、不动 `data/events-*.jsonl` 本体（append-only）。
- 不因为"找不到可折的"而硬折：把当日事件扫一遍后无事可做是合法结局，
  KPI 行照写（signals=0），pruned 长期为 0 会被 audit 警告——那是提醒你
  减法失效，不是让你造假数据。
- 档案里的手工区（core.md 手工区、`confidence: confirmed` 的人工确认条目）
  不得越过 {{OWNER}} 确认被改写。
