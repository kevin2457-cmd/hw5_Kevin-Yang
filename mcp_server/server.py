"""Campus Customs MCP 服务器：智能体通过这里的工具访问 data/campus_customs_new.db。"""

import os
import secrets
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reset_db import reset_working_db  # noqa: E402

# 根据本文件位置定位工作副本，与当前运行目录无关。
# 只有隔离测试会通过环境变量 CAMPUS_CUSTOMS_DB 指向另一份测试数据库。
DB_PATH = Path(os.environ.get("CAMPUS_CUSTOMS_DB") or Path(__file__).resolve().parent.parent / "data" / "campus_customs_new.db")

# 人工审批 / 重置口令：只由 FastAPI 后端在启动本服务器时通过环境变量传入。
# 智能体使用的 MCP 连接不带这个口令，因此智能体无法批准付款或重置数据库。
_HUMAN_TOKEN_ENV = "CC_HUMAN_ACTION_TOKEN"

TICKET_STATUSES = ("open", "waiting_approval", "resolved")

mcp = FastMCP("campus-customs")

# 第 5 题新增的两张辅助表：审批申请与客户消息草稿。
# 它们不改动原有业务表；恢复原始数据库后，下次连接会自动重新建表。
# 第 7 题为 approval_requests 增加 uid（随机编号，防止重置后旧链接误批新申请）、ticket_id、payee。
_EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS approval_requests (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    ticket_id INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('rent', 'invoice')),
    ref_id INTEGER NOT NULL,
    payee TEXT NOT NULL,
    amount REAL NOT NULL,
    account TEXT NOT NULL,
    due_date TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'executed')),
    approved_by TEXT,
    decided_at TEXT,
    payment_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customer_drafts (
    id INTEGER PRIMARY KEY,
    ticket_id INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """打开工作副本；文件不存在时报错，不会自动创建空数据库。"""
    if not DB_PATH.is_file():
        raise FileNotFoundError(f"找不到工作副本数据库：{DB_PATH}")
    # mode=rw：文件不存在时 SQLite 不会新建文件
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=rw", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    _migrate_approval_table(conn)
    conn.executescript(_EXTRA_TABLES)
    return conn


def _migrate_approval_table(conn: sqlite3.Connection) -> None:
    """第 5 题建的 approval_requests 没有 uid / ticket_id / payee。空表直接按新结构重建，非空表补列。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approval_requests)")}
    if not cols or "uid" in cols:
        return
    if conn.execute("SELECT COUNT(*) FROM approval_requests").fetchone()[0] == 0:
        conn.execute("DROP TABLE approval_requests")
    else:
        for col, ddl in (("ticket_id", "INTEGER"), ("payee", "TEXT NOT NULL DEFAULT ''"), ("uid", "TEXT")):
            if col not in cols:
                conn.execute(f"ALTER TABLE approval_requests ADD COLUMN {col} {ddl}")
        for (rid,) in conn.execute("SELECT id FROM approval_requests WHERE uid IS NULL").fetchall():
            conn.execute("UPDATE approval_requests SET uid = ? WHERE id = ?", (uuid.uuid4().hex, rid))
    conn.commit()


def _human_token_ok(token: str) -> bool:
    expected = os.environ.get(_HUMAN_TOKEN_ENV, "")
    return bool(expected) and secrets.compare_digest(expected, token or "")


def _today(conn: sqlite3.Connection) -> date:
    """店铺的“今天”一律取 desk.date_today，不使用电脑日期。"""
    row = conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
    if row is None:
        raise ValueError("desk 表中没有 date_today，无法判断店铺日期。")
    return date.fromisoformat(row["date_today"])


def _add_month(d: date) -> date:
    month = d.month % 12 + 1
    year = d.year + (d.month == 12)
    for day in (d.day, 30, 29, 28):
        try:
            return d.replace(year=year, month=month, day=day)
        except ValueError:
            continue
    raise ValueError(f"无法计算 {d} 的下一个月份日期")


def _vendor_status(conn: sqlite3.Connection, vendor: dict, today: date) -> dict:
    unpaid = [
        dict(r)
        for r in conn.execute(
            "SELECT id, amount, due_date, status, description FROM invoices "
            "WHERE vendor_id = ? AND status != ? ORDER BY due_date",
            (vendor["id"], "paid"),
        )
    ]
    for inv in unpaid:
        inv["days_overdue"] = max(0, (today - date.fromisoformat(inv["due_date"])).days)
    blocked = bool(unpaid)
    return {
        **vendor,
        "unpaid_invoices": unpaid,
        "unpaid_total": sum(i["amount"] for i in unpaid),
        "can_ship_new_orders": not blocked,
        "earliest_arrival_if_ordered_today": None
        if blocked
        else (today + timedelta(days=vendor["lead_days"])).isoformat(),
        "note": "该供应商有未付清发票，付清之前不会发新货。" if blocked else "没有未付发票，可以下单。",
    }


def _one(conn: sqlite3.Connection, sql: str, params: tuple) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def get_ticket_details(ticket_id: int) -> dict:
    """查询一张工单的完整内容，并带出它通过 lease_id / invoice_id 关联的租约、发票及发票对应的供应商。

    读取表：tickets, leases, invoices, vendors
    """
    with closing(_connect()) as conn:
        ticket = _one(conn, "SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        if ticket is None:
            return {"found": False, "message": f"未找到编号为 {ticket_id} 的工单。"}

        result = {"found": True, "ticket": ticket, "lease": None, "invoice": None, "vendor": None}

        if ticket["lease_id"] is not None:
            result["lease"] = _one(conn, "SELECT * FROM leases WHERE id = ?", (ticket["lease_id"],))
            if result["lease"] is None:
                result["lease"] = {"found": False, "message": f"未找到编号为 {ticket['lease_id']} 的租约。"}

        if ticket["invoice_id"] is not None:
            invoice = _one(conn, "SELECT * FROM invoices WHERE id = ?", (ticket["invoice_id"],))
            if invoice is None:
                result["invoice"] = {"found": False, "message": f"未找到编号为 {ticket['invoice_id']} 的发票。"}
            else:
                result["invoice"] = invoice
                result["vendor"] = _one(conn, "SELECT * FROM vendors WHERE id = ?", (invoice["vendor_id"],))
                if result["vendor"] is None:
                    result["vendor"] = {"found": False, "message": f"未找到编号为 {invoice['vendor_id']} 的供应商。"}

        return result


def check_stock_and_price(sku: str, size: str) -> dict:
    """查询某个商品某个尺码的库存数量、货位，以及该商品的成本价和标价。

    读取表：inventory, pricing
    """
    with closing(_connect()) as conn:
        stock = _one(
            conn,
            "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? AND size = ?",
            (sku, size),
        )
        price = _one(conn, "SELECT sku, unit_cost, list_price FROM pricing WHERE sku = ?", (sku,))

    if stock is None and price is None:
        return {"found": False, "message": f"库存表和价格表中都没有 {sku}（尺码 {size}）的记录。"}
    return {
        "found": True,
        "inventory": stock or {"found": False, "message": f"库存表中没有 {sku} 尺码 {size} 的记录。"},
        "pricing": price or {"found": False, "message": f"价格表中没有 {sku} 的记录。"},
    }


def get_cash_position() -> dict:
    """查询今天的日期、各现金账户余额，以及所有租约和未结清（status 不是 paid）的发票，用于判断付款能力。

    读取表：desk, cash_accounts, leases, invoices
    """
    with closing(_connect()) as conn:
        desk = _one(conn, "SELECT date_today, notes FROM desk LIMIT 1", ())
        accounts = [dict(r) for r in conn.execute("SELECT name, balance, date FROM cash_accounts")]
        leases = [dict(r) for r in conn.execute("SELECT * FROM leases ORDER BY next_due")]
        invoices = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM invoices WHERE status != ? ORDER BY due_date", ("paid",)
            )
        ]

    return {
        "today": desk["date_today"] if desk else None,
        "cash_accounts": accounts or {"found": False, "message": "cash_accounts 表中没有账户记录。"},
        "leases": leases or {"found": False, "message": "leases 表中没有租约记录。"},
        "unpaid_invoices": invoices or {"found": False, "message": "没有未结清的发票。"},
    }


def list_open_tickets() -> dict:
    """列出所有状态不是 closed 的工单（编号、类型、申请人、主题、状态），并给出店铺今天的日期。

    读取表：desk, tickets
    """
    with closing(_connect()) as conn:
        today = _today(conn)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, type, requester, subject, status, created_at FROM tickets "
                "WHERE status != ? ORDER BY id",
                ("closed",),
            )
        ]
    return {"today": today.isoformat(), "tickets": rows or {"found": False, "message": "没有未关闭的工单。"}}


def list_sizes_for_sku(sku: str) -> dict:
    """列出某个商品所有尺码的库存数量和货位，用于寻找替代尺码。

    读取表：inventory
    """
    with closing(_connect()) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT sku, name, size, qty, location FROM inventory WHERE sku = ? ORDER BY size", (sku,)
            )
        ]
    if not rows:
        return {"found": False, "message": f"库存表中没有商品 {sku}。"}
    return {"found": True, "sku": sku, "sizes": rows}


def get_vendor_status(vendor_id: int | None = None) -> dict:
    """查询供应商的专长、交货天数（lead_days）、未付发票，以及按店铺今天计算能否发新货和最早到货日期。
    不传 vendor_id 时返回全部供应商。有未付清发票的供应商不会发新货。

    读取表：desk, vendors, invoices
    """
    with closing(_connect()) as conn:
        today = _today(conn)
        if vendor_id is None:
            vendors = [dict(r) for r in conn.execute("SELECT * FROM vendors ORDER BY id")]
        else:
            v = _one(conn, "SELECT * FROM vendors WHERE id = ?", (vendor_id,))
            if v is None:
                return {"found": False, "message": f"未找到编号为 {vendor_id} 的供应商。"}
            vendors = [v]
        statuses = [_vendor_status(conn, v, today) for v in vendors]
    return {"today": today.isoformat(), "vendors": statuses}


def get_lease_status(lease_id: int) -> dict:
    """查询租约详情，按店铺今天计算距离到期还有几天（负数为已逾期），并列出该租约已有的付款记录和审批申请。

    读取表：desk, leases, payments, approval_requests
    """
    with closing(_connect()) as conn:
        today = _today(conn)
        lease = _one(conn, "SELECT * FROM leases WHERE id = ?", (lease_id,))
        if lease is None:
            return {"found": False, "message": f"未找到编号为 {lease_id} 的租约。"}
        payments = [
            dict(r)
            for r in conn.execute("SELECT * FROM payments WHERE kind = ? AND ref_id = ?", ("rent", lease_id))
        ]
        requests = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM approval_requests WHERE kind = ? AND ref_id = ?", ("rent", lease_id)
            )
        ]
    days_left = (date.fromisoformat(lease["next_due"]) - today).days
    return {
        "found": True,
        "today": today.isoformat(),
        "lease": lease,
        "days_until_due": days_left,
        "overdue": days_left < 0,
        "rent_payments": payments,
        "approval_requests": requests,
    }


def quote_bulk_price(sku: str, qty: int, unit_price: float) -> dict:
    """按 pricing 表的成本价计算一个报价的总收入、总成本、毛利和毛利率，并与标价对比折扣幅度。
    只做计算，不修改任何数据。

    读取表：pricing
    """
    if qty <= 0 or unit_price <= 0:
        return {"ok": False, "message": "数量和单价都必须大于 0。"}
    with closing(_connect()) as conn:
        price = _one(conn, "SELECT sku, unit_cost, list_price FROM pricing WHERE sku = ?", (sku,))
    if price is None:
        return {"ok": False, "message": f"价格表中没有 {sku}，无法计算利润。"}
    revenue, cost = unit_price * qty, price["unit_cost"] * qty
    return {
        "ok": True,
        "sku": sku,
        "qty": qty,
        "unit_price": unit_price,
        "unit_cost": price["unit_cost"],
        "list_price": price["list_price"],
        "discount_vs_list_pct": round((1 - unit_price / price["list_price"]) * 100, 1),
        "revenue": revenue,
        "cost": cost,
        "gross_profit": revenue - cost,
        "gross_margin_pct": round((revenue - cost) / revenue * 100, 1),
        "below_cost": unit_price < price["unit_cost"],
    }


def _linked_ticket(conn: sqlite3.Connection, kind: str, ref_id: int) -> int | None:
    """付款对象在数据库中直接关联的工单：房租看 tickets.lease_id，发票看 tickets.invoice_id。"""
    column = "lease_id" if kind == "rent" else "invoice_id"
    row = conn.execute(f"SELECT id FROM tickets WHERE {column} = ? ORDER BY id LIMIT 1", (ref_id,)).fetchone()
    return row["id"] if row else None


def request_payment_approval(kind: str, ref_id: int, requested_by: str, reason: str, ticket_id: int | None = None) -> dict:
    """为一笔房租（kind='rent'，ref_id=租约编号）或供应商发票（kind='invoice'，ref_id=发票编号）
    创建“待人工批准”的付款申请。金额、收款方和到期日从数据库读取，不能由调用者指定。
    本工具只创建申请，不付款，也不能批准。同一笔款已有未完成申请时不会重复创建。

    读取/写入表：desk, leases, invoices, vendors, cash_accounts, approval_requests
    """
    with closing(_connect()) as conn:
        today = _today(conn)
        if kind == "rent":
            lease = _one(conn, "SELECT * FROM leases WHERE id = ?", (ref_id,))
            if lease is None:
                return {"ok": False, "message": f"未找到编号为 {ref_id} 的租约。"}
            amount, due, payee = lease["monthly_rent"], lease["next_due"], lease["landlord"]
        elif kind == "invoice":
            inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (ref_id,))
            if inv is None:
                return {"ok": False, "message": f"未找到编号为 {ref_id} 的发票。"}
            if inv["status"] == "paid":
                return {"ok": False, "message": f"发票 {ref_id} 已经付清，不需要再付款。"}
            vendor = _one(conn, "SELECT name FROM vendors WHERE id = ?", (inv["vendor_id"],))
            amount, due = inv["amount"], inv["due_date"]
            payee = vendor["name"] if vendor else f"vendor #{inv['vendor_id']}"
        else:
            return {"ok": False, "message": "kind 只能是 'rent' 或 'invoice'。"}
        if ticket_id is not None and _one(conn, "SELECT id FROM tickets WHERE id = ?", (ticket_id,)) is None:
            return {"ok": False, "message": f"未找到编号为 {ticket_id} 的工单。"}
        # 付款归属以数据库关联为准（例如房租归属引用该租约的工单），没有直接关联时才用发起运行的工单
        ticket_id = _linked_ticket(conn, kind, ref_id) or ticket_id

        existing = _one(
            conn,
            "SELECT * FROM approval_requests WHERE kind = ? AND ref_id = ? AND due_date = ? "
            "AND status IN ('pending', 'approved', 'executed')",
            (kind, ref_id, due),
        )
        if existing:
            return {"ok": False, "message": "这笔款已经有付款申请，不重复创建。", "existing_request": existing}

        account = _one(conn, "SELECT name, balance FROM cash_accounts WHERE name = ?", ("checking",))
        if account is None:
            return {"ok": False, "message": "没有找到 checking 现金账户。"}
        with conn:
            cur = conn.execute(
                "INSERT INTO approval_requests (uid, ticket_id, kind, ref_id, payee, amount, account, due_date, "
                "requested_by, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, ticket_id, kind, ref_id, payee, amount, account["name"], due,
                 requested_by, reason, datetime.now().isoformat(timespec="seconds")),
            )
        request = _one(conn, "SELECT * FROM approval_requests WHERE id = ?", (cur.lastrowid,))
    return {
        "ok": True,
        "request": request,
        "today": today.isoformat(),
        "balance_now": account["balance"],
        "balance_sufficient_now": account["balance"] >= amount,
        "message": "已创建待人工批准的付款申请。必须由真人在审批入口批准后才能执行，智能体不能自行批准。",
    }


def list_approval_requests(status: str | None = None, ticket_id: int | None = None) -> dict:
    """列出付款审批申请及其状态（pending / approved / rejected / executed），可按状态或工单筛选。

    读取表：approval_requests
    """
    sql, params = "SELECT * FROM approval_requests WHERE 1 = 1", []
    if status:
        sql, params = sql + " AND status = ?", params + [status]
    if ticket_id is not None:
        sql, params = sql + " AND ticket_id = ?", params + [ticket_id]
    with closing(_connect()) as conn:
        requests = [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]
    return {"requests": requests or {"found": False, "message": "没有符合条件的付款申请。"}}


class _Refuse(Exception):
    """付款前检查不通过。code 供后端映射为 HTTP 状态码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _pay_in_transaction(conn: sqlite3.Connection, req: dict, approved_by: str) -> int:
    """在调用方已开启的 BEGIN IMMEDIATE 事务内完成一笔付款，返回 payment_id；检查不通过时抛出 _Refuse。"""
    if req["kind"] == "invoice":
        inv = _one(conn, "SELECT * FROM invoices WHERE id = ?", (req["ref_id"],))
        if inv is None or inv["status"] == "paid":
            raise _Refuse("already_paid", f"发票 {req['ref_id']} 不存在或已经付清，拒绝重复付款。")
    else:
        lease = _one(conn, "SELECT * FROM leases WHERE id = ?", (req["ref_id"],))
        if lease is None or lease["next_due"] != req["due_date"]:
            raise _Refuse("already_paid", f"租约 {req['ref_id']} 这一期（{req['due_date']}）已经付过或租约已变化，拒绝重复付款。")

    acct = _one(conn, "SELECT * FROM cash_accounts WHERE name = ?", (req["account"],))
    if acct is None:
        raise _Refuse("no_account", f"现金账户 {req['account']} 不存在。")
    if acct["balance"] < req["amount"]:
        raise _Refuse(
            "insufficient_funds",
            f"现金不足：余额 {acct['balance']}，需要 {req['amount']}，拒绝付款，不允许出现负余额。",
        )

    today = _today(conn).isoformat()
    cur = conn.execute(
        "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
        (req["kind"], req["ref_id"], req["amount"], req["account"], today, approved_by),
    )
    payment_id = cur.lastrowid
    conn.execute("UPDATE cash_accounts SET balance = balance - ?, date = ? WHERE name = ?", (req["amount"], today, req["account"]))
    if req["kind"] == "invoice":
        conn.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (req["ref_id"],))
    else:
        next_due = _add_month(date.fromisoformat(req["due_date"])).isoformat()
        conn.execute("UPDATE leases SET next_due = ? WHERE id = ?", (next_due, req["ref_id"]))
    conn.execute(
        "UPDATE approval_requests SET status = 'executed', approved_by = ?, decided_at = COALESCE(decided_at, ?), "
        "payment_id = ? WHERE id = ?",
        (approved_by, datetime.now().isoformat(timespec="seconds"), payment_id, req["id"]),
    )
    return payment_id


