#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小秘书 — 唯一操作入口

设计原则
--------
1. 飞书多维表格是唯一主库（手机实时可见、有统计、有推送）
2. 本脚本是唯一写入入口。人和 agent 都不直接改飞书，也不直接改归档文件
3. 每次操作后自动把最新快照归档到数据仓（archive_dir，Markdown，可 git 追溯）

目录形态（双仓结构）
--------
xiaomishu-data/            ← 数据仓（git clone 得到，含 config.json）
├── task.py                ← 从 xiaomishu 工具仓 clone / 拷贝过来
├── config.json            ← 实例配置（脚本从数据仓根读取）
├── DASHBOARD.md / data/ / archive/ / notes/   ← 生成物

config.json 查找顺序：脚本所在目录 → 当前工作目录 → 环境变量 XIAOMISHU_DATA 指向的目录。
archive_dir 支持相对路径（相对于 config.json 所在目录），"." 即数据仓根。

所有子命令都用 --json 输出，便于 agent 解析。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 配置
# 隐私字段默认为空：真实值一律来自数据仓的 config.json，代码里不硬编码。

CONFIG = {
    "base_token": "",
    "table_id": "",
    "base_url": "",
    "archive_dir": ".",
    "tz_offset_hours": 8,
    "lark_cli": "",
}


def _find_config_path():
    """按 脚本目录 → 当前目录 → XIAOMISHU_DATA 环境变量 的顺序找 config.json"""
    candidates = [
        Path(__file__).resolve().parent / "config.json",
        Path.cwd() / "config.json",
    ]
    env_dir = os.environ.get("XIAOMISHU_DATA")
    if env_dir:
        candidates.append(Path(env_dir).expanduser() / "config.json")
    for p in candidates:
        if p.exists():
            return p
    return None


CONFIG_PATH = _find_config_path()
if CONFIG_PATH:
    try:
        CONFIG.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "config.json 读取失败: %s" % e},
                         ensure_ascii=False))
        sys.exit(1)

# archive_dir 支持相对路径：相对于 config.json 所在目录解析
if CONFIG_PATH:
    _root = CONFIG_PATH.parent
    _ad = Path(CONFIG["archive_dir"])
    if not _ad.is_absolute():
        CONFIG["archive_dir"] = str((_root / _ad).resolve())
    else:
        CONFIG["archive_dir"] = str(_ad.resolve())

if not CONFIG.get("base_token") or not CONFIG.get("table_id"):
    _hint = ("未找到有效配置：base_token / table_id 为空。"
             "请在数据仓根目录放 config.json（可从 xiaomishu-data 仓获取），"
             "或设置环境变量 XIAOMISHU_DATA 指向数据仓。"
             if not CONFIG_PATH else
             "config.json 缺少 base_token / table_id，请补全后再运行。")
    print(json.dumps({"ok": False, "error": _hint}, ensure_ascii=False))
    sys.exit(1)

# tz 由偏移量算出，避免 config 里塞不可序列化的对象
CONFIG["tz"] = timezone(timedelta(hours=CONFIG.get("tz_offset_hours", 8)))

# 字段名常量：全部集中在这里，避免散落字符串拼错
F = {
    "id": "任务ID",
    "title": "任务",
    "outcome": "完成标准",
    "status": "状态",
    "pri": "优先级",
    "category": "分类",
    "project": "所属项目",
    "parent": "父任务ID",
    "plan_day": "计划日",
    "due": "截止日",
    "est": "预估分钟",
    "actual": "实际分钟",
    "defers": "顺延次数",
    "blocker": "卡点",
    "progress": "进展",
    "output": "产出链接",
    "summary": "小结",
    "started": "开始时间",
    "done_at": "完成时间",
    "archive": "归档月",
}

STATUS = {
    "todo": "待做",
    "doing": "进行中",
    "blocked": "卡住",
    "done": "已完成",
    "dropped": "已放弃",
}
STATUS_ALIASES = {
    "todo": "todo", "待做": "todo", "待办": "todo", "queued": "todo",
    "doing": "doing", "进行中": "doing", "在做": "doing",
    "blocked": "blocked", "卡住": "blocked", "阻塞": "blocked",
    "done": "done", "已完成": "done", "完成": "done",
    "dropped": "dropped", "已放弃": "dropped", "放弃": "dropped",
}
PRI_ALIASES = {"高": "高", "中": "中", "低": "低",
               "1": "高", "2": "中", "3": "低",
               "high": "高", "mid": "中", "low": "低"}


# ---------------------------------------------------------------- 工具

def now():
    return datetime.now(CONFIG["tz"])


def ms(dt):
    """datetime -> 飞书需要的毫秒时间戳"""
    return int(dt.timestamp() * 1000)


def day_start_ms(datestr):
    """'2026-11-13' -> 当天 00:00 的毫秒时间戳（本地时区）"""
    d = datetime.strptime(datestr, "%Y-%m-%d").replace(tzinfo=CONFIG["tz"])
    return ms(d)


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def fail(msg, detail=None, code=1):
    o = {"ok": False, "error": msg}
    if detail:
        o["detail"] = str(detail)[:2000]
    out(o, code)


def norm_status(v):
    if v is None:
        return None
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return None
    return STATUS_ALIASES.get(str(v).strip())


def norm_pri(v):
    if v is None:
        return "中"
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return "中"
    return PRI_ALIASES.get(str(v).strip(), "中")


CATEGORY_ALIASES = {
    "学习": "学习", "study": "学习", "xuexi": "学习",
    "工作": "工作", "work": "工作", "gongzuo": "工作",
}


def norm_category(v, default="工作"):
    if v is None:
        return default
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return default
    return CATEGORY_ALIASES.get(str(v).strip(), default)


def as_list(v):
    """select 字段写入必须是数组"""
    if v is None:
        return None
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------- lark-cli 调用

LARK_DEFAULT = os.path.expanduser(
    "~/.workbuddy/binaries/node/cli-connector-packages/lark-cli"
)


