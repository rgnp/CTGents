import json
import os
from datetime import datetime

from .config import SESSION_DIR, get_llm_client, DEEPSEEK_MODEL


def list_sessions() -> list[str]:
    """列出所有历史会话 ID，按时间倒序。"""
    if not os.path.isdir(SESSION_DIR):
        return []
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
        with open(_meta_path(session_id), "r", encoding="utf-8") as f:
            meta = json.load(f)
            return meta.get("name", session_id)
    except Exception:
        return session_id


def rename_session(session_id: str, name: str) -> None:
    """重命名会话。"""
    meta = {}
    meta_path = _meta_path(session_id)
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    meta["name"] = name
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def save_session(messages: list[dict], session_id: str | None = None) -> str:
    """保存会话。不传 session_id 则自动生成。返回 session_id。"""
    if session_id is None:
        session_id = datetime.now().strftime("%Y-%m-%d-%H%M%S")

    sess_dir = _session_path(session_id)
    os.makedirs(sess_dir, exist_ok=True)

    with open(_messages_path(session_id), "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

    summary = _generate_summary(messages)
    with open(_summary_path(session_id), "w", encoding="utf-8") as f:
        f.write(summary)

    return session_id


def load_session(session_id: str) -> tuple[list[dict], str]:
    """加载会话，返回 (messages, summary)。"""
    with open(_messages_path(session_id), "r", encoding="utf-8") as f:
        messages = json.load(f)

    summary_path = _summary_path(session_id)
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = f.read()
    else:
        summary = ""

    return messages, summary


def _generate_summary(messages: list[dict]) -> str:
    """让 LLM 提炼会话摘要，包括用户信息、讨论主题、偏好、进行中任务。"""
    if not messages:
        return ""

    prompt = (
        "请用 3-5 句话提炼以下对话的关键信息，包括："
        "用户是谁、在做什么、偏好什么、有哪些进行中的任务或待办。"
        "只输出摘要本身，不要加任何前缀。"
    )

    summary_messages: list[dict] = list(messages)
    summary_messages.append({"role": "user", "content": prompt})

    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=summary_messages,
        )
        return response.choices[0].message.content or ""
    except Exception:
        return ""
