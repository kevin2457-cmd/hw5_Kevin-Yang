# Campus Customs 多智能体运营：Harness 说明

本文件是多智能体系统的“工作说明书”，后续题目会继续补充。以下内容全部来自对 `data/campus_customs.db` 的只读检查（检查日期：2026-10-04），没有任何推测出来的表名或字段名。

---

## 1. 数据库文件

| 文件 | 用途 |
|---|---|
| `data/campus_customs.db` | 原始数据库，作为基准存档，只读，不做任何修改。 |
| `data/campus_customs_new.db` | 工作副本，由原始数据库直接复制而来（两者 MD5 一致：`46effb90d8811f03e219ea9b5fbdfa5a`）。后续题目中智能体的所有读写都在这个文件上进行。 |

数据库中的“今天”由 `desk.date_today` 决定，当前为 **2026-08-31**。

---

## 2. 数据表与字段

数据库共有 **9 张表**。

### `desk`（1 行）
- 字段：`date_today`, `notes`
- 作用：提供系统的“当前日期”，智能体判断是否逾期、几天后到期时都以它为准。

### `tickets`（3 行）
- 字段：`id`, `type`, `requester`, `subject`, `sku`, `size`, `qty`, `lease_id`, `invoice_id`, `status`, `notes`, `created_at`
- 作用：待处理的任务队列，是智能体的工作入口，`type` 决定由哪个智能体来处理。

### `inventory`（10 行）
- 字段：`sku`, `name`, `size`, `qty`, `location`（主键：`sku` + `size`）
- 作用：记录每个商品、每个尺码的库存和货位，智能体用它判断订单能否立即发货。

### `pricing`（4 行）
- 字段：`sku`, `unit_cost`, `list_price`（主键：`sku`）
- 作用：提供成本价和标价，智能体评估折扣请求时用它计算利润底线。

### `vendors`（3 行）
- 字段：`id`, `name`, `specialty`, `lead_days`
- 作用：供应商名单，记录各自的专长和交货天数，缺货时智能体靠它选择补货渠道、估算到货时间。

### `leases`（1 行）
- 字段：`id`, `space_name`, `landlord`, `monthly_rent`, `next_due`, `notes`
- 作用：记录店铺租约、月租和下次到期日，智能体处理房租通知时用它核对金额和期限。

### `cash_accounts`（1 行）
- 字段：`name`, `balance`, `date`（主键：`name`）
- 作用：记录现金账户余额，智能体付款前用它确认资金是否足够。

### `payments`（0 行）
- 字段：`id`, `kind`, `ref_id`, `amount`, `account`, `paid_at`, `approved_by`
- 作用：付款记录表（目前为空），智能体每付一笔款都要在这里留下可审计的记录，包括审批人。

### `invoices`（1 行）
- 字段：`id`, `vendor_id`, `amount`, `due_date`, `status`, `description`
- 作用：供应商发票，记录应付金额、到期日和状态，智能体用它安排付款优先级。

---

### 辅助表（第 5、7 题新增，服务器连接时自动创建）
- `approval_requests`：`id`, `uid`, `ticket_id`, `kind`, `ref_id`, `payee`, `amount`, `account`, `due_date`, `requested_by`, `reason`, `status`, `approved_by`, `decided_at`, `payment_id`, `created_at`
  - 作用：智能体提出、真人审批的付款申请；`uid` 是对外随机编号，`ticket_id` 按租约/发票与工单的关联确定。
- `customer_drafts`：`id`, `ticket_id`, `recipient`, `subject`, `body`, `author`, `status`, `created_at`
  - 作用：客服负责人保存的客户回复草稿，只保存、不发送。

## 3. 表之间的关联

**数据库明确声明的外键（`FOREIGN KEY`）：**
- `tickets.lease_id` → `leases.id`
- `tickets.invoice_id` → `invoices.id`
- `invoices.vendor_id` → `vendors.id`

> 注：SQLite 默认没有开启外键检查（`PRAGMA foreign_keys` = 0），所以这些约束只是声明，写入时数据库不会强制检查。

**根据字段名和数据推断的关联（数据库里没有声明）：**
- `tickets.sku` + `tickets.size` → `inventory.sku` + `inventory.size`
- `tickets.sku` → `pricing.sku`
- `inventory.sku` → `pricing.sku`
- `payments.account` → `cash_accounts.name`
- `payments.ref_id` → 根据 `payments.kind` 的不同，可能指向 `invoices.id` 或 `leases.id`（表目前为空，这一点是推断）

