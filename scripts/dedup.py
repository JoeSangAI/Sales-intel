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

# 使用统一的档案上下文管理
from scripts.profile_context import get_profile, set_profile, get_profile_data_dir


def _seen_urls_path() -> str:
    return os.path.join(get_profile_data_dir(), "seen_urls.json")

def _seen_events_path() -> str:
    return os.path.join(get_profile_data_dir(), "seen_events.json")


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
    5. 验证 URL 格式，无效则返回空字符串
    """
    if not url:
        return url
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(url)
        scheme = "https"
        netloc = parsed.netloc

        # 验证：必须有有效的域名（netloc 不能为空，且不能是纯 IP 或包含危险字符）
        if not netloc or not any(c.isalpha() for c in netloc):
            print(f"  [警告] 无效 URL 域名: {url[:60]}...")
            return ""

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
    except Exception as e:
        print(f"  [警告] URL 解析失败: {url[:60]}... ({e})")
        return ""


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


def get_recent_events_for_brand(brand: str, max_age_days: int = 14) -> list[dict]:
    """获取某品牌近期已推过的事件关键词列表"""
    events = load_seen_events()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    return [
        {"brand": e.get("brand", ""), "event_key": e.get("event_key", "")}
        for e in events
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
    三层去重：
    1. URL 去重（跨日，规范化后比较）
    2. 标题去重（同批次内同品牌）
    3. 事件去重（跨日，基于已推送事件的关键词匹配）
    """
    seen = load_seen_urls()
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

    # 清理过期记录（也做 URL 规范化）
    seen = {_normalize_url(url): ts for url, ts in seen.items() if ts > cutoff}

    # 第1层: URL 去重（使用规范化 URL）
    new_results = []
    url_dup_count = 0
    for r in results:
        url = r.get("url", "")
        normalized = _normalize_url(url)
        if normalized and normalized not in seen:
            new_results.append(r)
            seen[normalized] = now
        else:
            url_dup_count += 1

    save_seen_urls(seen)
    if url_dup_count > 0:
        print(f"  [去重] URL 去重: 过滤 {url_dup_count} 条")

    # 第2层: 标题去重
    before_title = len(new_results)
    new_results = dedup_by_title(new_results)
    title_dup_count = before_title - len(new_results)
    if title_dup_count > 0:
        print(f"  [去重] 标题去重: 过滤 {title_dup_count} 条")

    # 第3层: 事件去重（跨日，基于已推送事件关键词）
    seen_events = load_seen_events()
    if seen_events:
        event_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_event_keys = {
            (e.get("brand", ""), e.get("event_key", ""))
            for e in seen_events
            if e.get("date", "") >= event_cutoff and e.get("event_key")
        }
        if recent_event_keys:
            before_event = len(new_results)
            filtered = []
            for r in new_results:
                brand = r.get("brand", "")
                title_norm = _normalize_title(r.get("title", ""))
                # 检查标题是否包含已推送事件的关键词
                is_seen_event = False
                for ev_brand, ev_key in recent_event_keys:
                    if ev_brand == brand and ev_key and ev_key in title_norm:
                        is_seen_event = True
                        break
                if not is_seen_event:
                    filtered.append(r)
            event_dup_count = before_event - len(filtered)
            if event_dup_count > 0:
                print(f"  [去重] 事件去重: 过滤 {event_dup_count} 条（已推送过的事件）")
            new_results = filtered

    return new_results


# ── 融资公司名归一化（供 dedup 和 report 共用）──────────────────

def normalize_company(name: str) -> str:
    """归一化公司名，用于去重比较。

    从 report.py 提前到 dedup 阶段，解决"参半"/"小阔科技"/"深圳小阔科技"
    被当作不同公司的问题。
    """
    if not name:
        return ""
    # 提取括号内的品牌名(通常是核心品牌)
    bracket_match = re.search(r'[（(]([^）)]+)[）)]', name)
    if bracket_match:
        brand_name = bracket_match.group(1)
        if len(brand_name) >= 2 and not any(x in brand_name for x in ['有限', '股份', '公司']):
            return brand_name.strip()
    # 去掉公司类型后缀
    name = re.sub(r'(股份)?有限公司$', '', name)
    name = re.sub(r'集团$', '', name)
    # 去掉地区前缀
    name = re.sub(r'^(深圳|北京|上海|广州|杭州|成都|武汉)', '', name)
    # 去掉常见后缀
    for suffix in ["机器人", "科技", "智能", "网络", "数字", "系统"]:
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            name = name[:-len(suffix)]
    return name.strip()


def _extract_company_from_title(title: str) -> str:
    """从融资新闻标题中提取公司名。

    常见模式：'参半完成30亿融资'、'小阔科技获B轮融资'
    """
    # 模式1: X完成/获得/获X轮融资
    m = re.search(r'^(.{2,15}?)(完成|获得|获|宣布)', title)
    if m:
        return m.group(1).strip()
    # 模式2: 标题开头到第一个标点
    m = re.search(r'^([^，。！？、：\s]{2,15})', title)
    if m:
        return m.group(1).strip()
    return ""


def dedup_fundraising_by_company(results: list[dict]) -> list[dict]:
    """融资结果按公司名归一化去重，同一公司只保留内容最丰富的一条。"""
    if not results:
        return results

    by_company = {}  # normalized_name -> list of results
    no_company = []

    for r in results:
        # 先从标题提取公司名
        title = r.get("title", "")
        company = _extract_company_from_title(title)
        if not company:
            company = r.get("brand", "").replace("[融资]", "").strip()

        normalized = normalize_company(company)
        if not normalized:
            no_company.append(r)
            continue

        by_company.setdefault(normalized, []).append(r)

    # 每个公司只保留内容最长的一条
    deduped = []
    company_dup_count = 0
    for normalized, items in by_company.items():
        items.sort(key=lambda r: len(r.get("content", "")), reverse=True)
        deduped.append(items[0])
        company_dup_count += len(items) - 1

    if company_dup_count > 0:
        print(f"  [去重] 融资公司名去重: 过滤 {company_dup_count} 条（同一公司不同表述）")

    return deduped + no_company
