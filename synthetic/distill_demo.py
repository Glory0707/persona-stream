#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性规则折叠器：演示夜间蒸馏的完整写路径与产物形态。

生产中第 2 步的**语义裁决**由 LLM 按 distiller/FOLD_RULES.md 执行；
本脚本用固定规则替代那个 LLM，但走完全相同的管线——foldlib 受控写路径、
折叠账本、sweep 遗忘、DIGEST 确定性再生、三闸门前的游标纪律、KPI 行——
所以 demo 的产物与产线同构，只是"谁来做判断"不同。

依赖：PERSONA_HOME 须在 import 前指向 demo 数据目录（foldlib 的根取自它）。
"""
import json
import os
import sys
try:  # Windows 控制台 cp1252 兜底：中文输出强制 UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "distiller"))
sys.path.insert(0, os.path.join(REPO, "eval"))
import foldlib  # noqa: E402  PERSONA_HOME 须在 import 前设好
import metrics  # noqa: E402

DATA = foldlib.DATA
LEDGER = os.path.join(DATA, "fold_ledger.jsonl")
THREADS = foldlib.THREADS_DIR
PERSONA = foldlib.PERSONA


def dago(d):
    return (date.today() - timedelta(days=d)).isoformat()


def find_ev(events, marker):
    """按文本标记找唯一 prompt 事件（故事拍确定性入账的锚点）。"""
    hits = [e for e in events if e.get("type") == "prompt" and marker in (e.get("text") or "")]
    assert len(hits) == 1, "标记不唯一：%r → %d 条" % (marker, len(hits))
    return hits[0]


def thread_template(tid, topic, opened):
    return (
        "id: %s\nstatus: open\nopened: \"%s\"\nlast_seen: \"%s\"\n"
        "topic: %s\ndetail: |-\nevidence: []\n"
        % (tid, opened, opened, topic)
    )


class Distiller:
    """规则版夜间蒸馏。run() 返回摘要（demo.py 据此做行为断言）。"""

    def __init__(self, home):
        self.home = home
        self.events = []
        for name, _ln, _raw, obj, _err in foldlib.iter_events(DATA):
            if isinstance(obj, dict):
                self.events.append(obj)
        self.evidence = set()   # 全部被引用过的事件 id
        self.blocks = 0         # 写入正文块数（KPI pruned 口径）
        self.corrections = 0    # 纠错对折叠数

    # ---------- 第 0 步：档案骨架（建档是蒸馏的职责，块变更走 foldlib） ----------
    def skeleton(self):
        for sub in ("threads", "beliefs", "policies", "episodes", "journal"):
            os.makedirs(os.path.join(PERSONA, sub), exist_ok=True)
        core = os.path.join(PERSONA, "core.md")
        if not os.path.exists(core):
            with open(core, "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    "# 人格内核\n\n"
                    "<!-- 手工区：本人确认制，蒸馏不越线 -->\n\n"
                    "<!-- AUTO:BEGIN -->\n<!-- AUTO:END -->\n")

    # ---------- 第 2 步：语义折叠（规则版） ----------
    def fold_threads(self):
        tide_dir = os.path.join(self.home, "projects", "tide-forecast")
        novel_dir = os.path.join(self.home, "projects", "novel-starbarge")
        club_dir = os.path.join(self.home, "projects", "reading-club")
        specs = [
            ("tide-forecast", "毕设《潮汐预测》", 80, tide_dir, [
                (80, "开题", "潮汐预测的毕设今天开题"),
                (45, "数据到手", "数据集到手了"),
                (20, "基线跑通", "基线模型跑通了"),
                (2, "论文图表更新", "改论文第三章"),
            ]),
            ("novel-starbarge", "连载《星槎》", 70, novel_dir, [
                (70, "开新坑", "新坑连载《星槎》"),
                (35, "断更焦虑", "断更两周了有点焦虑"),
            ]),
            ("reading-club", "读书会", 65, club_dir, [
                (65, "立项", "拉个读书会吧"),
                (40, "首期办完", "读书会第一期办完了"),
            ]),
        ]
        for tid, topic, opened, proj_dir, blocks in specs:
            path = os.path.join(THREADS, tid + ".yaml")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(thread_template(tid, topic, dago(opened)))
            foldlib.add_paths(path, [proj_dir])
            for d, title, marker in blocks:
                ev = find_ev(self.events, marker)
                block = "【%s：%s】%s" % (dago(d)[5:].replace("-", "-"),
                                          title, ev["text"])
                add = foldlib.append_detail(path, block, [ev["id"]], ledger_path=LEDGER)
                self.evidence.update(add)
                self.blocks += 1
        # digest + last_seen（读链会逐一验证这些字段真的出现在注入里）
        tide = os.path.join(THREADS, "tide-forecast.yaml")
        novel = os.path.join(THREADS, "novel-starbarge.yaml")
        club = os.path.join(THREADS, "reading-club.yaml")
        foldlib.set_digest(tide, "潮汐预测：基线已跑通，待补消融实验")
        foldlib.set_digest(novel, "《星槎》连载：断更中，周末补第 12 章")
        foldlib.touch_thread(tide, day=dago(2))
        foldlib.touch_thread(novel, day=dago(35))
        return {"tide": tide, "novel": novel, "club": club}

    def close_reading_club(self, club_path):
        """闭合线头（蒸馏会话职责；audit 会校验 closed+closure 契约）。"""
        ids = []
        with open(club_path, encoding="utf-8") as f:
            s = f.read()
        import re
        ids = re.findall(r"[0-9a-f]{12}", re.search(r"^evidence: \[(.*?)\]", s, re.M | re.S).group(1))
        s = s.replace("status: open", "status: closed", 1)
        s = s.rstrip("\n") + "\nclosed: \"%s\"\nclosure: '首期办完，大家时间难约，先歇着（demo 合成）'\n" % dago(40)
        import foldlib as _f
        if not _f._yaml_ok(s):
            raise _f.YamlVerifyError("闭合后 YAML 校验失败")
        _f._write_atomic(club_path, s)
        foldlib.ledger_append("update", "threads/reading-club", "closed",
                              ids, new_summary="首期办完即闭合", day=dago(40),
                              ledger_path=LEDGER)
        return ids

    def fold_people(self):
        rows = [
            ("陈远", "同门师兄——消融思路与实验设计的讨论搭子（demo 合成人物）",
             [(60, "陈远师兄推荐用 HydroMix"), (55, "和陈远约了周四")]),
            ("苏芮", "导师——研究框架与写作节奏的把关人（demo 合成人物）",
             [(58, "苏芮老师提醒开题报告"), (30, "苏芮老师看了基线结果")]),
        ]
        lines = ["people:"]
        for name, brief, beats in rows:
            ids = [find_ev(self.events, m)["id"] for _d, m in beats]
            self.evidence.update(ids)
            rel = "；".join("%s：%s" % (dago(d), find_ev(self.events, m)["text"]) for d, m in beats)
            lines.append("  - name: %s" % name)
            lines.append("    brief: %s" % brief)
            lines.append("    relation: |-")
            for ln in rel.splitlines() or [rel]:
                lines.append("      " + ln)
            lines.append("    source: sessions")
            lines.append("    confidence: confirmed")
            lines.append("    evidence: [%s]" % ", ".join('"%s"' % i for i in ids))
        with open(os.path.join(PERSONA, "people.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def fold_protocols(self):
        markers = ["开工"]
        ids = [e["id"] for e in self.events
               if e.get("type") == "prompt" and (e.get("text") or "").strip() == "开工"]
        assert len(ids) >= 3, "口令复现不足 3 次不应沉淀协议（demo 数据错误）"
        self.evidence.update(ids)
        with open(os.path.join(PERSONA, "protocols.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "protocols:\n"
                "  - id: kickoff\n"
                "    when: 开工\n"
                "    spec: |-\n"
                "      按既定优先级过一遍今日三件事再动手，先汇报计划再执行。\n"
                "    evidence: [%s]\n" % ", ".join('"%s"' % i for i in ids)
            )
        return ids

    def fold_policies(self):
        pol = os.path.join(PERSONA, "policies")
        ev82 = find_ev(self.events, "把这段摘要润色一下")
        ev78 = find_ev(self.events, "以后技术讨论也全用中文")
        ev76 = find_ev(self.events, "我说过了")
        ev28 = find_ev(self.events, "找代码还是 rg 快")
        self.evidence.update([ev82["id"], ev78["id"], ev76["id"], ev28["id"]])
        with open(os.path.join(pol, "comms.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "domain: 沟通语言\n"
                "rules:\n"
                "  - when: 技术讨论夹英文术语\n"
                "    tendency: |-\n"
                "      默认中英混排，术语保留英文原文\n"
                "    confidence: 0.6\n"
                "    evidence: [\"%s\"]\n"
                "    updated: \"%s\"\n"
                % (ev82["id"], dago(82))
            )
        with open(os.path.join(pol, "tools.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "domain: 工具偏好\n"
                "rules:\n"
                "  - when: 找代码或找资料\n"
                "    tendency: |-\n"
                "      优先命令行检索（rg / FTS），少开 IDE 全局搜索\n"
                "    confidence: 0.7\n"
                "    evidence: [\"%s\"]\n"
                "    updated: \"%s\"\n"
                % (ev28["id"], dago(28))
            )
        # 纠错对 → 旧规则软失效（Graphiti 式：不删原文，标记由谁取代）
        foldlib.invalidate_rule(os.path.join(pol, "comms.yaml"), "英文术语",
                                dago(76), note="由全中文纠错对取代")
        self.corrections += 1
        foldlib.ledger_append("invalidate", "policies/comms.yaml",
                              "技术讨论夹英文术语", [ev78["id"], ev76["id"]],
                              old_summary="默认中英混排",
                              new_summary="全中文，术语留最少必要英文",
                              day=dago(76), ledger_path=LEDGER)
        return {"comms": os.path.join(pol, "comms.yaml")}

    def fold_beliefs(self):
        ev82 = find_ev(self.events, "把这段摘要润色一下")
        ev78 = find_ev(self.events, "以后技术讨论也全用中文")
        path = os.path.join(PERSONA, "beliefs", "language.yaml")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "topic: 语言习惯\n"
                "versions:\n"
                "  - v: 1\n"
                "    date: \"%s\"\n"
                "    claim: |-\n"
                "      技术讨论习惯中英混排，术语保留英文原文\n"
                "    trigger: [\"%s\"]\n"
                "  - v: 2\n"
                "    date: \"%s\"\n"
                "    claim: |-\n"
                "      技术讨论也全用中文，英文术语留最少必要的\n"
                "    trigger: [\"%s\", \"%s\"]\n"
                "current: 2\n"
                "tension:\n"
                "  - |-\n"
                "    术语回英文原词的效率偏好 vs 「全中文」的明确纠错（%s）——记录，不仲裁。\n"
                % (dago(82), ev82["id"], dago(78), ev78["id"],
                   find_ev(self.events, "我说过了")["id"], dago(78))
            )
        self.evidence.update([ev82["id"], ev78["id"]])

    def fold_episodes_and_assets_and_journal(self):
        ev41 = find_ev(self.events, "中期检查被批了")
        ev19 = find_ev(self.events, "松了口气")
        self.evidence.update([ev41["id"], ev19["id"]])
        ep = os.path.join(PERSONA, "episodes")
        with open(os.path.join(ep, "%s-中期受挫.yaml" % dago(41)), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "date: \"%s\"\n"
                "title: 中期检查受挫\n"
                "what: |-\n"
                "    %s\n"
                "motive: |-\n"
                "    数据来源单一被点名，方案要重想\n"
                "emotion: 4\n"
                "resolution: |-\n"
                "    补第二数据源交叉验证，一周后基线跑通翻盘\n"
                "evidence: [\"%s\"]\n" % (dago(41), ev41["text"], ev41["id"])
            )
        with open(os.path.join(ep, "%s-基线翻盘.yaml" % dago(19)), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "date: \"%s\"\n"
                "title: 基线跑通翻盘\n"
                "what: |-\n"
                "    %s\n"
                "motive: |-\n"
                "    受挫后调整方案得到验证\n"
                "emotion: 1\n"
                "resolution: |-\n"
                "    按当前框架动笔写论文\n"
                "evidence: [\"%s\"]\n" % (dago(19), ev19["text"], ev19["id"])
            )
        # 资料地图：只存位置与概述，不存正文
        ev45 = find_ev(self.events, "数据集到手")
        self.evidence.add(ev45["id"])
        csv_path = os.path.join(self.home, "projects", "tide-forecast", "data", "tides.csv")
        with open(os.path.join(PERSONA, "assets.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "assets:\n"
                "  - id: tides-csv\n"
                "    path: %s\n"
                "    brief: 潮位观测数据集（月度均值）——毕设数据源（demo 合成）\n"
                "    evidence: [\"%s\"]\n" % (csv_path, ev45["id"])
            )
        # journal：一问一答 + 待折叠（空）小节，示意主观层与蒸馏的衔接
        month = date.today().strftime("%Y-%m")
        with open(os.path.join(PERSONA, "journal", month + ".md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "# 日记 %s\n\n"
                "## %s\n"
                "**问**：今天最值得记的决定？\n"
                "**答**：基线跑通后决定先动笔写论文再补消融——苏芮老师背书过的顺序。\n\n"
                "## 待折叠\n\n" % (month, dago(19))
            )

    def write_core_and_snapshot(self):
        with open(os.path.join(PERSONA, "core.md"), "r", encoding="utf-8") as f:
            s = f.read()
        auto = "\n".join([
            "- 沟通全中文，英文术语留最少必要的（%s 起的明确纠错）" % dago(78),
            "- 找代码/资料优先命令行检索（rg / FTS），少开 IDE 全局搜索",
            "- 当前重心：毕设《潮汐预测》冲刺；连载《星槎》周末档（已断更，待复活）",
        ])
        s = s.replace("<!-- AUTO:BEGIN -->\n<!-- AUTO:END -->",
                      "<!-- AUTO:BEGIN -->\n%s\n<!-- AUTO:END -->" % auto)
        with open(os.path.join(PERSONA, "core.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(s)
        with open(os.path.join(PERSONA, "SNAPSHOT.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "# 人格速览\n\n"
                "## 性格\n"
                "- 受挫后能翻盘（中期检查 → 基线跑通）；对「记录、可追溯」有执念\n\n"
                "## 人物速览\n"
                "- 陈远：同门师兄，讨论搭子\n"
                "- 苏芮：导师，节奏把关人\n\n"
                "## 关系协议\n"
                "- 「开工」= 按既定优先级过一遍今日三件事再动手\n\n"
                "## 当夜快照\n"
                "- 潮汐预测基线已跑通；读书会已歇；连载断更待复活\n"
            )

    # ---------- 第 3-6 步：sweep / regen / 游标 / KPI ----------
    def sweep_and_digest(self):
        swept = foldlib.sweep_threads(threads_dir=THREADS, day=date.today().isoformat(),
                                      dormant_days=21, close_days=60, ledger_path=LEDGER)
        foldlib.regen_digest(threads_dir=THREADS,
                             out=os.path.join(THREADS, "DIGEST.md"))
        return swept

    def advance_cursor(self):
        counts = {}
        for name, ln, _raw, _obj, _err in foldlib.iter_events(DATA):
            counts[name] = ln
        import datetime as _dt
        state = {"updated": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                 "files": counts}
        with open(os.path.join(DATA, "state.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(state, f, ensure_ascii=False)
        return state

    def write_kpi(self):
        m = metrics.compute(data_dir=DATA, persona=PERSONA, ledger_path=LEDGER)
        signals = len(self.evidence)
        row = {
            "date": date.today().isoformat(),
            "kind": "nightly",
            "signals": signals,
            "corrections": self.corrections,
            "rate": round(self.corrections / max(signals, 1), 4),
            "pruned": self.blocks,
            "coverage": m["coverage"],
            "backlink": m["backlink"],
            "note": "demo 合成蒸馏（确定性规则折叠器）",
        }
        os.makedirs(os.path.join(self.home, "eval"), exist_ok=True)
        with open(os.path.join(self.home, "eval", "kpi.jsonl"), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row, m

    def run(self):
        self.skeleton()
        paths = self.fold_threads()
        closed_ids = self.close_reading_club(paths["club"])
        self.evidence.update(closed_ids)
        self.fold_people()
        proto_ids = self.fold_protocols()
        self.evidence.update(proto_ids)
        self.fold_policies()
        self.fold_beliefs()
        self.fold_episodes_and_assets_and_journal()
        self.write_core_and_snapshot()
        swept = self.sweep_and_digest()
        state = self.advance_cursor()
        kpi, m = self.write_kpi()
        return {"swept": swept, "kpi": kpi, "metrics": m, "cursor_files": state["files"],
                "paths": paths}


def main():
    home = os.environ.get("PERSONA_HOME") or REPO
    summary = Distiller(home).run()
    print("确定性蒸馏完成：sweep=%s" % summary["swept"])
    print("KPI：%s" % json.dumps(summary["kpi"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
