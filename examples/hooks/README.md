# Hook 配置示例

把 persona-stream 接进你的 agent 会话只需要两类 hook：

| Hook 事件 | 脚本 | 作用 |
|---|---|---|
| `UserPromptSubmit` / `Stop` / `PermissionRequest` / `PostToolUse` / `PostToolUseFailure` | `collector/collect.py <HookName>` | 全量保真采集（脱敏后追加进 `<PERSONA_HOME>/data/events-YYYYMMDD.jsonl`） |
| `SessionStart` | `collector/inject.py` | 向新会话注入人格内核（additionalContext） |

采集器的设计底线：顶层 try/except 兜底、永远 exit 0、stdout 恒为空、无网络、
预算 <100ms——它不可能拖垮你的会话；失败会静默写 `<data>/collector_err.log`。

## ZCode

复制 `zcode.example.json` 的 `hooks` 段到你的配置（项目级 `.zcode/config.json`
或用户级配置），替换 `<REPO>` 与 `<PYTHON>` 两个占位符。

## Claude Code / Cursor 等同构 hook 系统

事件名略有差异（如 `UserPromptSubmit`/`Stop`/`PostToolUse` 语义基本一致），
payload 都是 stdin JSON（含 `session_id`/`cwd`/`prompt` 等字段）。适配要点：

1. `collect.py` 从 stdin JSON 与 argv 取事件名，payload 字段缺失时优雅降级
   （prompt 取 `prompt` 或 `content`；回填类功能——rollout 末行解析——是
   ZCode 特有的，其他平台 `Stop.reply` 会是 `null`，蒸馏层照常处理）。
2. `inject.py` 输出一段 JSON：
   `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": …}}`。
   你的平台若不支持 SessionStart 注入，可以改为在系统提示词里引用
   `persona/SNAPSHOT.md` 与 `persona/threads/DIGEST.md`，或手动运行
   `python -c "import json,sys;sys.path.insert(0,'collector');import inject;print(json.dumps({'additionalContext':inject.build_context()}))"`。

## 数据目录：PERSONA_HOME

`collect.py` / `inject.py` 及全部 eval 工具都尊重 `PERSONA_HOME` 环境变量：
未设置时数据目录 = 代码仓目录（`data/`、`persona/` 均已 gitignore）；
推荐在定时蒸馏任务与 hook 环境里显式设置，把"代码仓"和"数据家"分开。