---

## 4. 三个未处理工单

三个工单的 `status` 都是 `open`，都创建于 2026-08-31。

### 工单 101：客户订单
- **内容：** `type` = `customer_order`，申请人 Tauhid Zaman，主题 “Bulldog tee”，需要 `CC-TEE-WHITE` 尺码 `S` 1 件。备注：“Needs a tee in size S.”
- **状态：** `open`
- **需要的表：**
  - `inventory`：`CC-TEE-WHITE` / `S` 的库存为 **0**（Aisle B），无法直接发货。
  - `pricing`：该商品成本 8.0，标价 28.0。
  - `invoices`：发票 501，供应商 1，金额 840.0，到期日 2026-08-28，状态 `open`，描述 “Rush reprint CC-TEE-WHITE S”。
  - `vendors`：供应商 1 是 Bulldog Print Co（apparel reprint，交货 5 天）。
- **关联方式：**
  - 明确声明：`tickets.invoice_id = 501` → `invoices.id`；`invoices.vendor_id = 1` → `vendors.id`。
  - 推断：`tickets.sku/size` → `inventory`，`tickets.sku` → `pricing`。
- **观察：** 按 `desk.date_today`（2026-08-31）计算，发票 501 已经逾期 3 天。

### 工单 102：房租通知
- **内容：** `type` = `rent_notice`，申请人 Elm City Properties，主题 “Rent due”。备注：“Email: shop rent due in 2 days.”
- **状态：** `open`
- **需要的表：**
  - `leases`：租约 1，Chapel Street shop，房东 Elm City Properties，月租 2400.0，下次到期日 2026-09-02。
  - `cash_accounts`：`checking` 余额 3400.0（2026-08-31）。
  - `payments`：付款后需要在这里记录（目前为空）。
  - `desk`：用于核对“2 天后到期”（2026-08-31 + 2 天 = 2026-09-02，与 `leases.next_due` 一致）。
- **关联方式：**
  - 明确声明：`tickets.lease_id = 1` → `leases.id`。
  - 推断：`requester` 与 `leases.landlord` 文字一致；付款时通过 `payments.account` 关联 `cash_accounts.name`。

### 工单 103：价格调整（批量折扣）
- **内容：** `type` = `price_override`，申请人 Yale AI Club，主题 “Bulk hoodie discount”，需要 `CC-HOOD-NAVY` 尺码 `M` 20 件。备注：“Student org wants 20 hoodies, size M, asking for a bulk discount.”
- **状态：** `open`
- **需要的表：**
  - `inventory`：`CC-HOOD-NAVY` / `M` 库存只有 **8**（Aisle A），不够 20 件。
  - `pricing`：成本 22.0，标价 58.0，是计算折扣底线的依据。
  - `vendors`：缺货时可能需要找 Bulldog Print Co 补货。
- **关联方式：**
  - 明确声明：无（`lease_id`、`invoice_id` 都为空）。
  - 推断：`tickets.sku/size` → `inventory`，`tickets.sku` → `pricing`；补货供应商是根据 `vendors.specialty` 推断的。

### 跨工单的观察
- 现金余额 3400.0。房租 2400.0 加上发票 501 的 840.0 一共 3240.0，余额够付，但付完只剩 160.0。后续智能体安排付款时需要考虑这一点。

---

## 5. MCP 工具（第 3 题）

服务器代码：`mcp_server/server.py`（FastMCP），连接工作副本 `data/campus_customs_new.db`。所有智能体都通过这些工具访问数据库，返回的数据全部来自数据库查询。

### 工具 1：`get_ticket_details(ticket_id)`
- **用途：** 按工单编号查出工单全文，并沿着 `lease_id` / `invoice_id` 带出关联的租约、发票和发票对应的供应商。
- **读取的表：** `tickets`, `leases`, `invoices`, `vendors`
- **帮助处理的工单：** **102**（同时也用于 101）
- **为什么是它：** 工单 102 只有一句 “rent due in 2 days” 的邮件，智能体必须通过 `lease_id = 1` 查到租约 1 的月租 2400.0、到期日 2026-09-02 和房东名称，才能核实这份通知是不是真的、该付多少钱。这个工具一次调用就把工单和租约一起返回。工单 101 也一样：它能通过 `invoice_id = 501` 查到那张 840.0、已逾期的加急重印发票和供应商 Bulldog Print Co。

