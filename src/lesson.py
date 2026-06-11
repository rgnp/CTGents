"""失败模式学习 — 从对话日志中提取反复出现的失败模式，存为策略记忆。

三层递进：
  检测器（本模块）→ 策略记忆（memory.py type=strategy）→ 注入引擎（llm.py volatile）

四指纹检测器：
  a) 签名变更漂移：修改 src 函数 → 测试失败 → 反复修补测试 → 仍失败
  b) 重复编辑同文件：同一路径被 write_file/edit_file_lines ≥3 次
  c) 工具参数错误后修正：工具返回 error → 后续用修正参数成功
  d) 预提交被拒：git_commit 含 pre-commit/门禁失败

工具参数来源：tool 消息不带 args，需从前序 assistant 的 tool_calls 中取。
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field


@dataclass
class Lesson:
    name: str
    content: str
    fingerprint: str  # signature_drift / repeated_edit / tool_arg_error / precommit_rejection
    severity: str = "medium"
    files_involved: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 日志解析辅助 — 构建 tool_call_id→args 映射
# ═══════════════════════════════════════════════════════════════


def _build_tool_args_map(log: list[dict]) -> dict[str, dict]:
    """扫描 assistant 消息的 tool_calls，构建 call_id → args 映射。"""
    arg_map: dict[str, dict] = {}
    for m in log:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            call_id = tc.get("id", "")
            if not call_id:
                continue
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, KeyError):
                args = {}
            arg_map[call_id] = args
    return arg_map


def _tool_results(log: list[dict]) -> list[dict]:
    return [m for m in log if m.get("role") == "tool"]


def _tool_result_for_id(log: list[dict], call_id: str) -> dict | None:
    for m in log:
        if m.get("role") == "tool" and m.get("tool_call_id") == call_id:
            return m
    return None


# ═══════════════════════════════════════════════════════════════
# 条件谓词（基于 arg_map 的 lookup）
# ═══════════════════════════════════════════════════════════════


def _tool_name(m: dict) -> str:
    return m.get("_tool_name", "")


def _edited_path(msg: dict, arg_map: dict[str, dict]) -> str:
    aid = msg.get("tool_call_id", "")
    return arg_map.get(aid, {}).get("path", "")


def _is_py_edit(msg: dict, arg_map: dict[str, dict]) -> bool:
    name = _tool_name(msg)
    if name not in ("write_file", "edit_file_lines", "delete_file"):
        return False
    return _edited_path(msg, arg_map).endswith(".py")


def _is_test_edit(msg: dict, arg_map: dict[str, dict]) -> bool:
    name = _tool_name(msg)
    if name not in ("write_file", "edit_file_lines"):
        return False
    path = _edited_path(msg, arg_map).replace("\\", "/")
    return path.startswith("tests/") and path.endswith(".py")


def _is_src_edit(msg: dict, arg_map: dict[str, dict]) -> bool:
    name = _tool_name(msg)
    if name not in ("write_file", "edit_file_lines"):
        return False
    path = _edited_path(msg, arg_map).replace("\\", "/")
    return path.startswith("src/") and path.endswith(".py")


def _is_precommit_fail(msg: dict) -> bool:
    if _tool_name(msg) != "git_commit":
        return False
    content = msg.get("content", "")
    return any(phrase in content for phrase in (
        "pre-commit", "门禁未通过", "拒绝提交", "ruff check",
    ))


def _is_pytest_fail(msg: dict, arg_map: dict[str, dict]) -> bool:
    if _tool_name(msg) != "run_command":
        return False
    content = msg.get("content", "").lower()
    is_fail = "failed" in content or "error" in content or "exit code: 1" in content
    cmd = arg_map.get(msg.get("tool_call_id", ""), {}).get("command", "").lower()
    return is_fail and "pytest" in cmd
# ═══════════════════════════════════════════════════════════════
# 检测器
# ═══════════════════════════════════════════════════════════════

def _detect_signature_drift(log: list[dict]) -> list[Lesson]:
    """改 src 函数签名 → 测试失败 → 改测试 → 测试还是失败。"""
    tools = _tool_results(log)
    if len(tools) < 4:
        return []

    arg_map = _build_tool_args_map(log)

    src_edits: list[str] = []
    test_edits: list[str] = []
    pytest_fails = 0
    commit_fails = 0

    for m in tools:
        if _is_src_edit(m, arg_map):
            src_edits.append(_edited_path(m, arg_map))
        elif _is_test_edit(m, arg_map):
            test_edits.append(_edited_path(m, arg_map))
        elif _is_pytest_fail(m, arg_map):
            pytest_fails += 1
        elif _is_precommit_fail(m):
            commit_fails += 1

    if not src_edits or not test_edits or (pytest_fails + commit_fails == 0):
        return []

    unique_src = sorted(set(src_edits))
    unique_test = sorted(set(test_edits))

    return [Lesson(
        name="lesson-signature-drift-grep-first",
        fingerprint="signature_drift",
        severity="high",
        files_involved=unique_src + unique_test,
        content=(
            "## 模式\n"
            "修改函数签名/重命名后漏了测试文件的 mock/调用点，"
            "导致 pytest 或 pre-commit 不绿。\n\n"
            "## 触发条件\n"
            f"- 修改了源文件: {', '.join(unique_src)}\n"
            f"- 反复调整测试文件: {', '.join(unique_test)}\n"
            f"- 同轮内测试/提交失败 {pytest_fails + commit_fails} 次\n\n"
            "## 预防\n"
            "1. 改前先 `grep_code` 全项目搜被改函数名\n"
            "2. 列全引用点（src/ + tests/），一次性全改\n"
            "3. 改完先跑相关测试验证\n"
        ),
    )]


def _detect_repeated_file_edit(log: list[dict]) -> list[Lesson]:
    """同文件被反复编辑 ≥3 次。"""
    tools = _tool_results(log)
    arg_map = _build_tool_args_map(log)

    edit_counts: dict[str, int] = {}
    for m in tools:
        if _is_py_edit(m, arg_map):
            path = _edited_path(m, arg_map)
            if path:
                edit_counts[path] = edit_counts.get(path, 0) + 1

    lessons: list[Lesson] = []
    for path, count in edit_counts.items():
        if count >= 3:
            safe = path.replace("/", "-").replace(".", "-")
            lessons.append(Lesson(
                name=f"lesson-repeated-edit-{safe}",
                fingerprint="repeated_edit",
                severity="medium",
                files_involved=[path],
                content=(
                    f"## 模式\n"
                    f"文件 `{path}` 被编辑了 {count} 次——反复改同一个文件。\n\n"
                    f"## 预防\n"
                    f"下次打开这个文件前，先用 `read_file` 完整读一遍，"
                    f"想清楚所有要改的地方，`write_file` 一次写完。\n"
                ),
            ))
    return lessons


def _detect_tool_arg_error(log: list[dict]) -> list[Lesson]:
    """工具返回 error → 后续同工具成功。"""
    # 收集每类工具的 failed/success 调用
    failures: dict[str, list[str]] = {}  # name → [result_snippets]
    successes_per_tool: dict[str, int] = {}

    for m in log:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tname = tc["function"]["name"]
            result = _tool_result_for_id(log, tc["id"])
            if not result:
                continue
            content = result.get("content", "")
            has_error = False
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "error" in parsed:
                    has_error = True
            except json.JSONDecodeError:
                if "error" in content.lower() or "失败" in content:
                    has_error = True
            if has_error:
                failures.setdefault(tname, []).append(content[:200])
            else:
                successes_per_tool[tname] = successes_per_tool.get(tname, 0) + 1

    lessons: list[Lesson] = []
    for tname, errors in failures.items():
        if len(errors) >= 1 and successes_per_tool.get(tname, 0) >= 1:
            lessons.append(Lesson(
                name=f"lesson-tool-arg-{tname}",
                fingerprint="tool_arg_error",
                severity="medium",
                content=(
                    f"## 模式\n"
                    f"工具 `{tname}` 参数出错 → 修正后成功。\n\n"
                    f"## 错误示例\n"
                    + "\n".join(f"- {e[:120]}" for e in errors[:3]) + "\n\n"
                    f"## 预防\n"
                    f"调用 `{tname}` 前检查参数格式。\n"
                ),
            ))
    return lessons


def _detect_precommit_rejection(log: list[dict]) -> list[Lesson]:
    """git_commit 被 pre-commit 门禁拒绝。"""
    tools = _tool_results(log)
    rejections = sum(1 for m in tools if _is_precommit_fail(m))
    if rejections < 1:
        return []

    return [Lesson(
        name="lesson-precommit-gate-fail",
        fingerprint="precommit_rejection",
        severity="high",
        content=(
            f"## 模式\n"
            f"提交被 pre-commit 门禁拒绝了 {rejections} 次——"
            f"通常是改了代码但没跑 ruff 或 pytest。\n\n"
            f"## 预防\n"
            f"1. 改完代码先 `ruff check src/`\n"
            f"2. 跑相关测试: `py -m pytest tests/ -k \"关键词\"`\n"
            f"3. 都绿了再 `git_commit`\n"
        ),
    )]


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

_DETECTORS = [
    ("signature_drift", _detect_signature_drift),
    ("repeated_edit", _detect_repeated_file_edit),
    ("tool_arg_error", _detect_tool_arg_error),
    ("precommit_rejection", _detect_precommit_rejection),
]


def extract_lessons(log: list[dict]) -> list[Lesson]:
    """从对话日志提取所有侦测到的教训。"""
    all_lessons: list[Lesson] = []
    for _label, detector in _DETECTORS:
        with contextlib.suppress(Exception):
            all_lessons.extend(detector(log))
    # 去重
    seen: dict[str, Lesson] = {}
    for le in all_lessons:
        key = f"{le.fingerprint}:{le.name}"
        seen[key] = le
    return list(seen.values())


# ═══════════════════════════════════════════════════════════════
# 注入引擎 — 在工具执行前匹配教训
# ═══════════════════════════════════════════════════════════════


def _load_strategy_memories() -> list[dict]:
    """从 memory/ 加载所有 type=strategy 记忆。"""
    from pathlib import Path

    from .config import MEMORY_DIR
    d = Path(MEMORY_DIR)
    if not d.is_dir():
        return []
    result: list[dict] = []
    for f in sorted(d.iterdir()):
        if f.suffix != ".md" or f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        if "type: strategy" not in text:
            continue
        meta, body = _split_frontmatter(text)
        name = meta.get("name", f.stem)
        result.append({"name": name, "content": body})
    return result


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    close = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if close is None:
        return {}, text
    meta: dict[str, str] = {}
    for raw in lines[1:close]:
        line = raw.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    body = "\n".join(lines[close + 1:]).strip()
    return meta, body


def match_lessons_for_action(action_desc: str, max_results: int = 3) -> list[str]:
    """返回与当前操作最相关的教训提醒文本（Jaccard 相似度）。"""
    import re
    lessons = _load_strategy_memories()
    if not lessons:
        return []

    def _tok(s: str) -> set[str]:
        t = s.lower()
        toks = set(re.findall(r"[a-z0-9]{2,}", t))
        for run in re.findall(r"[一-鿿]+", t):
            toks.update(run[i:i + 2] for i in range(len(run) - 1)) if len(run) > 1 else toks.add(run)
        return toks

    q_tokens = _tok(action_desc)
    if not q_tokens:
        return []

    scored: list[tuple[float, str]] = []
    for le in lessons:
        l_tokens = _tok(le["content"])
        if not l_tokens:
            continue
        jac = len(q_tokens & l_tokens) / len(q_tokens | l_tokens)
        if jac < 0.02:
            continue
        # 取第一条非标题、非空的实际内容行作为摘要
        first_line = ""
        for raw_line in le["content"].split("\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("##"):
                continue
            first_line = stripped[:120]
            break
        if not first_line:
            first_line = le["content"][:120]
        scored.append((jac, first_line))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_results]]


def inject_lesson_context(log: list[dict], tool_name: str, args: dict) -> None:
    """在 log 尾部注入匹配到的教训（volatile 系统消息）。"""
    if not tool_name:
        return

    desc_parts = [tool_name]
    if tool_name in ("write_file", "edit_file_lines", "delete_file"):
        desc_parts.append(args.get("path", ""))
    elif tool_name in ("run_command",):
        desc_parts.append(args.get("command", "")[:80])
    desc = " ".join(desc_parts)

    matches = match_lessons_for_action(desc)
    if not matches:
        return

    lines = ["[⚠️ 经验提醒] 你之前遇到过类似情况："]
    for m in matches:
        lines.append(f"  {m}")
    log.append({"role": "system", "content": "\n".join(lines), "_volatile": True})


def save_lessons(lessons: list[Lesson]) -> int:
    """将教训存入 memory/，作为 type=strategy 记忆。返回写入数。"""
    from datetime import UTC, datetime
    from pathlib import Path

    from .config import MEMORY_DIR
    from .tools.memory import _rebuild_index as _rebuild
    from .tools.memory import mark_dirty

    d = Path(MEMORY_DIR)
    d.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    saved = 0

    for le in lessons:
        existing = d / f"{le.name}.md"
        times = 1
        if existing.exists():
            try:
                meta, _ = _split_frontmatter(existing.read_text(encoding="utf-8"))
                times = int(meta.get("times_encountered", "1")) + 1
            except Exception:
                pass

        desc = le.content.split("\n")[0].lstrip("#").strip()[:50]
        text = (
            f"---\n"
            f"name: {le.name}\n"
            f"description: {desc}\n"
            f"metadata:\n"
            f"  type: strategy\n"
            f"  fingerprint: {le.fingerprint}\n"
            f"  severity: {le.severity}\n"
            f"  times_encountered: {times}\n"
            f"  last_encountered: {now}\n"
            f"---\n\n"
            f"{le.content}\n"
        )
        (d / f"{le.name}.md").write_text(text, encoding="utf-8")
        saved += 1

    _rebuild()
    mark_dirty()
    return saved
