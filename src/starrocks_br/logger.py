# Copyright 2025 deep-bi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import sys
import threading

_logger = None
_logger_lock = threading.Lock()


class _StderrHandler(logging.StreamHandler):
    """StreamHandler that resolves sys.stderr at write time.

    Plain StreamHandler() caches whatever sys.stderr was bound to
    when it was constructed. Since _logger is a module-level singleton,
    that stale reference outlives any later reassignment of sys.stderr
    (e.g. pytest's capsys or Click's CliRunner), silently swallowing output.
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value) -> None:
        pass


def setup_logging(level: int = logging.INFO) -> None:
    global _logger
    _logger = logging.getLogger("starrocks_br")
    _logger.setLevel(level)

    if _logger.handlers:
        _logger.handlers.clear()

    handler = _StderrHandler()

    if level == logging.DEBUG:
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    else:
        formatter = logging.Formatter("%(message)s")

    handler.setFormatter(formatter)
    _logger.addHandler(handler)
    _logger.propagate = False


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is None:
        with _logger_lock:
            if _logger is None:
                setup_logging()
    return _logger


def info(message: str) -> None:
    _get_logger().info(message)


def success(message: str) -> None:
    _get_logger().info(f"✓ {message}")


def warning(message: str) -> None:
    _get_logger().warning(f"⚠ {message}")


def error(message: str) -> None:
    _get_logger().error(f"Error: {message}")


def critical(message: str) -> None:
    _get_logger().critical(f"❌ CRITICAL: {message}")


def progress(message: str) -> None:
    _get_logger().info(f"⏳ {message}")


def tip(message: str) -> None:
    _get_logger().warning(f"💡 {message}")


def debug(message: str) -> None:
    _get_logger().debug(message)