def find_lark():
    """
    定位 lark-cli。顺序：
      1. config.json 里显式指定的路径
      2. 环境变量 LARK_CLI
      3. PATH 里的 lark-cli
      4. WorkBuddy 默认安装位置

    Windows 上 npm 包的可执行是 .cmd 包装脚本，Python 必须调 .cmd 而不是无扩展名的 sh 文件。
    """
    candidates = []
    if CONFIG.get("lark_cli"):
        candidates.append(CONFIG["lark_cli"])
    if os.environ.get("LARK_CLI"):
        candidates.append(os.environ["LARK_CLI"])

    found = shutil.which("lark-cli")
    if found:
        candidates.append(found)

    is_win = os.name == "nt"
    for ext in ((".cmd", ".exe", ".bat") if is_win else ("",)):
        candidates.append(LARK_DEFAULT + ext)

    for c in candidates:
        if not c:
            continue
        try:
            r = subprocess.run([c, "whoami"], capture_output=True, timeout=30)
            if r.returncode == 0 and b'"available": true' in r.stdout:
                return c
        except Exception:
            continue
    return None


def lark(args, timeout=120):
    """调用 lark-cli，返回解析后的 data 部分"""
    exe = find_lark()
    if not exe:
        fail("找不到 lark-cli。这套系统依赖飞书连接器，"
             "请在 WorkBuddy 里确认飞书连接器已安装并授权。")

    cmd = [exe] + args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        fail("lark-cli 超时（%ds）" % timeout, " ".join(cmd))
    except Exception as e:
        fail("lark-cli 调用失败", e)

    raw = r.stdout.decode("utf-8", "replace").strip()
    err = r.stderr.decode("utf-8", "replace").strip()

    if not raw:
        fail("lark-cli 无输出", err or "returncode=%d" % r.returncode)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail("lark-cli 返回非 JSON", raw[:1500] + ((" | stderr: " + err) if err else ""))

    if not payload.get("ok", False):
        msg = payload.get("error") or payload.get("msg") or "未知错误"
        fail("飞书接口报错: %s" % msg, raw[:1500])

    return payload.get("data", payload)


def base_args():
    return ["base", "--base-token", CONFIG["base_token"]]


# ---------------------------------------------------------------- 记录读取

def read_all():
    """一次调用读出全部记录（含 record_id 与字段值）"""
    d = lark(base_args() + [
        "+record-list",
        "--table-id", CONFIG["table_id"],
        "--format", "json",
    ])
    fields = d.get("fields") or []
    rows = d.get("data") or []
    rids = d.get("record_id_list") or []

    records = []
    for i, row in enumerate(rows):
        rec = {"_record_id": rids[i] if i < len(rids) else None}
        for j, name in enumerate(fields):
            rec[name] = row[j] if j < len(row) else None
        records.append(rec)
    return records


def simplify(rec):
    """把飞书原始记录转成干净的字典"""
    def scalar(v):
        if isinstance(v, list):
            return v[0] if v else None
        return v

    def dtstr(v):
        if not v:
            return None
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, CONFIG["tz"]).strftime("%Y-%m-%d %H:%M")
        return str(v)[:16].replace("T", " ")

    return {
        "id": scalar(rec.get(F["id"])),
        "title": scalar(rec.get(F["title"])) or "",
        "status": norm_status(rec.get(F["status"])) or "todo",
        "pri": norm_pri(rec.get(F["pri"])),
        "category": norm_category(scalar(rec.get(F["category"]))),
        "project": scalar(rec.get(F["project"])) or "",
        "parent": scalar(rec.get(F["parent"])) or "",
        "outcome": scalar(rec.get(F["outcome"])) or "",
        "blocker": scalar(rec.get(F["blocker"])) or "",
        "progress": scalar(rec.get(F["progress"])) or "",
        "output": scalar(rec.get(F["output"])) or "",
        "summary": scalar(rec.get(F["summary"])) or "",
        "est": rec.get(F["est"]) or 0,
        "actual": rec.get(F["actual"]) or 0,
        "defers": rec.get(F["defers"]) or 0,
        "plan_day": dtstr(rec.get(F["plan_day"])),
        "due": dtstr(rec.get(F["due"])),
        "started": dtstr(rec.get(F["started"])),
        "done_at": dtstr(rec.get(F["done_at"])),
        "updated": dtstr(rec.get("更新时间")),
        "archive_month": scalar(rec.get(F["archive"])) or "",
        "_record_id": rec.get("_record_id"),
    }


def find_by_id(records, tid):
    for rec in records:
        raw_id = rec.get(F["id"])
        if isinstance(raw_id, list):
            raw_id = raw_id[0] if raw_id else None
        if str(raw_id) == str(tid):
            return rec
    return None


# ---------------------------------------------------------------- 归档

def load_graveyard():
    """读 data/graveyard.json（被删记录收容所）。不存在则返回空骨架。"""
    gpath = Path(CONFIG["archive_dir"]) / "data" / "graveyard.json"
    if gpath.exists():
        try:
            g = json.loads(gpath.read_text(encoding="utf-8"))
            if isinstance(g, dict) and isinstance(g.get("entries"), list):
                return g, gpath
        except Exception:
            pass
    return {"updated_at": "", "entries": []}, gpath


def save_graveyard(g, gpath):
    g["updated_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")