def _payment_result(conn: sqlite3.Connection, req_id: int, payment_id: int, account: str) -> dict:
    return {
        "ok": True,
        "executed": True,
        "request": _one(conn, "SELECT * FROM approval_requests WHERE id = ?", (req_id,)),
        "payment": _one(conn, "SELECT * FROM payments WHERE id = ?", (payment_id,)),
        "cash_account": _one(conn, "SELECT name, balance, date FROM cash_accounts WHERE name = ?", (account,)),
    }


def execute_approved_payment(request_id: int) -> dict:
    """执行一笔已由真人批准的模拟付款。依次检查：申请存在且状态为 approved、有真人审批人、
    没有被执行过、款项仍未付、现金足够。全部通过才在同一个事务里：写入 payments、扣减现金余额、
    把发票标为 paid 或把租约 next_due 顺延一个月、把申请标为 executed。任何一项不满足都拒绝执行。

    读取/写入表：approval_requests, cash_accounts, payments, invoices, leases, desk
    """
    with closing(_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")  # 加写锁，防止同一笔款被并发重复执行
        try:
            req = _one(conn, "SELECT * FROM approval_requests WHERE id = ?", (request_id,))
            if req is None:
                raise _Refuse("not_found", f"未找到编号为 {request_id} 的付款申请。")
            if req["status"] == "executed":
                raise _Refuse("already_executed", f"申请 {request_id} 已经执行过（payment_id={req['payment_id']}），不能重复付款。")
            if req["status"] != "approved" or not (req["approved_by"] or "").strip():
                raise _Refuse("not_approved", f"申请 {request_id} 当前状态是 {req['status']}，没有真人批准，拒绝付款。")
            payment_id = _pay_in_transaction(conn, req, req["approved_by"])
            conn.execute("COMMIT")
        except _Refuse as refusal:
            conn.execute("ROLLBACK")
            return {"ok": False, "executed": False, "code": refusal.code, "message": str(refusal)}
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _payment_result(conn, req["id"], payment_id, req["account"])


# ---------------------------------------------------------------- 仅供 FastAPI 后端使用的工具
# 下面的工具不分配给任何智能体（见 backend/models.py 的 ROLE_TOOLS）。
# 批准、拒绝和重置还需要后端启动服务器时传入的口令，智能体的 MCP 连接没有这个口令。


def human_approve_and_pay(
    request_uid: str, approved_by: str, expected_amount: float, expected_payee: str, human_token: str
) -> dict:
    """【人工专用】真人批准一笔待审批付款申请并立即执行模拟付款。
    客户端提交的金额和收款方只用于核对，实际金额以数据库中保存的申请为准；不一致则拒绝。
    批准和付款在同一个事务里完成：现金不足、重复批准、款项已付都会整体回滚。

    读取/写入表：approval_requests, cash_accounts, payments, invoices, leases, desk
    """
    if not _human_token_ok(human_token):
        return {"ok": False, "executed": False, "code": "forbidden", "message": "没有有效的人工审批口令，拒绝执行。"}
    if not (approved_by or "").strip():
        return {"ok": False, "executed": False, "code": "invalid", "message": "必须填写真人审批人姓名。"}
    with closing(_connect()) as conn:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            req = _one(conn, "SELECT * FROM approval_requests WHERE uid = ?", (request_uid,))
            if req is None:
                raise _Refuse("not_found", "找不到这个付款申请（可能已因重置数据库而失效）。")
            if req["status"] == "executed":
                raise _Refuse("already_executed", f"申请 #{req['id']} 已经付款（payment_id={req['payment_id']}），不会重复扣款。")
            if req["status"] != "pending":
                raise _Refuse("not_pending", f"申请 #{req['id']} 当前状态是 {req['status']}，不能批准。")
            if abs(float(expected_amount) - req["amount"]) > 0.005 or expected_payee.strip() != req["payee"]:
                raise _Refuse(
                    "mismatch",
                    f"提交的金额/收款方（{expected_amount} / {expected_payee}）与服务器保存的申请"
                    f"（{req['amount']} / {req['payee']}）不一致，拒绝批准。",
                )
            payment_id = _pay_in_transaction(conn, req, approved_by.strip())
            conn.execute("COMMIT")
        except _Refuse as refusal:
            conn.execute("ROLLBACK")
            return {"ok": False, "executed": False, "code": refusal.code, "message": str(refusal)}
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _payment_result(conn, req["id"], payment_id, req["account"])


def human_reject_payment(request_uid: str, rejected_by: str, human_token: str) -> dict:
    """【人工专用】真人拒绝一笔待审批付款申请，不扣款。

    读取/写入表：approval_requests
    """
    if not _human_token_ok(human_token):
        return {"ok": False, "code": "forbidden", "message": "没有有效的人工审批口令，拒绝执行。"}
    if not (rejected_by or "").strip():
        return {"ok": False, "code": "invalid", "message": "必须填写真人审批人姓名。"}
    with closing(_connect()) as conn:
        with conn:
            req = _one(conn, "SELECT * FROM approval_requests WHERE uid = ?", (request_uid,))
            if req is None:
                return {"ok": False, "code": "not_found", "message": "找不到这个付款申请（可能已因重置数据库而失效）。"}
            if req["status"] != "pending":
                return {"ok": False, "code": "not_pending", "message": f"申请 #{req['id']} 当前状态是 {req['status']}，不能拒绝。"}
            conn.execute(
                "UPDATE approval_requests SET status = 'rejected', approved_by = ?, decided_at = ? WHERE id = ?",
                (rejected_by.strip(), datetime.now().isoformat(timespec="seconds"), req["id"]),
            )
        return {"ok": True, "request": _one(conn, "SELECT * FROM approval_requests WHERE id = ?", (req["id"],))}


def relink_approval_requests() -> dict:
    """把付款申请的 ticket_id 更正为数据库中直接关联的工单（只改归属，不改金额、状态和付款）。返回更正明细。

    读取/写入表：tickets, approval_requests
    """
    changes = []
    with closing(_connect()) as conn:
        with conn:
            for r in conn.execute("SELECT id, kind, ref_id, ticket_id FROM approval_requests").fetchall():
                linked = _linked_ticket(conn, r["kind"], r["ref_id"])
                if linked is not None and linked != r["ticket_id"]:
                    conn.execute("UPDATE approval_requests SET ticket_id = ? WHERE id = ?", (linked, r["id"]))
                    changes.append({"request_id": r["id"], "kind": r["kind"], "ref_id": r["ref_id"],
                                    "from_ticket": r["ticket_id"], "to_ticket": linked})
    return {"ok": True, "changes": changes}


def list_tickets() -> dict:
    """列出全部工单（含已解决的）及其状态，以及每张工单待审批的付款申请数量。

    读取表：desk, tickets, approval_requests
    """
    with closing(_connect()) as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT t.*, (SELECT COUNT(*) FROM approval_requests a WHERE a.ticket_id = t.id AND a.status = 'pending') "
                "AS pending_approvals FROM tickets t ORDER BY t.id"
            )
        ]
        today = _today(conn)
    return {"today": today.isoformat(), "tickets": rows}


