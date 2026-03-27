"""
Bocha 搜索引擎封装
负责根据品牌配置生成搜索查询，调用 Bocha API，返回结构化结果
"""

import os
import re
import json
import time
import requests
import concurrent.futures
from datetime import datetime, timedelta
from typing import Optional

# ── 搜索结果缓存（内存 + 每日磁盘文件）─────────────────────────
# 每天一个 JSON 文件，同一天内相同查询只调一次 Bocha API
# 文件位置：data/search_cache/bocha_YYYY-MM-DD.json
_DISK_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "search_cache")
_disk_cache: dict = {}       # 当天磁盘缓存（启动时加载一次）
_disk_cache_date: str = ""   # 已加载的日期


def _ensure_disk_cache() -> dict:
    """确保当天磁盘缓存已加载到内存（只读一次文件）"""
    global _disk_cache, _disk_cache_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _disk_cache_date == today:
        return _disk_cache
    # 日期变了或首次加载
    _disk_cache_date = today
    path = os.path.join(_DISK_CACHE_DIR, f"bocha_{today}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _disk_cache = json.load(f)
                print(f"  [缓存] 加载当天搜索缓存: {len(_disk_cache)} 条查询")
        except Exception:
            _disk_cache = {}
    else:
        _disk_cache = {}
    return _disk_cache


def _flush_disk_cache() -> None:
    """将内存缓存写回磁盘文件"""
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
    """判断今天是否应该运行融资专项搜索（仅周一和周三）"""
    today = datetime.now().weekday()
    return today in (0, 2)  # 0=周一, 2=周三


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
    主搜索函数：仅使用 Bocha，限流时自动等待重试。
    函数名保留 search_tavily 以兼容已有调用。
    """
    bocha_key = api_key or _get_bocha_key()
    if not bocha_key:
        return []

    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            results = _search_bocha(query, bocha_key, time_range, max_results)
            if results:
                print(f"  [Bocha OK] {len(results)} 条", flush=True)
                return results
            return []
        except requests.exceptions.HTTPError as e:
            # 429 限流：等待后重试
            if e.response is not None and e.response.status_code == 429:
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s
                print(f"  [Bocha 限流] 等待 {wait}s 后重试 ({attempt+1}/{max_retries})...", flush=True)
                time.sleep(wait)
                continue
            print(f"  [Bocha 失败] {e}", flush=True)
            return []
        except Exception as e:
            print(f"  [Bocha 失败] {e}", flush=True)
            return []

    print(f"  [Bocha 限流] 重试耗尽，跳过: {query[:40]}", flush=True)
    return []


def _search_toutiao(query: str, max_results: int = 5) -> list[dict]:
    """
    今日头条搜索 - 解析头条搜索结果获取真实文章URL
    头条搜索结果包含 article.zlink.toutiao.com 重定向，
    通过解码 h5_url 参数获取原始文章URL。
    """
    import urllib.parse
    import re as re_module
    try:
        encoded_q = urllib.parse.quote(query)
        search_url = f"https://so.toutiao.com/search?keyword={encoded_q}&source=input"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []

        html = resp.text
        results = []

        # 从重定向链接中提取真实文章URL
        zlink_pattern = re_module.compile(r'(article\.zlink\.toutiao\.com/[^\s"\'<>]+)')
        seen_urls = set()

        for match in zlink_pattern.findall(html):
            try:
                decoded = urllib.parse.unquote(urllib.parse.unquote(match))
                h5_match = re_module.search(r'h5_url=([^\s&]+)', decoded)
                if h5_match:
                    actual_url = urllib.parse.unquote(h5_match.group(1))
                    if actual_url not in seen_urls and actual_url.startswith('http'):
                        seen_urls.add(actual_url)
                        results.append({
                            "title": f"头条文章: {query}",
                            "url": actual_url,
                            "content": f"来源: 今日头条 | 关键词: {query}",
                            "score": 0.0,
                            "published_date": "",
                        })
            except Exception:
                continue

        return results[:max_results]
    except Exception as e:
        return []


def build_brand_queries(brand_config: dict) -> list[dict]:
    """根据品牌配置生成搜索查询列表

    策略：短查询 + 多角度覆盖，每条查询关键词不超过3个（Bocha 对长 OR 查询支持差）。
    每个品牌最多 3 条查询，控制 API 消耗。
    """
    queries = []
    name = brand_config["name"]
    sub_brands = brand_config.get("sub_brands", [name])
    keywords = brand_config.get("keywords", [])
    lang = brand_config.get("lang", "zh")
    all_names = list(set(sub_brands + [name]))

    # 查询 1：品牌 + 高价值信号词（短查询，最多3个 OR）
    high_signals = [k for k in keywords if k in ("发布会", "新品", "代言人", "新车", "品牌升级", "广告", "营销")]
    if not high_signals:
        high_signals = ["新品", "营销", "广告"]
    queries.append({
        "query": f'{name} {" OR ".join(high_signals[:3])}',
        "brand": name,
        "brand_names": all_names,
        "type": "brand_main",
        "lang": "zh",
    })

    # 查询 2：品牌 + 商业动态信号（融资/合作/上市等）
    biz_signals = [k for k in keywords if k in ("融资", "投资", "上市", "合作", "签约", "CMO", "品牌总监")]
    if biz_signals:
        queries.append({
            "query": f'{name} {" OR ".join(biz_signals[:3])}',
            "brand": name,
            "brand_names": all_names,
            "type": "brand_biz",
            "lang": "zh",
        })

    # 查询 3：重要子品牌（如果有不同于主品牌的子品牌）
    important_subs = [sb for sb in sub_brands if sb != name][:2]
    if important_subs:
        sub_str = " OR ".join(important_subs)
        queries.append({
            "query": f"({sub_str}) 新品 OR 发布 OR 上市",
            "brand": name,
            "brand_names": all_names,
            "type": "sub_brand",
            "lang": "zh",
        })

    # 查询 4：英文查询（仅限标记了 en 的品牌）
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
    # 一线财经/科技媒体
    "36kr.com", "jiemian.com", "thepaper.cn", "sina.com.cn",
    "163.com", "qq.com", "sohu.com", "ifeng.com",
    "caixin.com", "yicai.com", "cls.cn", "wallstreetcn.com",
    "huxiu.com", "tmtpost.com", "geekpark.net", "leiphone.com",
    "ithome.com", "cnbeta.com.tw", "cnr.cn", "xinhuanet.com",
    "k.sina.com.cn",
    # 汽车垂类
    "autohome.com.cn", "pcauto.com.cn", "dongchedi.com",
    # 投融资/创投媒体
    "donews.com", "pedaily.cn", "chinaventure.com.cn",
    "itjuzi.com", "iheima.com", "cyzone.cn",
    # 官方/权威
    "ce.cn", "stcn.com", "cnstock.com", "eastmoney.com",
    "china.com.cn", "chinanews.com.cn",
    # 科技/消费
    "guancha.cn", "pingwest.com", "jiqizhixin.com",
]

# 融资新闻专用域名白名单（比品牌白名单更宽，包含创投媒体）
ZH_FUNDRAISING_DOMAINS = ZH_NEWS_DOMAINS + [
    "finance.sina.com.cn", "finance.ifeng.com",
    "chuangye.com", "vc.cn", "newseed.cn",
]

# URL 噪音模式：匹配到的直接丢弃
_NOISE_URL_PATTERNS = [
    "/search", "/tag/", "/tags/", "/search-list/",
    "/category/", "/topics/", "/Solution/ListDetail/",
    "/ask/", "/authors/",
    "baike.baidu.com", "zhidao.baidu.com", "wenku.baidu.com",
    "wikipedia.org", "eschool.qq.com",
    "pitchhub.36kr.com",
]

# 低质量域名黑名单：行业报告/数据站/SEO 站，不是新闻事件
_NOISE_DOMAINS = [
    "chinairn.com", "chinabgao.com", "askci.com",      # 行业报告站
    "stockstar.com", "stock.sohu.com",                   # 股票论坛
    "trustexporter.com", "globalimporter.net",           # 外贸B2B
    "topnews.cn",                                         # SEO 聚合
    "bbs.q.sina.com.cn",                                  # 论坛
    "winshang.com",                                       # 商业地产
]


def _is_noise_url(url: str) -> bool:
    """判断 URL 是否为列表页/标签页/百科/低质量域名等噪音"""
    if any(p in url for p in _NOISE_URL_PATTERNS):
        return True
    if any(d in url for d in _NOISE_DOMAINS):
        return True
    return False


def _execute_query(q: dict, api_key: str, time_range: str, search_depth: str) -> list[dict]:
    """执行单条查询，过滤噪音 URL，格式化结果（带 5 分钟缓存）"""
    qtype = q.get("type", "")

    # 域名限制：融资搜索用融资专用白名单，行业搜索不限制但后续由 _is_noise_url 过滤
    if qtype in ("industry",):
        include_domains = None
    elif qtype in ("fundraising_amount", "fundraising_news", "fundraising_detail"):
        include_domains = None  # Bocha 不支持 include_domains，由后处理过滤
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
            max_results=20,
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
        if qtype in ("brand_main", "brand_biz", "sub_brand", "brand_en"):
            brand_names = q.get("brand_names", [q.get("brand", "")])
            text_to_check = f"{title} {content[:200]}"
            if not any(bn and bn.lower() in text_to_check.lower() for bn in brand_names):
                continue  # 品牌名不在标题和内容前200字中，丢弃

        # ── 融资查询的域名质量过滤 ──────────────────────────
        # 融资新闻必须来自可信媒体，过滤行业报告站/SEO站
        if qtype in ("fundraising_amount", "fundraising_news", "fundraising_detail"):
            if not any(d in url for d in ZH_FUNDRAISING_DOMAINS):
                continue

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


def is_first_run(profile_name: str) -> bool:
    """
    判断是否为某档案的首次运行。
    首次运行标准：data/profiles/{profile_name}/search_archive.db 不存在或为空。
    """
    if not profile_name:
        return True
    db_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "profiles", profile_name, "search_archive.db"
    )
    if not os.path.exists(db_path):
        return True
    # 检查文件是否为空（0字节）
    if os.path.getsize(db_path) == 0:
        return True
    return False


def run_search(
    brand_configs: list[dict],
    industry_configs: list[dict] = None,
    include_industry: bool = False,
    api_key: str = None,
    time_range: str = "day",
) -> list[dict]:
    """
    执行完整搜索流程
    返回: [{brand, query, title, url, content, score, published_date, type}, ...]

    time_range: 搜索时间窗口，"day"=今日，"week"=一周
    """
    if api_key is None:
        api_key = get_api_key()

    all_results = []

    # 品牌搜索（每日）
    for brand_cfg in brand_configs:
        queries = build_brand_queries(brand_cfg)
        for q in queries:
            try:
                results = _execute_query(q, api_key, time_range=time_range, search_depth="basic")
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
    缓存过期或不存在 → 调 MiniMax 生成 → 缓存 → 返回
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
    """赛道关键词生成 — 使用静态预设表（MiniMax-M2.7 为推理模型，输出不稳定）"""
    _STATIC_KEYWORDS = {
        "AI大模型": ["大模型融资", "生成式AI创业公司", "AI独角兽", "大模型厂商", "人工智能应用融资", "AI基础模型", "大模型企业"],
        "机器人/具身智能": ["人形机器人融资", "具身智能创业", "机器人独角兽", "工业机器人融资", "机器人公司融资", "智能机器人"],
        "智能硬件/IoT": ["智能硬件融资", "IoT创业公司", "可穿戴设备融资", "智能家居融资", "消费电子融资", "硬件独角兽"],
        "企业服务/SaaS": ["SaaS融资", "企业服务创业", "B2B软件融资", "企服独角兽", "云服务融资", "数字化转型融资"],
        "半导体": ["芯片公司融资", "半导体创业", "集成电路融资", "芯片独角兽", "半导体企业融资"],
        "新能源汽车": ["新能源汽车融资", "电动车创业", "智能汽车融资", "新势力融资", "汽车科技融资"],
        "消费品": ["新消费融资", "消费品牌融资", "新品牌融资", "消费独角兽", "快消品融资"],
    }
    # 精确匹配
    if track_name in _STATIC_KEYWORDS:
        return _STATIC_KEYWORDS[track_name]
    # 模糊匹配
    for key, kws in _STATIC_KEYWORDS.items():
        if any(part in track_name for part in key.split("/")) or any(part in key for part in track_name.split("/")):
            return kws
    # 兜底：用赛道名本身构造
    clean = _clean_track_name(track_name)
    return [f"{clean}融资", f"{clean}创业公司", f"{clean}独角兽", clean]


def _clean_track_name(name: str) -> str:
    """清理赛道名，移除 / 等特殊字符"""
    return name.replace("/", " ").replace("\\", " ").strip()


def build_fundraising_queries(fundraising_config: dict) -> list[dict]:
    """
    根据融资赛道配置生成搜索查询。

    策略：
    - 每赛道通过 LLM 生成多样化的搜索关键词（7天缓存）
    - 短查询 + 动作词（完成/获得），精准命中实际融资事件而非行业综述
    - 每赛道最多 4 个关键词，控制 API 消耗
    """
    queries = []
    tracks = fundraising_config.get("tracks", [])

    for track in tracks:
        raw_name = track.get("name", "")
        keywords = get_track_keywords(raw_name)
        # 兜底：赛道名本身
        if not keywords:
            keywords = [_clean_track_name(raw_name)]

        for kw in keywords[:4]:
            queries.append({
                "query": f'{kw} 完成 OR 获得 融资 亿',
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