def archive(records):
    """
    把当前全量快照写成归档文件。这是「换环境能恢复」的关键：
    归档里既有数据内容，也有恢复所需的元信息。

    同时做「消失检测」：上一轮快照里有、本轮表里没有的记录，
    唯一解释是被删除（done/drop 只改状态不会让记录消失）。
    这些记录收进 data/graveyard.json，供 restore 命令恢复。
    没有这一步，快照每次全量覆盖会把被删记录「洗掉」，
    误删将永远无法找回。
    """
    root = Path(CONFIG["archive_dir"])
    if not root.exists():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"ok": False, "error": "归档目录不可写: %s" % e}

    items = [simplify(r) for r in records]
    ts = now().strftime("%Y-%m-%d %H:%M:%S")

    # 0) 消失检测：必须在新快照覆盖写入之前读旧快照，读到的才是「上一轮」
    (root / "data").mkdir(exist_ok=True)
    snap_path = root / "data" / "tasks.json"
    snap_old = None
    if snap_path.exists():
        try:
            snap_old = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            snap_old = None
    vanished_new = 0
    if snap_old and isinstance(snap_old.get("tasks"), list):
        g, gpath = load_graveyard()
        known = {e.get("old_id") for e in g["entries"]}
        current_ids = {i["id"] for i in items if i["id"]}
        for t in snap_old["tasks"]:
            oid = t.get("id")
            if oid and oid not in current_ids and oid not in known:
                g["entries"].append({
                    "old_id": oid,
                    "task": t,
                    "vanished_at": ts,
                    "status": "active",
                })
                vanished_new += 1
        if vanished_new:
            save_graveyard(g, gpath)

    # 1) 快照：机器可读，恢复时直接读这个
    snap = {
        "snapshot_at": ts,
        "base_token": CONFIG["base_token"],
        "table_id": CONFIG["table_id"],
        "base_url": CONFIG["base_url"],
        "count": len(items),
        "tasks": items,
    }
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) 视图：人可读，手机上翻也方便
    md_path = root / "DASHBOARD.md"
    md_path.write_text(render_dashboard(items, ts), encoding="utf-8")

    # 3) 月度台账：只追加，做长期检索
    month_dir = root / "archive" / now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    ledger = month_dir / "ledger.md"

    # 记录已经入过账的（ID, 完成时间）二元组，避免重复追加。
    # 用二元组而非纯 ID：任务被 reopen 后再次完成时，done_at 是新的，
    # 二元组不重复，第二次完成能正常入账。
    seen = set()
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if len(parts) > 3:
                tid = parts[2].strip().strip("✅⛔ ").strip()
                if tid and "↩️" not in parts[3]:
                    seen.add((tid, parts[1].strip()))

    closed = [i for i in items if i["status"] in ("done", "dropped")]
    new_rows = []
    for it in sorted(closed, key=lambda x: x["done_at"] or x["updated"] or ""):
        if (it["id"], it["done_at"] or it["updated"] or "") in seen:
            continue
        mark = "✅" if it["status"] == "done" else "⛔"
        new_rows.append("| %s | %s | %s %s | %s | %s | %s |" % (
            it["done_at"] or it["updated"] or "",
            it["id"], mark, it["title"].replace("|", "/"),
            it["category"],
            it["project"].replace("|", "/"),
            (it["summary"] or it["output"] or "").replace("|", "/"),
        ))

    if new_rows:
        header = ""
        if not ledger.exists():
            header = ("# %s 月度台账\n\n"
                      "| 完成时间 | ID | 任务 | 分类 | 项目 | 结论 |\n"
                      "|---|---|---|---|---|---|\n" % now().strftime("%Y-%m"))
        with open(ledger, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write("\n".join(new_rows) + "\n")

    return {
        "ok": True,
        "snapshot": str(snap_path),
        "dashboard": str(md_path),
        "ledger_new_rows": len(new_rows),
        "graveyard_new": vanished_new,
        "count": len(items),
    }


def render_dashboard(items, ts):
    """生成 DASHBOARD.md — 给人看的那个"""
    L = []
    L.append("# 小秘书 · 看板\n")
    L.append("> 这是脚本自动生成的视图，**不要手改**。数据来源：飞书多维表格。")
    L.append("> 生成时间：%s ｜ 共 %d 条" % (ts, len(items)))
    L.append("> 入口：%s\n" % CONFIG["base_url"])

    doing = [i for i in items if i["status"] == "doing"]
    blocked = [i for i in items if i["status"] == "blocked"]
    todo = [i for i in items if i["status"] == "todo"]

    L.append("## 概览\n")
    counts = {k: len([i for i in items if i["status"] == k]) for k in STATUS}
    L.append("| 进行中 | 卡住 | 待做 | 已完成 | 已放弃 |")
    L.append("|---|---|---|---|---|")
    L.append("| %d | %d | %d | %d | %d |\n" % (
        counts["doing"], counts["blocked"], counts["todo"],
        counts["done"], counts["dropped"]))

    if doing:
        L.append("## 进行中\n")
        L.append("| 任务 | 项目 | 优先级 | 计划日 | 进展 |")
        L.append("|---|---|---|---|---|")
        for i in sorted(doing, key=lambda x: (x["pri"] != "高", x["plan_day"] or "")):
            L.append("| %s | %s | %s | %s | %s |" % (
                i["title"].replace("|", "/"), i["project"] or "-", i["pri"],
                (i["plan_day"] or "-")[:10], (i["progress"] or "-").replace("|", "/")[:40]))
        L.append("")

    if blocked:
        L.append("## 卡住（最该处理的一组）\n")
        L.append("| 任务 | 卡在哪 | 顺延次数 |")
        L.append("|---|---|---|")
        for i in blocked:
            L.append("| %s | %s | %d |" % (
                i["title"].replace("|", "/"),
                (i["blocker"] or "未写明").replace("|", "/")[:60], i["defers"]))
        L.append("")

    if todo:
        L.append("## 待做（前 15 条，高优先级在前）\n")
        L.append("| 任务 | 项目 | 优先级 | 预估 | 完成标准 |")
        L.append("|---|---|---|---|---|")
        for i in sorted(todo, key=lambda x: ({"高": 0, "中": 1, "低": 2}[x["pri"]],
                                            x["defers"] * -1))[:15]:
            L.append("| %s | %s | %s | %d 分 | %s |" % (
                i["title"].replace("|", "/"), i["project"] or "-", i["pri"],
                i["est"], (i["outcome"] or "-").replace("|", "/")[:40]))
        if len(todo) > 15:
            L.append("\n> 还有 %d 条未列出" % (len(todo) - 15))
        L.append("")

    # 项目汇总
    projects = {}
    for i in items:
        if i["status"] in ("done", "dropped"):
            continue
        p = i["project"] or "(未归类)"
        projects.setdefault(p, {"total": 0, "doing": 0})
        projects[p]["total"] += 1
        if i["status"] == "doing":
            projects[p]["doing"] += 1
    if projects:
        L.append("## 项目分布\n")
        L.append("| 项目 | 未完成 | 其中进行中 |")
        L.append("|---|---|---|")
        for p, v in sorted(projects.items(), key=lambda x: -x[1]["total"]):
            L.append("| %s | %d | %d |" % (p, v["total"], v["doing"]))
        L.append("")

    # 最近完成
    recent = sorted([i for i in items if i["status"] == "done" and i["done_at"]],
                    key=lambda x: x["done_at"], reverse=True)[:10]
    if recent:
        L.append("## 最近完成\n")
        L.append("| 完成时间 | 任务 | 结论 |")
        L.append("|---|---|---|")
        for i in recent:
            L.append("| %s | %s | %s |" % (
                i["done_at"], i["title"].replace("|", "/"),
                (i["summary"] or "-").replace("|", "/")[:50]))
        L.append("")

    return "\n".join(L)


# ---------------------------------------------------------------- 写操作

def create_record(fields):
    d = lark(base_args() + [
        "+record-batch-create",
        "--table-id", CONFIG["table_id"],
        "--json", json.dumps({"create_records": [fields]}, ensure_ascii=False),
    ])
    rids = d.get("record_id_list") or d.get("records") or []
    if isinstance(rids, list) and rids and isinstance(rids[0], dict):
        rids = [r.get("record_id") for r in rids]
    return rids[0] if rids else None


def update_record(record_id, fields):
    lark(base_args() + [
        "+record-batch-update",
        "--table-id", CONFIG["table_id"],
        "--json", json.dumps({"update_records": {record_id: fields}}, ensure_ascii=False),
    ])
    return True


# ---------------------------------------------------------------- 子命令

def cmd_add(a):
    if not a.title.strip():
        fail("任务名不能为空")

    status = STATUS_ALIASES.get(a.status, "todo") if a.status else "todo"
    category = norm_category(a.category)
    fields = {
        F["title"]: a.title.strip(),
        F["status"]: [STATUS[status]],
        F["pri"]: [norm_pri(a.pri)],
        F["category"]: [category],
    }
    if a.outcome:
        fields[F["outcome"]] = a.outcome.strip()
    if a.project:
        fields[F["project"]] = a.project.strip()
    if a.parent:
        fields[F["parent"]] = a.parent.strip()
    if a.est:
        fields[F["est"]] = int(a.est)
    if a.due:
        fields[F["due"]] = day_start_ms(a.due)
    if a.plan_day:
        fields[F["plan_day"]] = day_start_ms(a.plan_day)
    if status == "doing":
        fields[F["started"]] = ms(now())

    rid = create_record(fields)

    records = read_all()
    arch = archive(records)

    # 新建的记录 ID 由飞书自动生成，回读找出来
    created = None
    if rid:
        for r in records:
            if r.get("_record_id") == rid:
                created = simplify(r)
                break

    out({"ok": True, "action": "add", "record_id": rid,
         "task": created, "archive": arch})


def cmd_list(a):
    records = read_all()
    items = [simplify(r) for r in records]

    if a.status:
        want = {STATUS_ALIASES.get(x, x) for x in a.status.split(",") if x.strip()}
        items = [i for i in items if i["status"] in want]
    if a.project:
        items = [i for i in items if i["project"] == a.project]
    if a.parent:
        items = [i for i in items if i["parent"] == a.parent]
    if a.today:
        today = now().strftime("%Y-%m-%d")
        items = [i for i in items if (i["plan_day"] or "").startswith(today)]
    if a.search:
        kw = a.search.lower()
        items = [i for i in items
                 if kw in i["title"].lower() or kw in i["outcome"].lower()]

    pri_rank = {"高": 0, "中": 1, "低": 2}
    items.sort(key=lambda x: (pri_rank.get(x["pri"], 9),
                              -(x["defers"] or 0),
                              x["plan_day"] or "9999"))

    out({"ok": True, "count": len(items), "tasks": items})


def cmd_start(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)

    update_record(rec["_record_id"], {
        F["status"]: [STATUS["doing"]],
        F["started"]: ms(now()),
    })
    arch = archive(read_all())
    out({"ok": True, "action": "start", "id": a.id, "archive": arch})


def cmd_done(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)

    cur = simplify(rec)
    if not a.summary:
        fail("完成必须写一句结论（--summary）。"
             "这条规则是为了防止「假完成」：只发了个消息问别人不算完成。")

    category = cur["category"]

    # 报告内容：要么 --report 给完整文件，要么用结构化参数拼装
    report_body = None
    if a.report:
        rp = Path(a.report)
        if not rp.exists():
            fail("报告文件不存在：%s" % a.report)
        text = rp.read_text(encoding="utf-8").strip()
        missing = check_report_sections(text, category)
        if missing:
            fail("报告缺少必需章节：%s。学习报告需含「学习目标/学习过程/结论/推理过程」，"
                 "工作报告需含「工作任务/结果/核心过程/遗留」。" % "、".join(missing))
        report_body = text

    fields = {
        F["status"]: [STATUS["done"]],
        F["done_at"]: ms(now()),
        F["summary"]: a.summary.strip(),
        F["archive"]: now().strftime("%Y-%m"),
    }
    if a.actual:
        fields[F["actual"]] = int(a.actual)
    if a.output:
        fields[F["output"]] = a.output.strip()

    update_record(rec["_record_id"], fields)

    # 单件报告：按分类分文件夹存放
    root = Path(CONFIG["archive_dir"])
    notes_dir = root / "notes" / category / now().strftime("%Y")
    notes_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in cur["title"] if c not in '\\/:*?"<>|').strip()[:40]
    note = notes_dir / ("%s-%s.md" % (now().strftime("%Y-%m-%d"), safe))

    if report_body:
        # 完整报告：把元信息头拼在前面，正文原样保留
        head = render_report_head(cur, a.summary, actual=a.actual, output=a.output)
        note.write_text(head + report_body + "\n", encoding="utf-8")
    else:
        # 结构化参数模式：按分类套模板生成
        if category == "学习":
            body = render_learning_report(
                cur, a.summary,
                goal=a.goal, process=a.process,
                reasoning=a.reasoning, output=a.output)
        else:
            body = render_work_report(
                cur, a.summary,
                result=a.goal, process=a.process,
                leftover=a.leftover, output=a.output)
        note.write_text(body, encoding="utf-8")

    arch = archive(read_all())
    out({"ok": True, "action": "done", "id": a.id, "category": category,
         "report": str(note), "archive": arch})


