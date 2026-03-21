"""
Tavily 搜索引擎封装
负责根据品牌配置生成搜索查询，调用 Tavily API，返回结构化结果
"""

import os
import re
import json
import time
import requests
import concurrent.futures
from datetime import datetime, timedelta
from typing import Optional

# ── 搜索结果内存缓存（5分钟 TTL）─────────────────────────────
_search_cache: dict[str, tuple[float, list[dict]]] = {}  # {query: (timestamp, results)}


def _get_cached(query: str, ttl: int = 300) -> Optional[list[dict]]:
    """从缓存获取结果（未过期返回 results，过期或不存在返回 None）"""
    if query in _search_cache:
        ts, results = _search_cache[query]
        if time.time() - ts < ttl:
            return results
    return None


def _set_cached(query: str, results: list[dict]) -> None:
    """写入缓存"""
    _search_cache[query] = (time.time(), results)

# 加载 .env（如果环境变量未设置）
_env_loaded = False
def _ensure_env():
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


TAVILY_ENDPOINT = "https://api.tavily.com/search"
BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"

_BOCHA_API_KEY = os.environ.get("BOCHA_API_KEY", "")


def _get_bocha_key() -> str:
    """获取 Bocha API key（优先环境变量）"""
    _ensure_env()
    return os.environ.get("BOCHA_API_KEY", "")


def is_weekly_industry_day() -> bool:
    """判断今天是否为行业搜索日（周一、周四）"""
    return datetime.now().weekday() in (0, 3)


# ── 融资频率控制（每 3 天）───────────────────────────────────

def _fundraising_state_path() -> str:
    """融资运行日期存储路径（项目根目录，全局共享）"""
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    return os.path.join(base, "fundraising_last_run.json")


def get_last_fundraising_date() -> str:
    """获取上次融资搜索的日期字符串，格式 YYYY-MM-DD"""
    try:
        with open(_fundraising_state_path(), "r") as f:
            data = json.load(f)
        return data.get("last_date", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def set_last_fundraising_date(date_str: str) -> None:
    """保存本次融资搜索日期"""
    path = _fundraising_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"last_date": date_str}, f)


def is_fundraising_day() -> bool:
    """判断今天是否应该运行融资专项搜索（仅周一和周四）"""
    today = datetime.now().weekday()
    return today in (0, 3)  # 0=周一, 3=周四


def get_api_key() -> str:
    """优先返回 Tavily API key（Bocha 作为主引擎时仍需要备用）"""
    _ensure_env()
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        raise ValueError("TAVILY_API_KEY 环境变量未设置")
    return key


def _search_bocha(
    query: str,
    api_key: str,
    time_range: str = "day",
    max_results: int = 5,
) -> list[dict]:
    """调用 Bocha API 搜索"""
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


def _search_tavily(
    query: str,
    api_key: str,
    topic: str = "news",
    time_range: str = "day",
    search_depth: str = "basic",
    max_results: int = 5,
    include_domains: Optional[list] = None,
    exclude_domains: Optional[list] = None,
) -> list[dict]:
    """调用 Tavily API 搜索"""
    payload = {
        "api_key": api_key,
        "query": query,
        "topic": topic,
        "time_range": time_range,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": False,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    resp = requests.post(TAVILY_ENDPOINT, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def search_tavily(
    query: str,
    api_key: str = None,
    topic: str = "news",
    time_range: str = "day",
    search_depth: str = "basic",
    max_results: int = 5,
    include_domains: Optional[list] = None,
    exclude_domains: Optional[list] = None,
) -> list[dict]:
    """
    主搜索函数：Bocha 优先，Tavily fallback。
    api_key 参数兼容旧用法——如果传 Bocha key 则只用 Bocha，
    否则尝试 Bocha（BOCHA_API_KEY），失败则用 Tavily。
    """
    # 如果只传了 api_key 且是 Tavily 格式（兼容旧调用）
    if api_key and not _get_bocha_key():
        return _search_tavily(query, api_key, topic, time_range, search_depth, max_results, include_domains, exclude_domains)

    # 优先 Bocha
    bocha_key = _get_bocha_key()
    if bocha_key:
        try:
            results = _search_bocha(query, bocha_key, time_range, max_results)
            if results:
                print(f"  [Bocha OK] {len(results)} 条", flush=True)
                return results
        except Exception as e:
            print(f"  [Bocha 失败] {e}", flush=True)

    # Fallback Tavily
    tavily_key = api_key or get_api_key()
    try:
        results = _search_tavily(query, tavily_key, topic, time_range, search_depth, max_results, include_domains, exclude_domains)
        print(f"  [Tavily OK] {len(results)} 条", flush=True)
        return results
    except Exception as e:
        print(f"  [Tavily 失败] {e}", flush=True)
        return []


