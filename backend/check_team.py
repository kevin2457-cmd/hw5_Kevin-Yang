"""第 5 题的必要检查。不会重置数据库、不会付款、也不会完整处理全部工单。

    python -m backend.check_team          # 离线检查：提示词、角色、MCP 工具、审计追加、付款拦截
    python -m backend.check_team --live   # 额外跑一次只读的真实委派（会调用 gpt-6-luna）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import json
import re
import threading

from fastmcp import Client

from backend.audit import append_audit
from backend.config import AUDIT_PATH, BACKEND_DIR, MCP_SERVER_SCRIPT, MODEL_NAME
from backend.models import AGENT_TITLES, PROMPT_FILES, ROLE_TOOLS
from backend.team import build_agent, run_team


def _ok(label: str, passed: bool, detail: str = "") -> bool:
    print(f"[{'通过' if passed else '失败'}] {label}" + (f"：{detail}" if detail else ""))
    return passed


def _audit_records() -> list[dict]:
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8")) if AUDIT_PATH.exists() else []


async def offline_checks() -> bool:
    results = []

    # 1. 五个角色和提示词都能加载
    for name in AGENT_TITLES:
        agent = build_agent(name)
        prompt_len = len((BACKEND_DIR / "prompts" / PROMPT_FILES[name]).read_text(encoding="utf-8"))
        results.append(_ok(f"加载 {AGENT_TITLES[name]}（{agent.name}）", agent.name == name, f"提示词 {prompt_len} 字"))

    # 2. MCP 服务器上有每个角色需要的工具
    async with Client(MCP_SERVER_SCRIPT) as client:
        server_tools = {t.name for t in await client.list_tools()}
        print(f"MCP 服务器工具（{len(server_tools)} 个）：{', '.join(sorted(server_tools))}")
        needed = set().union(*ROLE_TOOLS.values())
        results.append(_ok("所有角色的工具都存在于 MCP 服务器", needed <= server_tools, str(sorted(needed - server_tools) or "")))

        # 3. 付款拦截：没有真人批准的申请无法执行（用不存在的申请编号，不改任何数据）
        res = (await client.call_tool("execute_approved_payment", {"request_id": 999999})).data
        results.append(_ok("execute_approved_payment 拒绝未批准的付款", res.get("executed") is False, res.get("message", "")))
        cash = (await client.call_tool("get_cash_position", {})).data
        results.append(_ok("检查后现金余额未变化", cash["cash_accounts"][0]["balance"] == 3400, str(cash["cash_accounts"])))

    # 4. 智能体代码不直接连接数据库
    direct = [p.name for p in BACKEND_DIR.glob("*.py") if re.search(r"^\s*import sqlite3|sqlite3\.connect", p.read_text(encoding="utf-8"), re.M)]
    results.append(_ok("backend/ 中没有直接连接数据库的代码", not direct, str(direct or "")))

    # 5. 审计记录：多线程并发追加不丢记录，JSON 仍然有效
    before = len(_audit_records())
    threads = [
        threading.Thread(target=append_audit, args=({"run_id": "self-check", "agent": "check", "action": "audit_append_test", "status": "ok", "n": i},))
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    after = len(_audit_records())
    results.append(_ok("审计记录并发追加 10 条", after == before + 10, f"{before} → {after} 条"))
    return all(results)


async def live_check() -> bool:
    """只读的真实委派：库存负责人查工单 103，并向财务负责人询问成本和标价下的毛利。"""
    task = (
        "这是一次只读检查，不要创建付款申请、不要保存草稿。"
        "请查询工单 103 需要的 CC-HOOD-NAVY M 码库存和缺口，"
        "然后委派财务负责人（finance）用 quote_bulk_price 计算 20 件按标价 58 出售时的毛利率，并把财务的结果汇总给我。"
    )
    before = len(_audit_records())
    result = await run_team(task, ticket_id=103, start_agent="inventory")
    new = _audit_records()[before:]
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    mcp_calls = [r["target"] for r in new if r["action"] == "mcp_tool_result"]
    checks = [
        _ok("真实运行完成", result.status == "completed", result.stop_reason or ""),
        _ok(f"使用模型 {MODEL_NAME}", any(r["action"] == "model_request" and r["target"] == MODEL_NAME for r in new)),
        _ok("委派给 finance 并拿回结果", any(r["action"] == "delegation_result" and r["target"] == "finance" and r["status"] == "ok" for r in new)),
        _ok("业务数据经由 MCP 工具获取", bool(mcp_calls), ", ".join(mcp_calls)),
        _ok("本次运行写入审计记录", len(new) > 0, f"新增 {len(new)} 条"),
    ]
    return all(checks)


async def main(live: bool) -> None:
    passed = await offline_checks()
    if live:
        passed = await live_check() and passed
    print("\n全部检查通过" if passed else "\n有检查未通过")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认编码无法输出中文
    parser.add_argument("--live", action="store_true", help="额外运行一次调用模型的只读委派检查")
    asyncio.run(main(parser.parse_args().live))