REPORT_SECTIONS = {
    "学习": ["学习目标", "学习过程", "结论", "推理过程"],
    "工作": ["工作任务", "结果", "核心过程", "遗留"],
}


def check_report_sections(text, category):
    """校验报告是否包含分类要求的章节标题，返回缺失列表"""
    required = REPORT_SECTIONS.get(category, ["结论"])
    missing = []
    for sec in required:
        # 匹配 markdown 标题行（## 学习目标 / ##二、学习目标 等写法）
        found = False
        for line in text.splitlines():
            s = line.strip().lstrip("#").strip()
            s = s.lstrip("一二三四五六七八九十、").strip()
            s = s.rstrip("：:").strip()
            if s == sec or s.startswith(sec):
                found = True
                break
        if not found:
            missing.append(sec)
    return missing


def render_report_head(task, summary, actual=0, output=""):
    L = ["# %s\n" % task["title"]]
    L.append("| | |")
    L.append("|---|---|")
    L.append("| 任务 ID | %s |" % task["id"])
    L.append("| 分类 | %s |" % task["category"])
    L.append("| 项目 | %s |" % (task["project"] or "-"))
    L.append("| 完成时间 | %s |" % now().strftime("%Y-%m-%d %H:%M"))
    actual_val = actual or task["actual"] or 0
    L.append("| 预估 / 实际 | %s 分 / %s 分 |" % (
        task["est"] or "-", actual_val if actual_val else "-"))
    if output:
        L.append("| 产出 | %s |" % output)
    L.append("")
    L.append("## 一句话结论\n")
    L.append(summary.strip() + "\n")
    return "\n".join(L) + "\n"


