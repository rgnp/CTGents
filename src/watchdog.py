"""看门狗 — 独立进程监控 agent 健康状态，崩溃后自动复活。

唯一不可变组件：agent 永远不能修改此文件（tier_4, 100%覆盖率要求）。

功能：
- 监控父进程 PID 是否存活
- 检查心跳文件是否过期（>120s = 僵死）
- 崩溃 → git reset --hard HEAD → 重启 agent
- 10分钟内连续 3 次崩溃 → 停止自动重启，写入告警文件

用法（仅由 main.py 通过 subprocess 启动）:
    py -m src.watchdog <parent_pid> <project_root>
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── 路径 ──
WATCHDOG_DIR = Path.home() / ".ctgents"
HEARTBEAT_FILE = WATCHDOG_DIR / "watchdog_heartbeat"
STATE_FILE = WATCHDOG_DIR / "watchdog_state.json"
ALERT_FILE = WATCHDOG_DIR / "watchdog_alert.txt"

# ── 参数 ──
CHECK_INTERVAL = 5       # 检查间隔（秒）
HEARTBEAT_TIMEOUT = 120  # 心跳超时（秒）
CRASH_WINDOW = 600       # 崩溃计数窗口（10 分钟）
CRASH_LIMIT = 3          # 连续崩溃上限


def _check_heartbeat() -> float:
    """读取心跳文件，返回距上次心跳的秒数。文件不存在返回 -1。"""
    try:
        if not HEARTBEAT_FILE.exists():
            return -1
        last = float(HEARTBEAT_FILE.read_text().strip())
        return time.time() - last
    except (ValueError, OSError):
        return -1


def _check_crash_limit(state: dict) -> bool:
    """检查是否超过崩溃上限。返回 True 表示应该停止自动重启。"""
    crashes = state.get("crashes", [])
    now = time.time()
    # 只保留窗口内的崩溃记录
    recent = [t for t in crashes if now - t < CRASH_WINDOW]
    return len(recent) >= CRASH_LIMIT


def _resurrect(project_root: str) -> int | None:
    """复活 agent: git reset --hard HEAD 后启动新进程。返回新 PID。"""
    try:
        subprocess.run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=project_root,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.main"],
            cwd=project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        return proc.pid
    except Exception:
        return None


def _write_state(state: dict) -> None:
    """写入看门狗状态文件。"""
    WATCHDOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError:
        pass


def _write_alert(msg: str) -> None:
    """写入告警文件。"""
    WATCHDOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ALERT_FILE.write_text(msg, encoding="utf-8")
    except OSError:
        pass


def run_watchdog(parent_pid: int, project_root: str) -> None:
    """看门狗主循环。"""
    state = {
        "parent_pid": parent_pid,
        "project_root": project_root,
        "crashes": [],
        "resurrections": 0,
        "started": time.time(),
    }
    _write_state(state)

    while True:
        time.sleep(CHECK_INTERVAL)

        # 1. 检查父进程是否存活
        try:
            # Windows: 用 exit code 检测 PID 是否有效
            if sys.platform == "win32":
                handle = __import__("ctypes").windll.kernel32.OpenProcess(0x0400, False, parent_pid)  # PROCESS_QUERY_INFORMATION
                if handle:
                    __import__("ctypes").windll.kernel32.CloseHandle(handle)
                    alive = True
                else:
                    alive = False
            else:
                os.kill(parent_pid, 0)
                alive = True
        except (OSError, PermissionError):
            alive = False
        except Exception:
            alive = True  # 不确定，假定存活

        # 2. 检查心跳
        heartbeat_age = _check_heartbeat()
        heartbeat_stale = heartbeat_age < 0 or heartbeat_age > HEARTBEAT_TIMEOUT

        if alive and not heartbeat_stale:
            # 一切正常，更新状态
            state["last_check"] = time.time()
            _write_state(state)
            continue

        # 3. 崩溃判定
        crash_reason = []
        if not alive:
            crash_reason.append(f"进程 {parent_pid} 已退出")
        if heartbeat_stale:
            crash_reason.append(f"心跳过期 ({heartbeat_age:.0f}s)")

        state.setdefault("crashes", []).append(time.time())
        state["last_crash_reason"] = "; ".join(crash_reason)

        # 4. 检查崩溃上限
        if _check_crash_limit(state):
            alert = (
                f"⚠️ Agent 在 {CRASH_WINDOW}s 内连续崩溃 {CRASH_LIMIT} 次，停止自动重启。\n"
                f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"项目: {project_root}\n"
                f"请手动检查问题后重启。"
            )
            _write_alert(alert)
            _write_state(state)
            sys.exit(1)

        # 5. 复活
        print(f"[watchdog] 检测到崩溃: {crash_reason}，尝试复活...", file=sys.stderr)
        new_pid = _resurrect(project_root)
        if new_pid:
            parent_pid = new_pid
            state["parent_pid"] = new_pid
            state["resurrections"] += 1
            state["last_resurrection"] = time.time()
            print(f"[watchdog] 已复活，新 PID: {new_pid}", file=sys.stderr)
        else:
            print("[watchdog] 复活失败", file=sys.stderr)

        _write_state(state)


def get_status() -> dict | None:
    """读取看门狗状态（供 /watchdog 命令使用）。"""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("用法: py -m src.watchdog <parent_pid> <project_root>", file=sys.stderr)
        sys.exit(2)
    pid = int(sys.argv[1])
    root = sys.argv[2]
    run_watchdog(pid, root)
