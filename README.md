# 小秘书（xiaomishu）

任务管理工具：飞书多维表格做唯一主库 + `task.py` 唯一写入入口 + 数据仓 git 持久化。它管三件事：**推动每个任务走到闭环、把已完成的整理成可查阅的记录、给出一眼能看清的看板。**

## 双仓结构

| 仓 | 内容 | 谁写入 |
|---|---|---|
| `xiaomishu`（本仓） | 工具本体：task.py、skill、设计文档、表结构说明 | 人 / agent 手动改进工具时 |
| `xiaomishu-data` | 实例数据：config.json、看板、快照、台账、总结 | task.py 每次操作后自动派生 + git 提交 |

本仓不含任何隐私信息（无令牌、无库地址、无个人路径），可安全开源或分享。

## 初次部署

```bash
# 1. clone 数据仓（含 config.json，私仓）
git clone https://gitcode.com/<你的用户名>/xiaomishu-data.git
cd xiaomishu-data

# 2. clone 工具仓到本仓根目录（内容拷出来，不留嵌套 .git）
git clone https://gitcode.com/<你的用户名>/xiaomishu.git /tmp/xiaomishu
cp -r /tmp/xiaomishu/task.py /tmp/xiaomishu/skills /tmp/xiaomishu/docs .

# 3. 自检（要求：Python 3.8+、本机 WorkBuddy 已装飞书连接器）
python task.py doctor

# 4. 确认能读数据
python task.py list
```

新实例需先建飞书表：按 `docs/feishu-schema.md` 的 21 字段建表，把 base_token / table_id 填进数据仓 `config.json`。

## 常用命令速查

```bash
python task.py add "把 ITL 口径定下来" --outcome "客户书面确认" --pri 高 --project "推理验收"
python task.py list --today          # 今天该做的
python task.py start T-20261113-01   # 开始做
python task.py update T-20261113-01 --progress "三节写完，卡在 ITL 定义"
python task.py block T-20261113-01 --reason "等客户确认口径"   # 卡住（顺延+1）
python task.py done T-20261113-01 --summary "口径定了"         # 完成（必须写结论）
python task.py stats                 # 盘点
python task.py doctor                # 换环境自检
python task.py sync                  # 重新生成看板/快照/台账
```

完整命令与设计决策见 `docs/DESIGN.md`。

## 三条硬规则

1. **完成必须写一句结论**（`done --summary`），防"假完成"
2. **卡住和放弃必须写原因**（`block/drop --reason`），具体到等谁、缺什么
3. **生成物不许手改**，一切通过脚本

## skill

`skills/xiaomishu/` 是给 AI 会话（WorkBuddy 等）用的操作规范。把整个 `skills` 目录拷进 WorkBuddy 的用户技能目录（`~/.workbuddy/skills/`）即可让任何会话学会正确操作小秘书。

## 依赖

| 依赖 | 用途 | 缺失时 |
|---|---|---|
| Python 3.8+ | 跑脚本 | 装一个即可，无第三方包 |
| lark-cli | 读写飞书 | WorkBuddy 装飞书连接器时自带 |
| git | 数据仓可追溯 | 可选，不影响功能 |