def render_learning_report(task, summary, goal="", process="",
                           reasoning="", output=""):
    """学习报告：目标 / 过程 / 结论 / 推理过程。
    目标、过程、推理没给全时用占位符标出，不能静默缺省。"""
    L = [render_report_head(task, summary, output=output)]
    L.append("## 学习目标\n")
    L.append((goal or task["outcome"] or "（未填写——补上这次学习要达到的具体目标）") + "\n")
    L.append("## 学习过程\n")
    L.append((process or "（未填写——按时间顺序记录学了什么、卡在哪、怎么解决）") + "\n")
    L.append("## 结论\n")
    L.append(summary.strip() + "\n")
    L.append("## 推理过程\n")
    L.append((reasoning or "（未填写——把推导链条写出来，让没有背景的人能跟上）") + "\n")
    if task["outcome"]:
        L.append("## 当初的完成标准\n")
        L.append(task["outcome"] + "\n")
    return "\n".join(L)


def render_work_report(task, summary, result="", process="",
                       leftover="", output=""):
    """工作报告：工作任务 / 结果 / 核心过程 / 遗留任务。
    结果默认就是一句话结论；过程、遗留没给时占位标出。"""
    L = [render_report_head(task, summary, output=output)]
    L.append("## 工作任务\n")
    L.append((task["title"] or "") + "\n")
    L.append("## 结果\n")
    L.append((result or summary).strip() + "\n")
    L.append("## 核心过程\n")
    L.append((process or "（未填写——记下关键步骤和决策点，以后照着复现）") + "\n")
    L.append("## 遗留任务\n")
    L.append((leftover or "无").strip() + "\n")
    if task["outcome"]:
        L.append("## 当初的完成标准\n")
        L.append(task["outcome"] + "\n")
    return "\n".join(L)


def cmd_block(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)
    if not a.reason:
        fail("必须写清卡在哪（--reason）。「有点难」不算卡点，要具体到等谁、缺什么。")

    cur = simplify(rec)
    update_record(rec["_record_id"], {
        F["status"]: [STATUS["blocked"]],
        F["blocker"]: a.reason.strip(),
        F["defers"]: (cur["defers"] or 0) + 1,
    })
    arch = archive(read_all())
    out({"ok": True, "action": "block", "id": a.id, "archive": arch})


def cmd_drop(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)
    if not a.reason:
        fail("放弃要写理由（--reason）。放弃是有效决定，但要留痕，"
             "否则三个月后自己都不知道当时发生了什么。")

    cur = simplify(rec)
    update_record(rec["_record_id"], {
        F["status"]: [STATUS["dropped"]],
        F["blocker"]: a.reason.strip(),
        F["summary"]: "放弃：%s" % a.reason.strip(),
        F["archive"]: now().strftime("%Y-%m"),
        F["done_at"]: ms(now()),
    })
    arch = archive(read_all())
    out({"ok": True, "action": "drop", "id": a.id, "title": cur["title"],
         "archive": arch})


