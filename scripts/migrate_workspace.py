"""Safely migrate legacy repo-local CTGents state to a personal workspace."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

RUNTIME_DIRS = ("memory", "knowledge", "sessions", "tasks", "stats")


def migrate(core: Path, workspace: Path, *, move: bool = False) -> list[str]:
    """Copy or move legacy runtime directories without overwriting targets."""
    core = core.resolve()
    workspace = workspace.resolve()
    if core == workspace:
        raise ValueError("核心项目和个人工作区不能是同一目录")
    workspace.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    for name in RUNTIME_DIRS:
        source = core / name
        target = workspace / name
        if (
            not source.exists()
            or source.is_symlink()
            or source.resolve() != source.absolute()
        ):
            continue
        if target.exists():
            raise FileExistsError(f"目标已存在，拒绝覆盖：{target}")
        if move:
            shutil.move(str(source), str(target))
            verb = "moved"
        else:
            shutil.copytree(source, target)
            verb = "copied"
        actions.append(f"{verb}: {source} -> {target}")
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, help="personal workspace directory")
    parser.add_argument("--core", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--move", action="store_true", help="move instead of the default safe copy")
    parser.add_argument(
        "--write-pointer",
        action="store_true",
        help="write the ignored .ctg-workspace pointer in the core checkout",
    )
    args = parser.parse_args()
    actions = migrate(args.core, args.workspace, move=args.move)
    if args.write_pointer:
        pointer = args.core.resolve() / ".ctg-workspace"
        pointer.write_text(str(args.workspace.resolve()) + "\n", encoding="utf-8")
        actions.append(f"pointer: {pointer}")
    print("\n".join(actions) if actions else "No legacy runtime directories found.")


if __name__ == "__main__":
    main()
