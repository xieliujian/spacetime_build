"""LocalProcessRunner 使用的真实子进程 fixture。

脚本只根据显式模式产生确定输出、退出或阻塞；阻塞使用 ``Event.wait``，不使用任
意 sleep。它不导入被测生产包，避免测试进程与被测进程共享实现状态。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def _ignore_posix_term() -> None:
    """让子孙在 POSIX 上忽略 TERM，以强制 runner 验证并升级完整组 KILL。"""
    if os.name != "nt":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)


def main() -> int:
    """执行命令行指定的单一 fixture 模式并返回退出码。"""
    mode = sys.argv[1]
    if mode == "inspect":
        print(
            json.dumps(
                {"argv": sys.argv[2:], "cwd": os.getcwd(), "env": os.environ.get("ONLY_KEY")}
            )
        )
        print("password=stderr-secret", file=sys.stderr)
        return 0
    if mode == "large":
        sys.stdout.write("O" * 200_000)
        sys.stderr.write("E" * 200_000)
        return 0
    if mode == "split_secret":
        # 第一段恰好填满 reader 的 64 KiB 读取块，使秘密值稳定跨越两次 feed。
        secret_prefix = "password=LEAKED_"
        sys.stdout.write("X" * (64 * 1024 - len(secret_prefix)) + secret_prefix + "SECRET")
        return 0
    if mode == "unicode_large":
        # 绕过 Windows 子进程的控制台编码选择，固定产生真正的 UTF-8 中文字节。
        sys.stdout.buffer.write(("界" * 30_000).encode("utf-8"))
        return 0
    if mode == "invalid_utf8":
        sys.stdout.buffer.write(b"before-\xff-after")
        return 0
    if mode == "exit":
        return int(sys.argv[2])
    if mode == "wait":
        threading.Event().wait()
        return 0
    if mode == "tree":
        pid_path = Path(sys.argv[2])
        nonce = sys.argv[3]
        subprocess.Popen(
            [sys.executable, __file__, "tree_child", str(pid_path), nonce],
            shell=False,
        )
        threading.Event().wait()
        return 0
    if mode == "tree_child":
        _ignore_posix_term()
        pid_path = Path(sys.argv[2])
        nonce = sys.argv[3]
        grandchild = subprocess.Popen([sys.executable, __file__, "tree_leaf"], shell=False)
        temporary_path = pid_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                {
                    "nonce": nonce,
                    "root_pid": os.getppid(),
                    "child_pid": os.getpid(),
                    "grandchild_pid": grandchild.pid,
                }
            ),
            encoding="utf-8",
        )
        os.replace(temporary_path, pid_path)
        threading.Event().wait()
        return 0
    if mode == "tree_leaf":
        _ignore_posix_term()
        threading.Event().wait()
        return 0
    raise ValueError(f"未知 fixture 模式: {mode}")


if __name__ == "__main__":
    raise SystemExit(main())
