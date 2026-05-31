"""目标驱动长任务：/goal 命令的核心引擎。

设计原则：
- 分层存储：信息类工具结果持久化到 knowledge（不被窗口淘汰），操作类走滑动窗口
- 紧凑 JSON 状态，最小化 token 消耗（GoalState.to_context()）
- 3 层错误恢复（重试工具 → LLM 换方案 → 暂停等用户）
- 代码修改后自动 importlib.reload
- 历史滑动窗口（最近 6 步完整 + 其余压缩摘要）
- 每步只让 LLM 输出一行 JSON，禁止发散
"""

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

logger = logging.getLogger(__name__)

# ── 常量 ──
MAX_ITERATIONS = 50             # 最多迭代次数
WINDOW_SIZE = 6                 # 保留完整历史的步数（从 3 增大）
MAX_CONSECUTIVE_ERRORS = 3      # 连续失败上限
KNOWLEDGE_MAX_CHARS = 12000     # knowledge 总字符上限
KNOWLEDGE_MAX_ENTRIES = 10      # knowledge 最多条目数

# ── 信息类工具（结果应持久化保留，不被窗口淘汰） ──
INFO_TOOLS: set[str] = {
    "read_file", "read_file_lines", "scan_project", "git_status",
    "git_diff", "git_log", "list_files", "rag_query", "grep_code",
    "read_page", "count_lines", "discover", "rag_status",
    "git_branch", "check_project",
}

# ═══════════════════════════════════════════════════════════════
# GoalState — 紧凑 JSON 状态，贯穿整个任务
# ═══════════════════════════════════════════════════════════════

