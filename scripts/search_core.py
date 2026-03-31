"""
搜索核心功能（共享模块）

从 search.py 抽取的共享函数，供 search.py 和 whitelist_crawler.py 使用，
解除循环依赖。
"""

import os
import json
import threading
import requests
from datetime import datetime
from typing import Optional

# ── 搜索结果缓存（内存 + 每日磁盘文件）─────────────────────────
_DISK_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "search_cache")
_disk_cache: dict = {}
_disk_cache_date: str = ""
_disk_lock = threading.Lock()

# ── 环境变量加载 ─────────────────────────────────────────────
_env_loaded = False


def _ensure_env():
    """加载 .env 文件到环境变量"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_ensure_env()

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"


def _ensure_disk_cache() -> dict:
    """确保当天磁盘缓存已加载到内存"""
    global _disk_cache, _disk_cache_date
    with _disk_lock:
        today = datetime.now().strftime("%Y-%m-%d")
        if _disk_cache_date == today:
            return _disk_cache
        _disk_cache_date = today
        path = os.path.join(_DISK_CACHE_DIR, f"bocha_{today}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _disk_cache = json.load(f)
                    print(f"  [缓存] 加载当天搜索缓存: {len(_disk_cache)} 条查询")
            except Exception as e:
                print(f"  [警告] 加载搜索缓存失败: {e}")
                _disk_cache = {}
        else:
            _disk_cache = {}
        return _disk_cache


def _flush_disk_cache() -> None:
    """将内存缓存写回磁盘文件"""
    with _disk_lock:
        os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(_DISK_CACHE_DIR, f"bocha_{today}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_disk_cache, f, ensure_ascii=False)


def _get_cached(query: str, ttl: int = 300) -> Optional[list[dict]]:
    """从缓存获取结果（当天磁盘缓存，不过期）"""
    cache = _ensure_disk_cache()
    if query in cache:
        return cache[query]
    return None


def _set_cached(query: str, results: list[dict]) -> None:
    """写入缓存（内存 + 磁盘）"""
    cache = _ensure_disk_cache()
    cache[query] = results
    _flush_disk_cache()


def get_api_key() -> str:
    """返回 Bocha API key"""
    _ensure_env()
    key = os.environ.get("BOCHA_API_KEY", "")
    if not key:
        raise ValueError("BOCHA_API_KEY 环境变量未设置")
    return key


def _search_bocha(
    query: str,
    api_key: str,
    time_range: str = "day",
    max_results: int = 5,
) -> list[dict]:
    """调用 Bocha API 搜索"""
    # 强制在查询中添加 2026,确保返回最新新闻
    if "2026" not in query:
        query = f"{query} 2026"

    freshness_map = {
        "day": "oneDay",
        "week": "oneWeek",
        "month": "oneMonth",
        "year": "oneYear",
    }
    freshness = freshness_map.get(time_range, "oneWeek")

    payload = {
        "query": query,
        "freshness": freshness,
        "summary": False,
        "count": max_results,
    }

    resp = requests.post(
        BOCHA_ENDPOINT,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    pages = data.get("data", {}).get("webPages", {}).get("value", [])
    for p in pages[:max_results]:
        results.append({
            "title": p.get("name", ""),
            "url": p.get("url", ""),
            "content": p.get("snippet", ""),
            "score": 0.0,
            "published_date": p.get("datePublished", ""),
        })
    return results