### 工具 2：`check_stock_and_price(sku, size)`
- **用途：** 按商品编号和尺码查出库存数量、货位，以及该商品的成本价和标价。
- **读取的表：** `inventory`, `pricing`
- **帮助处理的工单：** **103**（同时也用于 101）
- **为什么是它：** 工单 103 要求 20 件 `CC-HOOD-NAVY` M 码卫衣并且要打折，智能体需要知道 M 码只有 8 件库存（能否交货）和成本 22.0、标价 58.0（折扣最低能给到多少不亏本）。这个工具用工单里的 `sku` + `size` 同时查出这两方面的信息。对工单 101，它能确认 `CC-TEE-WHITE` S 码库存为 0，说明必须等补货。

### 工具 3：`get_cash_position()`
- **用途：** 返回今天的日期（`desk.date_today`）、各现金账户余额、所有租约的到期情况，以及所有状态不是 `paid` 的发票。
- **读取的表：** `desk`, `cash_accounts`, `leases`, `invoices`
- **帮助处理的工单：** **102**（同时也用于 101 关联的发票 501）
- **为什么是它：** 在为工单 102 付 2400.0 房租之前，智能体必须确认 `checking` 账户的 3400.0 够不够，还要把同样未付的发票 501（840.0，已逾期）算进去：两笔一共 3240.0，付完只剩 160.0。只看一张工单是看不出这个资金压力的，所以需要这个汇总所有应付款和余额的工具。

---

## 6. 五个智能体与相互委派（第 5 题）

代码在 `backend/`：`team.py`（智能体与委派）、`models.py`（数据类型）、`config.py`（模型与限制）、`audit.py`（审计记录）、`prompts/`（每个智能体一个中文提示词文件，运行时由 `load_prompt` 读取）。所有智能体都通过 Portkey 使用 `gpt-6-luna`（`OpenAIResponsesModel`），密钥从环境变量 `PORTKEY_API_KEY` 读取，没有备用模型。

| 智能体 | 提示词文件 | 职责 | 可用的 MCP 工具 |
|---|---|---|---|
| 总负责人 `boss` | `prompts/boss.md` | 读工单、分配工作、汇总同事结果、作出最终业务判断 | `list_open_tickets`, `get_ticket_details`, `get_cash_position`, `list_approval_requests` |
| 库存负责人 `inventory` | `prompts/inventory.md` | 查库存、识别缺货、找替代尺码、查补货供应商和交货时间 | `get_ticket_details`, `check_stock_and_price`, `list_sizes_for_sku`, `get_vendor_status` |
| 财务负责人 `finance` | `prompts/accounting.md` | 现金、发票、成本、利润率；准备待人工批准的付款申请；执行已批准的付款 | `get_ticket_details`, `get_cash_position`, `check_stock_and_price`, `get_vendor_status`, `quote_bulk_price`, `request_payment_approval`, `list_approval_requests`, `execute_approved_payment` |
| 场地负责人 `facilities` | `prompts/facilities.md` | 租约、房租、店铺场地；核实房租通知真伪 | `get_ticket_details`, `get_lease_status`, `get_cash_position`, `list_approval_requests` |
| 客服负责人 `customer_service` | `prompts/customer_service.md` | 根据已核实信息起草客户消息草稿 | `get_ticket_details`, `check_stock_and_price`, `list_sizes_for_sku`, `save_customer_draft` |

**委派机制：** 每个智能体都有 `ask_colleague(colleague, request)` 工具，可以找另外四位中的任何一位。被委派的智能体会真正运行一遍（用自己的提示词和工具），把结构化的 `AgentReport`（结论、核实的事实、执行的动作、是否需要真人、付款申请编号、缺失信息）返回给委派者。每次运行都会记录委派链（例如 `boss → finance → inventory`）。

**终止条件：** 不能委派给自己；不能委派给委派链上正在等待结果的人（防止 A→B→A 循环）；委派深度和次数有上限；所有智能体共用一个用量计数器（`usage=ctx.usage`），请求、工具调用和令牌达到上限时整个运行立即停止并说明原因。

## 7. 当前全部 MCP 工具

共 18 个，全部在 `mcp_server/server.py`，连接 `data/campus_customs_new.db`：下表前 12 个按角色分配给智能体，后 6 个只供 FastAPI 后端使用（不分配给任何智能体，带【人工专用】的还需要后端启动时随机生成的口令）。