def build_brand_queries(brand_config: dict) -> list[dict]:
    """根据品牌配置生成搜索查询列表

    策略：聚焦品牌本身的动态，不做宽泛的竞品搜索（噪音太大）。
    每个品牌最多 3 条查询，控制 API 消耗。
    """
    queries = []
    name = brand_config["name"]
    sub_brands = brand_config.get("sub_brands", [name])
    keywords = brand_config.get("keywords", [])
    lang = brand_config.get("lang", "zh")
    all_names = list(set(sub_brands + [name]))

    # 查询 1：主品牌 + 商机信号词
    queries.append({
        "query": f'"{name}" 发布会 OR 新品 OR 代言人 OR 融资 OR 广告',
        "brand": name,
        "brand_names": all_names,
        "type": "brand_main",
        "lang": "zh",
    })

    # 查询 2：重要子品牌（如果有不同于主品牌的子品牌）
    important_subs = [sb for sb in sub_brands if sb != name][:2]
    if important_subs:
        sub_str = " OR ".join(f'"{sb}"' for sb in important_subs)
        queries.append({
            "query": f"({sub_str}) 新品 OR 发布 OR 上市",
            "brand": name,
            "brand_names": all_names,
            "type": "sub_brand",
            "lang": "zh",
        })

    # 查询 3：英文查询（仅限标记了 en 的品牌）
    if isinstance(lang, list) and "en" in lang:
        queries.append({
            "query": f'"{name}" new product OR launch OR marketing OR campaign 2026',
            "brand": name,
            "brand_names": all_names,
            "type": "brand_en",
            "lang": "en",
        })

    return queries


def build_industry_queries(industry_config: dict) -> list[dict]:
    """根据行业配置生成搜索查询，每个关键词独立一条查询提高命中率"""
    queries = []
    name = industry_config["name"]
    keywords = industry_config.get("keywords", [])

    # 每个关键词单独一条查询，最多取前4个（控制 API 消耗）
    for kw in keywords[:4]:
        queries.append({
            "query": f"{kw} 2026",
            "brand": f"[行业]{name}",
            "type": "industry",
            "lang": "zh",
        })

    return queries


# 中文新闻源域名白名单（提高中文搜索质量）
ZH_NEWS_DOMAINS = [
    "36kr.com", "jiemian.com", "thepaper.cn", "sina.com.cn",
    "163.com", "qq.com", "sohu.com", "ifeng.com",
    "caixin.com", "yicai.com", "cls.cn", "wallstreetcn.com",
    "huxiu.com", "tmtpost.com", "geekpark.net", "leiphone.com",
    "ithome.com", "cnbeta.com.tw", "cnr.cn", "xinhuanet.com",
    "autohome.com.cn", "pcauto.com.cn", "dongchedi.com",
    "k.sina.com.cn",
]

# URL 噪音模式：匹配到的直接丢弃
_NOISE_URL_PATTERNS = [
    "/search", "/tag/", "/tags/", "/search-list/",
    "/category/", "/topics/", "/Solution/ListDetail/",
    "/ask/",
    "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com",
    "wikipedia.org", "eschool.qq.com",
    "pitchhub.36kr.com",
]


def _is_noise_url(url: str) -> bool:
    """判断 URL 是否为列表页/标签页/百科等噪音"""
    return any(p in url for p in _NOISE_URL_PATTERNS)