def cmd_reopen(a):
    """撤销完成/放弃，把任务拉回活跃。是 done/drop 的逆操作，不是 update 的别名：
    只接受终态任务，且清理 done/drop 专属字段，避免"进行中却挂着完成时间"的脏数据。"""
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)
    if not a.reason:
        fail("撤销要写理由（--reason）。撤销完成/放弃是不寻常动作，"
             "必须留痕说明为什么——误操作、结论不成立、还是范围回来了。")

    cur = simplify(rec)
    if cur["status"] not in ("done", "dropped"):
        fail("只有 已完成/已放弃 的任务能撤销。当前状态：%s。" % cur["status"])

    # 回退到哪个状态由用户选：默认 doing，也可 --back todo
    back = "todo" if a.back == "todo" else "doing"

    fields = {
        F["status"]: [STATUS[back]],
        # done/drop 专属字段清空
        F["done_at"]: None,
        F["summary"]: None,
        F["archive"]: None,
        # 撤销原因写进卡点字段留痕（原值被覆盖，但台账里还有闭环时的记录）
        F["blocker"]: "撤销：%s" % a.reason.strip(),
    }
    # 事实字段保留：实际分钟、产出链接是发生过的事，不随状态回退
    update_record(rec["_record_id"], fields)

    # 台账追加撤销行：闭环记录留着（历史真实发生过），撤销也入账（流水完整）
    root = Path(CONFIG["archive_dir"])
    month_dir = root / "archive" / now().strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    ledger = month_dir / "ledger.md"
    undo_line = "| %s | %s | ↩️ 撤销《%s》→ %s | %s | %s | %s |\n" % (
        now().strftime("%Y-%m-%d %H:%M"),
        cur["id"], cur["title"].replace("|", "/"), back,
        cur["category"], cur["project"].replace("|", "/") or "",
        "撤销原因：%s" % a.reason.strip().replace("|", "/"),
    )
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(undo_line)

    arch = archive(read_all())
    out({"ok": True, "action": "reopen", "id": a.id,
         "from": cur["status"], "to": back,
         "report": cur["output"],
         "hint": ("该任务曾生成报告/小结，路径见 output 字段；"
                  "报告文件不会自动删除，是否删由用户决定。" if cur["output"] else ""),
         "archive": arch})


def cmd_update(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)

    fields = {}
    if a.title:
        fields[F["title"]] = a.title.strip()
    if a.outcome:
        fields[F["outcome"]] = a.outcome.strip()
    if a.progress:
        fields[F["progress"]] = a.progress.strip()
    if a.pri:
        fields[F["pri"]] = [norm_pri(a.pri)]
    if a.project:
        fields[F["project"]] = a.project.strip()
    if a.est:
        fields[F["est"]] = int(a.est)
    if a.plan_day:
        fields[F["plan_day"]] = day_start_ms(a.plan_day)
    if a.due:
        fields[F["due"]] = day_start_ms(a.due)
    if a.status:
        st = STATUS_ALIASES.get(a.status)
        if not st:
            fail("状态只能是 todo/doing/blocked/done/dropped 或对应中文")
        fields[F["status"]] = [STATUS[st]]

    if not fields:
        fail("没有任何要改的字段")

    update_record(rec["_record_id"], fields)
    arch = archive(read_all())
    out({"ok": True, "action": "update", "id": a.id,
         "changed": list(fields.keys()), "archive": arch})


def cmd_show(a):
    records = read_all()
    rec = find_by_id(records, a.id)
    if not rec:
        fail("找不到任务 %s" % a.id)
    item = simplify(rec)

    # 附上子任务
    children = [simplify(r) for r in records
                if (simplify(r)["parent"] == item["id"])]
    item["children"] = children
    out({"ok": True, "task": item})


def cmd_stats(a):
    records = read_all()
    items = [simplify(r) for r in records]

    counts = {k: len([i for i in items if i["status"] == k]) for k in STATUS}

    by_project = {}
    for i in items:
        if i["status"] in ("done", "dropped"):
            continue
        p = i["project"] or "(未归类)"
        by_project.setdefault(p, 0)
        by_project[p] += 1

    overdue = [i for i in items
               if i["status"] in ("doing", "todo", "blocked")
               and i["due"] and i["due"][:10] < now().strftime("%Y-%m-%d")]

    stuck = [i for i in items if i["defers"] >= 2 and i["status"] != "done"]

    out({
        "ok": True,
        "total": len(items),
        "counts": counts,
        "by_project": by_project,
        "overdue": [{"id": i["id"], "title": i["title"], "due": i["due"]} for i in overdue],
        "repeatedly_deferred": [
            {"id": i["id"], "title": i["title"], "defers": i["defers"]} for i in stuck
        ],
    })


def cmd_doctor(a):
    """换环境后先跑这个：确认依赖齐不齐"""
    checks = []

    exe = find_lark()
    checks.append({"check": "lark-cli 可执行", "ok": bool(exe), "detail": exe or "未找到"})

    if exe:
        try:
            r = subprocess.run([exe, "whoami"], capture_output=True, timeout=30)
            txt = r.stdout.decode("utf-8", "replace")
            ok = '"available": true' in txt or '"available":true' in txt
            checks.append({"check": "飞书授权有效", "ok": ok,
                           "detail": "ready" if ok else txt[:200]})
        except Exception as e:
            checks.append({"check": "飞书授权有效", "ok": False, "detail": str(e)})

        try:
            d = lark(base_args() + ["+table-list"], timeout=60)
            names = [t.get("name") for t in (d.get("tables") or [])]
            ok = "任务" in names
            checks.append({"check": "目标表可访问", "ok": ok,
                           "detail": ",".join(names) or "无表"})
        except SystemExit:
            checks.append({"check": "目标表可访问", "ok": False, "detail": "调用失败"})
    else:
        checks.append({"check": "飞书授权有效", "ok": False, "detail": "跳过"})
        checks.append({"check": "目标表可访问", "ok": False, "detail": "跳过"})

    root = Path(CONFIG["archive_dir"])
    checks.append({"check": "归档目录存在", "ok": root.exists(), "detail": str(root)})
    if root.exists():
        writable = os.access(str(root), os.W_OK)
        checks.append({"check": "归档目录可写", "ok": writable, "detail": str(root)})
        checks.append({"check": "git 仓库已初始化", "ok": (root / ".git").exists(),
                       "detail": str(root / ".git")})

    all_ok = all(c["ok"] for c in checks)
    out({"ok": all_ok, "checks": checks,
         "hint": "" if all_ok else "有项目未通过，按 detail 提示修复后重跑 doctor"})


def cmd_export(a):
    """导出全量快照，用于换环境或备份"""
    records = read_all()
    arch = archive(records)
    out({"ok": True, "exported": arch})