| 工具 | 用途 | 使用的表 |
|---|---|---|
| `get_ticket_details` | 工单全文及关联租约、发票、供应商 | `tickets`, `leases`, `invoices`, `vendors` |
| `check_stock_and_price` | 某 sku/尺码的库存、货位、成本价、标价 | `inventory`, `pricing` |
| `get_cash_position` | 今天日期、现金余额、全部租约、未付发票 | `desk`, `cash_accounts`, `leases`, `invoices` |
| `list_open_tickets` | 未关闭工单清单和今天日期 | `desk`, `tickets` |
| `list_sizes_for_sku` | 同一商品所有尺码的库存（找替代） | `inventory` |
| `get_vendor_status` | 供应商 `lead_days`、未付发票、能否发新货、最早到货日 | `desk`, `vendors`, `invoices` |
| `get_lease_status` | 租约、距到期天数、房租付款记录和申请 | `desk`, `leases`, `payments`, `approval_requests` |
| `quote_bulk_price` | 报价的收入、成本、毛利、毛利率（只计算） | `pricing` |
| `request_payment_approval` | 创建待人工批准的房租或发票付款申请 | `desk`, `leases`, `invoices`, `cash_accounts`, `approval_requests` |
| `list_approval_requests` | 查看付款申请状态 | `approval_requests` |
| `execute_approved_payment` | 执行已由真人批准的模拟付款（多重检查） | `approval_requests`, `cash_accounts`, `payments`, `invoices`, `leases`, `desk` |
| `save_customer_draft` | 保存客户消息草稿（不发送） | `tickets`, `customer_drafts` |
| `list_tickets`（后端） | 全部工单、状态和每张工单待审批数 | `desk`, `tickets`, `approval_requests` |
| `set_ticket_status`（后端） | 把工单标为 `open` / `waiting_approval` / `resolved` | `tickets` |
| `relink_approval_requests`（后端，第 9 题） | 按 `tickets.lease_id` / `invoice_id` 更正付款申请所属工单，只改归属 | `tickets`, `approval_requests` |
| `human_approve_and_pay`（后端·人工专用） | 核对金额和收款方后批准并扣款，同一事务完成 | `approval_requests`, `cash_accounts`, `payments`, `invoices`, `leases`, `desk` |
| `human_reject_payment`（后端·人工专用） | 拒绝申请，不扣款 | `approval_requests` |
| `reset_working_database`（后端·人工专用） | 用原始数据库覆盖工作副本，旧申请失效 | 整个工作副本 |

`approval_requests` 和 `customer_drafts` 是第 5 题新增的辅助表，服务器连接时自动创建，不改动原有业务表。

## 8. 保护真实客户和资金的安全规则

1. **店铺日期：** 所有“今天”、逾期、剩余天数都用 `desk.date_today` 计算（工具内部 `_today()`），不用电脑日期。
2. **交货时间：** 只来自 `vendors.lead_days`（`get_vendor_status`）。
3. **未付发票停发：** 供应商有未付清发票时，`get_vendor_status` 返回 `can_ship_new_orders = false`，不给最早到货日期；提示词要求智能体不得假设补货能立即进行。
4. **真人批准：** MCP 服务器没有任何批准工具，智能体只能创建 `pending` 申请。`execute_approved_payment` 只接受 `status = approved` 且有真人 `approved_by` 的申请。委派请求和同事回复都不算批准，这一点写在每个提示词中，并在每次运行的动态说明里重复提醒。第 7 题起，真人在看板点击批准后由后端 `POST /api/approvals/{uid}/approve` 调用 `human_approve_and_pay` 扣款；智能体的 MCP 连接没有口令，也没有分配这个工具。第 9 题本轮的两笔付款都由真人（杨启荣）在看板上批准。
5. **现金不足拒绝：** 余额小于金额时拒绝执行，不会出现负余额。
6. **不模拟收入：** 没有任何工具能增加现金；提示词禁止把未来收入当作现金。
7. **一致更新、防重复：** 付款在一个 `BEGIN IMMEDIATE` 事务里同时写 `payments`、扣 `cash_accounts`、把发票标为 `paid` 或把租约 `next_due` 顺延一个月、把申请标为 `executed`；已执行的申请、已付发票、已顺延的租约期都会被拒绝。同一笔款不能重复创建申请。
8. **不对外联系：** 没有发送邮件或联系供应商的工具，客户消息只通过 `save_customer_draft` 保存为草稿。
9. **恢复数据：** `mcp_server/reset_db.py --yes` 或 `POST /api/reset`（`confirm=RESET`，可带 `label` 作为本轮标识）用原始数据库覆盖工作副本，只能由人明确触发；有工单运行或付款处理中时拒绝；服务器启动、单次调用和检查脚本都不会重置。
10. **审计：** 每一步都追加到 `output/audit_trail.json`，包括 `run_id`、时间、工单编号、智能体、深度、步骤号、动作（`run_start` / `agent_start` / `model_request` / `tool_call` / `mcp_tool_result` / `delegate_call` / `delegate` / `delegation_result` / `delegation_refused` / `agent_finished` / `run_end`，以及后端写入的 `agent_message` / `ticket_status` / `human_approval` / `human_rejection` / `database_reset` / `approval_relink` / `run_error`）、委派对象或工具名、输入和结果摘要（最多 400 字，过滤疑似密钥）、状态和 `needs_human_approval`。不记录密钥和模型的内部思考。写入时使用线程锁和锁文件，写到临时文件后原子替换，保留历史记录。
11. **密钥：** `PORTKEY_API_KEY` 只从环境变量读取，不写入代码、日志或文档。

