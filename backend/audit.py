"""审计记录：每一步都追加到 output/audit_trail.json，保留历史记录。

写入方式：进程内线程锁 + 跨进程锁文件，读出整个 JSON 数组、追加、写到临时文件后原子替换，
因此多个智能体（或多个进程）同时写入时不会丢记录，也不会写出半截 JSON。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import AUDIT_PATH

_thread_lock = threading.Lock()
_MAX_TEXT = 400  # 输入/结果摘要的最大字符数
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|pk-[A-Za-z0-9_\-]{8,}|PORTKEY_API_KEY\S*)")


def summarize(value: Any, limit: int = _MAX_TEXT) -> str:
    """把参数或结果压缩成一段短文本，并去掉任何像密钥的字符串。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = _SECRET.sub("[已隐藏]", text)
    return text if len(text) <= limit else text[:limit] + "…"


class _FileLock:
    def __init__(self, path: Path, timeout: float = 15.0):
        self.path, self.timeout = path, timeout

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                # 超过 60 秒的锁文件视为上次异常退出留下的，清除后重试
                try:
                    if time.time() - self.path.stat().st_mtime > 60:
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"等待审计文件锁超时：{self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def _read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 文件被外部破坏时不覆盖它，先另存一份再从空数组继续
        backup = path.with_name(f"audit_trail.corrupt-{datetime.now():%Y%m%d%H%M%S}.json")
        path.replace(backup)
        return []
    return data if isinstance(data, list) else [data]


def append_audit(entry: dict[str, Any], path: Path = AUDIT_PATH) -> None:
    record = {"time": datetime.now().astimezone().isoformat(timespec="milliseconds"), **entry}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock, _FileLock(path.with_suffix(".lock")):
        records = _read_existing(path)
        records.append(record)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
