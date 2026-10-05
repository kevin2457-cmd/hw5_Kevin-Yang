# Campus Customs MCP 服务器

这个服务器用 FastMCP 编写（代码在 `server.py`），把 Campus Customs 的数据库包装成一组工具。**所有智能体都必须通过这个服务器提供的工具查询店铺事实和修改业务数据，不要直接读写数据库文件。** `backend/` 中的五个智能体通过 PydanticAI 的 `MCPToolset`（stdio）连接本服务器。

## 使用的数据库

- 路径：`data/campus_customs_new.db`（工作副本），位置根据 `server.py` 所在目录计算，换运行目录也能找到。
- 如果工作副本不存在，服务器会直接报错，不会自动创建空数据库。
- 隔离测试时可以用环境变量 `CAMPUS_CUSTOMS_DB` 指向一份测试数据库。
- 服务器连接时会自动创建两张辅助表（`CREATE TABLE IF NOT EXISTS`，不改动原有业务表）：
  - `approval_requests`：付款审批申请（`pending` / `approved` / `rejected` / `executed`）。
  - `customer_drafts`：客户消息草稿，只保存，不发送。
- 原始数据库 `data/campus_customs.db` 只在手动运行 `reset_db.py` 时被读取，用来恢复工作副本，永远不会被修改。

## 工具清单（18 个）

| 工具 | 功能 | 使用的表 |
|---|---|---|
| `get_ticket_details(ticket_id)` | 一张工单的完整内容，带出关联的租约、发票和发票对应的供应商。 | `tickets`, `leases`, `invoices`, `vendors` |
| `check_stock_and_price(sku, size)` | 某商品某尺码的库存数量和货位，以及成本价和标价。 | `inventory`, `pricing` |
| `get_cash_position()` | 店铺今天日期、现金账户余额、所有租约和所有未结清发票。 | `desk`, `cash_accounts`, `leases`, `invoices` |
| `list_open_tickets()` | 所有未关闭工单的清单和店铺今天日期。 | `desk`, `tickets` |
| `list_sizes_for_sku(sku)` | 某商品所有尺码的库存，用于寻找替代尺码。 | `inventory` |
| `get_vendor_status(vendor_id=None)` | 供应商专长、`lead_days`、未付发票及逾期天数、能否发新货、最早到货日期（按 `desk.date_today` 计算）。 | `desk`, `vendors`, `invoices` |
| `get_lease_status(lease_id)` | 租约详情、距到期天数（按 `desk.date_today`）、已有房租付款和付款申请。 | `desk`, `leases`, `payments`, `approval_requests` |
| `quote_bulk_price(sku, qty, unit_price)` | 按成本价计算报价的收入、成本、毛利、毛利率和折扣幅度，只计算不写入。 | `pricing` |
| `request_payment_approval(kind, ref_id, requested_by, reason)` | 为房租（`rent`）或发票（`invoice`）创建待人工批准的付款申请；金额和到期日取自数据库；不重复创建。 | `desk`, `leases`, `invoices`, `cash_accounts`, `approval_requests` |
| `list_approval_requests(status=None)` | 列出付款申请及其状态。 | `approval_requests` |
| `execute_approved_payment(request_id)` | 执行已由真人批准的模拟付款：检查审批、重复付款、现金是否足够，全部通过才在一个事务里更新付款记录、余额、发票或租约。 | `approval_requests`, `cash_accounts`, `payments`, `invoices`, `leases`, `desk` |
| `save_customer_draft(ticket_id, recipient, subject, body, author)` | 保存客户消息草稿，不发送任何邮件。 | `tickets`, `customer_drafts` |

以下 6 个工具只供 FastAPI 后端使用（第 7 题），不分配给任何智能体；带【人工专用】的还需要后端启动时随机生成的口令（环境变量 `CC_HUMAN_ACTION_TOKEN`，只传给后端自己的 MCP 子进程）：

| 工具 | 功能 | 使用的表 |
|---|---|---|
| `list_tickets()` | 全部工单及状态、每张工单待审批申请数。 | `desk`, `tickets`, `approval_requests` |
| `set_ticket_status(ticket_id, status)` | 把工单标为 `open` / `waiting_approval` / `resolved`。 | `tickets` |
| `relink_approval_requests()` | （第 9 题）按 `tickets.lease_id` / `invoice_id` 关联更正付款申请所属工单，只改归属；后端在每次运行结束后调用。 | `tickets`, `approval_requests` |
| `human_approve_and_pay(request_uid, approved_by, expected_amount, expected_payee, human_token)` | 【人工专用】核对金额和收款方后批准并扣款，同一事务完成。 | `approval_requests`, `cash_accounts`, `payments`, `invoices`, `leases`, `desk` |
| `human_reject_payment(request_uid, rejected_by, human_token)` | 【人工专用】拒绝申请，不扣款。 | `approval_requests` |
| `reset_working_database(human_token)` | 【人工专用】用原始数据库覆盖工作副本，返回被作废的待审批申请。 | 整个工作副本 |

第 7 题起 `request_payment_approval` 增加可选参数 `ticket_id`（智能体运行时由后端自动填入），申请记录增加随机 `uid` 和从数据库读取的收款方 `payee`；`list_approval_requests` 可按 `ticket_id` 筛选。

找不到记录时，查询工具会返回 `"found": false` 和一条中文说明；被拒绝的操作返回 `"ok": false` 和拒绝原因。

## 付款安全

- 智能体拿不到任何批准工具；真人通过后端 `POST /api/approvals/{uid}/approve` 批准，后端带口令调用 `human_approve_and_pay`。没有口令的连接调用人工专用工具会被拒绝。
- 现金不足时拒绝付款，不会出现负余额；没有任何工具可以增加现金。
- 已执行的申请、已付清的发票、已顺延的租约期都不能再付一次；执行时使用 `BEGIN IMMEDIATE` 写锁，防止并发重复付款。

## 恢复原始数据

完整处理全部工单之前，由人手动运行（必须加 `--yes`）：

```bash
python mcp_server/reset_db.py --yes
```

或者调用后端 `POST /api/reset`（请求体 `{"confirm": "RESET"}`）。服务器启动、智能体单次调用和检查脚本都不会自动重置数据库。

## 依赖

```bash
pip install fastmcp
```