## 9. 运行限制与令牌限制

在 `backend/config.py` 中设置。一次团队运行内，所有智能体共享这些上限：

| 限制 | 数值 | 实现方式 |
|---|---|---|
| 模型请求次数 | 30 | `UsageLimits.request_limit` |
| 工具调用次数（MCP + 委派） | 60 | `UsageLimits.tool_calls_limit`（原为 40；第 9 题工单 103 第一次运行在 40 次触顶，修复重复核实后按真实需要上调） |
| 总令牌（输入 + 输出） | 200,000 | `UsageLimits.total_tokens_limit` |
| 单次请求输入令牌 | 60,000 | `UsageLimits.per_request_input_tokens_limit` |
| 委派深度 | 3 | `ask_colleague` 检查委派链长度 |
| 委派次数 | 8 | `ask_colleague` 检查本次运行已委派次数 |
| 循环步骤 | 80 | `Session.count_step()`，所有智能体的每个循环节点都计数 |
| 智能体重试 | 1 | `Agent(retries=1)` |
| MCP 工具重试 | 1 | `MCPToolset(max_retries=1)` |
| Portkey 网络重试 | 1 | `AsyncOpenAI(max_retries=1)` |

达到任何上限时，运行以 `stopped_by_limit` 结束，在结果和审计记录的 `run_end` 中写明原因，不会无限重试。

---

## 10. 后端接口（第 7 题）

代码在 `backend/main.py`（FastAPI）。在 `backend/` 文件夹运行 `uvicorn main:app --reload --port 8000`，地址 `http://localhost:8000`，接口文档在 `/docs`。所有业务数据都通过 MCP 服务器读写；智能体只能创建待审批申请，**只有人工批准接口会触发扣款**。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/tickets` | 获取工单 101/102/103 及其真实状态（`open` / `waiting_approval` / `resolved`）、待审批申请数、是否正在运行 |
| POST | `/api/tickets/{ticket_id}/run` | 让智能体团队（从总负责人开始）处理指定工单；校验编号、拒绝同一工单并发运行；立即返回 `run_id` 和 `running` 状态 |
| GET | `/api/tickets/{ticket_id}/agents` | （第 8 题补充）从审计记录重建该工单最近一次运行：运行状态，以及五个智能体是否参与、当前状态、最终总结、用过的工具和委派对象；后端重启后也能恢复 |
| GET | `/api/runs` | 列出本次后端启动以来的运行记录 |
| GET | `/api/runs/{run_id}` | 查询一次运行的状态（`running` / `waiting_approval` / `resolved` / `stopped_by_limit` / `failed`）、最终报告、用量和委派记录 |
| GET | `/api/events` | 从 `output/audit_trail.json` 读取近期事件（智能体发言、委派、工具调用及结果摘要、人工审批、重置），可按 `ticket_id`、`run_id`、`after` 增量拉取；不含模型内部思考和密钥 |
| GET | `/api/approvals` | 列出付款审批申请（默认只列 `pending`），含 `uid`、收款方、金额、关联工单 |
| POST | `/api/approvals/{uid}/approve` | 真人批准并执行模拟付款；请求体中的金额和收款方只用于核对，必须与服务器保存的申请一致；重复批准、余额不足、已付款都会被拒绝 |
| POST | `/api/approvals/{uid}/reject` | 真人拒绝付款申请，不扣款 |
| GET | `/api/cash` | 通过 MCP 读取 `cash_accounts` 中 `checking` 账户的真实余额 |
| POST | `/api/reset` | 请求体 `{"confirm": "RESET"}` 时把工作副本恢复为原始数据库；有工单运行或付款处理中时拒绝；旧申请全部失效；追加重置审计事件 |
| GET | `/api/health` | 健康检查 |

**付款安全补充：**
- 批准、拒绝、重置调用 MCP 的人工专用工具（`human_approve_and_pay`、`human_reject_payment`、`reset_working_database`），需要后端每次启动时随机生成的口令。口令只存在后端进程内存中，通过环境变量传给后端自己的 MCP 子进程；智能体的 MCP 子进程没有口令，也没有分配这些工具。
- 批准和扣款在同一个 `BEGIN IMMEDIATE` 事务中完成，失败时整体回滚，申请保持 `pending`。
- 付款申请使用随机 `uid` 作为对外编号，重置后旧链接无法误批新一轮的申请。
- 工单运行结束后，有待审批申请则标为 `waiting_approval`，否则标为 `resolved`；该工单的申请全部处理后再标为 `resolved`。点击“运行”永远不等于批准付款。
- `/api/tickets` 的每张工单增加 `last_run`（审计记录中最近一次运行及其状态），前端据此区分“运行失败 / 达到运行限制”和“已解决”。跨域来源默认 `http://localhost:5173`，可用环境变量 `CORS_ORIGINS` 覆盖。
- 错误统一返回 `{"detail": {"code", "message"}}`：工单不存在 404、重复运行 409、金额或收款方不符 409、重复批准 409、余额不足 409、MCP 工具失败 502。