def set_ticket_status(ticket_id: int, status: str) -> dict:
    """更新工单状态：open（未完成）、waiting_approval（等待人工批准）、resolved（已解决）。

    读取/写入表：tickets
    """
    if status not in TICKET_STATUSES:
        return {"ok": False, "message": f"status 只能是 {', '.join(TICKET_STATUSES)}。"}
    with closing(_connect()) as conn:
        with conn:
            cur = conn.execute("UPDATE tickets SET status = ? WHERE id = ?", (status, ticket_id))
        if cur.rowcount == 0:
            return {"ok": False, "message": f"未找到编号为 {ticket_id} 的工单。"}
        return {"ok": True, "ticket": _one(conn, "SELECT id, status FROM tickets WHERE id = ?", (ticket_id,))}


def reset_working_database(human_token: str) -> dict:
    """【人工专用】把工作副本恢复为原始数据库的内容（原始文件不变）。
    旧的付款申请、草稿随辅助表一起清除，旧申请的 uid 全部失效。返回重置前仍待审批的申请，供审计记录。

    读取/写入：data/campus_customs.db（只读）→ data/campus_customs_new.db（覆盖）
    """
    if not _human_token_ok(human_token):
        return {"ok": False, "code": "forbidden", "message": "没有有效的人工操作口令，拒绝重置。"}
    voided = []
    if DB_PATH.is_file():
        with closing(_connect()) as conn:
            voided = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, uid, ticket_id, kind, ref_id, amount FROM approval_requests WHERE status = 'pending'"
                )
            ]
    target = reset_working_db(DB_PATH)
    with closing(_connect()) as conn:
        balance = _one(conn, "SELECT name, balance FROM cash_accounts WHERE name = ?", ("checking",))
    return {"ok": True, "database": target.name, "voided_pending_requests": voided, "cash_account": balance}