@dataclass
class GoalState:
    """目标状态。信息类工具结果存入 knowledge（持久化），操作类走滑动窗口。"""

    description: str = ""                    # 用户原始目标
    plan: list[str] = field(default_factory=list)      # 分步计划
    done: list[str] = field(default_factory=list)      # 已完成步骤
    current_step: str = ""                   # 当前正在执行
    summary: str = ""                        # 旧步骤压缩摘要（不含最近 WINDOW_SIZE 步）
    history: list[dict] = field(default_factory=list)  # 最近 WINDOW_SIZE 步
    errors: list[str] = field(default_factory=list)    # 错误记录
    iterations: int = 0                      # 已迭代次数
    finished: bool = False                   # 是否完成
    # ── 知识缓存：信息类工具结果持久化，不被窗口淘汰 ──
    knowledge: dict[str, str] = field(default_factory=dict)   # key → result
    _knowledge_keys: list[str] = field(default_factory=list)  # FIFO 顺序

    # ── 发往 LLM 的紧凑上下文 ──

    def to_context(self) -> str:
        """生成紧凑上下文，含知识缓存（信息类工具结果持久化）。"""
        parts = [f"## 目标\n{self.description}"]

        if self.plan:
            plan_lines = []
            for p in self.plan:
                if p in self.done:
                    marker = "✓"
                elif p == self.current_step:
                    marker = "→"
                else:
                    marker = " "
                plan_lines.append(f"  [{marker}] {p}")
            parts.append("## 计划\n" + "\n".join(plan_lines))

        # ── 知识缓存：信息类工具结果（不参与窗口淘汰） ──
        if self.knowledge:
            kparts = ["## 已获取的项目信息（可复用，无需重复查询）"]
            # 最近条目优先
            for key in reversed(self._knowledge_keys[-KNOWLEDGE_MAX_ENTRIES:]):
                val = self.knowledge.get(key, "")
                if not val:
                    continue
                if len(val) > 3000:
                    display = val[:3000] + f"\n...(共 {len(val)} 字符，已截断)"
                else:
                    display = val
                kparts.append(f"### {key}\n{display}")
            # 总大小控制
            total = sum(len(v) for v in kparts)
            if total > KNOWLEDGE_MAX_CHARS:
                # 从旧到新裁剪条目，直到总大小在限制内
                kparts = ["## 已获取的项目信息（可复用，无需重复查询）"]
                remaining = KNOWLEDGE_MAX_CHARS - len(kparts[0])
                for key in reversed(self._knowledge_keys[-KNOWLEDGE_MAX_ENTRIES:]):
                    val = self.knowledge.get(key, "")
                    if not val:
                        continue
                    entry = f"### {key}\n{val[:3000]}"
                    if len(entry) > remaining:
                        entry = entry[:remaining] + "\n...(截断)"
                        kparts.append(entry)
                        break
                    kparts.append(entry)
                    remaining -= len(entry)
            parts.append("\n".join(kparts))

        if self.summary:
            parts.append(f"## 历史摘要\n{self.summary}")

        if self.history:
            parts.append("## 最近步骤")
            for h in self.history:
                parts.append(f"  Step {h['step']}: {h['action']}")
                result = h.get("result", "")
                if result:
                    if len(result) > 800:
                        result = result[:800] + f"...(共{len(h['result'])}字符)"
                    parts.append(f"    结果: {result}")
                if h.get("error"):
                    parts.append(f"    错误: {h['error']}")

        if self.errors:
            recent = self.errors[-3:]
            parts.append("## 错误记录\n" + "\n".join(f"  - {e}" for e in recent))

        ce = _consecutive_errors(self.errors)
        parts.append(
            f"\n## 状态\n"
            f"- 已完成: {len(self.done)} 步, 总迭代: {self.iterations}, 连续错误: {ce}"
            f", 知识条目: {len(self.knowledge)}"
        )

        return "\n".join(parts)

    # ── 更新方法 ──

    def add_history(self, step_num: int, action: str, tool_name: str = "",
                    result: str = "", error: str = ""):
        """追加一条历史。信息类工具→知识缓存；操作类→滑动窗口。"""
        is_info = tool_name in INFO_TOOLS

        # ── 信息类工具：结果持久化到 knowledge ──
        if is_info and result:
            self._add_knowledge(action, result)

        # ── 历史条目：信息类只记调用，操作类记结果 ──
        entry = {
            "step": step_num,
            "action": action[:200],
            "result": "" if is_info else result[:2000],
            "error": error[:300],
        }
        self.history.append(entry)
        if len(self.history) > WINDOW_SIZE:
            old = self.history.pop(0)
            self.summary += (
                f"Step{old['step']}: {old['action']}"
                + (f" [错误:{old['error'][:80]}]" if old.get("error") else "")
                + "\n"
            )

    def _add_knowledge(self, key: str, result: str):
        """存储信息类工具结果到知识缓存。同名 key 覆盖，FIFO 淘汰。"""
        # 同名覆盖（如 read_file 同一文件多次）
        if key in self.knowledge:
            self._knowledge_keys.remove(key)
        self.knowledge[key] = result
        self._knowledge_keys.append(key)
        # 超过条目上限 → 淘汰最旧
        while len(self._knowledge_keys) > KNOWLEDGE_MAX_ENTRIES * 2:
            old_key = self._knowledge_keys.pop(0)
            self.knowledge.pop(old_key, None)

    def mark_done(self, step: str):
        if step and step not in self.done:
            self.done.append(step)

    def add_error(self, tag: str, msg: str):
        self.errors.append(f"[{tag}] {msg}")


def _consecutive_errors(errors: list[str]) -> int:
    """从末尾往前数连续 tag 为 ERROR/WARN 的条目数。"""
    count = 0
    for e in reversed(errors):
        if e.startswith("[ERROR]") or e.startswith("[WARN]"):
            count += 1
        else:
            break
    return count


# ═══════════════════════════════════════════════════════════════
# 自动热加载
# ═══════════════════════════════════════════════════════════════

def _smart_reload(filepath: str) -> str | None:
    """文件路径是 Python 源码 → importlib.reload。返回 reload 信息或 None。"""
    if not filepath.endswith(".py"):
        return None

    # 路径 → 模块名
    normalized = filepath.replace("\\", "/").rstrip(".py")
    # 去掉项目根前缀，转为点分隔模块路径
    if "src/" in normalized:
        mod_part = normalized[normalized.index("src/") + 4:]
    elif "src\\" in normalized:
        mod_part = normalized[normalized.index("src\\") + 4:]
    else:
        return None
    mod_path = "src." + mod_part.replace("/", ".")

    if mod_path in sys.modules:
        try:
            import importlib
            importlib.reload(sys.modules[mod_path])
            return f"🔄 已热加载: {mod_path}"
        except Exception as e:
            logger.debug("reload 跳过 %s: %s", mod_path, e)
            return None
    return None


