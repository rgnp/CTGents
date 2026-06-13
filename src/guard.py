"""自我修改分级 — 自进化 agent 能改所有文件，但按"炸毁半径"分三层难度。

设计原则（见对话/记忆 ctgents-test-gate-speed）：
  覆盖率门禁那种"用脆弱 measurement 当墙"的老路已拆。难度建立在健壮的二元信号上
  （import 得动吗 / 测试绿吗 / 有还原点吗），不是飘忽的百分比。

三层：
  - 不可变安全核 IMMUTABLE_FILES：强制安全的机制本身。agent 改不了——若能改，它就能
    把安全改成摆设（改 pre-commit 让测试永远 pass、改 gate_audit 让绕门不被发现）。
    这是唯一"墙"正确的地方：不是"难"，是"不行"。
  - 核心业务 CORE_FILES：可改，但走"安全带"——改后 import 冒烟，挂了自动回滚到改前
    （见 file.py _post_write_check）。"困难一点"=更强验证+保证可撤销，不是墙。
  - 其余文件：测试门兜底即可，自由改。

机械拦截在 file.py 的 write/edit/delete 工具里，不靠 LLM 自觉。
"""

from pathlib import Path

_GUARD_FILE = Path(__file__).resolve()
_SRC_DIR = _GUARD_FILE.parent
_PROJECT_ROOT = _SRC_DIR.parent

# ── 不可变安全核：连 agent 都不能改（改了=安全变摆设）──
IMMUTABLE_FILES: frozenset[str] = frozenset({
    str(_GUARD_FILE),                                              # guard.py（分级表本身）
    str(_SRC_DIR / "tool_guard.py"),                              # 工具拦截层
    str(_SRC_DIR / "gate_audit.py"),                              # 门通行证审计
    str(_PROJECT_ROOT / "scripts" / "git-hooks" / "pre-commit"),  # 测试门（提交硬闸）
})

# ── 核心业务：可改但走安全带（改后 import 冒烟 + 挂了回滚）──
CORE_FILES: frozenset[str] = frozenset({
    str(_SRC_DIR / "main.py"),                  # 主循环入口
    str(_SRC_DIR / "commands.py"),              # 指令派发
    str(_SRC_DIR / "validate.py"),              # 验证流水线
    str(_SRC_DIR / "cache_context.py"),         # 缓存上下文
    str(_SRC_DIR / "llm.py"),                   # LLM 调用
    str(_SRC_DIR / "evolve.py"),                # 进化档案
    str(_SRC_DIR / "evolution_runner.py"),      # 进化运行态
    str(_SRC_DIR / "tools" / "__init__.py"),    # 工具注册表
})

# 向后兼容：旧语义"受保护"=硬锁=现在的不可变核（dashboard 风险面板复用此名）。
PROTECTED_FILES: frozenset[str] = IMMUTABLE_FILES


def _resolved(filepath: str | Path) -> str | None:
    try:
        return str(Path(filepath).resolve())
    except (OSError, ValueError):
        return None


def is_immutable(filepath: str | Path) -> bool:
    """不可变安全核：连 agent 都不能改（改了安全=摆设）。"""
    r = _resolved(filepath)
    return r in IMMUTABLE_FILES if r else False


def is_core(filepath: str | Path) -> bool:
    """核心业务文件：可改，但 file.py 会对它走 import 冒烟安全带（挂了回滚）。"""
    r = _resolved(filepath)
    return r in CORE_FILES if r else False


def is_protected(filepath: str | Path) -> bool:
    """向后兼容别名：旧语义"硬锁不可改"=现在的不可变核。"""
    return is_immutable(filepath)
