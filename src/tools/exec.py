import atexit
import contextlib
import os
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

from ..config import MAX_EXEC_TIMEOUT
from ..params import RUNTIME

# ── 安全配置 ──

_DANGEROUS_PATTERNS = [
    "rm -rf /", "rm -rf /*", "rmdir /s /q",
    "format", "mkfs", "dd if=",
    "shutdown", "reboot", "poweroff",
    "wget ", "curl ",
    "sudo ",
]

_BLOCKED_FILE_OPS = frozenset({
    "rm", "del", "rd", "rmdir",
    "move", "ren", "rename", "copy", "xcopy",
})
MAX_OUTPUT_LENGTH = 100_000
SHELL_META_CHARS = frozenset("&|;<>\n\r")

# ── 异步 Job 管理 ──

_JOB_TTL_SECONDS = 600
_JOB_MAX_COUNT = 50
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _kill_job(job: dict | None) -> None:
    """驱逐 job 前杀掉仍在跑的进程。

    否则 TTL/上限驱逐只 pop 出字典、进程还活着——既不被追踪也再没人能杀，
    异步任务反复 fire 时孤儿活进程堆积（实测 70 个把机器压垮的根因）。
    """
    if not job:
        return
    proc = job.get("proc")
    if proc is not None and proc.poll() is None:  # 仍在运行
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=2)


def _job_cleanup() -> None:
    now = time.time()
    victims: list[dict] = []
    with _jobs_lock:  # 锁内只摘除，杀进程/wait 放锁外，避免阻塞其他 job 操作
        expired = [jid for jid, j in _jobs.items() if now - j["created_at"] > _JOB_TTL_SECONDS]
        for jid in expired:
            victims.append(_jobs.pop(jid))
        while len(_jobs) > _JOB_MAX_COUNT:
            oldest = min(_jobs.keys(), key=lambda k: _jobs[k]["created_at"])
            victims.append(_jobs.pop(oldest))
    for job in victims:
        _kill_job(job)


def _kill_all_jobs() -> None:
    """进程退出兜底：杀光所有未回收的异步进程，不留孤儿。"""
    with _jobs_lock:
        jobs = list(_jobs.values())
        _jobs.clear()
    for job in jobs:
        _kill_job(job)


atexit.register(_kill_all_jobs)


def _start_job(command: str, timeout: int, workdir: str | None) -> str:
    cwd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
    if not cwd.exists() or not cwd.is_dir():
        raise ValueError(f"工作目录无效: {cwd}")
    cmd_parts = _split_command(command)
    if isinstance(cmd_parts, str):
        raise ValueError(cmd_parts)
    job_id = f"job-{uuid.uuid4().hex[:8]}"
    proc = subprocess.Popen(
        cmd_parts,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
    )
    _job_cleanup()
    with _jobs_lock:
        _jobs[job_id] = {
            "proc": proc,
            "command": command,
            "timeout": timeout,
            "workdir": str(cwd),
            "created_at": time.time(),
        }
    return job_id


def _poll_job(job_id: str) -> str:
    _job_cleanup()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return f"job {job_id} 不存在或已过期（TTL={_JOB_TTL_SECONDS}s）"

    proc: subprocess.Popen = job["proc"]
    timeout = job["timeout"]
    elapsed = time.time() - job["created_at"]

    def _expire() -> str:
        with contextlib.suppress(OSError):
            proc.kill()
        with _jobs_lock:
            _jobs.pop(job_id, None)
        return f"⏱️ job {job_id} 超时（>{timeout}s）: {job['command']}"

    if elapsed > timeout:
        return _expire()

    # long-poll：内部阻塞等到作业完成或等够预算才返回，避免 agent ~1s 一次忙等长任务
    # （每次 poll = 一整个 LLM 往返）。等待不超过作业剩余超时；锁已释放，长阻塞不占锁。
    wait = min(RUNTIME.poll_wait_seconds, max(timeout - elapsed, 0))
    try:
        proc.wait(timeout=wait)
    except subprocess.TimeoutExpired:
        elapsed = time.time() - job["created_at"]
        if elapsed > timeout:
            return _expire()
        return f"🔄 job {job_id}: 仍在运行（{elapsed:.0f}s / {timeout}s）: {job['command']}"

    stdout_bytes, stderr_bytes = proc.communicate()
    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    rc = proc.returncode

    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.rstrip())
    if stderr.strip():
        parts.append(f"[stderr]\n{stderr.rstrip()}")
    output = "\n".join(parts) if parts else "(无输出)"

    prefix = f"✅ job {job_id}: 完成（exit={rc}, {elapsed:.0f}s）: {job['command']}\n"
    if rc != 0:
        prefix = f"❌ job {job_id}: 完成（exit={rc}, {elapsed:.0f}s）: {job['command']}\n"

    with _jobs_lock:
        _jobs.pop(job_id, None)
    return prefix + _truncate_output(output)


