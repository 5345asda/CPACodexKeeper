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

    def format_line(self, level: str, message: str, indent: int = 0) -> str:
        prefix = self.PREFIX_MAP.get(level, f"[{level}]")
        return f"{'    ' * indent}{prefix} {message}"

    def log(self, level: str, message: str, indent: int = 0) -> None:
        with self._lock:
            print(self.format_line(level, message, indent=indent))

    def token_header(self, idx: int, total: int, name: str) -> None:
        with self._lock:
            print(f"[{idx}/{total}] Token: {name}")

    def banner(self, title: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.emit_lines([
            "=" * 60,
            self.format_line("INFO", title),
            self.format_line("INFO", f"当前时间: {now}"),
            "=" * 60,
        ])

    def divider(self) -> None:
        with self._lock:
            print("=" * 60)

    def blank_line(self) -> None:
        with self._lock:
            print()

    def emit_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        with self._lock:
            for line in lines:
                print(line)


class TokenLogger:
    def __init__(self, logger: ConsoleLogger, idx: int, total: int, name: str):
        self._logger = logger
        self._buffer: list[str] = []
        self._buffer.append(f"[{idx}/{total}] Token: {name}")

    def log(self, level: str, message: str, indent: int = 0) -> None:
        self._buffer.append(self._logger.format_line(level, message, indent=indent))

    def blank_line(self) -> None:
        self._buffer.append("")

    def flush(self) -> None:
        self._logger.emit_lines(self._buffer.copy())
        self._buffer.clear()
