"""模型、路径与运行限制配置。所有智能体共用这里的设置。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic_ai import UsageLimits
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
PROMPTS_DIR = BACKEND_DIR / "prompts"
MCP_SERVER_SCRIPT = PROJECT_DIR / "mcp_server" / "server.py"
# 隔离测试时可用环境变量 CAMPUS_CUSTOMS_AUDIT 指向另一份审计文件
AUDIT_PATH = Path(os.environ.get("CAMPUS_CUSTOMS_AUDIT") or PROJECT_DIR / "output" / "audit_trail.json")

# .env 可以放在 hw5/ 或上一级目录
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(PROJECT_DIR.parent / ".env")

# 作业要求：所有智能体只用 gpt-6-luna，不设备用模型，也不允许用环境变量改成别的模型
MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"

# ---- 运行限制（一次团队运行内所有智能体共享） ----
MAX_MODEL_REQUESTS = 30  # 模型请求总次数
MAX_TOOL_CALLS = 60  # 工具调用总次数（MCP 工具 + 委派）；第 9 题工单 103 在 40 次时触顶，按真实需要上调
MAX_TOTAL_TOKENS = 200_000  # 输入 + 输出令牌总量
MAX_INPUT_TOKENS_PER_REQUEST = 60_000  # 单次请求的输入令牌上限
MAX_DELEGATION_DEPTH = 3  # 委派链最长 3 层，例如 总负责人 → 财务 → 库存
MAX_DELEGATIONS = 8  # 一次运行内委派总次数
MAX_LOOP_STEPS = 80  # 所有智能体循环步骤（模型请求 / 工具调用 / 结束）总数
AGENT_RETRIES = 1  # 工具参数或输出格式出错时最多重试 1 次
MCP_TOOL_RETRIES = 1


def usage_limits() -> UsageLimits:
    return UsageLimits(
        request_limit=MAX_MODEL_REQUESTS,
        tool_calls_limit=MAX_TOOL_CALLS,
        total_tokens_limit=MAX_TOTAL_TOKENS,
        per_request_input_tokens_limit=MAX_INPUT_TOKENS_PER_REQUEST,
    )


def require_api_key() -> str:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 PORTKEY_API_KEY，请在 hw5/.env 或上一级目录的 .env 中设置。")
    return key


@lru_cache(maxsize=1)
def build_model() -> OpenAIResponsesModel:
    key = require_api_key()
    client = AsyncOpenAI(
        base_url=PORTKEY_BASE_URL,
        api_key=key,
        default_headers={"x-portkey-api-key": key, "x-portkey-provider": "openai"},
        max_retries=1,
    )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


# 启动 MCP 服务器子进程时不传入模型密钥
_SECRET_ENV = ("PORTKEY_API_KEY",)
HUMAN_TOKEN_ENV = "CC_HUMAN_ACTION_TOKEN"


def mcp_server_env(human_token: str | None = None) -> dict[str, str]:
    """MCP 服务器子进程的环境变量。只有 FastAPI 后端的人工操作连接会带上 human_token；
    智能体使用的连接不带，因此智能体无法批准付款或重置数据库。"""
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV and k != HUMAN_TOKEN_ENV}
    if human_token:
        env[HUMAN_TOKEN_ENV] = human_token
    return env


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"找不到提示词文件：{path}")
    return path.read_text(encoding="utf-8")
