"""Campus Customs 看板后端（FastAPI）。

在 backend/ 文件夹中启动：
    uvicorn main:app --reload --port 8000

- 所有业务数据都通过 MCP 服务器（mcp_server/server.py）读写，本文件不直接连接数据库。
- 智能体团队复用 backend/team.py；智能体只能创建待审批的付款申请。
- 只有 POST /api/approvals/{uid}/approve（真人点击批准）才会触发实际扣款。
- 批准、拒绝、重置使用本进程启动时随机生成的口令调用 MCP 的人工专用工具；
  智能体的 MCP 连接没有这个口令，无法批准付款或重置数据库。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

# 允许从 backend/ 目录直接运行 uvicorn main:app，同时让 backend.* 包导入可用
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.client.transports import PythonStdioTransport  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend.audit import append_audit, summarize  # noqa: E402
from backend.config import AUDIT_PATH, MCP_SERVER_SCRIPT, MODEL_NAME, mcp_server_env  # noqa: E402
from backend.models import AGENT_TITLES, AgentReport  # noqa: E402
from backend.team import new_run_id, run_team  # noqa: E402

VALID_TICKETS = (101, 102, 103)
TICKET_TASK = (
    "请处理工单 {ticket_id}。先读取工单和相关数据，按需要委派给最合适的同事，最后给出业务判断。"
    "如果需要付款或采购，只能请财务负责人创建待人工批准的付款申请，并在结果中说明等待人工批准；"
    "不得执行付款，也不得把本次运行或任何委派当作批准。客户消息只保存草稿。"
)

# 每次启动随机生成，只保存在本进程内存中，不写入日志或审计记录
_HUMAN_TOKEN = secrets.token_urlsafe(32)


# ====================================================================== 后台事件循环
# uvicorn --reload 在 Windows 上使用 SelectorEventLoop，不能启动子进程；
# MCP 服务器通过 stdio 子进程运行，所以智能体和 MCP 调用都放在一个独立线程的 ProactorEventLoop 中。


class Worker:
    def __init__(self) -> None:
        self.loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="agent-worker", daemon=True)
        self.thread.start()

    async def run(self, coro):
        """在后台循环中执行协程，并在当前（FastAPI）循环中等待结果。"""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self.loop))

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


class ToolFailure(Exception):
    pass


class McpGateway:
    """后端自己的 MCP 连接（带人工操作口令），用于列工单、查余额、批准、重置等。"""

    def __init__(self, worker: Worker) -> None:
        self.worker = worker
        self.client = Client(
            PythonStdioTransport(MCP_SERVER_SCRIPT, cwd=str(PROJECT_DIR), env=mcp_server_env(_HUMAN_TOKEN))
        )

    async def start(self) -> None:
        await self.worker.run(self.client.__aenter__())

    async def close(self) -> None:
        await self.worker.run(self.client.__aexit__(None, None, None))

    async def _call(self, name: str, args: dict[str, Any]) -> Any:
        result = await self.client.call_tool(name, args, raise_on_error=False)
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise ToolFailure(f"MCP 工具 {name} 调用失败：{text}")
        return result.data if result.data is not None else result.structured_content

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        try:
            return await self.worker.run(self._call(name, args or {}))
        except ToolFailure:
            raise
        except Exception as exc:  # 连接断开、子进程异常等
            raise ToolFailure(f"MCP 工具 {name} 调用失败：{type(exc).__name__}: {exc}") from exc

    async def call_in_worker(self, name: str, args: dict[str, Any] | None = None) -> Any:
        """已经在后台循环里时直接调用。"""
        return await self._call(name, args or {})


# ====================================================================== 运行状态


class RunRecord(BaseModel):
    run_id: str
    ticket_id: int
    status: Literal["running", "waiting_approval", "resolved", "stopped_by_limit", "failed"]
    started_at: str
    finished_at: str | None = None
    report: AgentReport | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    delegations: list[dict[str, Any]] = Field(default_factory=list)


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()  # FastAPI 循环和后台循环都会访问
        self.runs: dict[str, RunRecord] = {}
        self.active: dict[int, str] = {}  # ticket_id -> run_id
        self.approvals_in_progress = 0
        self.resetting = False


state = State()
worker: Worker | None = None
gateway: McpGateway | None = None


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _audit_backend(action: str, *, actor: str = "backend", ticket_id: int | None = None, run_id: str = "backend",
                   target: str | None = None, input: Any = None, result: Any = None, status: str = "ok",
                   needs_human_approval: bool = False) -> None:
    append_audit({
        "run_id": run_id,
        "ticket_id": ticket_id,
        "agent": actor,
        "agent_title": {"human": "人工审批人", "backend": "后端"}.get(actor, actor),
        "depth": 0,
        "step": 0,
        "action": action,
        "target": target,
        "input": None if input is None else summarize(input),
        "result": None if result is None else summarize(result),
        "status": status,
        "needs_human_approval": needs_human_approval,
    })


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _gw(name: str, args: dict[str, Any] | None = None) -> Any:
    try:
        return await gateway.call(name, args)
    except ToolFailure as exc:
        raise _err(502, "tool_failure", str(exc)) from exc


def _as_list(value: Any) -> list[dict[str, Any]]:
    """MCP 工具在没有记录时返回 {"found": false, ...}，这里统一成空列表。"""
    return value if isinstance(value, list) else []


# ====================================================================== 应用


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker, gateway
    worker = Worker()
    gateway = McpGateway(worker)
    await gateway.start()  # 只连接 MCP，不会重置数据库
    try:
        yield
    finally:
        try:
            # 关闭 MCP 子进程最多等 5 秒，避免 --reload 重启时卡住（Windows）
            await asyncio.wait_for(gateway.close(), timeout=5)
        except Exception:
            pass
        finally:
            worker.stop()


app = FastAPI(
    title="Campus Customs 看板后端",
    description="五智能体团队 + MCP 服务器。付款只能由人工批准接口触发。",
    version="1.0.0",
    lifespan=lifespan,
)
# 前端 Vite 页面来源；隔离测试时可用环境变量 CORS_ORIGINS（逗号分隔）覆盖
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------- 数据结构


class LastRun(BaseModel):
    run_id: str
    status: str = Field(description="running / completed / stopped_by_limit / failed / interrupted")
    started_at: str
    ended_at: str | None
    stop_reason: str | None


class Ticket(BaseModel):
    id: int
    type: str
    requester: str
    subject: str
    sku: str | None
    size: str | None
    qty: int | None
    notes: str | None
    created_at: str
    status: str = Field(description="数据库中的状态：open / waiting_approval / resolved")
    resolved: bool
    pending_approvals: int
    running: bool
    latest_run_id: str | None
    last_run: LastRun | None = Field(default=None, description="审计记录中该工单最近一次运行")


class TicketList(BaseModel):
    today: str
    tickets: list[Ticket]


class RunStarted(BaseModel):
    run_id: str
    ticket_id: int
    status: Literal["running"]
    message: str


class Event(BaseModel):
    id: int = Field(description="审计记录中的序号，可用作 after 参数增量拉取")
    time: str
    run_id: str
    ticket_id: int | None
    agent: str
    agent_title: str
    action: str
    target: str | None
    message: str
    input: str | None
    result: str | None
    status: str
    needs_human_approval: bool
    depth: int


class EventList(BaseModel):
    events: list[Event]
    last_id: int


class ApprovalRequest(BaseModel):
    uid: str
    id: int
    ticket_id: int | None
    kind: str
    ref_id: int
    payee: str
    amount: float
    account: str
    due_date: str
    requested_by: str
    reason: str
    status: str
    approved_by: str | None
    decided_at: str | None
    payment_id: int | None
    created_at: str


class ApproveBody(BaseModel):
    approved_by: str = Field(min_length=1, description="真人审批人姓名")
    amount: float = Field(description="审批人在界面上看到的金额，仅用于与服务器保存的申请核对")
    payee: str = Field(min_length=1, description="审批人在界面上看到的收款方，仅用于核对")


class RejectBody(BaseModel):
    rejected_by: str = Field(min_length=1)
    reason: str | None = None


class ApprovalResult(BaseModel):
    ok: bool
    request: dict[str, Any]
    payment: dict[str, Any] | None = None
    cash_account: dict[str, Any] | None = None
    ticket_status: str | None = None


class Cash(BaseModel):
    account: str
    balance: float
    as_of: str
    today: str


class ResetBody(BaseModel):
    confirm: Literal["RESET"] = Field(description="必须填写 RESET 才会执行")
    label: str | None = Field(default=None, max_length=80, description="可选：本轮运行标识，写入重置审计事件")


class ResetResult(BaseModel):
    ok: bool
    voided_pending_requests: list[dict[str, Any]]
    cash_account: dict[str, Any] | None
    message: str


# ---------------------------------------------------------------------- 1. 工单列表


@app.get("/api/tickets", response_model=TicketList, summary="获取工单列表及状态")
async def list_tickets() -> TicketList:
    data = await _gw("list_tickets")
    with state.lock:
        active = dict(state.active)
        latest = {}
        for r in state.runs.values():
            latest[r.ticket_id] = r.run_id
    last_runs = _latest_run_by_ticket(_runs_from_audit(_audit_records()), active)
    tickets = [
        Ticket(
            **{k: t[k] for k in ("id", "type", "requester", "subject", "sku", "size", "qty", "notes", "created_at", "status")},
            resolved=t["status"] == "resolved",
            pending_approvals=t["pending_approvals"],
            running=t["id"] in active,
            latest_run_id=active.get(t["id"]) or latest.get(t["id"]),
            last_run=last_runs.get(t["id"]),
        )
        for t in data["tickets"]
        if t["id"] in VALID_TICKETS
    ]
    return TicketList(today=data["today"], tickets=tickets)


# ---------------------------------------------------------------------- 2. 运行工单


async def _run_ticket(run_id: str, ticket_id: int) -> None:
    """在后台循环中运行智能体团队，结束后根据待审批申请更新工单状态。"""
    status, record_update = "failed", {}
    try:
        result = await run_team(TICKET_TASK.format(ticket_id=ticket_id), ticket_id, "boss", run_id)
        record_update = dict(report=result.report, stop_reason=result.stop_reason, usage=result.usage,
                             delegations=result.delegations)
        # 付款申请按数据库关联归属到正确的工单（例如处理 101 时顺带申请的房租归属 102）
        relinked = (await gateway.call_in_worker("relink_approval_requests", {}))["changes"]
        if relinked:
            _audit_backend("approval_relink", ticket_id=ticket_id, run_id=run_id, target="relink_approval_requests",
                           result="付款申请归属更正：" + "；".join(
                               f"申请 #{c['request_id']}（{c['kind']} #{c['ref_id']}）工单 {c['from_ticket']} → {c['to_ticket']}"
                               for c in relinked))
        if result.status == "completed":
            pending = _as_list((await gateway.call_in_worker(
                "list_approval_requests", {"status": "pending", "ticket_id": ticket_id}))["requests"])
            status = "waiting_approval" if pending else "resolved"
            await gateway.call_in_worker("set_ticket_status", {"ticket_id": ticket_id, "status": status})
            _audit_backend("ticket_status", ticket_id=ticket_id, run_id=run_id, target="set_ticket_status",
                           result=f"工单 {ticket_id} 状态更新为 {status}（待审批申请 {len(pending)} 笔）",
                           needs_human_approval=bool(pending))
        else:
            status = result.status
    except Exception as exc:
        record_update = dict(stop_reason=f"{type(exc).__name__}: {exc}")
        _audit_backend("run_error", ticket_id=ticket_id, run_id=run_id, result=str(exc), status="error")
    finally:
        with state.lock:
            rec = state.runs[run_id]
            state.runs[run_id] = rec.model_copy(update={**record_update, "status": status, "finished_at": _now()})
            state.active.pop(ticket_id, None)


@app.post("/api/tickets/{ticket_id}/run", response_model=RunStarted, status_code=202, summary="运行指定工单")
async def run_ticket(ticket_id: int) -> RunStarted:
    if ticket_id not in VALID_TICKETS:
        raise _err(404, "ticket_not_found", f"工单 {ticket_id} 不存在，只能运行 {VALID_TICKETS}。")
    tickets = {t["id"]: t for t in (await _gw("list_tickets"))["tickets"]}
    if ticket_id not in tickets:
        raise _err(404, "ticket_not_found", f"数据库中没有工单 {ticket_id}。")
    if tickets[ticket_id]["status"] == "resolved":
        raise _err(409, "already_resolved", f"工单 {ticket_id} 已解决；如需重新运行，请先重置数据库。")

    with state.lock:
        if state.resetting:
            raise _err(409, "resetting", "数据库正在重置，请稍后再试。")
        if ticket_id in state.active:
            raise _err(409, "already_running", f"工单 {ticket_id} 正在运行（{state.active[ticket_id]}），不能重复执行。")
        run_id = new_run_id()
        state.active[ticket_id] = run_id
        state.runs[run_id] = RunRecord(run_id=run_id, ticket_id=ticket_id, status="running", started_at=_now())

    worker.submit(_run_ticket(run_id, ticket_id))
    return RunStarted(run_id=run_id, ticket_id=ticket_id, status="running",
                      message="智能体团队已开始处理。请轮询 /api/runs/{run_id} 和 /api/events 查看进度。")


@app.get("/api/runs", response_model=list[RunRecord], summary="本次后端启动以来的运行记录")
async def list_runs(ticket_id: int | None = None) -> list[RunRecord]:
    with state.lock:
        runs = list(state.runs.values())
    return [r for r in runs if ticket_id is None or r.ticket_id == ticket_id]


@app.get("/api/runs/{run_id}", response_model=RunRecord, summary="查询一次运行的状态和结果")
async def get_run(run_id: str) -> RunRecord:
    with state.lock:
        rec = state.runs.get(run_id)
    if rec is None:
        raise _err(404, "run_not_found", f"找不到运行 {run_id}（后端重启后内存中的运行记录会清空，历史请查 /api/events）。")
    return rec


# ---------------------------------------------------------------------- 3. 近期事件

_HIDDEN_BY_DEFAULT = {"model_request", "tool_call", "delegate_call"}  # 与后续 mcp_tool_result / delegate 重复的底层步骤


def _event_message(r: dict[str, Any]) -> str:
    who = r.get("agent_title") or r.get("agent")
    target = r.get("target") or ""
    target_title = AGENT_TITLES.get(target, target)
    a = r["action"]
    text = {
        "run_start": f"{who} 开始处理任务",
        "agent_start": f"{who} 开始工作",
        "agent_message": f"{who}：{r.get('result') or ''}",
        "model_request": f"{who} 请求模型 {target}",
        "tool_call": f"{who} 准备调用 {target}",
        "mcp_tool_result": f"{who} 调用工具 {target} → {r.get('result') or ''}",
        "delegate_call": f"{who} 准备委派",
        "delegate": f"{who} 委派给 {target_title}：{r.get('input') or ''}",
        "delegation_result": f"{target_title} 回复 {who}：{r.get('result') or ''}",
        "delegation_refused": f"{who} 委派给 {target_title} 被拒绝：{r.get('result') or ''}",
        "agent_finished": f"{who} 完成：{r.get('result') or ''}",
        "run_end": f"运行结束（{r.get('status')}）",
        "ticket_status": r.get("result") or "工单状态更新",
        "human_approval": f"人工审批：{r.get('result') or ''}",
        "human_rejection": f"人工拒绝：{r.get('result') or ''}",
        "database_reset": f"数据库已重置：{r.get('result') or ''}",
        "run_error": f"运行出错：{r.get('result') or ''}",
        "approval_relink": r.get("result") or "付款申请归属更正",
    }.get(a)
    return text or f"{who} {a} {target}".strip()


@app.get("/api/events", response_model=EventList, summary="获取近期智能体事件（来自审计记录）")
async def list_events(
    ticket_id: int | None = None,
    run_id: str | None = None,
    after: int = Query(-1, description="只返回序号大于该值的事件"),
    limit: int = Query(200, ge=1, le=1000),
    include_steps: bool = Query(False, description="是否包含 model_request / tool_call 底层步骤"),
) -> EventList:
    records = _audit_records()
    events = []
    for i, r in enumerate(records):
        if i <= after or str(r.get("run_id", "")).startswith("self-check"):
            continue
        if ticket_id is not None and r.get("ticket_id") != ticket_id:
            continue
        if run_id is not None and r.get("run_id") != run_id:
            continue
        if not include_steps and r.get("action") in _HIDDEN_BY_DEFAULT:
            continue
        events.append(Event(
            id=i, time=r["time"], run_id=r.get("run_id", ""), ticket_id=r.get("ticket_id"),
            agent=r.get("agent", ""), agent_title=r.get("agent_title", ""), action=r["action"],
            target=r.get("target"), message=_event_message(r), input=r.get("input"), result=r.get("result"),
            status=r.get("status", "ok"), needs_human_approval=bool(r.get("needs_human_approval")),
            depth=r.get("depth", 0),
        ))
    return EventList(events=events[-limit:], last_id=len(records) - 1)


# ---------------------------------------------------------------------- 3b. 各角色工作总结（看板用）


def _audit_records() -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    try:
        data = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _runs_from_audit(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """根据审计记录重建每次工单运行（后端重启后也能恢复）。"""
    runs: dict[str, dict[str, Any]] = {}
    for r in records:
        rid = r.get("run_id", "")
        if r.get("action") == "run_start" and r.get("ticket_id") is not None and not rid.startswith("self-check"):
            runs[rid] = {"run_id": rid, "ticket_id": r["ticket_id"], "status": "running", "started_at": r["time"],
                         "ended_at": None, "stop_reason": None}
        elif r.get("action") == "run_end" and rid in runs:
            stop = None
            try:
                stop = json.loads(r.get("result") or "{}").get("stop_reason")
            except json.JSONDecodeError:
                stop = r.get("result")
            runs[rid].update(status=r.get("status", "completed"), ended_at=r["time"], stop_reason=stop)
    return runs


def _latest_run_by_ticket(runs: dict[str, dict[str, Any]], active: dict[int, str]) -> dict[int, LastRun]:
    latest: dict[int, LastRun] = {}
    for run in runs.values():  # 审计记录按时间顺序追加，后出现的覆盖先出现的
        info = dict(run)
        if info["status"] == "running" and active.get(info["ticket_id"]) != info["run_id"]:
            info["status"] = "interrupted"  # 没有结束记录且后端也不在运行它（例如后端中途重启）
        latest[info["ticket_id"]] = LastRun(**{k: info[k] for k in ("run_id", "status", "started_at", "ended_at", "stop_reason")})
    return latest


class AgentWork(BaseModel):
    agent: str
    title: str
    participated: bool
    state: Literal["idle", "working", "done"]
    summary: str | None = Field(description="该智能体本次运行最后交出的结论（agent_finished），未完成时为空")
    last_activity: str | None
    tools_used: list[str]
    delegated_to: list[str]
    needs_human: bool


class TicketAgents(BaseModel):
    ticket_id: int
    run: LastRun | None
    agents: list[AgentWork]


@app.get("/api/tickets/{ticket_id}/agents", response_model=TicketAgents, summary="某工单最近一次运行中五个智能体的工作总结")
async def ticket_agents(ticket_id: int, run_id: str | None = None) -> TicketAgents:
    if ticket_id not in VALID_TICKETS:
        raise _err(404, "ticket_not_found", f"工单 {ticket_id} 不存在。")
    records = _audit_records()
    with state.lock:
        active = dict(state.active)
    runs = {k: v for k, v in _runs_from_audit(records).items() if v["ticket_id"] == ticket_id}
    if run_id is None and runs:
        run_id = list(runs)[-1]
    if run_id is not None and run_id not in runs:
        raise _err(404, "run_not_found", f"工单 {ticket_id} 没有运行 {run_id}。")
    run = _latest_run_by_ticket({run_id: runs[run_id]}, active).get(ticket_id) if run_id else None

    work = {name: {"started": False, "finished": False, "summary": None, "last": None, "tools": [], "to": [], "human": False}
            for name in AGENT_TITLES}
    for r in records:
        if run_id is None or r.get("run_id") != run_id or r.get("agent") not in work:
            continue
        w = work[r["agent"]]
        action = r.get("action")
        if action == "agent_start":
            w["started"] = True
        elif action == "agent_finished":
            w.update(finished=True, summary=r.get("result"), human=bool(r.get("needs_human_approval")))
        elif action == "mcp_tool_result" and r.get("target") not in w["tools"]:
            w["tools"].append(r["target"])
        elif action == "delegate" and r.get("target") not in w["to"]:
            w["to"].append(r["target"])
        if action not in _HIDDEN_BY_DEFAULT and action not in ("run_start", "run_end"):
            w["last"] = _event_message(r)

    agents = []
    for name, w in work.items():
        if not w["started"]:
            st = "idle"
        elif w["finished"]:
            st = "done"
        else:
            st = "working" if run and run.status == "running" else "done"
        agents.append(AgentWork(agent=name, title=AGENT_TITLES[name], participated=w["started"], state=st,
                                summary=w["summary"], last_activity=w["last"], tools_used=w["tools"],
                                delegated_to=w["to"], needs_human=w["human"]))
    return TicketAgents(ticket_id=ticket_id, run=run, agents=agents)


# ---------------------------------------------------------------------- 4. 人工审批

_REFUSAL_HTTP = {
    "not_found": 404,
    "already_executed": 409,
    "already_paid": 409,
    "not_pending": 409,
    "mismatch": 409,
    "insufficient_funds": 409,
    "no_account": 409,
    "invalid": 422,
    "forbidden": 500,
}


@app.get("/api/approvals", response_model=list[ApprovalRequest], summary="列出付款审批申请")
async def list_approvals(status: str | None = "pending", ticket_id: int | None = None) -> list[ApprovalRequest]:
    args: dict[str, Any] = {}
    if status:
        args["status"] = status
    if ticket_id is not None:
        args["ticket_id"] = ticket_id
    return [ApprovalRequest(**r) for r in _as_list((await _gw("list_approval_requests", args))["requests"])]


async def _after_decision(ticket_id: int | None) -> str | None:
    """审批或拒绝后：该工单不再有待审批申请时，把等待中的工单标为已解决。"""
    if ticket_id is None:
        return None
    pending = _as_list((await _gw("list_approval_requests", {"status": "pending", "ticket_id": ticket_id}))["requests"])
    tickets = {t["id"]: t for t in (await _gw("list_tickets"))["tickets"]}
    current = tickets.get(ticket_id, {}).get("status")
    if not pending and current == "waiting_approval":
        await _gw("set_ticket_status", {"ticket_id": ticket_id, "status": "resolved"})
        _audit_backend("ticket_status", ticket_id=ticket_id, target="set_ticket_status",
                       result=f"工单 {ticket_id} 的付款申请都已处理，状态更新为 resolved")
        return "resolved"
    return current


@app.post("/api/approvals/{uid}/approve", response_model=ApprovalResult, summary="人工批准付款并执行模拟扣款")
async def approve(uid: str, body: ApproveBody) -> ApprovalResult:
    with state.lock:
        if state.resetting:
            raise _err(409, "resetting", "数据库正在重置，不能审批。")
        state.approvals_in_progress += 1
    try:
        res = await _gw("human_approve_and_pay", {
            "request_uid": uid, "approved_by": body.approved_by, "expected_amount": body.amount,
            "expected_payee": body.payee, "human_token": _HUMAN_TOKEN,
        })
    finally:
        with state.lock:
            state.approvals_in_progress -= 1

    if not res.get("ok"):
        _audit_backend("human_approval", actor="human", target="human_approve_and_pay",
                       input={"uid": uid, "approved_by": body.approved_by, "amount": body.amount, "payee": body.payee},
                       result=res.get("message"), status="refused")
        raise _err(_REFUSAL_HTTP.get(res.get("code"), 400), res.get("code", "refused"), res.get("message", "审批失败"))

    req = res["request"]
    _audit_backend("human_approval", actor="human", ticket_id=req.get("ticket_id"), target="human_approve_and_pay",
                   input={"uid": uid, "approved_by": body.approved_by},
                   result=f"{body.approved_by} 批准申请 #{req['id']}：向 {req['payee']} 支付 {req['amount']}，"
                          f"payment_id={res['payment']['id']}，余额 {res['cash_account']['balance']}")
    ticket_status = await _after_decision(req.get("ticket_id"))
    return ApprovalResult(ok=True, request=req, payment=res["payment"], cash_account=res["cash_account"],
                          ticket_status=ticket_status)


@app.post("/api/approvals/{uid}/reject", response_model=ApprovalResult, summary="人工拒绝付款申请（不扣款）")
async def reject(uid: str, body: RejectBody) -> ApprovalResult:
    res = await _gw("human_reject_payment", {"request_uid": uid, "rejected_by": body.rejected_by, "human_token": _HUMAN_TOKEN})
    if not res.get("ok"):
        raise _err(_REFUSAL_HTTP.get(res.get("code"), 400), res.get("code", "refused"), res.get("message", "操作失败"))
    req = res["request"]
    _audit_backend("human_rejection", actor="human", ticket_id=req.get("ticket_id"), target="human_reject_payment",
                   result=f"{body.rejected_by} 拒绝申请 #{req['id']}（{req['payee']} {req['amount']}）"
                          + (f"，原因：{body.reason}" if body.reason else ""))
    return ApprovalResult(ok=True, request=req, ticket_status=await _after_decision(req.get("ticket_id")))


# ---------------------------------------------------------------------- 5. 现金余额


@app.get("/api/cash", response_model=Cash, summary="查询支票账户当前余额")
async def cash() -> Cash:
    data = await _gw("get_cash_position")
    checking = next((a for a in _as_list(data["cash_accounts"]) if a["name"] == "checking"), None)
    if checking is None:
        raise _err(404, "no_account", "cash_accounts 中没有 checking 账户。")
    return Cash(account="checking", balance=checking["balance"], as_of=checking["date"], today=data["today"])


# ---------------------------------------------------------------------- 6. 重置数据库


@app.post("/api/reset", response_model=ResetResult, summary="把工作副本恢复为原始数据库（需 confirm=RESET）")
async def reset(body: ResetBody) -> ResetResult:
    with state.lock:
        if state.active:
            raise _err(409, "runs_active", f"有工单正在运行（{sorted(state.active)}），不能重置。")
        if state.approvals_in_progress:
            raise _err(409, "approval_in_progress", "有付款正在处理，不能重置。")
        if state.resetting:
            raise _err(409, "resetting", "已经在重置中。")
        state.resetting = True
    try:
        res = await _gw("reset_working_database", {"human_token": _HUMAN_TOKEN})
        if not res.get("ok"):
            raise _err(500, res.get("code", "reset_failed"), res.get("message", "重置失败"))
        voided = res["voided_pending_requests"]
        _audit_backend("database_reset", actor="human", target="reset_working_database",
                       input={"label": body.label} if body.label else None,
                       result=(f"【{body.label}】" if body.label else "") + f"工作副本已恢复为原始数据；作废待审批申请 {len(voided)} 笔"
                              + (f"（{', '.join('#' + str(v['id']) for v in voided)}）" if voided else "")
                              + f"；checking 余额 {res['cash_account']['balance']}")
    finally:
        with state.lock:
            state.resetting = False
    return ResetResult(ok=True, voided_pending_requests=voided, cash_account=res["cash_account"],
                       message="已恢复原始数据。旧的付款申请全部失效，审计记录保留。")


@app.get("/api/health", summary="健康检查")
async def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL_NAME, "mcp_server": str(MCP_SERVER_SCRIPT.name)}
