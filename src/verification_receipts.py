"""验证回执：把正常工具执行的确定性验证结果绑定到当时工作区状态。"""

from __future__ import annotations

import hashlib
import json
import platform
import shlex
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .paths import CORE_ROOT, TASKS_DIR

PROJECT_ROOT = CORE_ROOT
RECEIPTS_FILE = TASKS_DIR / "verification-receipts.jsonl"
MAX_RECEIPTS = 200
RECEIPT_MAX_AGE = timedelta(hours=24)
_SHELL_META_CHARS = frozenset("&|;<>\r\n")
_lock = threading.Lock()


@dataclass(frozen=True)
class VerificationReceipt:
    """一次确定性验证及其环境、代码状态和结果。"""

    command: str
    workdir: str
    workspace_fingerprint: str
    runtime: str
    passed: bool
    exit_code: int
    timestamp: str
    output_tail: str


def _command_parts(command: str) -> list[str] | None:
    if not command or any(char in command for char in _SHELL_META_CHARS):
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    return parts or None


def is_verification_command(command: str) -> bool:
    """只承认不会主动修改项目的测试、lint 和 diff 检查命令。"""
    parts = _command_parts(command)
    if not parts:
        return False
    executable = Path(parts[0]).stem.lower()
    args = [part.lower() for part in parts[1:]]
    if executable in {"python", "python3", "py"}:
        return (
            len(args) >= 2
            and args[0] == "-m"
            and (
                args[1] == "pytest"
                or (args[1] == "ruff" and len(args) >= 3 and args[2] == "check")
            )
        )
    if executable == "pytest":
        return True
    if executable == "ruff":
        return bool(args) and args[0] == "check"
    return executable == "git" and len(args) >= 2 and args[:2] == ["diff", "--check"]


def _canonical_command(command: str) -> str:
    parts = _command_parts(command)
    return json.dumps(parts or [], ensure_ascii=False, separators=(",", ":"))


def _runtime_signature() -> str:
    return f"{platform.system()}|{platform.machine()}|{sys.implementation.name}|{platform.python_version()}"


def _git_bytes(args: list[str], root: Path) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def workspace_fingerprint(root: Path | str = PROJECT_ROOT) -> str:
    """哈希 HEAD、全部 tracked diff 和普通未跟踪文件内容。"""
    project = Path(root).resolve()
    digest = hashlib.sha256()
    head = _git_bytes(["rev-parse", "HEAD"], project)
    diff = _git_bytes(["diff", "--no-ext-diff", "--binary", "HEAD"], project)
    untracked = _git_bytes(
        ["-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard", "-z"],
        project,
    )
    if head is not None and diff is not None and untracked is not None:
        digest.update(head)
        digest.update(diff)
        for raw_path in sorted(path for path in untracked.split(b"\0") if path):
            relative = raw_path.decode("utf-8", errors="surrogateescape")
            candidate = (project / relative).resolve()
            try:
                candidate.relative_to(project)
            except ValueError:
                continue
            if not candidate.is_file():
                continue
            digest.update(raw_path)
            try:
                digest.update(candidate.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
        return digest.hexdigest()

    # 非 Git 或 Git 故障时的保守 fallback，只覆盖项目代码/测试/配置。
    for base_name in ("src", "tests", "scripts"):
        base = project / base_name
        if not base.is_dir():
            continue
        for candidate in sorted(path for path in base.rglob("*") if path.is_file()):
            digest.update(str(candidate.relative_to(project)).encode("utf-8", errors="replace"))
            try:
                digest.update(candidate.read_bytes())
            except OSError:
                digest.update(b"<unreadable>")
    for name in ("pyproject.toml", "requirements.txt", "Makefile"):
        candidate = project / name
        if candidate.is_file():
            digest.update(name.encode())
            digest.update(candidate.read_bytes())
    return digest.hexdigest()


def _read_receipts() -> list[VerificationReceipt]:
    if not RECEIPTS_FILE.exists():
        return []
    receipts: list[VerificationReceipt] = []
    try:
        lines = RECEIPTS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            receipts.append(VerificationReceipt(**json.loads(line)))
        except (json.JSONDecodeError, TypeError):
            continue
    return receipts


def record_verification(
    command: str,
    workdir: Path | str,
    exit_code: int,
    output: str,
    *,
    expected_fingerprint: str | None = None,
) -> VerificationReceipt | None:
    """为白名单验证命令追加回执；普通命令返回 None。"""
    if not is_verification_command(command):
        return None
    cwd = Path(workdir).resolve()
    fingerprint = workspace_fingerprint(PROJECT_ROOT)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        return None
    receipt = VerificationReceipt(
        command=_canonical_command(command),
        workdir=str(cwd),
        workspace_fingerprint=fingerprint,
        runtime=_runtime_signature(),
        passed=exit_code == 0,
        exit_code=exit_code,
        timestamp=datetime.now(UTC).isoformat(),
        output_tail=output.strip()[-1000:] or "无输出",
    )
    with _lock:
        receipts = _read_receipts()
        receipts.append(receipt)
        receipts = receipts[-MAX_RECEIPTS:]
        try:
            RECEIPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            RECEIPTS_FILE.write_text(
                "".join(json.dumps(asdict(item), ensure_ascii=False) + "\n" for item in receipts),
                encoding="utf-8",
            )
        except OSError:
            return None
    return receipt


def find_valid_receipt(
    command: str,
    workdir: Path | str,
    *,
    max_age: timedelta = RECEIPT_MAX_AGE,
) -> VerificationReceipt | None:
    """返回命令、目录、运行时和当前代码状态均匹配的最新回执。"""
    if not is_verification_command(command):
        return None
    canonical = _canonical_command(command)
    cwd = str(Path(workdir).resolve())
    fingerprint = workspace_fingerprint(PROJECT_ROOT)
    runtime = _runtime_signature()
    now = datetime.now(UTC)
    with _lock:
        receipts = _read_receipts()
    for receipt in reversed(receipts):
        try:
            created = datetime.fromisoformat(receipt.timestamp)
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if now - created > max_age:
            continue
        if (
            receipt.command == canonical
            and receipt.workdir == cwd
            and receipt.workspace_fingerprint == fingerprint
            and receipt.runtime == runtime
        ):
            return receipt
    return None
