"""诊断模块测试：工具源码分析 + 异常诊断 + 格式化输出。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.diagnostics import (
    DiagnosticResult,
    _analyze_function,
    _analyze_source,
    _diagnose_failure,
    _diagnose_slow,
    _extract_function_body,
    _find_tool_source,
    _suggest_subprocess,
    diagnose_anomalies,
    diagnose_one,
    format_diagnostics,
)

# ═══════════════════════════════════════════════════════════════
# 工具源文件查找
# ═══════════════════════════════════════════════════════════════


def test_find_run_command_source():
    f = _find_tool_source("run_command")
    assert f is not None
    assert f.name == "exec.py"


def test_find_run_python_source():
    f = _find_tool_source("run_python")
    assert f is not None
    assert f.name == "exec.py"


def test_find_read_file_source():
    f = _find_tool_source("read_file")
    assert f is not None
    assert f.name == "file.py"


def test_find_nonexistent_tool():
    f = _find_tool_source("nonexistent_tool_xyz")
    assert f is None


# ═══════════════════════════════════════════════════════════════
# 函数体提取
# ═══════════════════════════════════════════════════════════════


def test_extract_function_body_finds_subprocess():
    source = _find_tool_source("run_command")
    body = _extract_function_body(source.read_text(encoding="utf-8"), "run_command")
    assert body is not None
    assert "subprocess" in body


def test_extract_function_body_not_found():
    body = _extract_function_body("def foo(): pass", "nonexistent")
    assert body is None


# ═══════════════════════════════════════════════════════════════
# 函数级分析 — 关键：不把同文件其他函数的模式误归给工具
# ═══════════════════════════════════════════════════════════════


def test_analyze_function_finds_subprocess_for_run_command():
    """run_command 函数体含 subprocess — 正确检测。"""
    source = _find_tool_source("run_command")
    patterns = _analyze_function(source, "run_command")
    assert "subprocess" in patterns


def test_analyze_function_no_subprocess_for_list_files():
    """list_files 不用 subprocess — 不应误报告。"""
    source = _find_tool_source("list_files")
    patterns = _analyze_function(source, "list_files")
    assert "subprocess" not in patterns


# ═══════════════════════════════════════════════════════════════
# 文件级分析（网络/缓存等合理文件级模式）
# ═══════════════════════════════════════════════════════════════


def test_analyze_source_none_returns_empty():
    patterns = _analyze_source(None)
    assert patterns == set()


def test_analyze_source_missing_file_returns_empty(tmp_path):
    patterns = _analyze_source(tmp_path / "nonexistent.py")
    assert patterns == set()


def test_analyze_source_network_tool():
    source = _find_tool_source("search_web")
    if source is not None:
        patterns = _analyze_source(source)
        assert "network" in patterns


# ═══════════════════════════════════════════════════════════════
# 慢工具诊断
# ═══════════════════════════════════════════════════════════════


def test_diagnose_slow_run_command():
    anomaly = {
        "tool": "run_command",
        "type": "slow",
        "detail": "run_command 本次平均 15786ms，基线中位数 2977ms（5.3x）",
        "severity": "warn",
    }
    d = _diagnose_slow("run_command", anomaly)
    assert d.anomaly_type == "slow"
    assert d.root_pattern == "subprocess_overhead"
    assert d.confidence > 0.8
    assert "src\\tools\\exec.py" in d.affected_files[0] or "src/tools/exec.py" in d.affected_files[0]


def test_diagnose_slow_run_python():
    anomaly = {
        "tool": "run_python",
        "type": "slow",
        "detail": "run_python 本次平均 1515ms，基线中位数 259ms（5.9x）",
        "severity": "warn",
    }
    d = _diagnose_slow("run_python", anomaly)
    assert d.root_pattern == "subprocess_overhead"
    assert "子进程" in d.suggested_action


def test_diagnose_slow_list_files_is_not_subprocess():
    """list_files 不用 subprocess，不应被诊断为 subprocess_overhead。"""
    anomaly = {
        "tool": "list_files",
        "type": "slow",
        "detail": "list_files 本次平均 12ms，基线中位数 4ms（3.1x）",
        "severity": "warn",
    }
    d = _diagnose_slow("list_files", anomaly)
    assert d.root_pattern != "subprocess_overhead"


def test_diagnose_slow_unknown_tool():
    anomaly = {
        "tool": "nonexistent_tool_xyz",
        "type": "slow",
        "detail": "nonexistent_tool 慢了",
        "severity": "warn",
    }
    d = _diagnose_slow("nonexistent_tool_xyz", anomaly)
    assert d.root_pattern == "unknown"
    assert d.confidence < 0.5


# ═══════════════════════════════════════════════════════════════
# 失败工具诊断
# ═══════════════════════════════════════════════════════════════


def test_diagnose_failure():
    anomaly = {
        "tool": "run_command",
        "type": "high_failure",
        "detail": "run_command 本次 5/10 次失败（50%）",
        "severity": "crit",
    }
    d = _diagnose_failure("run_command", anomaly)
    assert d.anomaly_type == "high_failure"
    assert d.root_pattern == "insufficient_data"
    assert d.actionable is True
    assert "traceback" in d.suggested_action.lower()


# ═══════════════════════════════════════════════════════════════
# diagnose_one 分发
# ═══════════════════════════════════════════════════════════════


def test_diagnose_one_dispatches_slow():
    anomaly = {"tool": "run_command", "type": "slow", "detail": "test", "severity": "warn"}
    d = diagnose_one(anomaly)
    assert d.root_pattern == "subprocess_overhead"


def test_diagnose_one_dispatches_failure():
    anomaly = {"tool": "run_command", "type": "high_failure", "detail": "test", "severity": "crit"}
    d = diagnose_one(anomaly)
    assert d.root_pattern == "insufficient_data"


def test_diagnose_one_unknown_type():
    anomaly = {"tool": "run_command", "type": "unknown_type_xyz", "detail": "test"}
    d = diagnose_one(anomaly)
    assert d.root_pattern == "unknown"
    assert d.confidence == 0.0


# ═══════════════════════════════════════════════════════════════
# diagnose_anomalies 批量
# ═══════════════════════════════════════════════════════════════


def test_diagnose_anomalies_empty():
    assert diagnose_anomalies([]) == []


def test_diagnose_anomalies_multiple():
    anomalies = [
        {"tool": "run_command", "type": "slow", "detail": "a", "severity": "warn"},
        {"tool": "run_python", "type": "slow", "detail": "b", "severity": "warn"},
    ]
    results = diagnose_anomalies(anomalies)
    assert len(results) == 2
    assert results[0].tool == "run_command"
    assert results[1].tool == "run_python"


# ═══════════════════════════════════════════════════════════════
# format_diagnostics 格式化
# ═══════════════════════════════════════════════════════════════


def test_format_diagnostics_empty():
    assert format_diagnostics([]) == ""


def test_format_diagnostics_includes_diagnosis():
    anomalies = [
        {"tool": "run_command", "type": "slow", "detail": "run_command 慢了 5x", "severity": "warn"},
    ]
    output = format_diagnostics(anomalies)
    assert "被动进化" in output
    assert "subprocess" in output.lower()
    assert "建议" in output
    assert "如果需要修复" in output


def test_format_diagnostics_unknown_has_suggestion():
    anomalies = [
        {"tool": "nonexistent_tool", "type": "slow", "detail": "test", "severity": "warn"},
    ]
    output = format_diagnostics(anomalies)
    assert "profile" in output.lower() or "Profile" in output


# ═══════════════════════════════════════════════════════════════
# suggest_subprocess
# ═══════════════════════════════════════════════════════════════


def test_suggest_run_command():
    s = _suggest_subprocess("run_command")
    assert "pytest" in s or "ruff" in s or "git" in s
    assert "缓存" in s


def test_suggest_run_python():
    s = _suggest_subprocess("run_python")
    assert "子进程" in s or "import" in s


def test_suggest_unknown_tool():
    s = _suggest_subprocess("unknown_cmd")
    assert "外部进程" in s or "检查" in s


# ═══════════════════════════════════════════════════════════════
# DiagnosticResult dataclass
# ═══════════════════════════════════════════════════════════════

def test_diagnostic_result_defaults():
    d = DiagnosticResult(
        tool="test", anomaly_type="slow", anomaly_detail="test",
        likely_cause="test", root_pattern="test",
    )
    assert d.affected_files == []
    assert d.suggested_action == ""
    assert d.confidence == 0.0
    assert d.actionable is False
