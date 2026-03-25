"""
搜索共享池 - 多人搜索结果复用
支持多档案并行时，相同查询只搜索一次，结果按档案分发。
"""

import os
import json
from datetime import datetime
from typing import Optional

from scripts.search import (
    run_search, run_fundraising_search,
    build_brand_queries, build_industry_queries,
    build_fundraising_queries,
    get_api_key, search_tavily, _is_noise_url,
    _search_toutiao, ZH_NEWS_DOMAINS, ZH_FUNDRAISING_DOMAINS,
)

_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "shared")


def _ensure_shared_dir():
    os.makedirs(_SHARED_DIR, exist_ok=True)


def _shared_cache_path(date_str: str) -> str:
    return os.path.join(_SHARED_DIR, f"search_cache_{date_str}.json")


def collect_all_queries(profiles: list[dict]) -> list[dict]:
    """
    合并所有档案的查询列表，按 query 字符串去重。

    返回: [{query, brand, track_name, priority, type, lang, _profiles_needing}]
    """
    seen = set()
    all_queries = []

    for profile in profiles:
        brand_configs = profile.get("brands", [])
        industry_configs = profile.get("industries", [])
        fundraising_config = profile.get("fundraising", {})

        # 品牌查询
        for brand_cfg in brand_configs:
            for q in build_brand_queries(brand_cfg):
                q_key = f"{q['query']}|{q.get('lang', 'zh')}"
                if q_key not in seen:
                    seen.add(q_key)
                    q["_profiles_needing"] = [profile.get("name", "default")]
                    all_queries.append(q)
                else:
                    # 找到已有的 query，追加 profile
                    for existing in all_queries:
                        if existing["query"] == q["query"]:
                            existing["_profiles_needing"].append(profile.get("name", "default"))
                            break

        # 行业查询（如果开启）
        if profile.get("_include_industry", False):
            for ind_cfg in industry_configs:
                for q in build_industry_queries(ind_cfg):
                    q_key = f"{q['query']}|{q.get('lang', 'zh')}"
                    if q_key not in seen:
                        seen.add(q_key)
                        q["_profiles_needing"] = [profile.get("name", "default")]
                        all_queries.append(q)
                    else:
                        for existing in all_queries:
                            if existing["query"] == q["query"]:
                                existing["_profiles_needing"].append(profile.get("name", "default"))
                                break

        # 融资查询
        for q in build_fundraising_queries(fundraising_config):
            q_key = f"{q['query']}|zh"
            if q_key not in seen:
                seen.add(q_key)
                q["_profiles_needing"] = [profile.get("name", "default")]
                all_queries.append(q)
            else:
                for existing in all_queries:
                    if existing["query"] == q["query"]:
                        existing["_profiles_needing"].append(profile.get("name", "default"))
                        break

    return all_queries


