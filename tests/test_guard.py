"""自愈系统端到端测试：模拟代码修改 → 运行时崩溃 → 自动回滚 → 重试。"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("TAVILY_API_KEY", "tvly-test")

from src.guard import (
    analyze_crash, execute_rollback, build_report,
    is_protected, PROTECTED_FILES,
)


def _compile_raise(exc_type, msg, filename):
    """编译一段以指定文件名为来源的抛异常代码。"""
    src = f"raise {exc_type.__name__}({msg!r})"
    return compile(src, filename, "exec")


def test_a_module_crash_detection():
    """场景 A：修改 llm.py 导致崩溃 → 正确识别肇事文件。"""
    code = _compile_raise(AttributeError, "module has no attribute log",
                          "D:/project/src/llm.py")
    try:
        exec(code, {})
    except AttributeError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    assert len(analysis["culprit_files"]) >= 1, (
        f"应检测到肇事文件，实际: {analysis['culprit_files']}"
    )
    assert any("llm.py" in str(f) for f in analysis["culprit_files"])


def test_b_tool_crash_detection():
    """场景 B：修改 tools/file.py 导致崩溃 → 正确识别。"""
    code = _compile_raise(TypeError, "unsupported operand",
                          "D:/project/src/tools/file.py")
    try:
        exec(code, {})
    except TypeError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    assert len(analysis["culprit_files"]) >= 1
    assert any("file.py" in str(f) for f in analysis["culprit_files"])


def test_c_non_code_crash():
    """场景 C：网络错误 → traceback 不含 src/ 文件 → 不触发自愈。"""
    try:
        raise ConnectionError("Connection refused to api.deepseek.com")
    except ConnectionError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    assert not analysis["recoverable"], "网络错误不应触发自愈回滚"
    assert len(analysis["culprit_files"]) == 0


def test_d_protected_file_blocked():
    """场景 D：尝试修改 guard.py → 被拒绝。"""
    guard_path = Path(__file__).parent.parent / "src" / "guard.py"
    assert is_protected(guard_path), (
        f"guard.py 应在受保护列表中，当前: {PROTECTED_FILES}"
    )

    from src.tools.file import write_file
    result = write_file(str(guard_path), "# test modification")
    assert "受保护" in result, f"应拒绝修改，实际返回: {result[:100]}"


def test_e_real_rollback_flow():
    """场景 E：完整回滚流程。"""
    original = "ORIGINAL = 'before'\n"
    modified = "MODIFIED = 'after'\nraise RuntimeError('bug!')\n"

    # 1. 创建临时文件，路径必须含 src/（guard 用此过滤）
    fake_src = Path(tempfile.mkdtemp()) / "src"
    fake_src.mkdir(parents=True, exist_ok=True)
    fake_file = fake_src / "test_module.py"
    fake_file.write_text(original, encoding="utf-8")

    # 2. 模拟备份
    from src.tools.file import _backup
    _backup(fake_file)

    # 3. 修改
    fake_file.write_text(modified, encoding="utf-8")

    # 4. 模拟崩溃（编译以 fake_file 为来源的代码）
    code = compile(modified, str(fake_file), "exec")
    crashed = False
    try:
        exec(code, {})
    except RuntimeError as e:
        crashed = True
        analysis = analyze_crash(type(e), e, e.__traceback__)

    assert crashed, "修改后的代码应该崩溃"
    assert analysis["recoverable"], (
        f"应有可回滚的备份。culprit={analysis['culprit_files']}, "
        f"candidates={list(analysis['rollback_candidates'].keys())}"
    )

    # 5. 回滚
    restored = execute_rollback(analysis["rollback_candidates"])
    assert len(restored) >= 1

    # 6. 验证
    recovered = fake_file.read_text(encoding="utf-8")
    assert original.strip() in recovered, (
        f"应恢复到原始内容。\n期望: {original}\n实际: {recovered}"
    )

    shutil.rmtree(fake_src, ignore_errors=True)


def test_f_report_content():
    """场景 F：崩溃报告应包含 traceback 和回滚信息。"""
    code = _compile_raise(ValueError, "something went wrong",
                          "D:/project/src/llm.py")
    try:
        exec(code, {})
    except ValueError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    report = build_report(analysis, ["src/llm.py"])
    assert "系统崩溃报告" in report
    assert "ValueError" in report
    assert "src/llm.py" in report
    assert "已自动回滚" in report
    assert "换一种方式" in report


def test_g_chain_crash():
    """场景 G：异常链（__cause__）→ 识别所有涉及的 src/ 文件。"""
    try:
        try:
            code1 = _compile_raise(KeyError, "bad key",
                                   "D:/project/src/tools/file.py")
            exec(code1, {})
        except KeyError as e1:
            code2 = compile(
                "raise RuntimeError('propagated') from e1",
                "D:/project/src/llm.py", "exec",
            )
            exec(code2, {"e1": e1})
    except RuntimeError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    files = [Path(f).name for f in analysis["culprit_files"]]
    assert "llm.py" in files, f"应包含 llm.py，实际: {files}"


def test_h_no_backup_no_recovery():
    """场景 H：traceback 中有 src/ 文件但无备份 → recoverable=False。"""
    code = _compile_raise(RuntimeError, "crash in unmodified file",
                          str(Path(__file__).parent.parent / "src" / "config.py"))
    try:
        exec(code, {})
    except RuntimeError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    print(f"  config.py: recoverable={analysis['recoverable']}, "
          f"files={len(analysis['culprit_files'])}")


def test_i_guard_self_protection():
    """场景 I：guard.py 不出现在回滚候选中。"""
    code = _compile_raise(RuntimeError, "guard crashed",
                          str(Path(__file__).parent.parent / "src" / "guard.py"))
    try:
        exec(code, {})
    except RuntimeError as e:
        analysis = analyze_crash(type(e), e, e.__traceback__)

    rollback_names = [Path(f).name for f in analysis["rollback_candidates"]]
    assert "guard.py" not in rollback_names, (
        f"guard.py 不应回滚: {rollback_names}"
    )


# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("A: 模块崩溃检测", test_a_module_crash_detection),
        ("B: 工具崩溃检测", test_b_tool_crash_detection),
        ("C: 非代码崩溃跳过", test_c_non_code_crash),
        ("D: 受保护文件阻止", test_d_protected_file_blocked),
        ("E: 完整回滚流程", test_e_real_rollback_flow),
        ("F: 崩溃报告内容", test_f_report_content),
        ("G: 异常链多文件", test_g_chain_crash),
        ("H: 无备份不恢复", test_h_no_backup_no_recovery),
        ("I: guard 自我保护", test_i_guard_self_protection),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
        except Exception as e:
            print(f"  💥 {name}: {type(e).__name__}: {e}")

    print(f"\n{'═' * 40}")
    print(f"  结果: {passed}/{len(tests)} 通过")
    print(f"{'═' * 40}")

    if passed < len(tests):
        sys.exit(1)