---

## 11. 前端看板（第 8 题）

代码在 `frontend/`（React + Vite + TypeScript），在该目录运行 `npm run dev`，访问 `http://localhost:5173`；直接请求 `http://localhost:8000` 的后端（后端已为 5173 配置跨域）。设计说明见 `output/design.md`。

- 左栏：工单 101/102/103 及真实状态（运行中 / 等待审批 / 已解决 / 运行失败 / 达到运行限制 / 未处理），全部取自后端。
- 中栏：当前工单、总负责人的最终判断、五个智能体卡片（颜色 + 图标 + 角色标签，显示正在做什么、最终总结、用过的工具和委派对象，未参与的标“本次未参与”）、来自审计记录的实时工作过程和协作路线。
- 右栏：`checking` 余额（批准成功后重新向后端读取）、待人工审批申请（工单、收款方、金额、用途、两步确认的批准按钮）、需要输入 `RESET` 的重置按钮。
- 运行中每 2 秒、空闲时每 6 秒轮询；刷新页面后完全从后端恢复状态；前端没有任何密钥。

## 12. 第 9 题正式运行（P9-ROUND-1）

- **起点：** 审计事件 #91（`POST /api/reset`，标识 `P9-ROUND-1`），工作副本恢复为原始数据，`checking` 起始余额 $3,400.00。三个工单在看板上依次运行，共用同一份连续变化的数据库，中途没有再重置。
- **结果：** 101、102、103 全部 `resolved`。真人批准 2 笔付款：发票 501 $840（工单 101）和 9 月房租 $2,400（工单 102）；工单 103 没有付款。期末余额 $160.00 = 3,400 − 3,240，与 `cash_accounts` 一致。详见 `output/desk_tickets.html`（实际执行结果、现金标签页）、`output/resolved_tickets.json`、`output/resolved_board.html`。
- **运行中发现并修复的问题（均保留在审计记录中）：**
  1. 处理工单 101 时，财务负责人顺带为房租创建了申请 #2，后端却按“当前运行的工单”自动标成 101。修复：`request_payment_approval` 改为按 `tickets.lease_id` / `invoice_id` 关联确定归属；新增后端工具 `relink_approval_requests`，每次运行结束后自动校正，并把 #2 更正为工单 102（审计 #191，只改归属，金额和状态不变）。
  2. 工单 103 第一次运行因重复核实（客服在已有核实事实时又委派库存和财务、财务重复试价）超过 40 次工具调用而停止（审计 #224–#347，无付款、无草稿）。修复：运行说明和提示词要求直接采用已核实事实、不重复查询，财务每次最多 3 次 `quote_bulk_price`，工具调用上限调为 60；重试只用了 17 次。
  3. Windows 下由预览工具启动的 `uvicorn --reload` 收到重载信号后旧进程不退出；已给关闭流程加 5 秒超时，本轮通过停止再启动后端来加载修改。
- **已知问题：** `approval_requests.created_at` / `decided_at` 记录电脑时间，而 `payments.paid_at` 使用 `desk.date_today`，工单 102 的总负责人在运行中指出了这一不一致，尚未修改。
