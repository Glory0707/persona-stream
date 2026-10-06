# schema 契约（采集层与沉淀层的文件格式）

> 2026-09-09 规则变更：全链条**全量保真**——采集不截断、不预丢弃，噪声判断移交蒸馏 LLM（详见 FOLD_RULES §9）。

## 1. 原始事件流 `data/events-YYYYMMDD.jsonl`（仅本地，gitignore）

JSONL，一行一事件。公共字段：`id`（12位hex）、`ts`（ISO8601 含时区）、`hook`（来源事件）、`session`（会话id截断64）、`cwd`。

| type | 专有字段 | 说明 |
|---|---|---|
| `prompt` | `tags[]`、`correction_cues[]`、`chars`、`text`（全文，脱敏） | 用户输入全量保真；`tags` 为启发式标签：`noise_like`（寒暄/测试）、`skip_like`（确认词）、`short_answer`（1-2位纯数字）——**只作 hint，最终判断由蒸馏 LLM 做** |
| `stop` | `preview`、`reply{}` | 轮次结束。`reply` 为 AI 侧捕获：`{finish, text(回复全文), reasoning(思考链), tool_calls[](全参数), rollout(来源路径)}`，从 `~/.zcode/cli/rollout/model-io-<session>.jsonl` 末行解析；末行超 4MB 时为 `{deferred: 路径, reason}`（夜间蒸馏按需补读）；无 rollout 文件时为 `null` |
| `permissionrequest` / `posttooluse` / `posttoolusefailure` | `payload{}`（全量，含 `tool_name/tool_input/tool_result/transcript_path`、`rollout` 引用） | 工具观察记录，不截断不砍列表 |
| `git_commit` | `author`、`hash`、`subject`、`src` | git 提交事件（backfill 与实时并存） |

## 2. 噪声策略（2026-09-09 起：打标不丢弃，判断归 LLM）

| 输入示例 | 启发式标签 | 处理 |
|---|---|---|
| `你好`、`test`、`在吗`、纯标点 | `noise_like` | 照常入事件流，蒸馏 LLM 语义判断 |
| `好的`、`嗯`、`继续`、`收到` | `skip_like` | 照常入事件流 |
| `1`~`99` 纯数字 | `short_answer` | 照常入事件流 |
| 系统回显（plan 注入、工具结果回显、自动化提示词） | — | 采集保留；蒸馏时默认不算用户行为，含真实决策原话则提炼 |

## 3. 脱敏（采集时执行，唯一保留的正则过滤）

`sk-…`、`ghp_…`、`ark-…`、`AKID…`、JWT（`eyJ…`）、BigModel 裸 key（`32hex.secret`）、
`token=/password=/api_key=…`、`Bearer …`、`密码…` → 替换为 `***`。
入库文件另有 `eval/audit_persona.py check_repo_secrets` 全量扫描兜底；
原始事件流由 `eval/check_events.py` 做未脱敏密钥兜底扫描。密钥事故处置见 docs/SECURITY.md。

## 4. 沉淀层契约（`persona/`，夜间任务维护）

- `core.md`：手工区（本人确认制）+ `<!-- AUTO:BEGIN -->`/`<!-- AUTO:END -->` 自动区（夜间任务唯一可写区）
- `SNAPSHOT.md`：人格速览（性格/人物/人际风格/关系协议/当夜快照），SessionStart 注入用；夜间任务维护
- `policies/<域>.yaml`：`id / domain / rules[]{when, tendency, confidence, evidence[], counter_evidence[], updated, invalidated?}`；被推翻的规则由 foldlib.invalidate_rule 写 `invalidated: "日期"` 软失效保留原文（不进 core 自动区）；归档进 `policies/_archive.yaml`
- `beliefs/<话题>.yaml`：`topic / versions[]{v, date, claim, trigger} / current / tension`（言行矛盾记录，不仲裁）；版本追加走 `foldlib.upsert_belief_version`
- `threads/`：线头条目，公共字段 `id/opened/topic/detail/evidence[]`；正文块是 detail 块标量内以 `；【` 开头的行分段。生命周期四级：
  - `open`：必须有 `last_seen`（超 14 天未更新被自检提示复核并入问题池）与 **`digest:`**（≤32 字摘要，DIGEST 确定性再生的唯一语义源）
  - `dormant`（遗忘）：open 超 21 天未动由 `foldlib.sweep_threads` 置入，须带 `dormant:` 日期；不进 DIGEST/注入，再折叠或 touch/related/paths 触碰自动唤醒
  - `closed`：必须有 `closed` 日期与 `closure`（结局一句话）；dormant 超 60 天无活动由 sweep 自动闭合（closure="重开即续线"）；closed 超 30 天由 `foldlib.archive_thread` 迁 `threads/_archive/` 留桩（永不删除，audit 不对 _archive 做 open 检查）
  - 可选 **`related: [线程id]`**（受控交叉引用，禁止自引用/死链）与 **`paths: [目录]`**（真实存在的项目目录，注入层按 cwd 定向匹配）；结论被推翻的正文块由 `foldlib.invalidate_block` 追加 `；〔已失效 日期，由 X 取代〕` 标记行保留原文
  - `DIGEST.md` 为 open 线头摘要（注入用），**只能由 foldlib.regen_digest 再生，禁止手工编辑**
