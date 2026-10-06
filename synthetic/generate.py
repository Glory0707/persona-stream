#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成事件流生成器：虚构人物「林小测」的 90 天行为流（种子确定性）。

硬约束：与任何真实本人零重合——名字、项目、关系、书名全虚构；负样本由
synthetic/demo.py 断言（真实人名/本机私有路径出现即 fail），隐私纪律由 demo 自证。

事件格式与 collector/collect.py 产线 1:1（公共字段 id/ts/hook/session/cwd；
prompt/stop/posttooluse 各自专有字段），且**直接复用采集器的真函数**做
打标（classify）与脱敏（redact）、真写入口（append_jsonl，含侧车锁）——
合成层走的每一步代码都与产线同一条。

用法：python synthetic/generate.py --home <demo 数据目录> [--days 90] [--seed 7]
依赖：须先设置 PERSONA_HOME=<home>（collect 的落盘根取自它）。
"""
import argparse
import hashlib
import os
import random
import sys
from datetime import datetime, date, time, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "collector"))
import collect  # noqa: E402  PERSONA_HOME 须在 import 前设好

TZ = datetime.now().astimezone().tzinfo

# 三个虚构项目（demo 会在 <home>/projects/ 下真实创建，供 paths 定向注入）
PROJECTS = {
    "tide": os.path.join("projects", "tide-forecast"),      # 毕设《潮汐预测》
    "novel": os.path.join("projects", "novel-starbarge"),   # 连载《星槎》
    "club": os.path.join("projects", "reading-club"),       # 读书会
}

FILLER = [
    "整理一下今天的待办清单",
    "把实验记录归档到周报里",
    "看看本周进展汇总，挑两件优先级高的",
    "更新项目文档的目录结构",
    "跑一遍回归测试，确认没有弄坏东西",
]


def eid(seed, d, i, kind):
    """12-hex 确定性事件 id（evidence 指针链要求；真 id 同形态）。"""
    return hashlib.md5(f"{seed}:{d}:{i}:{kind}".encode()).hexdigest()[:12]


class Gen:
    def __init__(self, home, days, seed):
        self.home = home
        self.days = days
        self.rng = random.Random(seed)
        self.seed = seed
        self.counter = 0
        self.n_noise = 0

    def ts(self, d, hh, mm):
        day = date.today() - timedelta(days=d)
        return datetime.combine(day, time(hh, mm), tzinfo=TZ)

    def session(self, d, slug):
        return ("sess_demo_d%03d_%s" % (d, slug))[:64]

    def base(self, d, hh, mm, slug):
        self.counter += 1
        return {
            "id": eid(self.seed, d, self.counter, "base"),
            "ts": self.ts(d, hh, mm).isoformat(timespec="seconds"),
            "hook": "UserPromptSubmit",
            "session": self.session(d, slug),
            "cwd": os.path.join(self.home, PROJECTS[slug]) if slug else self.home,
        }

    def prompt(self, d, hh, mm, slug, text, hook="UserPromptSubmit"):
        """prompt 事件：打标/脱敏/纠错线索全部走采集器真函数。"""
        base = self.base(d, hh, mm, slug)
        base["hook"] = hook
        text = collect.redact(text)          # 采集层唯一保留的正则：秘密脱敏
        self.counter += 1
        base["id"] = eid(self.seed, d, self.counter, "prompt")
        base["type"] = "prompt"
        base["tags"] = collect.classify(text)
        base["correction_cues"] = [c for c in collect.CORRECTION_CUES if c in text]
        base["chars"] = len(text)
        base["text"] = text
        if base["tags"]:
            self.n_noise += 1
        return base

    def stop(self, d, hh, mm, slug, reply_text):
        base = self.base(d, hh, mm, slug)
        base["hook"] = "Stop"
        self.counter += 1
        base["id"] = eid(self.seed, d, self.counter, "stop")
        base["type"] = "stop"
        base["preview"] = ""
        base["reply"] = {
            "finish": "end_turn",
            "text": collect.redact(reply_text),
            "reasoning": "",
            "tool_calls": [],
            "rollout": None,
        }
        return base

    def tool(self, d, hh, mm, slug, tool_name, tool_input, tool_result):
        base = self.base(d, hh, mm, slug)
        base["hook"] = "PostToolUse"
        self.counter += 1
        base["id"] = eid(self.seed, d, self.counter, "tool")
        base["type"] = "posttooluse"
        base["payload"] = {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "tool_result": tool_result,
        }
        return base


def story(g):
    """90 天故事弧：协议复现 ≥3、纠错对、线头生命周期、人物、情绪波、噪声打标、
    脱敏样本。返回事件列表（时间升序）——内容确定性由 seed 决定。"""
    ev = []

    def P(d, hh, mm, slug, text):
        ev.append(g.prompt(d, hh, mm, slug, text))

    # ---- 毕设《潮汐预测》：主线（开题 → 数据 → 受挫 → 基线 → 写论文）----
    P(80, 9, 30, "tide", "潮汐预测的毕设今天开题，先把近三年的水位观测数据抓下来")
    P(80, 10, 5, "tide", "开工")
    ev.append(g.stop(80, 10, 40, "tide", "已建好数据抓取脚本骨架，明天补解析与落盘。"))
    P(60, 14, 20, "tide", "陈远师兄推荐用 HydroMix 的思路做消融实验设计")
    P(55, 18, 0, "tide", "和陈远约了周四晚上过一遍实验设计")
    P(58, 11, 0, "tide", "苏芮老师提醒开题报告要补一节研究意义，别急着跑模型")
    P(45, 9, 50, "tide", "tide 数据集到手了，把 tides.csv 按月份切开看看分布")
    P(45, 10, 0, "tide", "开工")
    ev.append(g.tool(45, 10, 20, "tide", "Read",
                     {"file_path": os.path.join(g.home, PROJECTS["tide"], "data", "tides.csv")},
                     "349 行，月度潮位均值序列，无明显缺失"))
    P(41, 16, 30, "tide", "中期检查被批了数据来源太单一，心态有点崩，方案要重想")
    ev.append(g.stop(41, 17, 0, "tide", "建议补第二数据源做交叉验证；先把情绪放一放，明天重排计划。"))
    P(30, 15, 0, "tide", "苏芮老师看了基线结果，说可以先按这个框架动笔写论文")
    P(28, 10, 30, "tide", "找代码还是 rg 快，IDE 全局搜索可以退休了")
    P(20, 9, 40, "tide", "基线模型跑通了，误差比预期小，明天补消融实验")
    P(20, 9, 45, "tide", "开工")
    ev.append(g.tool(20, 10, 30, "tide", "Bash",
                     {"command": "python train.py --baseline"},
                     "val_mae=0.112，29 分钟跑完"))
    P(19, 18, 0, "tide", "基线跑通加上老师认可，这两天松了口气，今晚早点休息")
    P(2, 20, 30, "tide", "改论文第三章，把潮位预测的图表换成新跑出来的版本")

    # ---- 连载《星槎》：支线，35 天前断更（供 sweep 演示 dormant）----
    P(70, 21, 0, "novel", "新坑连载《星槎》开了，设定是星际时代的潮汐船队")
    P(69, 22, 0, "novel", "把第一章时间线捋一遍，潮汐船队的航行逻辑要自洽")
    P(35, 21, 30, "novel", "断更两周了有点焦虑，周末把第 12 章写完")

    # ---- 读书会：短线头，首期办完即闭合 ----
    P(65, 12, 0, "club", "拉个读书会吧，第一期就读《潮间带》，先约时间")
    P(40, 12, 0, "club", "读书会第一期办完了，讨论比预想热烈，记录已归档")

    # ---- 口令协议「开工」：tide 内共 4 次复现（80/55/45/20），≥3 沉淀为协议 ----
    P(55, 9, 0, "tide", "开工")

    # ---- 纠错对：沟通语言规则被推翻（v1 中英混排 → v2 全中文）----
    P(82, 10, 0, "tide", "把这段摘要润色一下，英文没问题，术语保留原文")
    P(78, 11, 20, "tide", "不对，以后技术讨论也全用中文，英文术语留最少必要的")
    P(76, 9, 15, "tide", "我说过了，全中文，别再混了")

    # ---- 脱敏样本：密钥形态必须在采集层被抹掉（demo 断言 sk-*** 已落盘）----
    # 假密钥在运行时拼接：仓库任何文件都不落完整密钥形态（泄漏扫描自证）
    fake_key = "sk-" + "demo" + chr(107) + "ey" + "1234567890" + "abcdef"
    P(50, 15, 0, "tide", "把 playground 的配置加上 APIKEY=%s，千万别提交" % fake_key)

    # ---- 噪声打标样本：照记不丢弃，判断归蒸馏（demo 断言未入画像）----
    P(60, 8, 55, "novel", "你好")
    P(33, 9, 0, "tide", "继续")
    P(10, 21, 0, "novel", "66")

    return ev


def fill(g, story_events, days, noise):
    """日常填充事件：确定性轮换的通用任务 + 按 noise 比例注入寒暄/确认类，
    让事件量与噪声比可调（--days / --noise）。"""
    ev = list(story_events)
    story_keys = {(e["ts"], e["type"]) for e in ev}
    for d in range(days - 1, -1, -1):
        for k in range(2 + g.rng.randrange(2)):
            hh, mm = 9 + k * 3, (g.rng.randrange(6)) * 10
            slug = ["tide", "novel", "club", ""][k % 4]
            text = FILLER[(d + k) % len(FILLER)]
            if g.rng.random() < noise:
                text = g.rng.choice(["你好", "在吗", "继续", "好的", "test", "33"])
            base = g.base(d, hh, mm, slug)
            if (base["ts"], "UserPromptSubmit") in story_keys:
                continue  # 让位给正片，保持故事密度
            e = g.prompt(d, hh, mm, slug, text)
            e["ts"] = base["ts"]
            ev.append(e)
    return ev


def main():
    ap = argparse.ArgumentParser(description="生成虚构人物「林小测」的合成事件流")
    ap.add_argument("--home", required=True, help="demo 数据目录（PERSONA_HOME）")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--noise", type=float, default=0.15, help="填充事件里的噪声比例")
    args = ap.parse_args()

    os.environ.setdefault("PERSONA_HOME", args.home)
    import importlib
    importlib.reload(collect)  # collect 的落盘根在 import 时刻读 PERSONA_HOME；独立运行本脚本时在此对齐

    # 项目目录与素材（真实存在，供 paths 定向注入与 assets 资料地图）
    for rel in PROJECTS.values():
        os.makedirs(os.path.join(args.home, rel), exist_ok=True)
    tide_data = os.path.join(args.home, PROJECTS["tide"], "data")
    os.makedirs(tide_data, exist_ok=True)
    with open(os.path.join(tide_data, "tides.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("month,mean_level_cm\n")
        for i, m in enumerate(range(1, 13), 1):
            f.write("%02d,%d\n" % (m, 180 + i * 3 % 25))

    g = Gen(args.home, args.days, args.seed)
    events = fill(g, story(g), args.days, args.noise)
    events.sort(key=lambda e: e["ts"])

    for e in events:
        day = e["ts"][:10].replace("-", "")
        collect.append_jsonl("events-%s.jsonl" % day, e)

    print("合成事件流完成：%d 条事件 / %d 个文件（seed=%d，含打标噪声 %d 条）"
          % (len(events), len({e["ts"][:10] for e in events}), args.seed, g.n_noise))


if __name__ == "__main__":
    sys.exit(main())