def _execute_query(q: dict, api_key: str, time_range: str, search_depth: str) -> list[dict]:
    """执行单条查询，过滤噪音 URL，格式化结果（带 5 分钟缓存）"""
    qtype = q.get("type", "")

    # 域名限制：行业/融资搜索不限制（让 Tavily 自由搜索，信息源发现交给 AI 过滤）
    if qtype in ("industry", "fundraising_amount", "fundraising_news", "fundraising_detail"):
        include_domains = None
    elif q.get("lang") == "zh":
        include_domains = ZH_NEWS_DOMAINS
    else:
        include_domains = None

    # 缓存查询（以 query 字符串为 key）
    cache_key = q["query"]
    cached = _get_cached(cache_key)
    if cached is not None:
        results = cached
    else:
        results = search_tavily(
            query=q["query"],
            api_key=api_key,
            topic="news",
            time_range=time_range,
            search_depth=search_depth,
            max_results=5,
            include_domains=include_domains,
        )
        _set_cached(cache_key, results)
    formatted = []
    for r in results:
        url = r.get("url", "")
        if _is_noise_url(url):
            continue

        title = r.get("title", "")
        content = r.get("content", "")

        # ── 英文标题检测 ──────────────────────────
        # 标题中汉字比例 < 20% 视为英文标题，标注供后续处理
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', title))
        is_english_title = chinese_chars == 0 and len(title) > 10

        # ── 品牌查询的主体过滤 ──────────────────────────
        # 品牌搜索结果必须在标题或内容前200字中包含品牌名/子品牌名
        # 否则是搜索引擎返回的不相关结果（如搜 vivo 返回维密新闻）
        if qtype in ("brand_main", "sub_brand", "brand_en"):
            brand_names = q.get("brand_names", [q.get("brand", "")])
            text_to_check = f"{title} {content[:200]}"
            if not any(bn and bn.lower() in text_to_check.lower() for bn in brand_names):
                continue  # 品牌名不在标题和内容前200字中，丢弃

        item = {
            "brand": q["brand"],
            "brand_names": q.get("brand_names", [q["brand"]]),
            "query": q["query"],
            "query_type": q["type"],
            "title": r.get("title", ""),
            "url": url,
            "content": r.get("content", ""),
            "score": r.get("score", 0),
            "published_date": r.get("published_date", ""),
            "is_english_title": is_english_title,
        }
        if "track_name" in q:
            item["track_name"] = q["track_name"]
        formatted.append(item)
    return formatted


def run_search(
    brand_configs: list[dict],
    industry_configs: list[dict] = None,
    include_industry: bool = False,
    api_key: str = None,
) -> list[dict]:
    """
    执行完整搜索流程
    返回: [{brand, query, title, url, content, score, published_date, type}, ...]
    """
    if api_key is None:
        api_key = get_api_key()

    all_results = []

    # 品牌搜索（每日）
    for brand_cfg in brand_configs:
        queries = build_brand_queries(brand_cfg)
        for q in queries:
            try:
                results = _execute_query(q, api_key, time_range="day", search_depth="basic")
                all_results.extend(results)
            except Exception as e:
                print(f"[搜索失败] {q['query']}: {e}")

    # 行业搜索（每周）
    if include_industry and industry_configs:
        for ind_cfg in industry_configs:
            queries = build_industry_queries(ind_cfg)
            for q in queries:
                try:
                    results = _execute_query(q, api_key, time_range="week", search_depth="advanced")
                    all_results.extend(results)
                except Exception as e:
                    print(f"[搜索失败] {q['query']}: {e}")

    return all_results


def load_brand_industry_map() -> dict:
    """返回 {品牌名: 行业名} 映射，供 memory.py 使用"""
    import yaml
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        brand_map = {}
        for brand_cfg in config.get("brands", []):
            brand_map[brand_cfg["name"]] = brand_cfg.get("industry", "")
        return brand_map
    except Exception:
        return {}


# ── 融资专项搜索 ──────────────────────────────────────────

_KEYWORD_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "keyword_cache.json")


