"""智能体团队使用的数据类型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AgentName = Literal["boss", "inventory", "finance", "facilities", "customer_service"]

AGENT_TITLES: dict[str, str] = {
    "boss": "总负责人",
    "inventory": "库存负责人",
    "finance": "财务负责人",
    "facilities": "场地负责人",
    "customer_service": "客服负责人",
}

# 每个角色可以使用的 MCP 工具（全部工具都在 mcp_server/server.py 中实现）
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "boss": frozenset({
        "list_open_tickets", "get_ticket_details", "get_cash_position", "list_approval_requests",
    }),
    "inventory": frozenset({
        "get_ticket_details", "check_stock_and_price", "list_sizes_for_sku", "get_vendor_status",
    }),
    "finance": frozenset({
        "get_ticket_details", "get_cash_position", "check_stock_and_price", "get_vendor_status",
        "quote_bulk_price", "request_payment_approval", "list_approval_requests", "execute_approved_payment",
    }),
    "facilities": frozenset({
        "get_ticket_details", "get_lease_status", "get_cash_position", "list_approval_requests",
    }),
    "customer_service": frozenset({
        "get_ticket_details", "check_stock_and_price", "list_sizes_for_sku", "save_customer_draft",
    }),
}

# 只供 FastAPI 后端使用、不分配给任何智能体的 MCP 工具
BACKEND_ONLY_TOOLS = frozenset({
    "list_tickets", "set_ticket_status", "relink_approval_requests", "human_approve_and_pay", "human_reject_payment", "reset_working_database",
})

# 提示词文件名；财务负责人的内部标识是 finance，提示词文件按作业要求命名为 accounting.md
PROMPT_FILES: dict[str, str] = {name: f"{name}.md" for name in AGENT_TITLES} | {"finance": "accounting.md"}


class AgentReport(BaseModel):
    """每个智能体完成任务后返回的结构化结果。"""

    summary: str = Field(description="用中文写的结论或答复，直接回应收到的任务")
    facts: list[str] = Field(default_factory=list, description="通过工具核实的事实，每条注明来源工具，例如“inventory：CC-HOOD-NAVY M 库存 8（check_stock_and_price）”")
    actions_taken: list[str] = Field(default_factory=list, description="本次实际执行的写入动作，例如创建了付款申请 #1、保存了草稿 #2；没有就留空")
    needs_human: bool = Field(default=False, description="是否需要真人处理或批准")
    human_action_needed: str | None = Field(default=None, description="需要真人做什么，写清楚金额、对象和原因")
    approval_request_ids: list[int] = Field(default_factory=list, description="本次创建或涉及的付款申请编号")
    missing_info: list[str] = Field(default_factory=list, description="数据库里查不到、无法确认的信息")


class TeamResult(BaseModel):
    run_id: str
    ticket_id: int | None
    start_agent: AgentName
    status: Literal["completed", "stopped_by_limit", "failed"]
    report: AgentReport | None = None
    stop_reason: str | None = None
    delegations: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
