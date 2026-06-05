import json
import os
from datetime import datetime

from openai import OpenAI

from .config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, SESSION_DIR


def list_sessions() -> list[str]:
    """列出所有历史会话 ID，按时间倒序。"""
    os.makedirs(SESSION_DIR, exist_ok=True)
    dirs = sorted(
        (d for d in os.listdir(SESSION_DIR) if os.path.isdir(os.path.join(SESSION_DIR, d))),
        reverse=True,
    )
    return dirs


def _session_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, session_id)


def _messages_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "messages.json")


def _summary_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "summary.txt")


def _meta_path(session_id: str) -> str:
    return os.path.join(_session_path(session_id), "meta.json")


def get_session_name(session_id: str) -> str:
    """获取会话名称，未设置则返回会话 ID。"""
    try:
        with open(_meta_path(session_id), encoding="utf-8") as f:
            meta = json.load(f)
            return meta.get("name", session_id)
    except Exception:
        return session_id




def delete_session(session_id: str) -> None:
    """删除指定会话目录。"""
    import shutil
    path = _session_path(session_id)
    if os.path.isdir(path):
        shutil.rmtree(path)


def _sanitize_surrogates(obj):
    """递归替换字符串中的孤立代理字符（U+D800-U+DFFF）。

    Windows 上 subprocess 管道可能因编码不匹配产生代理字符，
    这些字符无法被 UTF-8 编码，会导致 json.dump 失败。
    """
    if isinstance(obj, str):
        return obj.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(obj, dict):
        return {k: _sanitize_surrogates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_surrogates(v) for v in obj]
    return obj


def save_session(messages: list[dict], session_id: str | None = None) -> str:
    """保存会话。不传 session_id 则自动生成。返回 session_id。"""
    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    sess_dir = _session_path(session_id)
    os.makedirs(sess_dir, exist_ok=True)

    # 过滤掉运行时注入的易变消息（环境上下文等）
    persist = [m for m in messages if not m.get("_volatile")]

    with open(_messages_path(session_id), "w", encoding="utf-8") as f:
        json.dump(_sanitize_surrogates(persist), f, ensure_ascii=False, indent=2)

    return session_id


def load_session(session_id: str) -> tuple[list[dict], str]:
    """加载会话，返回 (messages, summary)。"""
    with open(_messages_path(session_id), encoding="utf-8") as f:
        messages = json.load(f)

    summary_path = _summary_path(session_id)
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            summary = f.read()
    else:
        summary = ""

    return messages, summary


def _generate_summary(messages: list[dict]) -> str:
    """让 LLM 提炼会话摘要，关注任务主题和进行中事项。"""
    if not messages:
        return ""

    prompt = (
        "请用 1-2 句话提炼以下对话的核心内容："
        "讨论了什么主题、有哪些进行中的任务或待办。"
        "不要描述用户的身份或个人背景。只输出摘要本身。"
    )

    summary_messages: list[dict] = list(messages)
    summary_messages.append({"role": "user", "content": prompt})
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        from .config import MODEL_FLASH
        response = client.chat.completions.create(
            model=MODEL_FLASH,
            messages=summary_messages,
        )
        return response.choices[0].message.content or ""
    except Exception:
        return ""
