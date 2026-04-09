"""
统一 MiniMax API 客户端，所有 LLM 调用都经过这里。
使用 Session 连接池 + urllib3 传输层重试，大幅降低 SSL 断连概率。
"""

import json
import os
import time
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ── 全局 Session（连接复用，避免反复 SSL 握手）──────────────────

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """懒初始化全局 Session，带传输层自动重试"""
    global _session
    if _session is not None:
        return _session

    _session = requests.Session()

    # urllib3 传输层重试：专治 SSL EOF / ConnectionReset / 连接中断
    transport_retry = Retry(
        total=3,
        backoff_factor=2,            # 重试间隔: 0s, 2s, 4s
        status_forcelist=[502, 503, 529],
        allowed_methods=["POST"],
        raise_on_status=False,       # 不抛异常，交给上层处理
    )
    adapter = HTTPAdapter(
        max_retries=transport_retry,
        pool_connections=4,
        pool_maxsize=4,
    )
    _session.mount("https://", adapter)
    _session.mount("http://", adapter)
    return _session


def extract_json(text: str) -> str | None:
    """
    从 MiniMax 返回文本中健壮提取 JSON。
    处理：markdown 代码块包裹、前缀非 JSON 文本。
    核心策略：找到所有完整 JSON 对象/数组，返回最后一个（最长）。
    """
    if not text:
        return None
    text = text.strip()
    # 去掉 markdown 代码块
    text = re.sub(r'^```(?:json)?[^\n]*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    if not text:
        return None
    # 尝试直接解析
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # 策略：找到所有完整 JSON 对象/数组，返回最后一个（最长的）
    # 用栈来匹配括号，遇到 complete 的就记录
    results = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ' \t\n\r':
            i += 1
            continue
        if ch == '[':
            # 尝试从 [ 开始解析
            end = _find_json_end(text, i, '[', ']')
            if end is not None:
                candidate = text[i:end+1]
                try:
                    json.loads(candidate)
                    results.append(candidate)
                    i = end + 1
                    continue
                except json.JSONDecodeError:
                    pass
        elif ch == '{':
            end = _find_json_end(text, i, '{', '}')
            if end is not None:
                candidate = text[i:end+1]
                try:
                    json.loads(candidate)
                    results.append(candidate)
                    i = end + 1
                    continue
                except json.JSONDecodeError:
                    pass
        i += 1

    return results[-1] if results else None


def _find_json_end(text: str, start: int, open_char: str, close_char: str) -> int | None:
    """从 open_char 位置开始，找到匹配的 close_char 位置（支持嵌套和字符串）。"""
    depth = 1
    in_str = False
    escape = False
    i = start + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == '\\':
            escape = True
            i += 1
            continue
        if ch == '"':
            in_str = not in_str
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def call_minimax(
    prompt: str,
    timeout: int = 120,
    max_tokens: int = 4000,
    retries: int = 3,
    model: str = "MiniMax-M2.7",
    json_mode: bool = False,
) -> str:
    """
    调用 MiniMax M2.7，返回原始文本内容。

    连接策略：
    - 使用全局 Session 复用 TCP + SSL 连接（避免反复握手）
    - urllib3 底层自动处理 SSL EOF / 502 / 503 / 529
    - 上层再做业务级指数退避重试（5xx / 超时 / 连接断开）

    Args:
        prompt: 输入 prompt
        timeout: 读取超时（秒），连接超时固定 15s
        max_tokens: 最大返回 token 数
        retries: 最大业务级重试次数
        model: 模型名称
        json_mode: 是否强制 JSON 输出（使用 response_format）

    Returns:
        str: LLM 返回的文本内容，失败返回空字符串
    """
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        print("  [MiniMax 警告] MINIMAX_API_KEY 未设置")
        return ""

    session = _get_session()
    backoff = [5, 15, 45]

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    for attempt in range(retries + 1):
        try:
            resp = session.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {minimax_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=(15, timeout),  # (连接超时, 读取超时)
            )

            if resp.status_code == 400 and json_mode:
                # JSON mode 下的 400 说明模型没有严格返回 JSON
                body_copy = body.copy()
                del body_copy["response_format"]
                resp_text = resp.text[:500]
                print(f"  [MiniMax JSON_MODE 400] 退回非 JSON 模式: {resp_text}", flush=True)
                resp = session.post(
                    "https://api.minimax.chat/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {minimax_key}",
                        "Content-Type": "application/json",
                    },
                    json=body_copy,
                    timeout=(15, timeout),
                )

            if resp.status_code >= 500:
                msg = f"服务器错误 {resp.status_code}"
                if attempt < retries:
                    wait = backoff[min(attempt, len(backoff) - 1)]
                    print(f"  [MiniMax {msg} (重试)] 等待 {wait}s ({attempt + 1}/{retries})", flush=True)
                    time.sleep(wait)
                    continue
                print(f"  [MiniMax {msg}] 重试耗尽，返回空", flush=True)
                return ""

            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
            else:
                content = ""

            content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
            return content

        except requests.exceptions.Timeout:
            if attempt < retries:
                wait = backoff[min(attempt, len(backoff) - 1)]
                print(f"  [MiniMax 超时 (重试)] 等待 {wait}s ({attempt + 1}/{retries})", flush=True)
                time.sleep(wait)
                continue
            print("  [MiniMax 超时] 重试耗尽，返回空", flush=True)
            return ""

        except requests.exceptions.ConnectionError as e:
            # SSL EOF 会走这里；先关闭旧连接再重试
            session.close()
            _reset_session()
            if attempt < retries:
                wait = backoff[min(attempt, len(backoff) - 1)]
                print(f"  [MiniMax 连接断开 (重试)] 等待 {wait}s ({attempt + 1}/{retries})", flush=True)
                time.sleep(wait)
                session = _get_session()
                continue
            print(f"  [MiniMax 连接断开] 重试耗尽，返回空: {e}", flush=True)
            return ""

        except Exception as e:
            print(f"  [MiniMax 未知错误] {e}", flush=True)
            return ""

    return ""


def _reset_session():
    """SSL 断连后重建 Session，强制新建连接"""
    global _session
    _session = None
