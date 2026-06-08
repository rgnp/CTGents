"""Git 操作工具：状态查看、变更对比、提交、推送、PR、日志。"""

import re
import subprocess
from pathlib import Path

# ── 工具定义 ──

TOOLS_GIT = [
    {
        "_meta": {"label": "Git 状态", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "查看工作区状态：分支、变更文件、暂存、未跟踪。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 差异", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "查看文件变更详情。staged/working, 可按文件过滤。",
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "已暂存变更（True）或未暂存（False）",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                    "file": {
                        "type": "string",
                        "description": "指定文件，不传=所有文件",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 日志", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "查看提交历史：hash、作者、日期、提交信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "显示的提交数量，默认 10",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 审查"},
        "type": "function",
        "function": {
            "name": "git_review",
            "description": "审查暂存变更，提交前检查：类型注解/bare except/密钥/死代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 提交", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "暂存变更并提交。message 为空则自动生成。先调 git_review 审查。",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "提交信息，不传=自动生成",
                    },
                    "auto_stage": {
                        "type": "boolean",
                        "description": "是否自动暂存具体变更文件，默认 True",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 推送", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "推送提交到远程仓库（默认 origin 当前分支）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "remote": {
                        "type": "string",
                        "description": "远程仓库名，默认 origin",
                    },
                    "branch": {
                        "type": "string",
                        "description": "分支名，默认当前分支",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "是否强制推送，默认 False",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git PR", "plan_blocked": True, "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "git_pr",
            "description": "创建 Pull Request。title/body 不传则自动分析生成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "PR 标题，不传=自动生成",
                    },
                    "body": {
                        "type": "string",
                        "description": "PR 描述，不传=自动生成",
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "目标分支，默认 main",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "Git 分支", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "查看和管理分支。列出本地分支，标记当前分支。",
            "parameters": {
                "type": "object",
                "properties": {
                    "all": {
                        "type": "boolean",
                        "description": "是否显示远程分支，默认 False",
                    },
                    "path": {
                        "type": "string",
                        "description": "Git 仓库路径，默认当前目录",
                    },
                },
                "required": [],
            },
        },
    },
]


# ── Git 命令执行辅助 ──


def _git(args: list[str], cwd: str | None = None) -> dict:
    """执行 Git 命令，返回结构化结果。"""
    workdir = Path(cwd).resolve() if cwd else Path.cwd()
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            cwd=workdir,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Git 命令执行超时（30 秒）", "returncode": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "未找到 Git 命令，请确认已安装 Git", "returncode": -1}
    except OSError as e:
        return {"success": False, "stdout": "", "stderr": f"执行失败: {e}", "returncode": -1}


def _is_git_repo(path: str | None = None) -> bool:
    """检查指定目录是否为 Git 仓库。"""
    r = _git(["rev-parse", "--git-dir"], path)
    return r["success"]


def _get_current_branch(path: str | None = None) -> str:
    """获取当前分支名。"""
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], path)
    if r["success"]:
        return r["stdout"].strip()
    return "unknown"


def _changed_paths_for_stage(workdir: str) -> list[str]:
    """返回需要暂存的具体路径。"""
    r = _git(["status", "--porcelain"], workdir)
    if not r["success"]:
        return []

    paths: list[str] = []
    for raw_line in r["stdout"].splitlines():
        if not raw_line or raw_line.startswith("##"):
            continue
        path_text = raw_line[3:].strip()
        if " -> " in path_text:
            path_text = path_text.split(" -> ", 1)[1].strip()
        if path_text:
            paths.append(path_text.strip('"'))
    return sorted(set(paths))


def _scope_paths_to_active_run(paths: list[str]) -> list[str]:
    """进化 run 进行中时，把暂存范围限定为本轮真正改动的文件。

    排除"启动前就已脏"的无关文件，避免一锅端（见 7da964e：补 docstring 的提交
    把启动前已脏的 evolution_runner.py 砍了 82 行）。无 active run 时原样返回。
    """
    try:
        from ..evolution_runner import (
            append_run_event,
            load_active_evolution_run,
            run_owned_paths,
        )
        run = load_active_evolution_run()
        if run is None:
            return paths
        owned = set(run_owned_paths(run))
        scoped = [p for p in paths if p in owned]
        skipped = [p for p in paths if p not in owned]
        if skipped:
            append_run_event(run.run_id, "commit_scoped", {
                "committed": scoped, "skipped_preexisting_dirty": skipped,
            })
        return scoped
    except Exception:
        return paths  # 任何异常都不阻断正常提交


def _stage_changed_files(workdir: str) -> str | None:
    paths = _changed_paths_for_stage(workdir)
    paths = _scope_paths_to_active_run(paths)
    if not paths:
        return None
    r = _git(["add", "--", *paths], workdir)
    if not r["success"]:
        return f"暂存失败:\n{r['stderr']}"
    return None


def _classify_porcelain(index: str, worktree: str) -> str:
    """把 porcelain 的 XY 状态码分类到 staged/unstaged/untracked/conflict。

    顺序要紧：untracked('??') 的 worktree 位也是 '?'，必须先于 worktree 判断，
    否则会被误判为 unstaged（历史 bug）。
    """
    if index == "U" or worktree == "U":
        return "conflict"
    if index == "?":
        return "untracked"
    if index != " ":
        return "staged"
    if worktree != " ":
        return "unstaged"
    return ""


def _format_diff_stats(diff_text: str) -> str:
    """从 diff 输出中提取统计信息。"""
    lines = diff_text.split("\n")
    stats = []
    for line in lines:
        # 匹配 git diff --stat 格式
        m = re.match(r'^\s*(.+?)\s*\|\s*(\d+)\s*([+-]+)', line)
        if m:
            stats.append(f"  {m.group(1)} ({m.group(2)} 行变更)")
    return "\n".join(stats)


# ── 工具实现 ──


def git_status(path: str | None = None) -> str:
    """查看 Git 工作区状态。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    branch = _get_current_branch(path)

    # git status --porcelain 用于解析结构化状态
    r = _git(["status", "--porcelain"], path)
    if not r["success"]:
        return f"Git 命令失败:\n{r['stderr']}"

    porcelain = r["stdout"].strip()
    staged = []
    unstaged = []
    untracked = []
    conflicts = []

    if porcelain:
        for line in porcelain.split("\n"):
            line = line.rstrip()
            if not line:
                continue
            index = line[0]
            worktree = line[1]
            filepath = line[3:].strip()

            bucket = _classify_porcelain(index, worktree)
            if bucket == "conflict":
                conflicts.append(filepath)
            elif bucket == "staged":
                staged.append(filepath)
            elif bucket == "unstaged":
                unstaged.append(filepath)
            elif bucket == "untracked":
                untracked.append(filepath)

    # 构建输出
    lines = [f"当前分支: {branch}\n"]

    if not porcelain:
        lines.append("工作区干净，无未提交的变更。")

    if staged:
        lines.append(f"📋 已暂存（{len(staged)} 个文件）：")
        for f in staged:
            lines.append(f"  ✅ {f}")

    if unstaged:
        lines.append(f"\n📝 未暂存（{len(unstaged)} 个文件）：")
        for f in unstaged:
            lines.append(f"  ✏️  {f}")

    if untracked:
        lines.append(f"\n🔍 未跟踪（{len(untracked)} 个文件）：")
        for f in untracked:
            lines.append(f"  ❓ {f}")

    if conflicts:
        lines.append(f"\n⚠️  合并冲突（{len(conflicts)} 个文件）：")
        for f in conflicts:
            lines.append(f"  🔴 {f}")

    return "\n".join(lines)


def git_diff(staged: bool = False, path: str | None = None, file: str | None = None) -> str:
    """查看文件变更详情。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    args = ["diff"]
    if staged:
        args.append("--cached")
    if file:
        args.append("--")
        args.append(file)

    r = _git(args, path)
    if not r["success"]:
        return f"Git 命令失败:\n{r['stderr']}"

    output = r["stdout"].strip()
    if not output:
        label = "已暂存" if staged else "未暂存"
        return f"无 {label} 变更"

    # 截断过长的 diff，保留前 5000 字符
    max_diff = 5000
    if len(output) > max_diff:
        output = output[:max_diff] + f"\n\n...（diff 过长，已截断至 {max_diff} 字符，共 {len(output)} 字符）"

    # 计算统计信息
    add_count = output.count("\n+")
    del_count = output.count("\n-")
    changed_files = len({
        m.group(1) for m in re.finditer(r'^diff --git a/(.+?) b/', output, re.MULTILINE)
    })

    summary = (
        f"变更统计：{changed_files} 个文件，+{add_count}/-{del_count} 行\n"
        f"{'─' * 40}\n"
    )
    return summary + output


def git_log(count: int = 10, path: str | None = None) -> str:
    """查看提交历史。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    # 格式化日志：hash, 作者日期, 作者, 提交信息
    fmt = "%h|%ai|%an|%s"
    args = ["log", f"-{count}", f"--format={fmt}", "--no-merges"]
    r = _git(args, path)
    if not r["success"]:
        return f"Git 命令失败:\n{r['stderr']}"

    raw = r["stdout"].strip()
    if not raw:
        return "暂无提交记录"

    lines = [f"最近 {count} 条提交：\n"]
    for entry in raw.split("\n"):
        parts = entry.split("|", 3)
        if len(parts) == 4:
            hash_short, date, author, msg = parts
            # 日期截取到时分
            date_short = date[:19] if len(date) > 19 else date
            lines.append(f"  [{hash_short}] {date_short}  {author}")
            lines.append(f"         {msg}")
            lines.append("")

    return "\n".join(lines)


def git_review(path: str | None = None) -> str:
    """审查暂存变更的问题。返回审查结果。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库"

    workdir = Path(path).resolve() if path else Path.cwd()
    repo_path = str(workdir)

    # 获取 staged diff
    r = _git(["diff", "--cached"], repo_path)
    if not r["success"] or not r["stdout"].strip():
        r = _git(["diff"], repo_path)  # 没有 staged 就看工作区
    if not r["success"] or not r["stdout"].strip():
        return "没有变更需要审查"

    diff = r["stdout"]
    if len(diff) > 8000:
        diff = diff[:8000] + f"\n...（共 {len(diff)} 字符，已截断）"

    # 非 LLM 的静态检查（0 token）
    static_issues: list[str] = []

    # 检查 bare except / pass
    if re.search(r"except\s*:", diff):
        static_issues.append("裸 except（except:）缺少指定异常类型")
    if re.search(r"except\s+\w+\s*:\s*\n\s*pass", diff):
        static_issues.append("except 块中只有 pass")
    if re.search(r"^\s+pass\s*$", diff, re.MULTILINE):
        pass  # 这个太常见了，不报

    # 检查硬编码 secrets
    secret_pattern = re.compile(r'(api_key|secret|password|token|apikey)\s*[=:]\s*["\'][^"\']+["\']', re.IGNORECASE)
    if secret_pattern.search(diff):
        static_issues.append("可能硬编码了密钥/Token")

    # 检查调试代码
    if re.search(r"(print|pprint)\(.*\)", diff) and not re.search(r"logger\.", diff):
        static_issues.append("包含 print 调试语句（考虑用 logger）")
    if "import pdb;" in diff or "pdb.set_trace()" in diff:
        static_issues.append("包含 pdb 断点（import pdb / pdb.set_trace）")

    # 检查大文件提交
    changed_files = re.findall(r'\+\+\+\s+[ab]/(.+)', diff)
    file_count = len(set(changed_files))
    if file_count > 15:
        static_issues.append(f"一次提交 {file_count} 个文件，考虑拆分为更小的提交")

    # 检查 TODO 残留
    if re.search(r"#\s*TODO|#\s*FIXME|#\s*HACK", diff, re.IGNORECASE):
        static_issues.append("包含 TODO/FIXME 标记")

    static_part = ""
    if static_issues:
        static_part = "## 静态检查发现的问题\n" + "\n".join(f"- {s}" for s in static_issues) + "\n"

    # ── LLM 审查（仅对代码文件，纯文档跳过以节省 ~15s）──
    code_extensions = {".py", ".rs", ".go", ".js", ".ts", ".c", ".cpp", ".h", ".hpp",
                       ".sh", ".toml", ".yaml", ".yml", ".json", ".tf", ".proto"}
    has_code_changes = any(
        any(f.endswith(ext) for ext in code_extensions)
        for f in changed_files
    ) if changed_files else False

    if not has_code_changes:
        llm_review = "（纯文档/配置变更，跳过 LLM 审查）"
    else:
        from ..llm import _invoke_llm, auto_select_model

        review_prompt = (
            "审查以下代码 diff，只关注真正重要的问题，不要水话。\n"
            "按严重程度输出（critical > warning > info），无事则说'无问题'。\n"
            "检查项：\n"
            "- 是否漏了错误处理（返回值没检查、异常没捕获）\n"
            "- 是否改了接口但没改调用方\n"
            "- 是否有多余代码（死代码、注释掉的代码）\n"
            "- 性能问题（N+1 查询、不必要的大对象拷贝）\n"
            "- 安全问题（eval、shell injection 风险）\n\n"
            f"```diff\n{diff}\n```"
        )

        try:
            backend = auto_select_model(review_prompt)  # 始终 Pro
            llm_review, _ = _invoke_llm(backend, [
                {"role": "system", "content": "你是一个代码审查助手。只说关键问题，不要凑字数。"},
                {"role": "user", "content": review_prompt},
            ], lambda _: None)
        except Exception as e:
            llm_review = f"（LLM 审查失败: {e}）"

    # 确认有 diff 内容
    diff_stat = f"共 {len(changed_files)} 个文件, diff 大小 {len(diff)} 字符"

    result = f"## 代码审查报告\n{diff_stat}\n\n"
    if static_part:
        result += static_part + "\n"
    result += f"## LLM 审查\n{llm_review or '（无结果）'}"
    return result
def git_commit(message: str | None = None, auto_stage: bool = True, path: str | None = None) -> str:
    """暂存变更并提交。含 .py 变更由 pre-commit 钩子强制先过 ruff+测试。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    workdir = Path(path).resolve() if path else Path.cwd()

    # 自动暂存
    if auto_stage:
        stage_error = _stage_changed_files(str(workdir))
        if stage_error:
            return stage_error

    # 检查是否有变更需要提交
    r_check = _git(["status", "--porcelain"], str(workdir))
    if not r_check["stdout"].strip():
        return "没有需要提交的变更"

    # 提交前质量门禁（ruff + pytest）由 pre-commit 钩子对【暂存快照】强制执行，
    # 不在此内联重跑：内联测的是整个工作区（含未暂存脏改动）→ 双跑且可能假阴性误拒。

    # 如果没有提供 message，自动分析变更生成
    if not message:
        message = _generate_commit_message(str(workdir))

    # 提交（pre-commit 钩子在此跑质量门禁；门禁失败输出多在 stdout）
    r2 = _git(["commit", "-m", message], str(workdir))
    if not r2["success"]:
        detail = (r2["stdout"] + r2["stderr"]).strip()
        return f"提交失败（可能是 pre-commit 质量门禁未通过）:\n{detail[-1800:]}"

    # 提交后触发变更追踪：提醒需要同步的文档
    from .file import _track_changes
    track = _track_changes("(git_commit)")
    runner_note = _complete_active_runner_after_commit(message)
    return f"✅ 提交成功\n\n{message}\n\n{r2['stdout'].strip()}{track}{runner_note}"


def _complete_active_runner_after_commit(message: str) -> str:
    """Close the active evolution runner after a successful commit."""
    try:
        from ..evolution_runner import RunnerStatus, complete_evolution_run, load_active_evolution_run
        run = load_active_evolution_run()
        if run is None:
            return ""
        complete_evolution_run(run.run_id, RunnerStatus.PASSED, note=message)
        return f"\n\nEvolution runner: {run.run_id} marked passed."
    except Exception as e:
        return f"\n\nEvolution runner close failed: {e}"


def _generate_commit_message(repo_path: str) -> str:
    """根据当前变更自动生成 commit message。"""
    # 获取变更文件列表
    r = _git(["diff", "--cached", "--name-status"], repo_path)
    if not r["success"] or not r["stdout"].strip():
        r = _git(["diff", "--name-status"], repo_path)
    if not r["success"] or not r["stdout"].strip():
        # 尝试看未跟踪文件
        r = _git(["status", "--porcelain"], repo_path)

    changes = r["stdout"].strip()
    if not changes:
        return "update"

    # 解析变更类型
    added = []
    modified = []
    deleted = []
    renamed = []

    for line in changes.split("\n"):
        line = line.strip()
        if not line:
            continue
        # git diff --name-status 格式: M file.py
        if line[0] in ("A", "M", "D", "R"):
            filename = line[1:].strip() if len(line) > 1 else ""
            if line.startswith("??") or line.startswith("A"):
                added.append(filename)
            elif line.startswith("M"):
                modified.append(filename)
            elif line.startswith("D"):
                deleted.append(filename)
            elif line.startswith("R"):
                renamed.append(filename)
        # git status --porcelain 格式: ?? file.py
        elif line.startswith("??"):
            added.append(line[2:].strip())
        elif line[0] == "?":
            added.append(line[1:].strip())

    # 构建 message

    # 检测主要变更类型，生成 scope
    all_files = added + modified + deleted + renamed
    scopes = set()
    for f in all_files:
        parts_path = f.replace("\\", "/").split("/")
        if len(parts_path) > 1:
            scopes.add(parts_path[0])
        else:
            scopes.add("root")

    # 构建标题
    if added and not modified and not deleted:
        prefix = "feat"
    elif deleted and not added:
        prefix = "remove"
    elif modified and not added:
        prefix = "fix" if any("bug" in f.lower() or "fix" in f.lower() for f in modified) else "refactor"
    else:
        prefix = "feat"

    scope_str = "/".join(sorted(scopes)[:3])
    title = f"{prefix}({scope_str}): "

    # 详细信息
    details = []
    if added:
        details.append(f"新增 {len(added)} 个文件: {', '.join(added[:5])}")
        if len(added) > 5:
            details[-1] += f" 等 {len(added)} 个文件"
    if modified:
        details.append(f"修改 {len(modified)} 个文件: {', '.join(modified[:5])}")
        if len(modified) > 5:
            details[-1] += f" 等 {len(modified)} 个文件"
    if deleted:
        details.append(f"删除 {len(deleted)} 个文件: {', '.join(deleted[:3])}")
    if renamed:
        details.append(f"重命名 {len(renamed)} 个文件")

    # 根据文件类型更精确地推断
    if any(f.endswith((".py", ".js", ".ts", ".java", ".go", ".rs")) for f in all_files):
        title = f"{prefix}(code): "
    elif any(f.endswith((".md", ".txt", ".rst")) for f in all_files):
        title = f"{prefix}(docs): "
    elif any(f.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".cfg")) for f in all_files):
        title = f"{prefix}(config): "
    elif any(f.endswith((".png", ".jpg", ".svg", ".ico", ".css", ".scss")) for f in all_files):
        title = f"{prefix}(assets): "

    detail_text = "; ".join(details)
    full_message = title + detail_text if detail_text else title + "更新代码"

    return full_message


def git_push(remote: str = "origin", branch: str | None = None,
             force: bool = False, path: str | None = None) -> str:
    """推送本地提交到远程仓库。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    # 检查远程仓库是否存在
    r_check = _git(["remote"], path)
    remotes = r_check["stdout"].strip().split("\n") if r_check["stdout"].strip() else []
    if remote not in remotes:
        return f"远程仓库 '{remote}' 不存在。可用远程: {', '.join(remotes) if remotes else '无'}"

    # 获取当前分支
    if not branch:
        branch = _get_current_branch(path)

    # 构建推送命令
    args = ["push", remote, branch]
    if force:
        args.append("--force")

    r = _git(args, path)
    if r["success"]:
        return f"✅ 已推送到 {remote}/{branch}\n{r['stdout'].strip()}"
    else:
        stderr = r["stderr"]
        if "has no commits yet" in stderr:
            return f"当前分支 '{branch}' 没有提交，请先 commit"
        if "rejected" in stderr:
            return (
                f"❌ 推送被拒绝: 远程仓库有本地没有的提交。\n"
                f"  建议: 先执行 git pull --rebase，或使用 force 推送（慎重）。\n"
                f"  {stderr[:500]}"
            )
        return f"❌ 推送失败:\n{stderr[:500]}"


def git_pr(title: str | None = None, body: str | None = None,
           base_branch: str | None = None, path: str | None = None) -> str:
    """创建 Pull Request。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    workdir = Path(path).resolve() if path else Path.cwd()
    current_branch = _get_current_branch(str(workdir))

    if current_branch in ("main", "master", "HEAD"):
        return "当前在主干分支上，请先切换到特性分支再创建 PR。"

    # 确定目标分支
    if not base_branch:
        # 检测默认分支
        r = _git(["rev-parse", "--abbrev-ref", "origin/HEAD"], str(workdir))
        base_branch = r["stdout"].strip().replace("origin/", "") if r["success"] else "main"

    # 尝试用 gh CLI
    try:
        gh_check = subprocess.run(
            ["gh", "--version"], capture_output=True, encoding="utf-8", errors="replace", timeout=5
        )
        gh_available = gh_check.returncode == 0
    except (FileNotFoundError, OSError):
        gh_available = False

    if gh_available:
        # 自动生成 title 和 body
        if not title:
            # 获取当前分支与目标分支的差异
            r = _git(["log", f"{base_branch}..{current_branch}", "--oneline"], str(workdir))
            commits = r["stdout"].strip() if r["success"] else ""
            if commits:
                lines = commits.split("\n")
                title = lines[0]  # 用第一个 commit message 作为 PR 标题
                if len(lines) > 1:
                    body = "## 变更内容\n\n" + "\n".join(f"- {line}" for line in lines)
                else:
                    body = "自动生成的 PR 描述。"
            else:
                title = f"feat: {current_branch}"

        if not body:
            body = f"从分支 `{current_branch}` 合并到 `{base_branch}`。"

        # 执行 gh pr create
        args = ["gh", "pr", "create", "--base", base_branch, "--title", title, "--body", body]
        try:
            r = subprocess.run(
                args, capture_output=True, encoding="utf-8", errors="replace", timeout=30, cwd=workdir
            )
            if r.returncode == 0:
                pr_url = r.stdout.strip()
                return (
                    "✅ Pull Request 已创建\n\n"
                    f"标题: {title}\n目标: {current_branch} → {base_branch}\n链接: {pr_url}"
                )
            else:
                return f"❌ 创建 PR 失败（gh CLI）:\n{r.stderr[:500]}"
        except subprocess.TimeoutExpired:
            return "创建 PR 超时（30 秒）"
    else:
        # 没有 gh CLI，提供手动操作指引
        r = _git(["remote", "-v"], str(workdir))
        remote_info = r["stdout"].strip() if r["success"] else "（未配置远程仓库）"

        return (
            f"需要创建 Pull Request：{current_branch} → {base_branch}\n\n"
            f"未检测到 GitHub CLI (gh)，请手动操作：\n\n"
            f"1. 确保已推送分支:\n"
            f"   git push -u origin {current_branch}\n\n"
            f"2. 在远程仓库页面创建 PR:\n"
            f"   {remote_info}\n\n"
            f"或安装 GitHub CLI 后重试:\n"
            f"   https://cli.github.com/"
        )


def git_branch(all_branches: bool = False, path: str | None = None) -> str:
    """查看分支列表。"""
    if not _is_git_repo(path):
        return "当前目录不是 Git 仓库（未找到 .git 目录）"

    args = ["branch"]
    if all_branches:
        args.append("-a")

    r = _git(args, path)
    if not r["success"]:
        return f"Git 命令失败:\n{r['stderr']}"

    branches = r["stdout"].strip()
    if not branches:
        return "无分支信息"

    # 美化输出
    current = _get_current_branch(path)
    lines = ["分支列表：\n"]
    for b in branches.split("\n"):
        b = b.strip()
        if b.startswith("* "):
            lines.append(f"  🌿 {b}")
        elif b:
            marker = "← 当前" if b == current else ""
            lines.append(f"     {b}  {marker}")

    return "\n".join(lines)


# ── 调度 ──


def execute(name: str, args: dict) -> str | None:
    if name == "git_status":
        return git_status(args.get("path"))
    if name == "git_diff":
        return git_diff(
            staged=args.get("staged", False),
            path=args.get("path"),
            file=args.get("file"),
        )
    if name == "git_log":
        return git_log(
            count=args.get("count", 10),
            path=args.get("path"),
        )
    if name == "git_commit":
        return git_commit(
            message=args.get("message"),
            auto_stage=args.get("auto_stage", True),
            path=args.get("path"),
        )
    if name == "git_push":
        return git_push(
            remote=args.get("remote", "origin"),
            branch=args.get("branch"),
            force=args.get("force", False),
            path=args.get("path"),
        )
    if name == "git_pr":
        return git_pr(
            title=args.get("title"),
            body=args.get("body"),
            base_branch=args.get("base_branch"),
            path=args.get("path"),
        )
    if name == "git_branch":
        return git_branch(
            all_branches=args.get("all", False),
            path=args.get("path"),
        )
    if name == "git_review":
        return git_review(path=args.get("path"))
    return None