def _load_keyword_cache() -> dict:
    if os.path.exists(_KEYWORD_CACHE_PATH):
        try:
            with open(_KEYWORD_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_keyword_cache(cache: dict):
    os.makedirs(os.path.dirname(_KEYWORD_CACHE_PATH), exist_ok=True)
    with open(_KEYWORD_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_track_keywords(track_name: str) -> list[str]:
    """
    获取某赛道的搜索关键词（LLM 生成 + 7 天缓存）。

    命中缓存且未过期 → 直接返回
    缓存过期或不存在 → 调 DeerAPI 生成 → 缓存 → 返回
    LLM 调用失败 → 回退到基础关键词（赛道名本身 + 通用词）
    """
    cache = _load_keyword_cache()
    entry = cache.get(track_name)
    ttl_days = 7

    if entry:
        generated = entry.get("generated_at", "")
        try:
            gen_time = datetime.fromisoformat(generated)
            if datetime.now() - gen_time < timedelta(days=ttl_days):
                return entry.get("keywords", [])
        except Exception:
            pass

    # 缓存未命中或已过期，尝试 LLM 生成
    keywords = _generate_keywords_via_llm(track_name)
    if keywords:
        cache[track_name] = {
            "keywords": keywords,
            "generated_at": datetime.now().isoformat(),
            "ttl_days": ttl_days,
        }
        _save_keyword_cache(cache)
        return keywords

    # LLM 也失败了，用最小回退
    return [_clean_track_name(track_name)]


def _generate_keywords_via_llm(track_name: str) -> list[str]:
    """调 MiniMax 生成赛道关键词（主），DeerAPI fallback"""
    try:
        import requests
        minimax_key = os.environ.get("MINIMAX_API_KEY", "")
        if minimax_key:
            resp = requests.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {minimax_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-Text-01",
                    "max_tokens": 200,
                    "temperature": 0.3,
                    "messages": [{
                        "role": "user",
                        "content": f"给出「{track_name}」赛道在中国融资新闻中常见的5-8个搜索关键词。\n要求：多样化表达，覆盖不同新闻写法，纯中文，每行一个，不要编号，不要解释。\n示例输出：\n大模型\n生成式AI创业公司\n人工智能应用层公司\nAI独角兽\n大模型厂商\n智能助手应用"
                    }],
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            content = ""
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")

            # 解析
            keywords = []
            for line in content.strip().split("\n"):
                line = line.strip().strip("-+*.0123456789. \t")
                if line and len(line) >= 2:
                    keywords.append(line)
            if keywords:
                print(f"  [关键词生成] {track_name}: {keywords}")
                return keywords[:8]
            return []
    except Exception as e:
        print(f"  [MiniMax关键词失败] {e}", flush=True)

    # Fallback DeerAPI
    try:
        import requests
        deer_key = os.environ.get("DEER_API_KEY", "")
        if not deer_key:
            return []

        resp = requests.post(
            "https://api.deerapi.com/v1/messages",
            headers={
                "Authorization": f"Bearer {deer_key}",
                "Content-Type": "application/json",
                "anthropic-beta": "compact-2026-01-12",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "temperature": 0.3,
                "messages": [{
                    "role": "user",
                    "content": f"给出「{track_name}」赛道在中国融资新闻中常见的5-8个搜索关键词。\n要求：多样化表达，覆盖不同新闻写法，纯中文，每行一个，不要编号，不要解释。\n示例输出：\n大模型\n生成式AI创业公司\n人工智能应用层公司\nAI独角兽\n大模型厂商\n智能助手应用"
                }],
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [{}])[0].get("text", "")

        keywords = []
        for line in content.strip().split("\n"):
            line = line.strip().strip("-+*.0123456789. \t")
            if line and len(line) >= 2:
                keywords.append(line)
        if keywords:
            print(f"  [关键词生成(Deer)] {track_name}: {keywords}")
            return keywords[:8]
        return []
    except Exception as e:
        print(f"  [DeerAPI关键词失败] {e}", flush=True)
        return []

    except Exception as e:
        print(f"  [关键词生成失败] {track_name}: {e}")
        return []


def _clean_track_name(name: str) -> str:
    """清理赛道名，移除 / 等特殊字符"""
    return name.replace("/", " ").replace("\\", " ").strip()


def build_fundraising_queries(fundraising_config: dict) -> list[dict]:
    """
    根据融资赛道配置生成搜索查询。

    策略：
    - 每赛道通过 LLM 生成多样化的搜索关键词（7天缓存）
    - 每个关键词生成 1 条查询，不加 site: 限制（让 Tavily 自由搜索）
    """
    queries = []
    tracks = fundraising_config.get("tracks", [])

    for track in tracks:
        raw_name = track.get("name", "")
        keywords = get_track_keywords(raw_name)
        # 兜底：赛道名本身
        if not keywords:
            keywords = [_clean_track_name(raw_name)]

        for kw in keywords:
            queries.append({
                "query": f'"{kw}" 融资 亿元 2026',
                "brand": f"[融资]{raw_name}",
                "track_name": raw_name,
                "priority": track.get("priority", 5),
                "type": "fundraising_amount",
                "lang": "zh",
            })

    return queries


def build_track_research_queries(tracks: list[dict], include_tracks: list[str]) -> list[dict]:
    """
    当发现某赛道有高价值融资事件时，
    补充搜索该赛道近期动态（增强行业洞察）

    include_tracks: 触发深度搜索的赛道名列表
    """
    queries = []
    for track_name in include_tracks:
        queries.append({
            "query": f'"{track_name}" 近期动态 市场格局 2026',
            "brand": f"[行业洞察]{track_name}",
            "track_name": track_name,
            "type": "track_research",
            "lang": "zh",
        })
    return queries


def run_fundraising_search(fundraising_config: dict, api_key: str = None) -> list[dict]:
    """执行融资专项搜索，返回结构化结果（多线程并发）"""
    if api_key is None:
        api_key = get_api_key()

    queries = build_fundraising_queries(fundraising_config)
    all_results = []

    def _search_one(q):
        try:
            return _execute_query(q, api_key, time_range="week", search_depth="basic")
        except Exception as e:
            print(f"[融资搜索失败] {q['query']}: {e}")
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_search_one, q) for q in queries]
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())

    return all_results


def run_track_research(tracks: list[dict], include_tracks: list[str], api_key: str = None) -> list[dict]:
    """对高价值融资赛道执行深度行业研究搜索（多线程并发）"""
    if api_key is None:
        api_key = get_api_key()
    if not include_tracks:
        return []

    track_configs = [t for t in tracks if t.get("name") in include_tracks]
    queries = build_track_research_queries(track_configs, include_tracks)

    def _search_one(q):
        try:
            return _execute_query(q, api_key, time_range="week", search_depth="advanced")
        except Exception as e:
            print(f"[赛道研究搜索失败] {q['query']}: {e}")
            return []

    all_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_search_one, q) for q in queries]
        for future in concurrent.futures.as_completed(futures):
            all_results.extend(future.result())

    return all_results


