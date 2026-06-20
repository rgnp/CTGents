"""统一工具定义助手 — 去掉每个工具 ~25 行 schema dict + execute if-chain 样板。

一个工具 = 一个带类型注解的函数 + `@tk.tool(...)`：
    tk = Toolkit()

    @tk.tool(label="移动文件", params={"src": "现路径", "dst": "目标路径"})
    def move_file(src: str, dst: str) -> str:
        '''移动或重命名文件。'''
        ...

    TOOLS_X = tk.schemas          # _tool_meta 自动发现（TOOLS_* 赋值）
    def execute(name, args):       # 必须是真 def（发现要求 FunctionDef 名为 execute）
        return tk.execute(name, args)

schema 从函数签名（参数名 + 类型注解 → JSON 类型；无默认值 → required）+ docstring 首行
（→ description）派生；每参描述用可选 `params` 补。生成的结构与手写 TOOLS_* 完全等价，
故与现有自动发现/缓存/_tool_meta 派生零摩擦——新模块用它、旧模块原样不动，渐进迁移。

标准化 observation：execute 把工具抛出的异常统一包成 {"error": msg} JSON
（storm/审计本就识别此形），成功返回保持可读字符串（保留 marker）。
"""
from __future__ import annotations

import inspect
import json
import types
import typing
from collections.abc import Callable

# Python 类型 → JSON schema 类型
_JSON_TYPES: dict[type, str] = {
    str: "string", int: "integer", float: "number",
    bool: "boolean", list: "array", dict: "object",
}


def _json_type(annotation: object) -> str:
    """把 Python 类型注解映射成 JSON schema 类型。Optional[X]/X|None 取非 None 基类型。"""
    origin = typing.get_origin(annotation)
    is_union = origin is typing.Union or isinstance(annotation, types.UnionType)
    if is_union:
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        annotation = non_none[0] if non_none else str
    return _JSON_TYPES.get(annotation, "string")  # 未知/无注解 → string（最宽松）


class Toolkit:
    """收集 @tool 装饰的函数，导出 .schemas（TOOLS_* 兼容）+ .execute（分发器）。"""

    def __init__(self) -> None:
        self.schemas: list[dict] = []
        self._fns: dict[str, Callable] = {}

    def tool(
        self,
        *,
        label: str | None = None,
        params: dict[str, str] | None = None,
        parallel_safe: bool = False,
        skip_compress: bool = False,
        dedup_blacklist: bool = False,
        no_dedup: bool = False,
    ) -> Callable[[Callable], Callable]:
        """装饰器：注册一个工具，从签名 + docstring 派生 OpenAI function schema。

        label/parallel_safe/... 写进 _meta（与手写 TOOLS_* 的 _meta 同义）。
        params: {参数名: 描述}，补每参 description（签名给不出文字描述）。
        """
        def deco(fn: Callable) -> Callable:
            name = fn.__name__
            doc = (fn.__doc__ or "").strip()
            description = doc.split("\n", 1)[0].strip() if doc else name
            properties, required = self._derive_params(fn, params)
            meta: dict = {"label": label or name}
            if parallel_safe:
                meta["parallel_safe"] = True
            if skip_compress:
                meta["skip_compress"] = True
            if dedup_blacklist:
                meta["dedup_blacklist"] = True
            if no_dedup:
                meta["no_dedup"] = True
            self.schemas.append({
                "_meta": meta,
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
            self._fns[name] = fn
            return fn

        return deco

    @staticmethod
    def _derive_params(fn: Callable, params: dict[str, str] | None) -> tuple[dict, list]:
        """从函数签名派生 JSON schema 的 properties + required。"""
        sig = inspect.signature(fn)
        try:
            hints = typing.get_type_hints(fn)  # 解析字符串注解(from __future__ annotations)
        except Exception:
            hints = {}
        properties: dict = {}
        required: list[str] = []
        for pname, p in sig.parameters.items():
            if pname == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            prop: dict = {"type": _json_type(hints.get(pname, p.annotation))}
            desc = (params or {}).get(pname)
            if desc:
                prop["description"] = desc
            properties[pname] = prop
            if p.default is inspect.Parameter.empty:
                required.append(pname)
        return properties, required

    def execute(self, name: str, args: dict) -> str | None:
        """分发：找到工具就调用并返回结果；外来名返回 None（派发链契约）。

        标准化：抛出的异常统一包成 {"error": ...} JSON；非字符串返回值 json 序列化。
        """
        fn = self._fns.get(name)
        if fn is None:
            return None
        try:
            sig = inspect.signature(fn)
            kwargs = {k: v for k, v in args.items() if k in sig.parameters}
            result = fn(**kwargs)
        except Exception as e:  # noqa: BLE001  # 统一兜成标准 error observation
            return json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
