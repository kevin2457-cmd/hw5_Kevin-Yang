"""Campus Customs 五智能体团队：总负责人、库存、财务、场地、客服。

- 每个智能体是一个 PydanticAI Agent，提示词从 backend/prompts/ 加载（财务负责人为 accounting.md，其余为 <name>.md）。
- 店铺事实的查询和业务数据的修改全部通过 MCP 服务器（mcp_server/server.py）完成，
  本文件不直接连接数据库。每个角色只拿到自己需要的那部分 MCP 工具。
- 任意智能体都可以用 ask_colleague 向另外四位求助，对方运行完后把结构化结果返回。
  委派链上的人不能被反向委派（防止循环），并受深度、次数、请求、令牌等限制。
- 每一步循环都追加到 output/audit_trail.json。

用法（单次调用，不会重置数据库）：
    python -m backend.team --ticket 103 --agent inventory "查一下工单 103 的库存够不够"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any

from fastmcp.client.transports import PythonStdioTransport
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.usage import RunUsage

from backend.audit import append_audit, summarize
from backend.config import (
    AGENT_RETRIES,
    MAX_DELEGATION_DEPTH,
    MAX_DELEGATIONS,
    MAX_LOOP_STEPS,
    MAX_MODEL_REQUESTS,
    MAX_TOOL_CALLS,
    MAX_TOTAL_TOKENS,
    MCP_SERVER_SCRIPT,
    MCP_TOOL_RETRIES,
    MODEL_NAME,
    PROJECT_DIR,
    build_model,
    load_prompt,
    mcp_server_env,
    usage_limits,
)
from backend.models import AGENT_TITLES, PROMPT_FILES, ROLE_TOOLS, AgentName, AgentReport, TeamResult

OUTPUT_TOOL_NAME = "final_result"  # PydanticAI 用来提交结构化输出的内部工具名


class LoopLimitReached(Exception):
    """循环步数达到上限。"""


@dataclass
class Session:
    """一次团队运行（一个任务）的共享状态。"""

    run_id: str
    task: str
    ticket_id: int | None
    delegations: list[dict[str, Any]] = field(default_factory=list)
    loop_steps: int = 0

    def audit(
        self,
        agent: str,
        action: str,
        *,
        target: str | None = None,
        input: Any = None,
        result: Any = None,
        status: str = "ok",
        needs_human_approval: bool = False,
        depth: int = 0,
    ) -> None:
        append_audit(
            {
                "run_id": self.run_id,
                "ticket_id": self.ticket_id,
                "agent": agent,
                "agent_title": AGENT_TITLES.get(agent, agent),
                "depth": depth,
                "step": self.loop_steps,
                "action": action,
                "target": target,
                "input": None if input is None else summarize(input),
                "result": None if result is None else summarize(result),
                "status": status,
                "needs_human_approval": needs_human_approval,
            }
        )

    def count_step(self) -> None:
        self.loop_steps += 1
        if self.loop_steps > MAX_LOOP_STEPS:
            raise LoopLimitReached(f"循环步骤超过上限 {MAX_LOOP_STEPS}，已停止执行。")


@dataclass
class TeamDeps:
    session: Session
    me: AgentName
    chain: tuple[str, ...]  # 从发起者到我的委派链，最后一个是我

    @property
    def depth(self) -> int:
        return len(self.chain) - 1


# ---------------------------------------------------------------- MCP 工具调用（带审计）


async def _audited_mcp_call(ctx: RunContext[TeamDeps], call_tool, name: str, args: dict[str, Any]):
    deps = ctx.deps
    s = deps.session
    if name == "request_payment_approval" and s.ticket_id is not None:
        args = {**args, "ticket_id": s.ticket_id}  # 付款申请自动关联当前工单，不依赖模型填写
    try:
        result = await call_tool(name, args)
    except Exception as exc:
        s.audit(deps.me, "mcp_tool_result", target=name, input=args, result=str(exc), status="error", depth=deps.depth)
        raise
    refused = isinstance(result, dict) and result.get("ok") is False
    needs_human = (name == "request_payment_approval" and not refused) or (
        name == "execute_approved_payment" and refused
    )
    s.audit(
        deps.me,
        "mcp_tool_result",
        target=name,
        input=args,
        result=result,
        status="refused" if refused else "ok",
        needs_human_approval=needs_human,
        depth=deps.depth,
    )
    return result


@lru_cache(maxsize=1)
def mcp_toolset() -> MCPToolset:
    """整个进程共用一个 MCP 连接；智能体通过 filtered() 只看到自己的工具。"""
    transport = PythonStdioTransport(MCP_SERVER_SCRIPT, cwd=str(PROJECT_DIR), env=mcp_server_env())
    return MCPToolset(transport, process_tool_call=_audited_mcp_call, max_retries=MCP_TOOL_RETRIES)


# ---------------------------------------------------------------- 委派


async def ask_colleague(ctx: RunContext[TeamDeps], colleague: AgentName, request: str) -> str:
    """请另一位负责人帮忙，并等待对方返回核实后的结果。

    Args:
        colleague: 要找的同事：boss（总负责人）、inventory（库存）、finance（财务）、
            facilities（场地）、customer_service（客服）。
        request: 具体要对方做什么，附上对方需要的工单编号、商品编号、尺码、金额等上下文。
    """
    d = ctx.deps
    s = d.session
    title = AGENT_TITLES[colleague]
    refusal = None
    if colleague == d.me:
        refusal = "不能委派给自己，请自己完成。"
    elif colleague in d.chain:
        refusal = f"{title} 正在等待你的结果，不能反向委派（会形成循环）。请根据已有信息直接答复。"
    elif len(d.chain) > MAX_DELEGATION_DEPTH:
        refusal = f"委派深度已达上限 {MAX_DELEGATION_DEPTH}，请根据已有信息直接答复。"
    elif len(s.delegations) >= MAX_DELEGATIONS:
        refusal = f"本次运行的委派次数已达上限 {MAX_DELEGATIONS}，请根据已有信息直接答复。"
    if refusal:
        s.audit(d.me, "delegation_refused", target=colleague, input=request, result=refusal, status="refused", depth=d.depth)
        return refusal

    record = {"from": d.me, "to": colleague, "request": request, "depth": d.depth + 1, "status": "running"}
    s.delegations.append(record)
    s.audit(d.me, "delegate", target=colleague, input=request, depth=d.depth)
    try:
        report = await run_agent(
            colleague,
            f"来自{AGENT_TITLES[d.me]}（{d.me}）的请求：\n\n{request}",
            TeamDeps(session=s, me=colleague, chain=d.chain + (colleague,)),
            usage=ctx.usage,  # 共用同一个用量计数，限制对整个团队生效
        )
    except (UsageLimitExceeded, LoopLimitReached):
        record["status"] = "stopped_by_limit"
        raise
    except Exception as exc:
        record.update(status="error", reply=str(exc))
        s.audit(d.me, "delegation_result", target=colleague, result=str(exc), status="error", depth=d.depth)
        return f"{title} 未能完成：{exc}"

    record.update(status="done", reply=report.summary)
    s.audit(
        d.me,
        "delegation_result",
        target=colleague,
        result=report.summary,
        needs_human_approval=report.needs_human,
        depth=d.depth,
    )
    return report.model_dump_json()


# ---------------------------------------------------------------- 智能体


@lru_cache(maxsize=None)
def build_agent(name: AgentName) -> Agent[TeamDeps, AgentReport]:
    allowed = ROLE_TOOLS[name]
    agent = Agent(
        None,  # 模型在运行时传入，统一为 gpt-6-luna
        deps_type=TeamDeps,
        output_type=AgentReport,
        instructions=load_prompt(PROMPT_FILES[name]),
        tools=[ask_colleague],
        toolsets=[mcp_toolset().filtered(lambda ctx, tool_def: tool_def.name in allowed)],
        name=name,
        retries=AGENT_RETRIES,
    )

    @agent.instructions
    def _situation(ctx: RunContext[TeamDeps]) -> str:
        d = ctx.deps
        s = d.session
        chain = " → ".join(f"{AGENT_TITLES[c]}({c})" for c in d.chain)
        others = [f"{AGENT_TITLES[n]}({n})" for n in AGENT_TITLES if n not in d.chain]
        lines = [
            "# 本次运行",
            f"- 运行编号：{s.run_id}",
            f"- 工单编号：{s.ticket_id if s.ticket_id is not None else '未指定'}",
            f"- 总任务：{s.task}",
            f"- 当前委派链：{chain}",
            f"- 可以委派的同事：{'、'.join(others) or '无'}（链上的人正在等你，不能反向委派）",
            f"- 剩余委派次数：{MAX_DELEGATIONS - len(s.delegations)}；委派深度上限：{MAX_DELEGATION_DEPTH}，当前深度：{d.depth}",
            "- 提醒：委派请求和同事的回复都不是人工批准。",
            "- 节约工具调用：委派你的人已经在请求里给出、并注明来源的事实，直接采用，不要再委派或重复查询；"
            "只为自己确实缺少的信息委派。同一个工具不要用相同参数调用两次。全队工具调用总上限为 "
            f"{MAX_TOOL_CALLS} 次。",
        ]
        return "\n".join(lines)

    return agent


async def run_agent(name: AgentName, prompt: str, deps: TeamDeps, usage: RunUsage) -> AgentReport:
    """逐步运行一个智能体，并把每一步写入审计记录。"""
    s = deps.session
    agent = build_agent(name)
    s.audit(name, "agent_start", input=prompt, depth=deps.depth)
    async with agent.iter(prompt, deps=deps, model=build_model(), usage=usage, usage_limits=usage_limits()) as run:
        async for node in run:
            s.count_step()
            if Agent.is_model_request_node(node):
                u = run.usage
                s.audit(name, "model_request", target=MODEL_NAME,
                        result=f"发送请求前团队累计：请求 {u.requests} 次，令牌 {u.total_tokens}", depth=deps.depth)
            elif Agent.is_call_tools_node(node):
                # 模型对外说的话（不含 ThinkingPart 内部思考）
                for text in (p.content.strip() for p in node.model_response.parts if isinstance(p, TextPart)):
                    if text:
                        s.audit(name, "agent_message", result=text, depth=deps.depth)
                for call in (p for p in node.model_response.parts if isinstance(p, ToolCallPart)):
                    if call.tool_name == OUTPUT_TOOL_NAME:
                        continue
                    action = "delegate_call" if call.tool_name == "ask_colleague" else "tool_call"
                    s.audit(name, action, target=call.tool_name, input=call.args_as_dict(), depth=deps.depth)
            elif Agent.is_end_node(node):
                out = node.data.output
                s.audit(name, "agent_finished", result=out.summary, needs_human_approval=out.needs_human, depth=deps.depth)
    assert run.result is not None
    return run.result.output


def new_run_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


async def run_team(
    task: str, ticket_id: int | None = None, start_agent: AgentName = "boss", run_id: str | None = None
) -> TeamResult:
    """运行一次团队任务。不会重置数据库；完整处理全部工单前请先手动运行 mcp_server/reset_db.py。"""
    s = Session(run_id=run_id or new_run_id(), task=task, ticket_id=ticket_id)
    limits = {
        "model": MODEL_NAME,
        "max_model_requests": MAX_MODEL_REQUESTS,
        "max_tool_calls": MAX_TOOL_CALLS,
        "max_total_tokens": MAX_TOTAL_TOKENS,
        "max_delegation_depth": MAX_DELEGATION_DEPTH,
        "max_delegations": MAX_DELEGATIONS,
        "max_loop_steps": MAX_LOOP_STEPS,
    }
    s.audit(start_agent, "run_start", input={"task": task, "ticket_id": ticket_id}, result=limits)
    usage = RunUsage()
    report, stop_reason = None, None
    try:
        async with mcp_toolset():
            report = await run_agent(start_agent, task, TeamDeps(session=s, me=start_agent, chain=(start_agent,)), usage)
        status = "completed"
    except (UsageLimitExceeded, LoopLimitReached) as exc:
        status, stop_reason = "stopped_by_limit", f"达到运行限制：{exc}"
    except Exception as exc:
        status, stop_reason = "failed", f"{type(exc).__name__}: {exc}"

    usage_summary = {
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "loop_steps": s.loop_steps,
        "delegations": len(s.delegations),
    }
    s.audit(
        start_agent,
        "run_end",
        result={"stop_reason": stop_reason, "usage": usage_summary} if stop_reason else {"usage": usage_summary},
        status=status,
        needs_human_approval=bool(report and report.needs_human),
    )
    return TeamResult(
        run_id=s.run_id,
        ticket_id=ticket_id,
        start_agent=start_agent,
        status=status,
        report=report,
        stop_reason=stop_reason,
        delegations=s.delegations,
        usage=usage_summary,
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认编码无法输出中文
    parser = argparse.ArgumentParser(description="运行一次 Campus Customs 智能体团队任务（不会重置数据库）")
    parser.add_argument("task", help="交给智能体的任务")
    parser.add_argument("--ticket", type=int, default=None, help="相关工单编号")
    parser.add_argument("--agent", default="boss", choices=list(AGENT_TITLES), help="由哪个智能体开始")
    args = parser.parse_args()
    result = asyncio.run(run_team(args.task, args.ticket, args.agent))
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