def save_customer_draft(ticket_id: int, recipient: str, subject: str, body: str, author: str) -> dict:
    """保存一封客户消息草稿，供后续看板展示和人工审阅。不会发送任何邮件。

    读取/写入表：tickets, customer_drafts
    """
    with closing(_connect()) as conn:
        if _one(conn, "SELECT id FROM tickets WHERE id = ?", (ticket_id,)) is None:
            return {"ok": False, "message": f"未找到编号为 {ticket_id} 的工单，草稿未保存。"}
        with conn:
            cur = conn.execute(
                "INSERT INTO customer_drafts (ticket_id, recipient, subject, body, author, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, recipient, subject, body, author, datetime.now().isoformat(timespec="seconds")),
            )
        draft = _one(conn, "SELECT * FROM customer_drafts WHERE id = ?", (cur.lastrowid,))
    return {"ok": True, "draft": draft, "message": "草稿已保存，未发送给任何真实客户。"}


for _tool in (
    get_ticket_details,
    check_stock_and_price,
    get_cash_position,
    list_open_tickets,
    list_sizes_for_sku,
    get_vendor_status,
    get_lease_status,
    quote_bulk_price,
    request_payment_approval,
    list_approval_requests,
    execute_approved_payment,
    save_customer_draft,
    list_tickets,
    set_ticket_status,
    relink_approval_requests,
    human_approve_and_pay,
    human_reject_payment,
    reset_working_database,
):
    mcp.tool(_tool)


if __name__ == "__main__":
    mcp.run()