def cmd_remote(a):
    """
    从 config.json 恢复 git 远端凭据（含令牌），免去换机器后手输账号。

    仓库的 .git/config 不随 git 同步，新机器 clone 下来只带匿名地址。
    本命令把 config.json 里的 gitcode 配置写回两仓 remote，使 push 免密。
    """
    gc = CONFIG.get("gitcode") or {}
    user, token = gc.get("user"), gc.get("token")
    host = gc.get("host") or "gitcode.com"
    if not user or not token:
        fail("config.json 缺少 gitcode.user / gitcode.token，无法恢复远端凭据")

    data_root = Path(CONFIG["archive_dir"]).resolve()
    targets = [
        (gc.get("repo_data") or "xiaomishu-data", data_root),
        (gc.get("repo_tool") or "xiaomishu", data_root.parent / (gc.get("repo_tool") or "xiaomishu")),
    ]
    done = []
    for repo, path in targets:
        if not (path / ".git").exists():
            done.append({"repo": repo, "path": str(path), "ok": False,
                         "detail": "不是 git 仓库，跳过"})
            continue
        url = "https://%s:%s@%s/%s/%s.git" % (user, token, host, user, repo)
        r = subprocess.run(["git", "-C", str(path), "remote", "set-url", "origin", url],
                           capture_output=True, text=True)
        ok = r.returncode == 0
        done.append({"repo": repo, "path": str(path), "ok": ok,
                     "detail": "凭据已写入" if ok else (r.stderr or "")[:200],
                     "url": "https://%s:***@%s/%s/%s.git" % (user, host, user, repo)})

    all_ok = all(d["ok"] for d in done)
    out({"ok": all_ok, "remotes": done,
         "hint": "" if all_ok else "有仓库未恢复，检查路径是否存在 .git"})


def task_to_fields(t, new_id=None):
    """graveyard 里的任务快照 -> 飞书写入格式（select 包数组、日期转毫秒）"""
    def ms_from_str(v):
        if not v:
            return None
        try:
            d = datetime.strptime(str(v)[:16], "%Y-%m-%d %H:%M")
            return ms(d.replace(tzinfo=CONFIG["tz"]))
        except ValueError:
            return None

    status = t.get("status") or "todo"
    if status not in STATUS:
        status = "todo"
    fields = {
        F["title"]: t.get("title") or "",
        F["status"]: [STATUS[status]],
        F["pri"]: [norm_pri(t.get("pri"))],
        F["category"]: [norm_category(t.get("category"))],
    }
    if t.get("outcome"):
        fields[F["outcome"]] = t["outcome"]
    if t.get("project"):
        fields[F["project"]] = t["project"]
    if new_id:
        fields[F["parent"]] = new_id  # 挂新 ID（子任务恢复时由调用方替换为对照表中的新值）
    elif t.get("parent"):
        fields[F["parent"]] = t["parent"]
    if t.get("est"):
        fields[F["est"]] = int(t["est"])
    if t.get("actual"):
        fields[F["actual"]] = int(t["actual"])
    if t.get("defers"):
        fields[F["defers"]] = int(t["defers"])
    if t.get("blocker"):
        fields[F["blocker"]] = t["blocker"]
    if t.get("progress"):
        fields[F["progress"]] = t["progress"]
    if t.get("output"):
        fields[F["output"]] = t["output"]
    if t.get("summary"):
        fields[F["summary"]] = t["summary"]
    if t.get("started"):
        v = ms_from_str(t["started"])
        if v:
            fields[F["started"]] = v
    if t.get("done_at"):
        v = ms_from_str(t["done_at"])
        if v:
            fields[F["done_at"]] = v
    if t.get("plan_day"):
        v = ms_from_str(t["plan_day"])
        if v:
            fields[F["plan_day"]] = v
    if t.get("due"):
        v = ms_from_str(t["due"])
        if v:
            fields[F["due"]] = v
    if t.get("archive_month"):
        fields[F["archive"]] = t["archive_month"]
    return fields


def cmd_restore(a):
    """
    从 graveyard 恢复被误删的任务。

    机制：sync/archive 做消失检测，被删记录收进 data/graveyard.json。
    restore 默认只做「查库+预览」；--yes 才真正回灌。
    回灌后记录获得新 ID（auto_number 不可手填），输出新旧 ID 对照表，
    并把表里其它任务的父任务ID 引用从旧 ID 改成新 ID。
    """
    g, gpath = load_graveyard()
    entries = [e for e in g["entries"] if e.get("status") == "active"]

    if not entries:
        out({"ok": True, "restored": [], "id_map": {},
             "note": "graveyard 为空，没有可恢复的被删记录"})

    # 目标选择：--id 精确选，默认全部
    if a.id:
        picked = [e for e in entries if e.get("old_id") == a.id]
        if not picked:
            fail("graveyard 里没有 %s（或已恢复过）" % a.id)
    else:
        picked = entries

    if not a.yes:
        previews = []
        for e in picked:
            t = e.get("task") or {}
            previews.append({
                "old_id": e.get("old_id"),
                "title": t.get("title"),
                "status": t.get("status"),
                "category": t.get("category"),
                "vanished_at": e.get("vanished_at"),
            })
        out({"ok": True, "preview": previews,
             "note": "预览模式。确认后加 --yes 执行恢复；--id <旧ID> 可只恢复一条"})

    # ---- 真正恢复 ----
    records = read_all()
    id_map = {}
    restored = []
    for e in picked:
        t = e.get("task") or {}
        rid = create_record(task_to_fields(t))
        records = read_all()
        created = None
        if rid:
            for r in records:
                if r.get("_record_id") == rid:
                    created = simplify(r)
                    break
        if not created:
            continue
        id_map[e.get("old_id")] = created["id"]
        e["status"] = "restored"
        e["restored_at"] = now().strftime("%Y-%m-%d %H:%M:%S")
        e["new_id"] = created["id"]
        restored.append(created)

    # 修父任务引用：表里其它任务 parent 指向旧 ID 的，改成新 ID
    parent_fixed = []
    if id_map:
        records = read_all()
        for rec in records:
            cur = simplify(rec)
            if cur["parent"] and cur["parent"] in id_map:
                update_record(rec["_record_id"], {F["parent"]: id_map[cur["parent"]]})
                parent_fixed.append({"id": cur["id"],
                                     "parent_old": cur["parent"],
                                     "parent_new": id_map[cur["parent"]]})

    save_graveyard(g, gpath)
    arch = archive(read_all())
    out({"ok": True, "action": "restore", "restored": restored,
         "id_map": id_map, "parent_fixed": parent_fixed, "archive": arch})


