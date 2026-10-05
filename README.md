# Campus Customs 多智能体运营看板（MGT 409 第 5 次作业）

一家校园文创店 Campus Customs 有三张待处理工单（101 客户订单、102 房租通知、103 批量折扣）。本项目用 **五个 PydanticAI 智能体**（总负责人、库存、财务、场地、客服）协作处理工单；所有店铺数据都通过 **MCP 服务器** 读写 SQLite 数据库；**FastAPI 后端** 提供接口；**React 看板** 让人实时观看智能体工作，并由**真人批准**付款。智能体只能提出付款申请，不能自行批准或扣款。

所有智能体都通过 **Portkey** 使用 **`gpt-6-luna`**，模型名写死在 `backend/config.py`，没有备用模型。

## 目录结构

```
hw5/
├── AI_prompts.md            # 每道题我输入的提示词记录
├── README.md                # 本文件
├── requirements.txt         # Python 依赖（后端 + MCP 服务器）
├── .env.example             # 环境变量模板（复制为 .env）
├── .gitignore
├── .mcp.json                # 让 Claude Code 等编程助手连接 MCP 服务器
├── data/
│   ├── campus_customs.db        # 原始数据库（只读，用来恢复）
│   └── campus_customs_new.db    # 工作副本（第 9 题完整运行后的状态）
├── mcp_server/
│   ├── server.py            # FastMCP 服务器：18 个工具
│   ├── reset_db.py          # 用原始数据库覆盖工作副本
│   └── README.md            # 工具清单与付款安全说明
├── backend/
│   ├── main.py              # FastAPI 应用（看板接口、人工审批、重置）
│   ├── team.py              # 五个智能体、相互委派、运行限制、审计
│   ├── models.py            # 数据类型、角色与工具分配
│   ├── config.py            # 模型（Portkey + gpt-6-luna）与运行限制
│   ├── audit.py             # 追加写入 output/audit_trail.json
│   ├── check_team.py        # 第 5 题的检查脚本
│   └── prompts/             # 每个智能体一个中文提示词
│       ├── boss.md  inventory.md  accounting.md  facilities.md  customer_service.md
├── frontend/                # React + Vite + TypeScript 看板
└── output/
    ├── harness.md           # 数据表、MCP 工具、智能体、接口、安全规则、运行限制总说明
    ├── mcp_smoke.json       # 第 4 题 MCP 工具测试证据
    ├── desk_tickets.html    # 三个工单的预期计划 / 实际结果 / 现金 / 反思（双击打开）
    ├── design.md            # 看板设计说明
    ├── resolved_tickets.json# 第 9 题每个工单的结果、贡献和审批
    ├── resolved_board.html  # 三个工单解决后的真实看板截图（双击打开）
    ├── audit_trail.json     # 全部审计记录（只追加）
    └── github_url.txt       # 本仓库地址
```

说明：财务负责人在代码中的内部标识是 `finance`，它的提示词文件按作业要求命名为 `backend/prompts/accounting.md`（映射写在 `backend/models.py` 的 `PROMPT_FILES`）。

## 1. 安装依赖

需要 **Python 3.11+** 和 **Node.js 20+**（作者本机为 Python 3.14、Node 24）。

```bash
# 在 hw5/ 根目录
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm install
```

## 2. 配置 Portkey 密钥

```bash
# 在 hw5/ 根目录
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

打开 `.env`，把 `PORTKEY_API_KEY=` 后面换成你自己的 Portkey 密钥。`backend/config.py` 会依次读取 `hw5/.env` 和上一级目录的 `.env`。密钥只从环境变量读取，不写进代码、日志或审计记录；启动 MCP 服务器子进程时也不会把它传过去。

## 3. MCP 服务器

- **后端会自动启动 MCP 服务器**：FastAPI 启动时用 stdio 方式运行 `mcp_server/server.py`（后端自己的连接），每次运行工单时再为智能体启动一个连接。正常使用看板时**不需要手动启动**。
- **连接到编程助手（可选）**：根目录的 `.mcp.json` 配置了名为 `campus-customs` 的服务器（`python mcp_server/server.py`）。在 `hw5/` 目录打开 Claude Code，按提示启用该服务器即可调用工具。`python` 需要是装好 `requirements.txt` 的那个解释器（例如先激活 `.venv`）。
- 服务器连接的是 `data/campus_customs_new.db`，路径按 `server.py` 所在位置计算，不依赖运行目录；找不到文件会直接报错，不会新建空库。

## 4. 启动后端（在 `backend/` 目录）

```bash
cd backend
uvicorn main:app --reload --port 8000
```

后端地址 `http://localhost:8000`，接口文档 `http://localhost:8000/docs`。

