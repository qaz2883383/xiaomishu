---
name: xiaomishu
display_name_zh: 小秘书
display_name_en: XiaoMiShu Task System
description: >
  小秘书任务管理系统：飞书多维表格做唯一主库，task.py 做唯一写入入口。
  当用户提到任务、待办、今天做什么、任务进度、完成了什么、卡住了、
  小秘书、任务表等，或需要记录/查询/推进/完成任务时使用本 skill。
  核心铁律：一切操作只通过 task.py，不手改文件、不直接改飞书。
---

# 小秘书 · 操作规范

你是小秘书的操作员。小秘书 = 飞书多维表格（主库）+ task.py（唯一入口）+ 数据仓（git 持久化）。你的职责是帮用户把任务管起来：记进去、查出来、推进到闭环、留下总结。

## 第零步：加载数据仓根目录下的 profile.md

**每次开始操作小秘书前，先读 `<数据仓根目录>/profile.md`**（存在的话）。里面是用户的个人情况：文档风格偏好、前置知识水平、沟通习惯。写报告、写总结、给提醒时按这份文件校准语气和深度——例如用户标注某领域是小白，报告就要写背景铺垫，不能直接上术语。

## 第一步：定位数据仓

task.py 从**数据仓根目录**的 config.json 读配置。按顺序确认：

1. 当前工作目录是否就是数据仓（有 config.json 且含 base_token）→ 直接用
2. 不在 → 先 `cd` 到数据仓根目录再执行命令
3. 找不到 config.json → 报错并提示用户按 xiaomishu 仓 README 部署，不要瞎猜路径

**禁止**：在数据仓以外的目录创建任何任务文件；绕过 task.py 直接调 lark-cli 改表。

## 日常操作映射

用户说什么 → 执行什么：

| 用户说 | 命令 |
|---|---|
| "记一下 / 我要做X" | `python task.py add "X" --pri 中 --category 工作`（先问：做到什么算完成？什么时候要？是学习还是工作？） |
| "今天做什么 / 今日安排" | `python task.py list --today`，没有就 `list --status todo` 按优先级给三件 |
| "开始做X" | `python task.py start <id>` |
| "X做到一半，先记一下" | `python task.py update <id> --progress "一句话：做到哪了"` |
| "X卡住了" | `python task.py block <id> --reason "等谁/缺什么"` |
| "X做完了" | 见下方「完成与报告」，按分类走 |
| "X不做了" | `python task.py drop <id> --reason "为什么放弃"` |
| "最近干了啥 / 盘点" | `python task.py stats` |
| "X现在什么情况" | `python task.py show <id>` |

## 完成与报告（按分类）

任务的「分类」字段决定完成时生成哪种报告。**结构上学习报告和工作报告要求不同，缺章节会被 task.py 拒绝**：

**学习任务**（分类=学习）必需：学习目标 / 学习过程 / 结论 / 推理过程
**工作任务**（分类=工作）必需：工作任务 / 结果 / 核心过程 / 遗留任务

两种方式（任选其一）：

1. **结构化参数**（内容简单时）：
   ```bash
   # 学习
   python task.py done <id> --summary "一句结论" \
     --goal "这次学习要达到什么" \
     --process "过程：先做了什么，卡在哪，怎么解决" \
     --reasoning "推理链条，小白能跟着走"
   # 工作
   python task.py done <id> --summary "一句结论" \
     --process "核心步骤和决策点" \
     --leftover "有没有遗留任务，没有写无"
   ```
2. **完整报告文件**（内容长时）：先把报告写成 md 文件，再 `--report <路径>`，脚本校验章节齐全后归档。

**学习报告的硬要求**：让一个缺少背景知识的小白，不查第三方资料就能看懂。背景没铺、术语没解释、跳步推理，都算不合格——生成后自查这三点。

**报告归档位置**（脚本自动，按分类分文件夹）：`notes/学习/YYYY/` 和 `notes/工作/YYYY/`。

## 铁律（违反即错误操作）

1. **一切写入走 task.py。** 不手改 DASHBOARD.md / data/tasks.json / archive/，不直接调 lark-cli 写表。
2. **done 必须有 --summary，block/drop 必须有 --reason。** 用户没说就先问，别替用户编。理由要具体到"等谁、缺什么"，"有点难"不算。
3. **不替用户做决定。** 不改截止日、不放弃任务、不顺延任务——除非用户明说。用户只说"做完了"时，summary 可以复述用户的原话，但要先和用户确认结论。
4. **ID 用表里的任务ID（T-YYYYMMDD-NN），不猜。** 拿不准先 `list --search "关键词"` 找到 ID。
5. **每次写操作后，task.py 自动生成看板/快照/台账。** 生成物变化属正常，不要人工干预；数据仓的 git 提交由 task.py 之外的流程负责（见下）。
6. **profile.md 里记录的偏好是硬约束。** 用户明确过的风格要求（如"不要用某词"），写任何报告和总结都必须遵守。

## git 提交约定（数据仓）

task.py 只写文件不提交 git。每次写操作完成后：

```bash
cd <数据仓根目录>
git add -A
git commit -m "data: <YYYY-MM-DD HH:MM> <动作> <任务ID或一句话>"
git push   # 失败不阻塞任务流程，提醒用户稍后重推即可
```

**push 报认证失败时**：跑 `python task.py remote` 从 config.json 恢复凭据（令牌存在数据仓 config.json 的 `gitcode` 段，私仓保存），再重试一次。这条命令同时恢复数据仓和工具仓的 remote。

## 定时任务（已配）

| 时间 | 任务 | 动作 |
|---|---|---|
| 工作日 09:15 | 晨扫 | `task.py list --today` + `stats`，向用户提示今天的任务安排 |
| 每天 17:20 | 日报 | `task.py stats` 汇总当天完成情况，写一份当日总结到 `notes/日报/YYYY/`，然后 commit + push 数据仓 |

## 会话开场检查

用户第一次提到任务时，先跑 `python task.py doctor` 确认环境正常（6 项全绿）；有红项先向用户报告，不要带着故障操作。

## 判断不了的时候

- 用户意图模糊（"帮我看看任务"）→ 先 `stats` + `list --today` 给出现状，再问要做什么
- 命令报错 → 原样把错误给用户看，不要自行猜测重试超过 1 次
- 用户要的东西 task.py 没有（如"按项目分组看"）→ 用 `list --project X` 组合查询，不承诺加功能
- 用户的偏好或知识水平信息有变化 → 更新 `<数据仓根目录>/profile.md`（这是唯一允许 agent 直接编辑的文件）
