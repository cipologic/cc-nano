"""后台线程，用于监听 Escape 键。

直接打开 /dev/tty（绕过 sys.stdin），避免 prompt_toolkit 的终端操作产生干扰。
当检测到 ESC 时，监听器会调用 on_cancel 回调，立即中止活动的 HTTP 流。
"""

from __future__ import annotations

import os
import select
import signal
import sys
import threading
from typing import Callable

try:
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False


class EscListener:
    """上下文管理器，在守护线程中监听 ESC 键。

    激活期间，终端处于 cbreak 模式。在读取交互式输入（例如权限提示）之前调用 ``pause()``，
    之后调用 ``resume()``，以免监听线程“偷走”按键。
    """

    def __init__(self, on_cancel: Callable[[], None] | None = None):
        self.pressed = False
        self._on_cancel = on_cancel
        self._stop = threading.Event()
        self._paused = threading.Event()  # set = 暂停, clear = 运行中
        self._thread: threading.Thread | None = None
        self._tty_fd: int | None = None  # 专用的 /dev/tty 文件描述符
        self._old_settings = None

    # -- 上下文管理器 --------------------------------------------------

    def __enter__(self):
        self.pressed = False
        self._stop.clear()
        self._paused.clear()

        # 直接打开 /dev/tty —— 独立于 sys.stdin / prompt_toolkit
        try:
            self._tty_fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        except OSError:
            # 如果 /dev/tty 不可用，回退到 stdin 的文件描述符
            self._tty_fd = sys.stdin.fileno()

        # 保存终端设置，并切换到 cbreak 模式
        try:
            self._old_settings = termios.tcgetattr(self._tty_fd)
            tty.setcbreak(self._tty_fd)
        except termios.error:
            self._old_settings = None

        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        # 恢复终端
        if self._old_settings is not None and self._tty_fd is not None:
            try:
                termios.tcsetattr(self._tty_fd, termios.TCSADRAIN, self._old_settings)
            except termios.error:
                pass
            self._old_settings = None
        # 关闭我们私有的文件描述符（只有当我们自己打开 /dev/tty 时才关闭）
        if self._tty_fd is not None and self._tty_fd > 2:
            try:
                os.close(self._tty_fd)
            except OSError:
                pass
        self._tty_fd = None

    # -- 用于交互式输入的暂停/恢复 ---------------------------------------

    def pause(self):
        """暂停监听，以便权限提示等可以读取 stdin。"""
        self._paused.set()

    def resume(self):
        """交互式输入结束后恢复监听。"""
        self._paused.clear()

    # -- 主线程非阻塞检查 ESC ------------------------------------------

    def check_esc_nonblocking(self) -> bool:
        """如果后台线程已经检测到 ESC，返回 True。"""
        return self.pressed

    # -- 内部方法 --------------------------------------------------------

    def _has_data(self, timeout: float) -> bool:
        if self._tty_fd is None:
            return False
        try:
            return bool(select.select([self._tty_fd], [], [], timeout)[0])
        except (OSError, ValueError):
            return False

    def _drain(self):
        while self._has_data(0.01):
            try:
                os.read(self._tty_fd, 64)
            except OSError:
                break

    def _listen(self):
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.05)
                continue

            if not self._has_data(0.1):
                continue
            if self._paused.is_set():
                continue

            try:
                b = os.read(self._tty_fd, 1)
            except OSError:
                break

            if not b:
                break

            if b == b"\x1b":
                if self._has_data(0.05):
                    self._drain()
                    continue
                # 真正的 ESC —— 发送 SIGINT（效果等同于 Ctrl+C）
                self.pressed = True
                os.kill(os.getpid(), signal.SIGINT)
                return


# ---------------------------------------------------------------------------
# Windows 回退实现
# ---------------------------------------------------------------------------
if not _HAS_TERMIOS:
    import msvcrt

    class EscListener:  # type: ignore[no-redef]
        """使用 msvcrt 的 Windows 版本。"""

        def __init__(self, on_cancel: Callable[[], None] | None = None):
            self.pressed = False
            self._on_cancel = on_cancel
            self._stop = threading.Event()
            self._paused = threading.Event()
            self._thread: threading.Thread | None = None

        def __enter__(self):
            self.pressed = False
            self._stop.clear()
            self._paused.clear()
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._thread.start()
            return self

        def __exit__(self, *_exc):
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=0.5)

        def pause(self):
            self._paused.set()

        def resume(self):
            self._paused.clear()

        def check_esc_nonblocking(self) -> bool:
            if self.pressed:
                return True
            while msvcrt.kbhit():
                if msvcrt.getch() == b"\x1b":
                    self.pressed = True
                    if self._on_cancel:
                        self._on_cancel()
                    return True
            return False

        def _listen(self):
            while not self._stop.is_set():
                if self._paused.is_set():
                    self._stop.wait(0.05)
                    continue
                if not msvcrt.kbhit():
                    self._stop.wait(0.05)
                    continue
                if self._paused.is_set():
                    continue
                if msvcrt.getch() == b"\x1b":
                    self.pressed = True
                    if self._on_cancel:
                        self._on_cancel()
                    return