def _full_reload() -> str:
    """热加载所有已加载的 src.* 模块 + 重新初始化工具注册表。"""
    import importlib
    reloaded = []
    errors = []

    # 先收集所有 src.* 模块（避免迭代中修改 dict）
    targets = sorted(
        k for k in sys.modules
        if k.startswith("src.") and sys.modules[k] is not None
    )
    for mod_name in targets:
        try:
            importlib.reload(sys.modules[mod_name])
            reloaded.append(mod_name)
        except Exception as e:
            errors.append(f"{mod_name}: {e}")

    # 重新初始化工具注册表
    try:
        from .tools import _init_registry
        _init_registry()
    except Exception as e:
        errors.append(f"tools._init_registry: {e}")

    summary = f"已热加载 {len(reloaded)} 个模块"
    if errors:
        summary += f"\n  ⚠️ {len(errors)} 个失败: " + "; ".join(errors[:3])
    return summary


# ═══════════════════════════════════════════════════════════════
# JSON 解析
# ═══════════════════════════════════════════════════════════════

def _parse_action(content: str | None) -> dict | None:
    """从 LLM 输出中提取 JSON action。处理三种情况：裸 JSON、```json 块、首尾花括号。"""
    if not content:
        return None

    text = content.strip()

    # 1) 直接 JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2) ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 3) 第一个 { 到最后一个 }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ═══════════════════════════════════════════════════════════════
# GoalRunner
# ═══════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """你是一个自动任务执行 Agent，在 /goal 模式下自主完成用户指定的目标。

{context}

## 输出方式

### 执行工具
可用工具已在系统中注册，直接用 function calling 调用即可。每步只调一个工具。

### 停止（没有工具调用）
如果目标完成或无法继续，在 content 中输出 JSON（不要 function calling）：
{{"action":"done","summary":"简述"}}
或
{{"action":"fail","reason":"原因"}}

### content 中的元信息
每次回复的 content 开头输出一行 JSON 元信息（不要 markdown 代码块）：
{{"reasoning":"为什么（一句话）","plan":["步骤1","步骤2"],"current_step":"当前步骤","mark_done":"完成的步骤"}}

然后跟工具调用或推理过程。