# ── 工具定义 ──

TOOLS_EXEC = [
    {
        "_meta": {"label": "执行代码", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "run_python",
            "description": "执行 Python 代码并返回输出。子进程运行，有超时，禁止交互/窗口。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "_meta": {"label": "执行命令", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "执行 Shell 命令。构建/测试/git/文件操作等，有超时和输出限制。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell 命令，如 'pytest -q'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录，默认当前项目",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "_meta": {"label": "异步执行", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "run_async",
            "description": (
                "后台启动一个 Shell 命令（不阻塞），返回 job_id。适合长命令（全量测试等）。"
                "完成后系统会自动通知，无需 poll——派发后直接结束本轮或继续别的事。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要异步执行的 Shell 命令，如 'pytest tests/'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 120",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "工作目录，默认当前项目",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "_meta": {"label": "轮询异步任务", "parallel_safe": True, "no_dedup": True},
        "type": "function",
        "function": {
            "name": "poll",
            "description": (
                "（通常不需要——后台作业完成会自动通知你。）查询某个异步 job 状态，"
                "仍在跑返回 running，完成返回 stdout/stderr + exit_code。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "run_async 返回的 job_id",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
]


# ── 安全检测 ──


def _is_blocked(command: str) -> tuple[bool, str]:
    cmd_lower = command.lower().strip()
    for pat in _DANGEROUS_PATTERNS:
        if pat in cmd_lower:
            return True, f"命令包含禁止操作: {pat}"
    try:
        parts = shlex.split(cmd_lower)
    except ValueError:
        parts = cmd_lower.split()
    if parts and parts[0] in _BLOCKED_FILE_OPS:
        return True, (
            f"禁止直接执行 {parts[0]} 命令"
            f" - 文件增删改必须走工具 API（write_file/delete_file/edit_file_lines）。"
            f" git {parts[0]} 等子命令不受此限。"
        )
    return False, ""


def _check_git_hook_bypass(parts: list[str]) -> str:
    if not parts or Path(parts[0]).stem.lower() != "git":
        return ""
    joined = " ".join(parts).lower()
    if "core.hookspath" in joined:
        return "git 命令覆盖 core.hooksPath = 替换质量门钩子"
    if "commit" in parts and ("--no-verify" in parts or "-n" in parts):
        return "git commit --no-verify/-n 绕过质量门"
    if "push" in parts and "--no-verify" in parts:
        return "git push --no-verify 绕过钩子"
    return ""


def _check_git_destructive(parts: list[str]) -> str:
    """拦"毁工作区/丢未提交工作"的 git 命令。

    根因事故：agent 自驱自我修改时跑 `git reset --hard`（reflog 一串），把用户未提交
    的活整片冲掉（靠 pre-commit 的 stash 才捞回）。这些命令对"放手的助手"是负价值——
    要回退/丢弃由用户拍板，agent 不该一把梭。见 [[ctgents-agent-loop]] 自驱事故。
    """
    if not parts or Path(parts[0]).stem.lower() != "git":
        return ""
    p = [x.lower() for x in parts]
    if "reset" in p and "--hard" in p:
        return "git reset --hard 会丢弃工作区所有未提交改动"
    if "clean" in p and any(a.startswith("-") and "f" in a for a in p):
        return "git clean -f 会删除未跟踪文件（含未提交的新文件/数据）"
    if "checkout" in p and ("--" in p or "." in p) and "-b" not in p:
        return "git checkout -- / . 会丢弃工作区改动"
    if "restore" in p and "--staged" not in p and "-s" not in p:
        return "git restore（非 --staged）会丢弃工作区改动"
    if "stash" in p and ("clear" in p or "drop" in p):
        return "git stash clear/drop 会删除暂存的工作（可能是待恢复的活）"
    return ""


def _git_guard_block(parts: list[str]) -> str:
    """Git 命令护栏：绕质量门 / 毁工作区 → 返回拦截消息，否则空串。两个执行入口共用。"""
    bypass = _check_git_hook_bypass(parts)
    if bypass:
        return (
            f"⛔ 命令被拦截: {bypass}。\n"
            f"门超时不等于门失败——质量门要跑全量测试（~40s+）。正道：\n"
            f"1. 用 git_commit 工具提交（推荐），或 run_command 不传 timeout"
            f"（commit 自动抬到 {RUNTIME.git_commit_timeout_floor}s 地板）；\n"
            f"2. 门真失败时，修复失败的测试/lint，再提交；\n"
            f"3. 人工放行是用户的决定——向用户报告障碍，不要替用户决定绕过。"
        )
    destructive = _check_git_destructive(parts)
    if destructive:
        return (
            f"⛔ 命令被拦截: {destructive}。\n"
            f"自我修改/自驱时这会把用户未提交的活冲掉（已发生过一次，靠 stash 才捞回）。正道：\n"
            f"1. 要回到干净状态：先 git stash 保存，别用 --hard / clean -f 一把梭；\n"
            f"2. 只想撤销自己刚加的改动：用具体文件路径精确操作；\n"
            f"3. 真要强制丢弃是用户的决定——向用户报告，别替用户决定。"
        )
    return ""


def _is_test_command(parts: list[str]) -> bool:
    """命令是否在跑 pytest——用于抬高超时地板。

    快速套件 ~40s、全量 ~150s 都 > run_command 默认 30s/run_async 默认 120s，
    不抬地板则跑测试必超时被杀，逼 agent 转 async+反复 poll（烧回合/缓存）。
    覆盖 'pytest ...'、'python -m pytest'、'py -m pytest' 等。
    """
    return any("pytest" in p for p in parts)


def _truncate_output(output: str, max_len: int = MAX_OUTPUT_LENGTH) -> str:
    if len(output) <= max_len:
        return output
    return output[:max_len] + f"\n\n...（输出已截断，共 {len(output)} 字符，仅显示前 {max_len} 字符）"


def _split_command(command: str) -> list[str] | str:
    if any(ch in command for ch in SHELL_META_CHARS):
        return "命令包含 shell 元字符（& | ; < > 或换行），请改用单个可执行文件加参数。"
    try:
        parts = shlex.split(command)
    except ValueError as exc:
        return f"命令解析失败: {exc}"
    if not parts:
        return "命令为空"
    return parts


# ── 同步执行 ──


def run_python(code: str) -> str:
    env = {**os.environ, "MPLBACKEND": "Agg"}
    try:
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=MAX_EXEC_TIMEOUT,
            cwd=Path.cwd(),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"代码执行超时（{MAX_EXEC_TIMEOUT} 秒）"
    except FileNotFoundError:
        return "找不到 Python 解释器"
    except OSError as e:
        return f"执行失败: {e}"

    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    output = "\n".join(parts) if parts else "(无输出)"
    return _truncate_output(output)


def run_command(command: str, timeout: int = 30, workdir: str | None = None) -> str:
    is_blocked, reason = _is_blocked(command)
    if is_blocked:
        return f"⛔ 命令被拦截: {reason}\n命令: {command}"

    cwd = Path(workdir).expanduser().resolve() if workdir else Path.cwd()
    if not cwd.exists():
        return f"工作目录不存在: {cwd}"
    if not cwd.is_dir():
        return f"路径不是目录: {cwd}"

    cmd_parts = _split_command(command)
    if isinstance(cmd_parts, str):
        return cmd_parts

    guard_block = _git_guard_block(cmd_parts)
    if guard_block:
        return guard_block

    if cmd_parts and Path(cmd_parts[0]).stem.lower() == "git" and "commit" in cmd_parts:
        timeout = max(timeout, RUNTIME.git_commit_timeout_floor)
    if _is_test_command(cmd_parts):
        timeout = max(timeout, RUNTIME.test_timeout_floor)

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return f"命令执行超时（{timeout} 秒）: {command}"
    except OSError as e:
        return f"执行失败: {e}"

    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.rstrip())
    if result.stderr.strip():
        parts.append(f"[stderr]\n{result.stderr.rstrip()}")
    output = "\n".join(parts) if parts else "(无输出)"
    if result.returncode != 0:
        output = f"退出码: {result.returncode}\n\n" + output
    return _truncate_output(output)


# ── 异步执行 ──


def run_async(command: str, timeout: int = 120, workdir: str | None = None) -> str:
    is_blocked, reason = _is_blocked(command)
    if is_blocked:
        return f"⛔ 命令被拦截: {reason}"
    cmd_parts = _split_command(command)
    if isinstance(cmd_parts, str):
        return cmd_parts
    guard_block = _git_guard_block(cmd_parts)
    if guard_block:
        return guard_block
    if _is_test_command(cmd_parts):
        timeout = max(timeout, RUNTIME.test_timeout_floor)
    try:
        job_id = _start_job(command, timeout, workdir)
    except ValueError as e:
        return str(e)
    return (
        f"🚀 已后台启动 job {job_id}: {command}（超时 {timeout}s）。"
        f"完成后会自动通知你——不要 poll 轮询，本轮可直接结束或去做别的事。"
    )


def poll_job(job_id: str) -> str:
    return _poll_job(job_id)


def running_job_count() -> int:
    """当前在跑的后台作业数（状态栏只读用，廉价）。"""
    with _jobs_lock:
        return len(_jobs)


def drain_finished_jobs() -> list[str]:
    """收割所有已结束的后台作业，返回完成通知（每条一段）。

    非阻塞：只 communicate 已结束的进程，未结束的留着下次收。REPL 在回合间调用
    → 「派发即停 + 完成自动通知」模型，取代 agent 反复 poll 忙等（那是刷屏 + 把
    216s 的活 babysit 成 10 分钟体感的根，见 [[ctgents-test-gate-speed]]）。
    """
    notices: list[str] = []
    with _jobs_lock:
        items = list(_jobs.items())
    for job_id, job in items:
        proc = job["proc"]
        if proc.poll() is None:
            continue  # 仍在运行，下次再收
        elapsed = time.time() - job["created_at"]
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
        except Exception:
            continue
        with _jobs_lock:
            _jobs.pop(job_id, None)
        stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
        rc = proc.returncode
        parts: list[str] = []
        if stdout.strip():
            parts.append(stdout.rstrip())
        if stderr.strip():
            parts.append(f"[stderr]\n{stderr.rstrip()}")
        output = "\n".join(parts) if parts else "(无输出)"
        mark = "✅" if rc == 0 else "❌"
        notices.append(
            f"{mark} 后台 job {job_id} 完成（exit={rc}, {elapsed:.0f}s）: {job['command']}\n"
            + _truncate_output(output)
        )
    return notices


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "run_python":
        return run_python(args["code"])
    if name == "run_command":
        return run_command(
            args["command"],
            args.get("timeout", 30),
            args.get("workdir"),
        )
    if name == "run_async":
        return run_async(
            args["command"],
            args.get("timeout", 120),
            args.get("workdir"),
        )
    if name == "poll":
        return poll_job(args["job_id"])
    return None
