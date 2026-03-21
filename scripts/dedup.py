"""
去重机制
1. URL 去重：维护已推送 URL 列表，防止跨日重复推送
2. 标题去重：同一品牌下，标题高度相似的多条报道只保留第一条
3. 事件去重：维护已推送事件摘要，供 AI 判断跨日跟进报道
"""

import json
import os
import re
from datetime import datetime, timedelta


# 支持 profile 数据目录隔离
_BASE_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_profile_name = None

def _data_dir() -> str:
    """当前数据目录路径"""
    if _profile_name:
        return os.path.join(_BASE_DATA_DIR, "profiles", _profile_name)
    return os.path.join(_BASE_DATA_DIR, "profiles", "default")

def _seen_urls_path() -> str:
    return os.path.join(_data_dir(), "seen_urls.json")

def _seen_events_path() -> str:
    return os.path.join(_data_dir(), "seen_events.json")


def set_profile(name: str = None):
    """切换到指定 profile 的数据目录（None=默认/default）"""
    global _profile_name
    _profile_name = name
    # 确保目录存在
    os.makedirs(_data_dir(), exist_ok=True)


def get_profile() -> str:
    """获取当前 profile 名"""
    return _profile_name or "default"


def load_seen_urls() -> dict:
    """加载已推送 URL 记录 {url: timestamp}"""
    path = _seen_urls_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen_urls(seen: dict):
    """保存已推送 URL 记录"""
    path = _seen_urls_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def _normalize_url(url: str) -> str:
    """
    URL 规范化：
    1. 强制 https://
    2. 移除 www. 前缀
    3. 移除尾部斜杠
    4. 移除 UTM 参数
    """
    if not url:
        return url
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(url)
    scheme = "https"
    netloc = parsed.netloc

    # 移除 www. 前缀
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # 移除 UTM 参数
    query_params = parse_qs(parsed.query)
    utm_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id"}
    filtered_params = {k: v for k, v in query_params.items() if k not in utm_keys}
    query = "&".join(f"{k}={v[0]}" for k, v in filtered_params.items())

    # 移除尾部斜杠
    path = parsed.path.rstrip("/")

    result = f"{scheme}://{netloc}{path}"
    if query:
        result += f"?{query}"
    if parsed.fragment:
        result += f"#{parsed.fragment}"
    return result


def load_seen_events() -> list[dict]:
    """加载已推送事件记录 [{brand, event_key, date}, ...]"""
    path = _seen_events_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen_events(events: list[dict]):
    """保存已推送事件记录"""
    path = _seen_events_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def get_recent_events_for_brand(brand: str, max_age_days: int = 14) -> list[str]:
    """获取某品牌近期已推过的事件关键词列表"""
    events = load_seen_events()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    return [
        e["event_key"] for e in events
        if e.get("brand") == brand and e.get("date", "") >= cutoff
    ]


def record_pushed_events(pushed_events: list[dict]):
    """记录本次推送的新事件（由 main.py 在报告生成后调用）"""
    events = load_seen_events()
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    # 清理过期
    events = [e for e in events if e.get("date", "") >= cutoff]
    today = datetime.now().strftime("%Y-%m-%d")
    for e in pushed_events:
        events.append({**e, "date": today})
    save_seen_events(events)



def _normalize_title(title: str) -> str:
    """标题归一化：去除标点、空格、来源标注，便于相似度比较"""
    # 去掉常见后缀 "- IT之家", "| 36氪", "_腾讯新闻" 等
    title = re.sub(r'[\s]*[|\-_—–·][\s]*[^\s]+$', '', title)
    # 去掉标点和空格
    title = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019「」【】\s\-_|·]', '', title)
    return title.lower()


def _title_similar(t1: str, t2: str) -> bool:
    """判断两个标题是否高度相似（归一化后包含关系或重合度 > 70%）"""
    n1 = _normalize_title(t1)
    n2 = _normalize_title(t2)
    if not n1 or not n2:
        return False
    # 短标题完全包含在长标题中
    if n1 in n2 or n2 in n1:
        return True
    # 字符重合度
    common = set(n1) & set(n2)
    shorter = min(len(set(n1)), len(set(n2)))
    if shorter > 0 and len(common) / shorter > 0.7:
        return True
    return False


def dedup_by_title(results: list[dict]) -> list[dict]:
    """同一品牌下，标题高度相似的结果只保留第一条"""
    kept = []
    seen_titles_by_brand = {}  # {brand: [title1, title2, ...]}

    for r in results:
        brand = r.get("brand", "")
        title = r.get("title", "")
        if not title:
            kept.append(r)
            continue

        if brand not in seen_titles_by_brand:
            seen_titles_by_brand[brand] = []

        is_dup = False
        for seen_title in seen_titles_by_brand[brand]:
            if _title_similar(title, seen_title):
                is_dup = True
                break

        if not is_dup:
            kept.append(r)
            seen_titles_by_brand[brand].append(title)

    return kept


def deduplicate(results: list[dict], max_age_days: int = 30) -> list[dict]:
    """
    两层去重：
    1. URL 去重（跨日，规范化后比较）
    2. 标题去重（同批次内同品牌）
    """
    seen = load_seen_urls()
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

    # 清理过期记录（也做 URL 规范化）
    seen = {_normalize_url(url): ts for url, ts in seen.items() if ts > cutoff}

    # URL 去重（使用规范化 URL）
    new_results = []
    for r in results:
        url = r.get("url", "")
        normalized = _normalize_url(url)
        if normalized and normalized not in seen:
            new_results.append(r)
            seen[normalized] = now

    save_seen_urls(seen)

    # 标题去重
    new_results = dedup_by_title(new_results)

    return new_results