def execute_shared_search(queries: list[dict], date_str: str = None) -> dict:
    """
    执行所有唯一查询，Tavily 每条只搜一次。
    结果缓存到 data/shared/search_cache_{date}.json。

    返回: {query_key: [results], query_key: [results], ...}
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    _ensure_shared_dir()
    cache_path = _shared_cache_path(date_str)

    # 尝试加载缓存（今天跑过的直接用）
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == date_str:
                print(f"  [搜索池] 加载缓存: {len(cached.get('results', {}))} 条查询")
                return cached.get("results", {})
        except Exception:
            pass

    api_key = get_api_key()
    pool = {}  # {query_key: [results]}

    print(f"  [搜索池] 开始执行 {len(queries)} 条唯一查询...")
    for q in queries:
        q_key = f"{q['query']}|{q.get('lang', 'zh')}"
        if q_key in pool:
            continue

        try:
            qtype = q.get("type", "")
            # brand_wechat 查询走今日头条（可获取真实文章URL）
            if qtype == "brand_wechat":
                results = _search_toutiao(query=q["query"], max_results=5)
            else:
                results = search_tavily(
                    query=q["query"],
                    api_key=api_key,
                    topic="news",
                    time_range="week",
                    search_depth="basic",
                    max_results=5,
                )
            formatted = []
            for r in results:
                url = r.get("url", "")
                if _is_noise_url(url):
                    continue
                # 品牌查询强制要求高质量域名（中文新闻源白名单）
                if qtype in ("brand_main", "brand_biz", "sub_brand", "brand_en"):
                    if not any(d in url for d in ZH_NEWS_DOMAINS):
                        continue
                # 融资查询使用融资专用白名单
                if qtype in ("fundraising_amount", "fundraising_news", "fundraising_detail"):
                    if not any(d in url for d in ZH_FUNDRAISING_DOMAINS):
                        continue
                item = {
                    "brand": q["brand"],
                    "brand_names": q.get("brand_names", [q["brand"]]),
                    "query": q["query"],
                    "query_type": q.get("type", ""),
                    "title": r.get("title", ""),
                    "url": url,
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                    "published_date": r.get("published_date", ""),
                    "track_name": q.get("track_name", ""),
                    "priority": q.get("priority", 5),
                    "lang": q.get("lang", "zh"),
                }
                formatted.append(item)
            pool[q_key] = formatted
        except Exception as e:
            print(f"  [搜索失败] {q['query']}: {e}")
            pool[q_key] = []

    # 保存缓存
    cache_data = {"date": date_str, "results": pool}
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

    print(f"  [搜索池] 完成 {len(pool)} 条查询，结果 {sum(len(v) for v in pool.values())} 条")
    return pool


def distribute_results(pool: dict, profile: dict) -> list[dict]:
    """
    将搜索池结果分发给指定档案。

    逻辑：保留所有结果，但只分发该档案关心的品牌/赛道。
    每个结果的 brand/track_name 标签保持不变（由搜索时的 profile 决定）。
    """
    results = []
    profile_name = profile.get("name", "default")

    for q_key, items in pool.items():
        # 检查这个 query 是否是这个 profile 需要的
        # 需要从原始 query 重建来判断
        query_str = q_key.split("|")[0]

        # 简单判断：profile 关心哪些品牌/赛道
        # 只要有结果就分发（因为 profile 已经在 collect 阶段标记了 _profiles_needing）
        # 实际按 brand_names 过滤更精准
        for item in items:
            brand = item.get("brand", "")
            track = item.get("track_name", "")

            # 融资结果：按赛道过滤
            if brand.startswith("[融资]"):
                if track:
                    track_configs = profile.get("fundraising", {}).get("tracks", [])
                    track_names = [t.get("name", "") for t in track_configs]
                    if track in track_names or not track_names:
                        results.append(item)
                continue

            # 品牌结果：按品牌名过滤
            brand_names_in_profile = [b.get("name", "") for b in profile.get("brands", [])]
            # 判断 item 的 brand 是否在 profile 关注范围内
            # 品牌查询结果的 brand 就是查询时的品牌，匹配即可
            item_brand = brand
            if item_brand.startswith("[行业]"):
                # 行业结果
                industry_configs = profile.get("industries", [])
                ind_names = [i.get("name", "") for i in industry_configs]
                ind_tag = item_brand.replace("[行业]", "")
                if ind_names and ind_tag in ind_names:
                    results.append(item)
            elif any(b in item_brand or item_brand in b for b in brand_names_in_profile):
                results.append(item)

    return results


def collect_single_profile_queries(profile: dict, include_industry: bool = None) -> list[dict]:
    """
    单档案查询收集（用于 --profile 单独运行，不走共享池）。
    等同于把 run_search 的查询构建逻辑抽出来。
    """
    from scripts.search import is_weekly_industry_day
    if include_industry is None:
        include_industry = is_weekly_industry_day()

    queries = []

    # 品牌查询
    for brand_cfg in profile.get("brands", []):
        queries.extend(build_brand_queries(brand_cfg))

    # 行业查询
    if include_industry:
        for ind_cfg in profile.get("industries", []):
            queries.extend(build_industry_queries(ind_cfg))

    # 融资查询
    queries.extend(build_fundraising_queries(profile.get("fundraising", {})))

    return queries
