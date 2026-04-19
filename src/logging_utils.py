import threading
from datetime import datetime


class ConsoleLogger:
    PREFIX_MAP = {
        "INFO": "[*]",
        "OK": "[OK]",
        "WARN": "[!]",
        "ERROR": "[ERROR]",
        "DRY": "[DRY-RUN]",
        "DELETE": "[DELETE]",
        "ENABLE": "[ENABLED]",
        "DISABLE": "[DISABLED]",
        "REFRESH": "[REFRESH]",
        "SKIP": "[SKIP]",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def log(self, level: str, message: str, indent: int = 0) -> None:
        prefix = self.PREFIX_MAP.get(level, f"[{level}]")
        with self._lock:
            print(f"{'    ' * indent}{prefix} {message}")

    def token_header(self, idx: int, total: int, name: str) -> None:
        with self._lock:
            print(f"[{idx}/{total}] {name}")

    def banner(self, title: str) -> None:
        self.divider()
        self.log("INFO", title)
        self.log("INFO", f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    def divider(self) -> None:
        with self._lock:
            print("=" * 60)

    def blank_line(self) -> None:
        with self._lock:
            print()

    def emit_lines(self, lines: list[str]) -> None:
        """一次性输出多行日志，保证原子性（线程安全）。"""
        if not lines:
            return
        with self._lock:
            for line in lines:
                print(line)


class TokenLogger:
    """单个 Token 处理过程的日志收集器，收集完成后一次性输出。"""

    def __init__(self, logger: ConsoleLogger, idx: int, total: int, name: str):
        self._logger = logger
        self._buffer: list[str] = []
        self._buffer.append(f"[{idx}/{total}] {name}")

    def log(self, level: str, message: str, indent: int = 0) -> None:
        prefix = ConsoleLogger.PREFIX_MAP.get(level, f"[{level}]")
        self._buffer.append(f"{'    ' * indent}{prefix} {message}")

    def blank_line(self) -> None:
        self._buffer.append("")

    def flush(self) -> None:
        """一次性输出收集的所有日志。"""
        self._logger.emit_lines(self._buffer.copy())
        self._buffer.clear()
