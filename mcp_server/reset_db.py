"""把工作副本 data/campus_customs_new.db 恢复为原始数据库 data/campus_customs.db 的内容。

只在“完整处理全部工单”之前由人手动运行，或由 FastAPI 的 POST /api/reset（人工明确调用）通过 MCP 触发；
服务器启动、智能体单次调用和检查脚本都不会调用它。命令行必须加 --yes 才会执行。

    python mcp_server/reset_db.py --yes
"""

import argparse
import os
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ORIGINAL = DATA_DIR / "campus_customs.db"
WORKING = Path(os.environ.get("CAMPUS_CUSTOMS_DB") or DATA_DIR / "campus_customs_new.db")


def reset_working_db(target: Path = WORKING) -> Path:
    if not ORIGINAL.is_file():
        raise FileNotFoundError(f"找不到原始数据库：{ORIGINAL}")
    if Path(target).resolve() == ORIGINAL.resolve():
        raise ValueError("目标不能是原始数据库本身。")
    shutil.copyfile(ORIGINAL, target)  # 只读原始文件，覆盖工作副本
    return Path(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="用原始数据库覆盖工作副本")
    parser.add_argument("--yes", action="store_true", help="确认覆盖工作副本")
    if not parser.parse_args().yes:
        parser.error("这会覆盖工作副本的全部修改；确认请加 --yes")
    print(f"已恢复：{reset_working_db()}")