## 规则
- 写完代码立即测试；测试通过立即 git commit
- 错误先分析、换方案，同一工具连续失败则换方法
- 上下文中的「已获取的项目信息」是之前 read_file/scan_project 等的结果，已持久化保留
- 需要项目结构或文件内容时，先检查「已获取的项目信息」是否已有，已有则直接复用，不要重复查询
- 只有确认文件已被修改（write_file/edit_file_lines 后）才需要重新读取该文件"""


class GoalRunner:
    """目标驱动长任务执行器。

    用法:
        runner = GoalRunner("实现用户登录功能", on_output=print)
        success = runner.run()
    """

    def __init__(self, description: str, on_output=None):
        self.state = GoalState(description=description)
        self._out = on_output or (lambda msg, end="": None)
        self._interrupted = False

    def run(self) -> bool:
        """执行目标，返回 True 表示成功完成。"""
        from .llm import _invoke_llm, auto_select_model, clear_interrupt, is_interrupt_requested

        self._say(f"\n{'─' * 50}")
        self._say(f"🎯 目标: {self.state.description}")
        self._say(f"{'─' * 50}\n")

        t0 = time.time()
        backend = auto_select_model(self.state.description)
        self._say(f"🤖 模型: {backend.info.name}\n")

        for it in range(1, MAX_ITERATIONS + 1):
            self.state.iterations = it

            # ── 中断检查 ──
            if is_interrupt_requested():
                clear_interrupt()
                self._say("\n⏹️ 用户中断\n")
                return False

            # ── 连续错误过多 → 退出 ──
            if _consecutive_errors(self.state.errors) >= MAX_CONSECUTIVE_ERRORS:
                self._say(f"\n❌ 连续 {MAX_CONSECUTIVE_ERRORS} 次错误，退出\n")
                return False

            # ── 构建消息 + 调用 LLM ──
            messages = self._build_messages()
            self._say(f"[{it}/{MAX_ITERATIONS}] ", end="")

            try:
                content, tool_calls = _invoke_llm(backend, messages, lambda _: None)
            except Exception as e:
                self._handle_error(it, f"LLM调用失败: {e}")
                continue

            # ── 解析 LLM 输出 ──
            # 两种情况：
            # 1) LLM 走了 function calling → tool_calls 不为空，直接提取 action
            # 2) LLM 输出 JSON 文本 → tool_calls 为空，解析 content
            action = None
            if tool_calls:
                action = self._tool_call_to_action(tool_calls, content)
            if not action:
                action = _parse_action(content)
            if not action:
                self._handle_error(it, f"无法解析输出: {str(content)[:200]}")
                continue

            action_type = action.get("action", "")
            reasoning = action.get("reasoning", "")

            # ── 更新计划/当前步骤 ──
            if action.get("plan"):
                self.state.plan = action["plan"]
            if action.get("current_step"):
                self.state.current_step = action["current_step"]

            # ── 完成 / 失败 ──
            if action_type == "done":
                summary = action.get("summary", "完成")
                self._say(f"✅ {summary}\n")
                self.state.finished = True
                break

            if action_type == "fail":
                reason = action.get("reason", "未知原因")
                self._say(f"❌ {reason}\n")
                self.state.add_error("ERROR", reason)
                return False

            # ── 工具调用 ──
            if action_type == "tool_call":
                tool_name = action.get("tool", "")
                tool_args = action.get("args", {})
            else:
                self._handle_error(it, f"未知action类型: {action_type}")
                continue

            if not tool_name:
                self._handle_error(it, "缺少工具名")
                continue

            self._say(f"{tool_name} ", end="")
            if reasoning:
                self._say(f"— {reasoning[:80]}", end="")
            self._say("")

            # 执行
            result, error = self._exec(tool_name, tool_args)

            # 记录历史
            self.state.add_history(it, f"{tool_name}({args_str})", tool_name, result, error)

            if action.get("mark_done"):
                self.state.mark_done(action["mark_done"])

            # ── 自动热加载 ──
            if not error and tool_name in ("write_file", "edit_file_lines"):
                filepath = tool_args.get("path", "") or tool_args.get("filepath", "")
                if "src/" in filepath or "src\\" in filepath:
                    reload_msg = _smart_reload(filepath)
                    if reload_msg:
                        self._say(f"  {reload_msg}\n")
        # ── 结束 ──
        elapsed = time.time() - t0
        if self.state.finished:
            self._say(f"\n✅ 目标完成 — {self.state.iterations}步, {elapsed:.0f}s\n")
            return True
        else:
            self._say(f"\n⚠️ 达到最大迭代({MAX_ITERATIONS})或提前终止\n")
            return False

    # ── 内部 ──

    def _build_messages(self) -> list[dict]:
        ctx_text = self.state.to_context()
        system = _SYSTEM_PROMPT.format(context=ctx_text)
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": f"执行目标：{self.state.description}"},
        ]

    def _tool_call_to_action(self, tool_calls: list[dict], content: str | None) -> dict | None:
        """将 LLM function calling 的 tool_calls + content 元信息转为 action dict。"""
        if not tool_calls:
            return None

        # 从 content 解析元信息（首行 JSON）
        meta: dict = {}
        if content:
            first_line = content.strip().split("\n")[0]
            try:
                meta = json.loads(first_line)
            except (json.JSONDecodeError, ValueError):
                pass

        tc = tool_calls[0]  # 每步只调一个工具
        func = tc.get("function", {})
        name = func.get("name", "")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, ValueError):
            args = {}

        return {
            "action": "tool_call",
            "tool": name,
            "args": args,
            "reasoning": meta.get("reasoning", content or ""),
            "plan": meta.get("plan", []),
            "current_step": meta.get("current_step", ""),
            "mark_done": meta.get("mark_done", ""),
        }


    def _exec(self, tool_name: str, tool_args: dict) -> tuple[str, str]:
        """执行工具，返回 (result_str, error_str)。error 为空表示成功。"""
        from .tools import execute_tool

        tc = SimpleNamespace(
            function=SimpleNamespace(
                name=tool_name,
                arguments=json.dumps(tool_args, ensure_ascii=False),
            )
        )
        try:
            return execute_tool(tc), ""
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self.state.add_error("ERROR", f"{tool_name}: {err}")
            return "", err

    def _handle_error(self, iteration: int, msg: str):
        self._say(f"  ⚠️ {msg}\n")
        self.state.add_error("WARN", f"Step{iteration}: {msg}")

    def _say(self, msg: str, end: str = "\n"):
        self._out(msg, end=end)
