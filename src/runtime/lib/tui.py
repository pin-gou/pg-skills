"""tui.py — 零依赖终端控制层"""

import fcntl
import os
import select
import struct
import sys
import termios


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m"


# ----- 渲染辅助 -----


def render_tab_bar(
    tab_names: list[str], current: int, keys: list[str] | None = None
) -> str:
    parts = []
    for i, name in enumerate(tab_names):
        label = f" {name} "
        if keys and i < len(keys):
            label = f" {keys[i]}){name} "
        if i == current:
            parts.append(_c("97;44", label))
        else:
            parts.append(_c("30;100", label))
    sep = _c("2;37", "│")
    return f" {sep.join(parts)} "


def render_menu(title: str, items: list, current: int, back_option: bool, term_width: int = 80) -> list[str]:
    sep_width = max(20, min(60, term_width - 4))
    lines = [f"\n{_c('90;100', '─' * sep_width)}", f"  {title}", ""]
    sel_idx = 0
    for item in items:
        if isinstance(item, str):
            lines.append(f"  {item}")
            continue
        label, desc = item
        desc_str = f" — {desc}" if desc else ""
        marker = _c("1;34", ">") if sel_idx == current else " "

        plain = f" {marker} {sel_idx + 1:2d}) {label}{desc_str}"
        max_visible = term_width - 3
        if len(plain) > max_visible:
            avail = max_visible - 7 - len(label)
            desc_str = f" — {desc[:avail - 6]}..." if avail >= 6 else ""

        label_code = "1;36;44" if sel_idx == current else "1;36"
        if desc_str:
            lines.append(
                f" {marker} {sel_idx + 1:2d}) {_c(label_code, label)}{_c('2;37', desc_str)}"
            )
        else:
            lines.append(
                f" {marker} {sel_idx + 1:2d}) {_c(label_code, label)}"
            )
        sel_idx += 1
    if back_option:
        lines.append(f"  --------------------")
        lines.append(f" {' '}  b) 上级")
    lines.append(f"  --------------------")
    lines.append(f" {' '}  c) 切换环境  q) 退出")
    return lines


# ----- 终端控制 -----


class Term:
    def __init__(self):
        self._fd = sys.stdin.fileno()
        self._old: list | None = None

    def __enter__(self):
        if sys.stdin.isatty():
            self._old = termios.tcgetattr(self._fd)
            new = termios.tcgetattr(self._fd)
            new[0] = new[0] & ~(
                termios.BRKINT | termios.ICRNL | termios.INPCK | termios.ISTRIP | termios.IXON
            )
            new[2] = new[2] & ~(termios.CSIZE | termios.PARENB)
            new[2] = new[2] | termios.CS8
            new[3] = new[3] & ~(
                termios.ECHO | termios.ECHONL | termios.ICANON | termios.IEXTEN
            )
            termios.tcsetattr(self._fd, termios.TCSADRAIN, new)
        return self

    def __exit__(self, *args):
        if self._old is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)

    @property
    def width(self) -> int:
        try:
            return struct.unpack("hh", fcntl.ioctl(sys.stdout, termios.TIOCGWINSZ, b"\x00" * 4))[1]
        except Exception:
            return 80

    # ----- 光标控制 -----

    def save(self):
        sys.stdout.write("\033[s")

    def restore(self):
        sys.stdout.write("\033[u")

    def up(self, n: int):
        sys.stdout.write(f"\033[{n}A")

    def clear_down(self):
        sys.stdout.write("\033[J")

    def clear_line(self):
        sys.stdout.write("\033[K")

    def up_and_clear(self, n: int):
        sys.stdout.write(f"\033[{n}A\033[J")

    # ----- 输出 -----

    def write(self, s: str):
        sys.stdout.write(s)

    def writeln(self, s: str = ""):
        sys.stdout.write(s + "\n")

    def flush(self):
        sys.stdout.flush()

    # ----- 样式 -----

    def style(self, text: str, *codes: str) -> str:
        if not codes:
            return text
        return f"\033[{';'.join(codes)}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.style(text, "1")

    def dim(self, text: str) -> str:
        return self.style(text, "2")

    def yellow(self, text: str) -> str:
        return self.style(text, "33")

    def cyan(self, text: str) -> str:
        return self.style(text, "36")

    def green(self, text: str) -> str:
        return self.style(text, "32")

    def red(self, text: str) -> str:
        return self.style(text, "31")

    def white_bg(self, text: str) -> str:
        return self.style(text, "7", "37")

    # ----- 键盘 -----

    def getch(self, timeout: float | None = None) -> bytes:
        if timeout is not None:
            r, _, _ = select.select([self._fd], [], [], timeout)
            if not r:
                return b""
        return os.read(self._fd, 1)

    def get_escape(self) -> bytes:
        seq = b"\x1b"
        if select.select([self._fd], [], [], 0.15)[0]:
            seq += os.read(self._fd, 1)
            if select.select([self._fd], [], [], 0.05)[0]:
                seq += os.read(self._fd, 1)
        return seq
