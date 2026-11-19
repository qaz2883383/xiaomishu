# 小秘书（xiaomishu）

个人任务管理系统。核心职责三项：**推动每个任务走到闭环、把已完成的整理成可查阅的记录、提供一眼可读的看板。**

## 1. 问题定义

个人任务管理常见的三类失败模式：

| 失败模式 | 表现 | 本系统的对策 |
|---|---|---|
| 半途而废 | 任务开头后停滞，重提时已丢失上下文 | `update --progress` 强制记录停手点；卡住必须写明"等谁/缺什么" |
| 假完成 | 把"发了个消息"当成"事情做完了" | `done` 必须带 `--summary` 结论；按分类生成结构化报告 |
| 无声腐烂 | 任务长期滞留，既不推进也不正式放弃 | 顺延计数永久保留；卡住任务在看板置顶展示 |

三个问题的共同点是当事人自己不会主动发现，因此系统必须负责暴露，而非依赖自觉。

## 2. 架构

系统由三部分构成，职责边界固定：

```
┌────────────────────────────────────────────────────────┐
│                      AI 会话（agent）                     │
│   加载 skill（操作规范）+ profile.md（用户偏好）           │
│   职责：澄清任务、执行命令、生成报告、提醒与总结            │
└───────────────┬────────────────────────┬───────────────┘
                │ python task.py <cmd>   │ 读写生成物
                ▼                        ▼
┌───────────────────────┐    ┌───────────────────────────┐
│   飞书多维表格「任务」    │    │   数据仓 xiaomishu-data    │
│   唯一主库（权威数据）    │    │   config.json（接入参数）   │
│   手机实时可读           │    │   DASHBOARD.md / tasks.json│
│   字段级校验在写入时完成  │    │   archive/台账 · notes/报告 │
│                        │    │   profile.md · git 持久化  │
└───────────────────────┘    └───────────────────────────┘
```

**逻辑关系**：

- **飞书表是唯一权威数据源**。所有任务记录只存这里；手机端实时可读，不依赖本地机器在线。
- **task.py 是唯一写入口**。agent 与人都不直接改表、不手改文件。校验（结论必填、原因必填、报告章节齐全）在写入那一刻执行，不依赖事后自觉。
- **数据仓是持久化层**。task.py 每次写操作后自动派生看板（DASHBOARD.md）、机器快照（data/tasks.json）、月度台账（archive/）、单件报告（notes/按分类分文件夹）。这些是冷备与检索层，删除任意一份可随时从主库重建。
- **agent 是操作员**。它没有独立状态，一切上下文来自：skill（操作规范）、profile.md（用户偏好与知识水平）、飞书表（任务数据）。换会话、换 agent，行为保持一致。

**数据流**：用户提出任务 → agent 澄清（分类、完成标准、计划日）→ `task.py add` 写入飞书 → 推进期 `start`/`update`/`block` 留痕 → `done` 按分类生成报告并归档 → 生成物自动落盘数据仓 → git 提交持久化。

**报告分型**（由任务「分类」字段驱动）：

| 分类 | 报告必需章节 | 质量要求 | 归档位置 |
|---|---|---|---|
| 学习 | 学习目标 / 学习过程 / 结论 / 推理过程 | 无背景的小白不查第三方资料可读懂 | `notes/学习/YYYY/` |
| 工作 | 工作任务 / 结果 / 核心过程 / 遗留任务 | 核心过程可复现；遗留任务明确列出 | `notes/工作/YYYY/` |

## 3. 双仓结构

| 仓 | 定位 | 内容 |
|---|---|---|
| `xiaomishu`（本仓） | 工具本体，零隐私，可公开 | task.py、skills/、docs/（设计文档、建表手册） |
| `xiaomishu-data`（私仓） | 实例数据 | config.json（含飞书与 git 凭据）、profile.md、生成物（看板/快照/台账/报告） |

同步链路：本地 push → GitCode（权威）→ Push 镜像 → GitHub（灾备，只读）。

**为什么拆两仓**：工具演进低频且应可分享，数据增长高频且含隐私。合仓会导致分享工具时泄漏配置，或数据膨胀污染工具历史。

## 4. 部署（新环境从零搭建）

前提：Python 3.8+（纯标准库，无 pip 依赖）、本机 WorkBuddy 已装飞书连接器（提供 lark-cli）、git。

```bash
# 1. clone 数据仓（私仓，含 config.json）
git clone https://gitcode.com/<user>/xiaomishu-data.git && cd xiaomishu-data

# 2. 取工具件放进数据仓根目录（task.py / skills / docs）
git clone https://gitcode.com/<user>/xiaomishu.git /tmp/xiaomishu
cp -r /tmp/xiaomishu/task.py /tmp/xiaomishu/skills /tmp/xiaomishu/docs .

# 3. 恢复 git 远端凭据（从 config.json 读，免密 push）
python task.py remote

# 4. 自检
python task.py doctor

# 5. 验证数据链路
python task.py list
```

已有飞书表时 4、5 步应全绿。**全新实例**（无既有表）需先按 `docs/feishu-schema.md` 的重建手册建表（22 字段 + 5 视图 + 仪表盘），再把 base_token / table_id 填进 config.json。

## 5. 命令速查

```bash
python task.py add "任务名" --category 学习 --outcome "完成标准" --pri 高 --est 45
python task.py list --today              # 今日任务
python task.py list --status doing,blocked
python task.py start T-20261113-01
python task.py update T-20261113-01 --progress "做到哪了"
python task.py block T-20261113-01 --reason "等谁/缺什么"
python task.py reopen T-20261113-01 --reason "为什么撤销"   # 撤销完成/放弃，回到进行中（--back todo 回待做）
python task.py done T-20261113-01 --summary "结论" \
    --goal "学习目标/任务结果" --process "过程" \
    --reasoning "推理（学习）" --leftover "遗留（工作）"
python task.py done T-20261113-01 --summary "结论" --report 报告.md   # 完整报告
python task.py stats                     # 统计盘点
python task.py doctor                    # 环境自检
python task.py sync                      # 重新生成看板/快照/台账（并捕捉被删记录进 graveyard）
python task.py restore                   # 预览可恢复的误删任务
python task.py restore --yes             # 确认恢复（--id T-xxx 只恢复一条）
python task.py remote                    # 恢复 git 远端凭据
```

## 6. skill 安装

`skills/xiaomishu/` 是 agent 操作规范。拷进 WorkBuddy 用户技能目录后，任何会话提到任务即自动加载：

```bash
cp -r skills/xiaomishu ~/.workbuddy/skills/
```

**必做的一步定制**：SKILL.md 顶部「环境常量」表中的数据仓路径是占位符，安装后必须替换为本机实际路径（如 `D:/任务管理/xiaomishu-data`）。这是 skill 全文唯一需要按机器修改的内容——不替换，agent 会因为找不到数据仓而卡死或展开全盘搜索。

skill 定义了操作映射、报告分型规则、铁律（不手改生成物、不替用户决定、ID 不猜、无触发词不写、事实参数不编、单步执行）以及 profile.md 加载规则。

## 7. 目录说明

```
xiaomishu/
├── task.py                # 唯一操作入口（纯标准库）
├── skills/xiaomishu/SKILL.md   # agent 操作规范
└── docs/
    ├── DESIGN.md          # 设计文档（需求/决策/实现）
    └── feishu-schema.md   # 飞书表重建手册（22 字段+视图+仪表盘）
```

设计决策的完整论证（为什么是飞书、为什么单一入口、归档产物三层定位）见 `docs/DESIGN.md`。