- `people.yaml`：人物关系图谱（`name / brief / relation / source / confidence`）——**brief** 为 ≤150 字单行速览，SessionStart 只注入 name+brief 索引（2026-09-10 起，完整 relation 证据原话按需读取；audit 校验 brief 必填与索引预算 4500 字）
- `assets.yaml`：资料地图（2026-09-24 起）——`id / path / brief(≤80字单行) / evidence[]`，会话填表/找材料直接定位查证；只存位置与内容类别概述，不存资料正文；上限 12 条，归档进 `assets_archive.yaml`；path 失效由使用会话核实回写。折叠规则见 FOLD_RULES §10
- `episodes/`：事件卡 `{date, title, what, motive, emotion(0-5), resolution}`，允许追加 `what_*` 补录节
- `journal/YYYY-MM.md`：每日一问/周日五问的问答记录；`## 待折叠` 小节由夜间蒸馏消化后清空
- `protocols.yaml`：口令协议（`id/when/spec/evidence[]`，§7 上限 12 条）

## 5. 游标 `data/state.json`

```json
{"updated": "ISO8601 实际运行时刻", "files": {"events-20260904.jsonl": 12}}
```
每文件已处理行数；**最后一步**才推进 → 任何失败重跑幂等。时间必须是实际运行时刻（禁止预设）。

## 6. 其他文件

- **折叠账本 `data/fold_ledger.jsonl`**（2026-10-02 起，仅本地 gitignore）：每次折叠动作一行 `{date, action: add|update|prune|invalidate, target, block_key, event_ids[12-hex], old_summary?, new_summary?}`——精确幂等判重（夜间第 2 步先于启发式守卫）、evidence id 反向索引、old→new 变更审计；audit 校验行级合法性。
- **事件索引 `data/index/events.db`**（2026-10-02 起，仅本地 gitignore）：SQLite FTS5（trigram 分词，无嵌入），`eval/build_index.py` 随夜间维护增量构建，`eval/search_events.py "关键词"` 检索；覆盖 prompt.text / stop.reply / payload。
- **`distiller/foldlib.py`**：夜间折叠唯一写入口（行为规则见 FOLD_RULES §11）——写前查重、超限（32KB）报错附块清单、写前 YAML 预检、原子落盘、全部变更器持侧车锁；**禁止临时脚本字符串切片**。
- **注入读链验证 `eval/check_injection.py`**：夜间第三闸门——重建注入全文断言 DIGEST/线头 digest/协议 when/人名/资料路径在接收方可见（写侧成功≠接收方可见）。
- `eval/kpi.jsonl` 每行须带 `kind`：`nightly`(夜间折叠，signals=折叠事件数) / `mining`(离线深挖，signals=实际折叠引用的事件数、原始扫描量写 note) / `maint`(工程维护)——趋势图只统计 nightly；每行须含 date/kind/signals/pruned，2026-10-06 起 nightly 行还须含 `coverage`/`backlink`（`eval/metrics.py` 产出），rate 须与 corrections/signals 相符，同日同 signals 的 nightly 重复行会被自检判 ERROR。
- 质检脚本：`eval/audit_persona.py`（档案+全仓库密钥扫描）、`eval/check_events.py`（原始事件流完整性/密钥兜底/游标一致性）、`eval/backup.py`（每周备份+sha256 清单+保留 8 代）。
- `collector_err.log`（采集错误，超 256KB 自动轮转一代）、`distill_err.log`（蒸馏错误）。`data/` 整体 gitignore；蒸馏中间产物（`data/_*`、临时脚本）折叠后即删（见 AUTOMATION_PROMPT 第 6 步）。

## 7. 长文本字段的 YAML 写法（硬约束）

含引号、冒号、中英文混排的长字段（`claim` / `detail` / `tension` / `what` / `relation` 等）**一律用块标量**：

```yaml
tension:
  - |-
    她的底线型沟通 vs 我的说服型沟通——"你一向都是说服我"（2026-05-12）
```

裸标量里的英文引号会提前闭合、`：` 冒号会被当作映射分隔符，历史上已导致两个档案对 YAML 读取方完全损坏（2026-09-09 修复）。`eval/audit_persona.py` 会检出此类失败。
