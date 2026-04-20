import atexit
import logging
import sys
import threading
import zipfile
from datetime import datetime
from pathlib import Path


class ConsoleLogger:
    PREFIX_MAP = {
        "INFO": "INFO",
        "OK": "OK",
        "WARN": "WARN",
        "ERROR": "ERROR",
        "DRY": "DRY-RUN",
        "DELETE": "DELETE",
        "ENABLE": "ENABLED",
        "DISABLE": "DISABLED",
        "REFRESH": "REFRESH",
        "SKIP": "SKIP",
    }

    def __init__(self, log_dir: Path | None = None, archive_max_size_bytes: int = 500 * 1024 * 1024) -> None:
        self._lock = threading.Lock()
        self._log_dir = Path(log_dir) if log_dir else Path(__file__).resolve().parents[1] / "logs"
        self._archive_dir = self._log_dir / "archive"
        self._archive_max_size_bytes = archive_max_size_bytes
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._archive_existing_logs()
        self._prune_archives()
        self._log_file_path = self._log_dir / f"{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.txt"
        self._stream_handler = logging.StreamHandler(sys.stdout)
        self._file_handler = logging.FileHandler(self._log_file_path, encoding="utf-8")
        atexit.register(self.close)

    def _archive_existing_logs(self) -> None:
        for log_file in sorted(self._log_dir.glob("*.txt")):
            archive_name = self._archive_dir / f"{log_file.stem}.zip"
            with zipfile.ZipFile(archive_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(log_file, arcname=log_file.name)
            log_file.unlink()

    def _prune_archives(self) -> None:
        archives = sorted(self._archive_dir.glob("*.zip"), key=lambda path: path.name)
        total_size = sum(path.stat().st_size for path in archives)
        while archives and total_size > self._archive_max_size_bytes:
            oldest = archives.pop(0)
            total_size -= oldest.stat().st_size
            oldest.unlink()

    def _timestamp(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _write_with_handler(self, handler: logging.StreamHandler, line: str) -> None:
        handler.acquire()
        try:
            handler.stream.write(f"{line}{handler.terminator}")
            handler.flush()
        finally:
            handler.release()

    def _write_line(self, line: str) -> None:
        self._write_with_handler(self._stream_handler, line)
        self._write_with_handler(self._file_handler, line)

    def close(self) -> None:
        with self._lock:
            for handler in (self._stream_handler, self._file_handler):
                try:
                    handler.flush()
                except Exception:
                    pass
                try:
                    handler.close()
                except Exception:
                    pass

    def format_line(self, level: str, message: str, indent: int = 0) -> str:
        return self.format_log_record(level, message, indent=indent)

    def format_log_record(self, level: str, message: str, indent: int = 0) -> str:
        indent_prefix = "    " * indent
        level_tag = self.PREFIX_MAP.get(level, level)
        return f"{indent_prefix}[{self._timestamp()}][{level_tag}]: {message}"

    def log(self, level: str, message: str, indent: int = 0) -> None:
        with self._lock:
            self._write_line(self.format_log_record(level, message, indent=indent))

    def token_header(self, idx: int, total: int, name: str) -> None:
        with self._lock:
            self._write_line(self.format_log_record("INFO", f"[{idx}/{total}] Token: {name}"))

    def banner(self, title: str) -> None:
        self.emit_lines([
            self.format_log_record("INFO", "=" * 60),
            self.format_log_record("INFO", title),
            self.format_log_record("INFO", f"当前时间: {self._timestamp()}"),
            self.format_log_record("INFO", "=" * 60),
        ])

    def divider(self) -> None:
        with self._lock:
            self._write_line(self.format_log_record("INFO", "=" * 60))

    def blank_line(self) -> None:
        with self._lock:
            self._write_line("")

    def emit_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        with self._lock:
            for line in lines:
                self._write_line(line)


class TokenLogger:
    def __init__(self, logger: ConsoleLogger, idx: int, total: int, name: str):
        self._logger = logger
        self._buffer: list[str] = []
        self._buffer.append(self._logger.format_log_record("INFO", f"[{idx}/{total}] Token: {name}"))

    def log(self, level: str, message: str, indent: int = 0) -> None:
        self._buffer.append(self._logger.format_log_record(level, message, indent=indent))

    def blank_line(self) -> None:
        self._buffer.append("")

    def flush(self) -> None:
        self._logger.emit_lines(self._buffer.copy())
        self._buffer.clear()
