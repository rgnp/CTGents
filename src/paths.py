"""Boundary between the CTGents core and its mutable personal workspace."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

CORE_ROOT = Path(__file__).resolve().parent.parent
_POINTER_FILE = CORE_ROOT / ".ctg-workspace"
_RUNTIME_TOP_LEVEL = frozenset({"knowledge", "memory", "sessions", "stats", "tasks"})
load_dotenv(CORE_ROOT / ".env")


def _workspace_root() -> Path:
    configured = os.getenv("CTG_WORKSPACE_DIR", "").strip()
    if not configured and _POINTER_FILE.is_file():
        try:
            configured = _POINTER_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            configured = ""
    root = Path(configured).expanduser() if configured else Path.home() / ".ctgents"
    return root.resolve()


WORKSPACE_ROOT = _workspace_root()
KNOWLEDGE_DIR = WORKSPACE_ROOT / "knowledge"
MEMORY_DIR = WORKSPACE_ROOT / "memory"
SESSIONS_DIR = WORKSPACE_ROOT / "sessions"
STATS_DIR = WORKSPACE_ROOT / "stats"
TASKS_DIR = WORKSPACE_ROOT / "tasks"
RAG_INDEX_DIR = WORKSPACE_ROOT / ".rag-index"

# Psyche and skills are versioned, distributable product resources.
RESOURCE_ROOT = Path(__file__).resolve().parent / "ctgents_resources"
PSYCHE_ROOT = RESOURCE_ROOT / "psyche"
SKILLS_ROOT = RESOURCE_ROOT / "skills"


def ensure_workspace() -> Path:
    """Create the workspace skeleton and return its root."""
    for directory in (
        WORKSPACE_ROOT,
        KNOWLEDGE_DIR,
        MEMORY_DIR,
        SESSIONS_DIR,
        STATS_DIR,
        TASKS_DIR,
        TASKS_DIR / "archive",
        TASKS_DIR / "pending",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    current = TASKS_DIR / "current.md"
    if not current.exists():
        current.touch()
    return WORKSPACE_ROOT


def resolve_runtime_path(path: str | Path, project_root: str | Path = CORE_ROOT) -> Path:
    """Resolve mutable virtual namespaces against the personal workspace.

    Other relative paths use ``project_root`` so installed CTGents can operate
    on the caller's current project instead of its package directory.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    if candidate.parts and candidate.parts[0].lower() in _RUNTIME_TOP_LEVEL:
        return (WORKSPACE_ROOT / candidate).resolve()
    return (Path(project_root) / candidate).resolve()


def display_path(path: str | Path) -> str:
    """Return a stable path, preferring virtual workspace/core-relative names."""
    resolved = Path(path).resolve()
    for root in (WORKSPACE_ROOT, CORE_ROOT):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return str(resolved)
