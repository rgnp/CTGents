"""外部仓库管理工具：clone、查看状态、列出已克隆仓库。
用于研究探索——快速获取开源代码库。
"""

import subprocess
from pathlib import Path

# ── 默认存放目录 ──
DEFAULT_REPO_ROOT = Path(r"D:\git")


# ── 工具定义 ──

TOOLS_REPO = [
    {
        "_meta": {"label": "克隆仓库", "dedup_blacklist": True},
        "type": "function",
        "function": {
            "name": "repo_clone",
            "description": "克隆指定 Git 仓库到本地。支持 GitHub URL 或完整远程地址，自动识别仓库名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "仓库 URL，如 'https://github.com/tianweiy/CenterPoint' "
                            "或 'git@github.com:user/repo.git'"
                        ),
                    },
                    "target": {
                        "type": "string",
                        "description": f"克隆目标目录。不传则自动使用 {DEFAULT_REPO_ROOT}/<仓库名>",
                    },
                    "branch": {
                        "type": "string",
                        "description": "指定分支，默认使用远程默认分支",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "_meta": {"label": "查看已克隆仓库", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "repo_list",
            "description": f"列出 {DEFAULT_REPO_ROOT} 下已克隆的所有仓库及其状态。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "_meta": {"label": "仓库状态", "parallel_safe": True},
        "type": "function",
        "function": {
            "name": "repo_status",
            "description": "查看指定仓库的详细信息：分支、最近提交、远程地址、工作区状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "仓库名（如 'CenterPoint'）或完整路径",
                    },
                },
                "required": ["repo"],
            },
        },
    },
]


# ── 实现 ──


def _git(args: list[str], cwd: str) -> dict:
    """执行 Git 命令，返回结构化结果。"""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=cwd,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "Git 命令执行超时（120 秒）", "returncode": -1}
    except FileNotFoundError:
        return {"success": False, "stdout": "", "stderr": "未找到 Git，请确认已安装并加入 PATH", "returncode": -1}
    except OSError as e:
        return {"success": False, "stdout": "", "stderr": f"执行失败: {e}", "returncode": -1}


def _repo_name_from_url(url: str) -> str:
    """从 URL 提取仓库名。"""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    name = url.rsplit("/", 1)[-1]
    return name


def _resolve_repo_path(repo: str) -> Path | None:
    """解析仓库路径。先看直接路径，再看 {DEFAULT_REPO_ROOT}/{repo}。"""
    direct = Path(repo).expanduser()
    if direct.exists() and direct.is_dir():
        return direct.resolve()
    under = DEFAULT_REPO_ROOT / repo
    if under.exists() and under.is_dir():
        return under.resolve()
    return None


def _is_git_repo(path: str) -> bool:
    """检查指定目录是否为 Git 仓库。"""
    r = _git(["rev-parse", "--git-dir"], path)
    return r["success"]


def repo_clone(url: str, target: str | None = None, branch: str | None = None) -> str:
    """克隆 Git 仓库到本地。"""
    repo_name = _repo_name_from_url(url)

    if target:
        dest = Path(target).expanduser().resolve()
    else:
        DEFAULT_REPO_ROOT.mkdir(parents=True, exist_ok=True)
        dest = DEFAULT_REPO_ROOT / repo_name

    if dest.exists():
        if _is_git_repo(str(dest)):
            return (
                f"仓库已存在: {dest}\n\n"
                f"如需重新克隆，请先手动删除该目录，或使用 repo_status 查看当前状态。"
            )
        return f"目标路径已存在但不是 Git 仓库: {dest}\n请指定其他 target 路径。"

    # 构建 clone 命令
    clone_args = ["clone", "--progress"]
    if branch:
        clone_args.extend(["-b", branch])
    clone_args.extend([url, str(dest)])
    r = _git(clone_args, str(DEFAULT_REPO_ROOT if not target else dest.parent))

    if r["success"]:
        # 提取关键信息
        size_info = ""
        try:
            total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
            if total_size > 1024 * 1024:
                size_info = f" (约 {total_size // (1024*1024)} MB)"
        except OSError:
            pass

        branch_info = ""
        b = _git(["rev-parse", "--abbrev-ref", "HEAD"], str(dest))
        if b["success"]:
            branch_info = f", 分支: {b['stdout'].strip()}"

        return (
            f"✅ 克隆成功\n\n"
            f"  仓库: {repo_name}\n"
            f"  路径: {dest}{size_info}{branch_info}\n\n"
            f"{r['stdout'].strip()[-500:] if r['stdout'].strip() else ''}"
        )
    else:
        # 清理失败的克隆
        if dest.exists():
            import shutil
            shutil.rmtree(str(dest), ignore_errors=True)
        return f"❌ 克隆失败:\n{r['stderr'][:1000]}\n\n命令: git {' '.join(clone_args)}"


