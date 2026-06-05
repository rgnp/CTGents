"""Evolution runner with persistent run state and preflight snapshots."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_ROOT = Path.home() / ".ctgents" / "evolution"
RUNS_DIR = RUN_ROOT / "runs"
ACTIVE_RUN_FILE = RUN_ROOT / "active.json"
STATE_FILE_NAME = "state.json"
PATCH_FILE_NAME = "before.patch"
RUN_ID_TS_FORMAT = "%Y%m%d-%H%M%S"
RUN_ID_UUID_CHARS = 8
GIT_TIMEOUT_SECONDS = 10
PROMPT_STATUS_LIMIT = 1600


class EvolutionPhase(StrEnum):
    """Runner phases for a self-evolution attempt."""

    PREFLIGHT = "preflight"
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    GENERATION = "generation"
    VERIFICATION = "verification"
    DECISION = "decision"
    COMPLETE = "complete"


class RunnerStatus(StrEnum):
    """Lifecycle status for an evolution runner."""

    ACTIVE = "active"
    PASSED = "passed"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class GitCommandResult:
    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class EvolutionRun:
    run_id: str
    goal: str
    root: str
    run_dir: str
    patch_path: str
    state_path: str
    status: str = RunnerStatus.ACTIVE.value
    phase: str = EvolutionPhase.RESEARCH.value
    created_at: str = ""
    updated_at: str = ""
    preflight: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    validations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class EvolutionRunStart:
    run: EvolutionRun
    summary: str


def start_evolution_run(goal: str, root: Path | None = None) -> EvolutionRunStart:
    """Create a persistent evolution run. Returns summary for display.

    The runner records state in background. The agent works as normal —
    it receives the goal as a regular user message, not a special prompt.
    """
    project_root = (root or PROJECT_ROOT).resolve()
    run_id = _new_run_id()
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    now = _utc_now()
    patch_path = run_dir / PATCH_FILE_NAME
    preflight = _collect_preflight(project_root)
    patch_status = _write_patch_snapshot(project_root, patch_path)
    preflight["patch_snapshot"] = patch_status

    run = EvolutionRun(
        run_id=run_id,
        goal=goal,
        root=str(project_root),
        run_dir=str(run_dir),
        patch_path=str(patch_path),
        state_path=str(run_dir / STATE_FILE_NAME),
        created_at=now,
        updated_at=now,
        preflight=preflight,
    )
    run.events.append(_event("runner_started", {"goal": goal}))
    _save_run(run)
    _write_active_run(run)

    return EvolutionRunStart(run=run, summary=_format_start_summary(run))


def advance_evolution_phase(run_id: str, phase: EvolutionPhase | str, note: str = "") -> EvolutionRun:
    """Advance a persisted run to a new phase."""
    run = load_evolution_run(run_id)
    next_phase = phase.value if isinstance(phase, EvolutionPhase) else phase
    run.phase = next_phase
    run.updated_at = _utc_now()
    run.events.append(_event("phase_advanced", {"phase": next_phase, "note": note}))
    _save_run(run)
    return run


def record_validation_result(
    changed_files: list[str],
    output: str,
    passed: bool,
    run_id: str | None = None,
) -> EvolutionRun | None:
    """Append a validation result to the active run, if one exists."""
    run = load_evolution_run(run_id) if run_id else load_active_evolution_run()
    if run is None:
        return None
    run.phase = EvolutionPhase.DECISION.value
    run.updated_at = _utc_now()
    run.validations.append({
        "changed_files": changed_files,
        "passed": passed,
        "output_preview": output[:PROMPT_STATUS_LIMIT],
        "timestamp": run.updated_at,
    })
    run.events.append(_event("validation_recorded", {"passed": passed}))
    _save_run(run)
    return run


def complete_evolution_run(run_id: str, status: RunnerStatus | str, note: str = "") -> EvolutionRun:
    """Mark an evolution run complete."""
    run = load_evolution_run(run_id)
    final_status = status.value if isinstance(status, RunnerStatus) else status
    run.status = final_status
    run.phase = EvolutionPhase.COMPLETE.value
    run.updated_at = _utc_now()
    run.events.append(_event("runner_completed", {"status": final_status, "note": note}))
    _save_run(run)
    if _read_active_run_id() == run_id:
        ACTIVE_RUN_FILE.unlink(missing_ok=True)
    return run


def load_evolution_run(run_id: str) -> EvolutionRun:
    """Load a persisted evolution run by id."""
    state_path = RUNS_DIR / run_id / STATE_FILE_NAME
    data = json.loads(state_path.read_text(encoding="utf-8"))
    return EvolutionRun(**data)


def load_active_evolution_run() -> EvolutionRun | None:
    """Load the active evolution run, if present."""
    run_id = _read_active_run_id()
    if not run_id:
        return None
    try:
        return load_evolution_run(run_id)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None


def describe_active_evolution_run() -> str:
    """Return a compact human-readable active runner summary."""
    run = load_active_evolution_run()
    if run is None:
        return "无 active evolution runner。"
    return _format_start_summary(run)


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime(RUN_ID_TS_FORMAT)
    suffix = uuid.uuid4().hex[:RUN_ID_UUID_CHARS]
    return f"evo-{stamp}-{suffix}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _event(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "timestamp": _utc_now(), "data": data}


def _run_git(root: Path, args: list[str]) -> GitCommandResult:
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        return GitCommandResult(cmd, 127, stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        return GitCommandResult(cmd, 124, stdout=exc.stdout or "", stderr=exc.stderr or "")
    return GitCommandResult(cmd, result.returncode, result.stdout, result.stderr)


def _collect_preflight(root: Path) -> dict[str, Any]:
    status = _run_git(root, ["status", "--porcelain"])
    branch = _run_git(root, ["branch", "--show-current"])
    head = _run_git(root, ["rev-parse", "--short", "HEAD"])
    warnings: list[str] = []
    if status.exit_code != 0:
        warnings.append("git status 不可用，runner 将继续但无法确认工作区基线。")
    elif status.stdout.strip():
        warnings.append("工作区启动时已有改动，后续必须只处理本轮相关文件。")
    return {
        "git_status": asdict(status),
        "branch": branch.stdout.strip(),
        "head": head.stdout.strip(),
        "dirty_files": _split_status(status.stdout),
        "warnings": warnings,
    }


def _write_patch_snapshot(root: Path, patch_path: Path) -> dict[str, Any]:
    diff = _run_git(root, ["diff", "--binary", "HEAD"])
    if diff.exit_code != 0:
        patch_path.write_text(diff.stderr or "git diff unavailable\n", encoding="utf-8")
        return {"ok": False, "reason": diff.stderr.strip(), "path": str(patch_path)}
    content = diff.stdout or "No working-tree diff at runner start.\n"
    patch_path.write_text(content, encoding="utf-8")
    return {"ok": True, "bytes": len(content.encode("utf-8")), "path": str(patch_path)}


def _split_status(output: str) -> list[str]:
    return [line.strip() for line in output.splitlines() if line.strip()]


def _save_run(run: EvolutionRun) -> None:
    run_path = Path(run.state_path)
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(
        json.dumps(asdict(run), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_active_run(run: EvolutionRun) -> None:
    ACTIVE_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_RUN_FILE.write_text(
        json.dumps({"run_id": run.run_id, "state_path": run.state_path}, ensure_ascii=False),
        encoding="utf-8",
    )


def _read_active_run_id() -> str:
    try:
        data = json.loads(ACTIVE_RUN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    run_id = data.get("run_id", "")
    return run_id if isinstance(run_id, str) else ""


def _format_start_summary(run: EvolutionRun) -> str:
    dirty_count = len(run.preflight.get("dirty_files", []))
    warnings = run.preflight.get("warnings", [])
    warning_text = "；".join(warnings) if warnings else "无"
    return "\n".join([
        "自进化 runner 已启动",
        f"目标: {run.goal}",
        f"run_id: {run.run_id}",
        f"phase: {run.phase}",
        f"run_dir: {run.run_dir}",
        f"启动时改动: {dirty_count} 项",
        f"preflight: {warning_text}",
    ])
