"""
统一 MiniMax API 客户端，所有 LLM 调用都经过这里。
包含指数退避重试，专门处理 5xx（特别是 529）错误。
"""

import os
import time
import requests
import re


def call_minimax(
    prompt: str,
    timeout: int = 120,
    max_tokens: int = 4000,
    retries: int = 3,
    model: str = "MiniMax-M2.7",
) -> str:
    """
    调用 MiniMax M2.7，返回原始文本内容。

    重试策略：指数退避（5s → 15s → 45s），专门针对 5xx 错误加长等待。
    529 (Server Overloaded) 需要更长恢复时间。

    Args:
        prompt: 输入 prompt
        timeout: 请求超时（秒）
        max_tokens: 最大返回 token 数
        retries: 最大重试次数
        model: 模型名称

    Returns:
        str: LLM 返回的文本内容，失败返回空字符串
    """
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        print("  [MiniMax 警告] MINIMAX_API_KEY 未设置")
        return ""

    # 指数退避序列：5s, 15s, 45s（对 529 需要足够长）
    backoff = [5, 15, 45]

    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {minimax_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )

            # 5xx 错误需要特殊处理
            if resp.status_code >= 500:
                server_error_msg = f"服务器错误 {resp.status_code}"
                if attempt < retries:
                    wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]
                    print(f"  [MiniMax {server_error_msg} {'(重试)' if attempt < retries else ''}] "
                          f"等待 {wait}s 后重试 ({attempt + 1}/{retries})", flush=True)
                    time.sleep(wait)
                    continue
                else:
                    print(f"  [MiniMax {server_error_msg}] 重试耗尽，返回空", flush=True)
                    return ""

            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
            else:
                content = ""

            # 去掉 MiniMax 思考块
            content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
            return content

        except requests.exceptions.Timeout:
            if attempt < retries:
                wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]
                print(f"  [MiniMax 超时 (重试)] 等待 {wait}s ({attempt + 1}/{retries})", flush=True)
                time.sleep(wait)
                continue
            print("  [MiniMax 超时] 重试耗尽，返回空", flush=True)
            return ""

        except requests.exceptions.ConnectionError as e:
            if attempt < retries:
                wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]
                print(f"  [MiniMax 连接断开 (重试)] 等待 {wait}s ({attempt + 1}/{retries})", flush=True)
                time.sleep(wait)
                continue
            print(f"  [MiniMax 连接断开] 重试耗尽，返回空: {e}", flush=True)
            return ""

        except Exception as e:
            print(f"  [MiniMax 未知错误] {e}", flush=True)
            return ""

    return ""