> 注意：uvicorn 的写法必须是 `main:app`（模块名:应用对象），只写 `uvicorn main` 会报错。
> Windows 上如果用 `--reload` 修改代码后旧进程没有退出，请直接 Ctrl+C 停止再重新启动（作者在第 9 题遇到过）。

## 5. 启动看板（在 `frontend/` 目录）

```bash
cd frontend
npm run dev
```

打开 `http://localhost:5173`。后端已允许这个来源跨域访问；如需其他来源，可设置环境变量 `CORS_ORIGINS`（逗号分隔）。

## 6. 使用看板：运行工单与人工批准付款

1. 左侧选择工单 101、102 或 103，中间点击 **“▶ 启动智能体团队”**。运行中按钮会锁定，五个智能体卡片和“实时工作过程”每 2 秒刷新。
2. 运行结束后：
   - 没有付款需求 → 工单显示 **✓ 已解决**；
   - 智能体提出了付款申请 → 工单显示 **⏳ 等待审批**，右侧“待人工审批”出现申请卡片（工单、收款方、金额、用途）。
3. 在右侧填写 **审批人姓名**，点击 **“批准并支付 $X”**，再在确认框点 **“确认支付”**。后端核对服务器保存的金额和收款方后才扣款；余额不足、重复批准都会被拒绝并显示原因。
4. 批准成功后看板重新读取余额，该工单的申请全部处理完才会标为已解决。启动工单**不等于**批准付款。

## 7. 全新运行：恢复原始数据库

工作副本 `data/campus_customs_new.db` 目前保存的是第 9 题完整运行后的结果（三个工单已解决、余额 $160.00）。如果要重新跑一遍，先恢复原始数据（会覆盖这些结果，原始库 `data/campus_customs.db` 不变）。三种方式任选其一：

```bash
# 方式 A：命令行（在 hw5/ 根目录，必须加 --yes）
python mcp_server/reset_db.py --yes
```

```bash
# 方式 B：直接复制文件（先停止后端）
cp data/campus_customs.db data/campus_customs_new.db
```

方式 C：在看板右下角点击 **“重置数据库”**，输入 `RESET` 确认（等同于 `POST /api/reset`，有工单运行或付款处理中时会拒绝）。

**完整运行三个工单前只重置一次**，然后依次运行 101、102、103；三个工单之间**不要**分别重置，它们需要共用同一份连续变化的数据库（例如 101 付清供应商发票后，103 的供应商才能发货）。服务器启动、单次调用和检查脚本都不会自动重置。

## 8. 查看结果

| 内容 | 位置 |
|---|---|
| 三个工单的预期计划、实际执行、现金核对、反思 | `output/desk_tickets.html`（双击打开，五个标签页） |
| 三个工单解决后的真实看板截图 | `output/resolved_board.html`（截图已嵌入，双击打开） |
| 每个工单的结果、各智能体贡献、人工审批 | `output/resolved_tickets.json` |
| 每一步委派、工具调用、人工审批、重置的记录 | `output/audit_trail.json`（只追加；第 9 题本轮为事件 #91–#409） |
| 数据表、MCP 工具、接口、安全规则、运行限制 | `output/harness.md` |
| 看板运行时的事件流 | `GET http://localhost:8000/api/events` |

## 9. 检查脚本（可选）

```bash
# 在 hw5/ 根目录：离线检查五个智能体、MCP 工具、付款拦截和审计追加（不调用模型、不付款）
python -m backend.check_team
```

注意：检查脚本会向 `output/audit_trail.json` 追加 10 条 `run_id = self-check` 的测试记录（看板事件接口会自动过滤它们）。