def repo_list() -> str:
    """列出所有已克隆的仓库。"""
    if not DEFAULT_REPO_ROOT.exists():
        return f"仓库目录不存在: {DEFAULT_REPO_ROOT}\n\n请先用 repo_clone 克隆一个仓库。"

    dirs = [d for d in DEFAULT_REPO_ROOT.iterdir() if d.is_dir()]
    repos = []
    for d in sorted(dirs):
        if _is_git_repo(str(d)):
            repos.append(d)

    if not repos:
        return f"{DEFAULT_REPO_ROOT} 下暂无 Git 仓库。\n\n使用 repo_clone 克隆第一个仓库。"

    lines = [f"已克隆仓库 ({len(repos)} 个)：\n"]
    for repo_path in repos:
        name = repo_path.name
        r = _git(["log", "-1", "--format=%ci | %s"], str(repo_path))
        last_commit = r["stdout"].strip()[:120] if r["success"] else "（无法获取）"
        r2 = _git(["remote", "get-url", "origin"], str(repo_path))
        remote = r2["stdout"].strip()[:80] if r2["success"] else "?"

        lines.append(f"  📁 {name}")
        lines.append(f"     remote: {remote}")
        lines.append(f"     last:   {last_commit}")
        lines.append("")

    return "\n".join(lines)


def repo_status(repo: str) -> str:
    """查看指定仓库的详细信息。"""
    repo_path = _resolve_repo_path(repo)
    if repo_path is None:
        return f"未找到仓库: {repo}\n已尝试路径: {repo}, {DEFAULT_REPO_ROOT / repo}\n\n用 repo_list 查看可用仓库。"

    path_str = str(repo_path)

    # 获取远程 URL
    r = _git(["remote", "-v"], path_str)
    remote = r["stdout"].strip() if r["success"] else "（无远程）"

    # 获取当前分支
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], path_str)
    branch = r["stdout"].strip() if r["success"] else "?"

    # 获取最近 3 条提交
    r = _git(["log", "-3", "--format=%h | %ci | %an | %s"], path_str)
    recent = r["stdout"].strip() if r["success"] else "（无法获取）"

    # 获取工作区状态
    r = _git(["status", "--short"], path_str)
    status = r["stdout"].strip() if r["success"] else ""
    dirty_count = len(status.split("\n")) if status else 0

    # 获取文件统计
    py_files = 0
    total_files = 0
    try:
        for f in repo_path.rglob("*"):
            if f.is_file() and ".git" not in f.parts:
                total_files += 1
                if f.suffix == ".py":
                    py_files += 1
    except OSError:
        pass

    lines = [
        f"仓库: {repo_path.name}",
        f"路径: {repo_path}",
        f"分支: {branch}",
        f"文件: {total_files} 个（{py_files} 个 .py）",
    ]

    if dirty_count > 0:
        lines.append(f"状态: ⚠️ {dirty_count} 个文件有未提交变更")
    else:
        lines.append("状态: ✅ 干净")

    lines.append(f"\n远程:\n{remote}")
    lines.append(f"\n最近提交:\n{recent if recent else '（无法获取）'}")

    if status:
        lines.append(f"\n变更文件:\n{status[:800]}")

    return "\n".join(lines)


# ── 调度 ──

def execute(name: str, args: dict) -> str | None:
    if name == "repo_clone":
        return repo_clone(
            url=args["url"],
            target=args.get("target"),
            branch=args.get("branch"),
        )
    if name == "repo_list":
        return repo_list()
    if name == "repo_status":
        return repo_status(args["repo"])
    return None