# ── 信息源质量追踪 ────────────────────────────────────────
# 记录高分结果的来源域名，长期积累形成各赛道专属优质信息源

_SOURCE_QUALITY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "source_quality.json")


def record_source_hits(results: list[dict], min_score: int = 7):
    """
    遍历结果，统计各域名的高分命中次数。

    results: 包含 {url, track_name?, brand?, analysis?} 的列表
    min_score: 只记录 >= 此分数的结果
    """
    cache = {}
    if os.path.exists(_SOURCE_QUALITY_PATH):
        try:
            with open(_SOURCE_QUALITY_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            pass

    for r in results:
        score = r.get("analysis", {}).get("relevance_score", 0)
        if score < min_score:
            continue
        url = r.get("url", "")
        if not url:
            continue
        try:
            domain = url.split("/")[2]
        except Exception:
            continue

        track = r.get("track_name", "")
        if not track:
            brand = r.get("brand", "")
            if brand.startswith("[融资]"):
                track = brand[4:]
            else:
                continue  # 品牌/行业结果暂不追踪域名

        if track not in cache:
            cache[track] = {}
        if domain not in cache[track]:
            cache[track][domain] = {"hits": 0, "high_relevance_hits": 0, "last_seen": ""}

        cache[track][domain]["hits"] += 1
        cache[track][domain]["high_relevance_hits"] += 1
        cache[track][domain]["last_seen"] = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(os.path.dirname(_SOURCE_QUALITY_PATH), exist_ok=True)
    with open(_SOURCE_QUALITY_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