def cmd_sync(a):
    """只重新生成归档，不改数据。改完飞书后手动刷一次用"""
    records = read_all()
    arch = archive(records)
    out({"ok": True, "synced": arch})


# ---------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(
        description="小秘书 — 唯一操作入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用：
  task.py add "把 ITL 口径定下来" --outcome "客户书面确认" --pri 高 --est 45
  task.py list --status doing,blocked
  task.py list --today
  task.py start T-20261113-01
  task.py done T-20261113-01 --summary "口径定了，客户回了邮件" --output "notes/xxx.md"
  task.py done T-20261113-02 --summary "学完了" --process "先看了...再做了..." --reasoning "因为...所以..."
  task.py reopen T-20261113-01 --reason "误标完成，客户又提了新问题"
  task.py block T-20261113-01 --reason "等客户 ITL 定义确认"
  task.py drop T-20261113-01 --reason "客户改用平均值口径，这条作废"
  task.py restore                     # 预览可恢复的误删任务
  task.py restore --yes              # 全部恢复（确认后）
  task.py restore --id T-20261113-03 --yes   # 只恢复一条
  task.py stats
  task.py doctor
        """)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="新增任务")
    pa.add_argument("title", help="任务名，一句话说清做什么")
    pa.add_argument("--outcome", default="", help="做到什么算完成（可验收）")
    pa.add_argument("--pri", default="中", help="高/中/低")
    pa.add_argument("--category", default="工作", help="分类：学习/工作")
    pa.add_argument("--project", default="", help="项目名，留空为独立任务")
    pa.add_argument("--parent", default="", help="父任务 ID，做子任务时填")
    pa.add_argument("--est", type=int, default=0, help="预估分钟")
    pa.add_argument("--due", default="", help="截止日 YYYY-MM-DD")
    pa.add_argument("--plan-day", default="", help="计划日 YYYY-MM-DD")
    pa.add_argument("--status", default="todo", help="初始状态，默认 todo")
    pa.set_defaults(func=cmd_add)

    pl = sub.add_parser("list", help="列出任务")
    pl.add_argument("--status", default="", help="doing,blocked 逗号分隔")
    pl.add_argument("--project", default="")
    pl.add_argument("--parent", default="", help="只看某个任务的子任务")
    pl.add_argument("--today", action="store_true", help="只看计划日是今天的")
    pl.add_argument("--search", default="", help="按关键词搜任务名和完成标准")
    pl.set_defaults(func=cmd_list)

    for name, fn, extra in [
        ("start", cmd_start, "开始做"),
        ("done", cmd_done, "标记完成"),
        ("block", cmd_block, "标记卡住"),
        ("drop", cmd_drop, "放弃"),
        ("reopen", cmd_reopen, "撤销完成/放弃，拉回活跃"),
        ("show", cmd_show, "看单个任务详情"),
    ]:
        sp = sub.add_parser(name, help=extra)
        sp.add_argument("id", help="任务 ID，如 T-20261113-01")
        if name == "done":
            sp.add_argument("--summary", default="", help="一句话结论（必填）")
            sp.add_argument("--output", default="", help="产出在哪")
            sp.add_argument("--actual", type=int, default=0, help="实际分钟")
            sp.add_argument("--points", default="", help="还没搞清楚的地方")
            sp.add_argument("--report", default="",
                            help="完整报告 md 文件路径；给了就校验章节后原样归档")
            sp.add_argument("--goal", default="",
                            help="学习目标（学习）/ 任务结果（工作）")
            sp.add_argument("--process", default="",
                            help="学习过程 / 核心过程")
            sp.add_argument("--reasoning", default="",
                            help="推理过程（学习任务用）")
            sp.add_argument("--leftover", default="",
                            help="遗留任务（工作任务用，默认无）")
        if name in ("block", "drop"):
            sp.add_argument("--reason", default="", help="原因（必填）")
        if name == "reopen":
            sp.add_argument("--reason", default="",
                            help="为什么撤销（必填）：误操作/结论不成立/范围回来了")
            sp.add_argument("--back", default="doing",
                            help="回到哪个状态：doing（默认）/ todo")
        sp.set_defaults(func=fn)

    pu = sub.add_parser("update", help="改字段")
    pu.add_argument("id")
    pu.add_argument("--title", default="")
    pu.add_argument("--outcome", default="")
    pu.add_argument("--progress", default="", help="进展：上次停手时做到哪")
    pu.add_argument("--pri", default="")
    pu.add_argument("--project", default="")
    pu.add_argument("--est", type=int, default=0)
    pu.add_argument("--plan-day", default="")
    pu.add_argument("--due", default="")
    pu.add_argument("--status", default="")
    pu.set_defaults(func=cmd_update)

    for name, fn, h in [
        ("stats", cmd_stats, "统计汇总"),
        ("doctor", cmd_doctor, "环境自检"),
        ("sync", cmd_sync, "重新生成归档"),
        ("export", cmd_export, "导出全量快照"),
        ("remote", cmd_remote, "从 config 恢复 git 远端凭据"),
        ("restore", cmd_restore, "恢复误删任务（先预览，--yes 执行）"),
    ]:
        sp = sub.add_parser(name, help=h)
        if name == "restore":
            sp.add_argument("--id", default="", help="只恢复这一条（旧任务 ID）")
            sp.add_argument("--yes", action="store_true", help="确认执行（默认只预览）")
        sp.set_defaults(func=fn)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
