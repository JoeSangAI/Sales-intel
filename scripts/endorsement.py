"""
代言人新闻获取模块
数据来源：中国广告协会代言人周报（微信公众号）
每周通过 Bocha 搜索找到最新一期文章，用 Playwright 获取内容，AI 提取结构化数据
"""

import os
import re
import json
import requests
from datetime import datetime
from typing import Optional

# Playwright for Chrome CDP (绕过反爬)
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("  [代言人] Playwright 未安装，将使用备用方案")


def _fetch_with_chrome(url: str, timeout: int = 30000) -> Optional[str]:
    """
    使用 Chrome CDP (Playwright) 获取页面内容，绕过反爬
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=timeout, wait_until="networkidle")
            content = page.content()
            browser.close()
            return content
    except Exception as e:
        print(f"  [Chrome CDP 获取失败] {e}")
        return None

# 缓存目录
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "endorsement_cache")
os.makedirs(_CACHE_DIR, exist_ok=True)


def _get_cache_path() -> str:
    """获取本周缓存文件路径（按周区分）"""
    # 每周一个缓存
    week_str = datetime.now().strftime("%Y-W%W")
    return os.path.join(_CACHE_DIR, f"endorsement_{week_str}.json")


def _load_cache() -> list[dict]:
    """加载本周缓存"""
    path = _get_cache_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_cache(data: list[dict]) -> None:
    """保存本周缓存"""
    path = _get_cache_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 代言人来源配置
# 中国广告协会代言人周报 - 每周一更新，最权威来源
# 每周通过 Bocha 搜索找到最新一期微信文章 URL
ENDORSEMENT_WECHAT_FIXED_URL = "https://mp.weixin.qq.com/s/0MDUpdPy0sXdu2pLN1qPfA"


def _search_latest_wechat_article() -> Optional[str]:
    """
    用 Bocha 搜索找到中广协代言人周报的最新一期微信文章 URL
    """
    try:
        from scripts.search import search_tavily, get_api_key
        api_key = get_api_key()

        # 搜索中广协代言人周报
        search_results = search_tavily(
            query="广告代言人周报 中广协 内容营销",
            api_key=api_key,
            topic="news",
            time_range="week",
            max_results=10,
        )

        if not search_results:
            print("  [代言人搜索] Bocha 无结果，尝试固定 URL")
            return None

        print(f"  [代言人搜索] Bocha 返回 {len(search_results)} 条")

        # 找微信文章链接
        for r in search_results:
            url = r.get("url", "")
            title = r.get("title", "")
            if "mp.weixin.qq.com" in url and "代言人" in title:
                print(f"  [代言人搜索] 找到微信文章: {url}")
                return url

        # 如果没找到微信，用第一个结果
        first_url = search_results[0].get("url", "")
        if first_url and "mp.weixin.qq.com" in first_url:
            print(f"  [代言人搜索] 使用搜索结果: {first_url}")
            return first_url

        print("  [代言人搜索] 未找到微信文章，使用固定 URL")
        return None

    except Exception as e:
        print(f"  [代言人搜索] 失败: {e}")
        return None


def fetch_latest_endorsement_article() -> Optional[dict]:
    """
    获取代言人信息文章
    策略：
    1. 先尝试从缓存加载
    2. Bocha 搜索找到最新一期微信文章
    3. Playwright 获取文章内容
    4. 备用：固定微信 URL + Bocha 搜索

    返回: {
        "url": str,
        "title": str,
        "content": str,
        "date": str
    } 或 None
    """
    # 先尝试从缓存加载
    cached = _load_cache()
    if cached:
        print(f"  [代言人缓存] 使用本周缓存，共 {len(cached)} 条")
        return {"url": "cached", "title": "本周代言人缓存", "content": "", "items": cached}

    # Bocha 搜索找到最新一期
    article_url = _search_latest_wechat_article()

    # 如果搜索失败，用固定 URL
    if not article_url:
        article_url = ENDORSEMENT_WECHAT_FIXED_URL
        print(f"  [代言人] 使用固定 URL: {article_url}")

    # 用 Playwright 获取文章
    content = _fetch_with_chrome(article_url, timeout=30000)
    if content:
        print(f"  [代言人] 获取到微信文章")
        parsed = _parse_wechat_page(content, article_url)
        if parsed:
            return parsed

    # 备用：使用 Bocha 搜索
    try:
        from scripts.search import search_tavily, get_api_key
        api_key = get_api_key()

        # 搜索广告代言动态相关文章
        search_results = search_tavily(
            query="品牌代言人 明星代言 官宣 2026",
            api_key=api_key,
            topic="news",
            time_range="week",
            max_results=20,
        )

        if not search_results:
            print("  [代言人] 搜索无结果")
            return None

        print(f"  [代言人] 搜索到 {len(search_results)} 条结果")

        # 汇总所有搜索结果的内容作为分析材料
        combined_content = []
        for r in search_results:
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            combined_content.append(f"标题：{title}\n内容：{content}\n来源：{url}")

        full_content = "\n\n---\n\n".join(combined_content)

        return {
            "url": search_results[0].get("url", ""),
            "title": "本周代言人动态搜索结果",
            "content": full_content,
            "date": datetime.now().strftime("%Y-%m-%d")
        }
    except Exception as e:
        print(f"  [代言人获取失败] {e}")

    return None


def _parse_wechat_page(content: str, url: str) -> Optional[dict]:
    """
    解析微信文章页面内容，提取代言人信息
    """
    import re
    from html import unescape

    # 提取标题 - 微信标题在 og:title 或 h1 中
    title_match = re.search(r'<h1[^>]*class="rich_media_title"[^>]*>([^<]+)</h1>', content)
    if not title_match:
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', content)
    title = title_match.group(1).strip() if title_match else "广告代言人周报"

    # 清理 HTML 标签，提取纯文本
    text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    # 提取正文 - 找 "代言人周报" 或 "艺人动态" 之后的内容
    markers = ["代言人周报", "艺人动态", "代言—", "全球品牌大使", "全球彩妆代言人"]
    start_pos = 0
    for marker in markers:
        pos = text.find(marker)
        if pos != -1:
            start_pos = pos
            break

    main_content = text[start_pos:start_pos + 5000]

    # 提取日期
    date_match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    date_str = ""
    if date_match:
        date_str = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    return {
        "url": url,
        "title": title,
        "content": main_content,
        "date": date_str
    }


def parse_endorsement_with_ai(article: dict, llm_call_fn) -> list[dict]:
    """
    用 AI 从文章内容中提取结构化代言人信息

    llm_call_fn: 接受 prompt 返回文本的函数
    """
    content = article.get("content", "")
    if not content:
        return []

    prompt = ENDORSEMENT_EXTRACTION_PROMPT.format(
        title=article.get("title", ""),
        content=content[:3000]  # 限制长度
    )

    try:
        response = llm_call_fn(prompt)
        return _parse_endorsement_response(response)
    except Exception as e:
        print(f"  [代言人解析失败] {e}")
        return []


ENDORSEMENT_EXTRACTION_PROMPT = """你是分众传媒的情报官，专门从文章中提取明星代言人信息。

你的任务：从以下文章内容中，提取所有品牌代言人官宣信息。

# 输出格式（严格JSON数组）

提取规则：
- brand: 品牌名（要准确）
- celebrity: 明星名
- endorsement_type: 代言类型（全球品牌代言人/品牌大使/品牌挚友/品牌形象大使等）
- industry: 所属行业（3C数码/新能源汽车/护肤美妆/食品饮料/家居/服装/其他）
- 只需要提取有明确品牌+明星组合的信息

文章标题：{title}

文章内容：
{content}

# 输出格式（严格JSON数组，每个元素包含 brand, celebrity, endorsement_type, industry）

请直接输出JSON数组，不要有其他内容。
"""


def _parse_endorsement_response(response_text: str) -> list[dict]:
    """解析 AI 返回的代言人 JSON"""
    import json as json_lib

    # 找到 JSON 数组
    start = response_text.find('[')
    if start == -1:
        return []

    # 找到数组结束
    depth = 0
    end = start
    for i, ch in enumerate(response_text[start:], start):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        items = json_lib.loads(response_text[start:end])
        if isinstance(items, list):
            return items
    except Exception:
        pass

    return []


def filter_by_industry(endorsements: list[dict], target_industries: list[str]) -> list[dict]:
    """
    按行业过滤代言人信息

    target_industries: 目标行业列表，如 ["新能源汽车", "3C数码", "护肤美妆"]
    """
    if not target_industries:
        return endorsements

    filtered = []
    for e in endorsements:
        industry = e.get("industry", "")
        if any(ti.lower() in industry.lower() or industry.lower() in ti.lower()
               for ti in target_industries):
            filtered.append(e)

    return filtered


# 行业关键词映射（用于匹配销售关注的行业）
INDUSTRY_KEYWORDS = {
    "3C数码": ["手机", "电脑", "数码", "电子", "智能硬件", "IoT"],
    "新能源汽车": ["汽车", "电动车", "新能源", "智驾", "车载"],
    "AI科技": ["AI", "人工智能", "大模型", "机器人"],
    "护肤美妆": ["护肤", "美妆", "化妆品", "美容", "个护"],
    "食品饮料": ["饮料", "食品", "零食", "乳制品", "酒", "茶"],
    "家居": ["家居", "家具", "家电", "厨卫"],
    "服装": ["服装", "鞋", "箱包", "配饰", "服饰"],
    "其他": []
}


def match_industry(endorsement: dict) -> str:
    """根据品牌名匹配行业"""
    brand = endorsement.get("brand", "")

    for industry, keywords in INDUSTRY_KEYWORDS.items():
        for kw in keywords:
            if kw in brand:
                return industry

    # 根据代言类型判断
    celebrity = endorsement.get("celebrity", "")

    return "其他"


# 代言人分析 prompt（用于判断分众机会）
ENDORSEMENT_ANALYSIS_PROMPT = """你是分众传媒的销售情报专家。

从以下代言人信息中，分析这对分众销售意味着什么。

# 业务逻辑
品牌请代言人 → 意味着后续有持续宣传需求 → 是潜在客户的销售机会
分众电梯媒体是品牌代言人宣传的重要渠道

# 输入信息
品牌：{brand}
明星：{celebrity}
代言类型：{endorsement_type}
行业：{industry}

# 输出格式（严格JSON）

{{
  "relevance_score": 7,
  "urgency": "🟡",
  "event_key": "品牌签约明星",
  "intel_summary": "1-2句话：谁签了谁+为什么这对分众有价值",
  "prospect_leads": [
    {{"name": "品牌名", "reason": "刚签代言人，后续宣传需求大"}}
  ],
  "filter": false
}}
"""


def analyze_endorsement(endorsement: dict, llm_call_fn) -> dict:
    """
    分析单条代言人信息的分众机会
    """
    prompt = ENDORSEMENT_ANALYSIS_PROMPT.format(
        brand=endorsement.get("brand", ""),
        celebrity=endorsement.get("celebrity", ""),
        endorsement_type=endorsement.get("endorsement_type", ""),
        industry=endorsement.get("industry", ""),
    )

    try:
        response = llm_call_fn(prompt)
        return _parse_analysis_response(response)
    except Exception:
        return {
            "relevance_score": 5,
            "urgency": "⚪",
            "event_key": f"{endorsement.get('brand', '')}签约{endorsement.get('celebrity', '')}",
            "intel_summary": "",
            "prospect_leads": [],
            "filter": False
        }


def _parse_analysis_response(response_text: str) -> dict:
    """解析分析结果 JSON"""
    import json as json_lib

    start = response_text.find('{')
    if start == -1:
        return {}

    depth = 0
    end = start
    for i, ch in enumerate(response_text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        return json_lib.loads(response_text[start:end])
    except Exception:
        return {}


def collect_all_industries(profiles: list[dict]) -> list[str]:
    """从所有档案中收集关注的行业列表"""
    industries = set()
    for p in profiles:
        for brand in p.get("brands", []):
            ind = brand.get("industry", "")
            if ind:
                industries.add(ind)
        for ind_cfg in p.get("industries", []):
            if isinstance(ind_cfg, dict):
                industries.add(ind_cfg.get("name", ""))
            else:
                industries.add(str(ind_cfg))
    return sorted(industries)


def fetch_wechat_article_via_bocha(url: str) -> str:
    """用 Bocha web-search API 抓取微信文章内容"""
    from scripts.search import get_api_key
    try:
        api_key = get_api_key()
        resp = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"query": url, "freshness": "noLimit", "summary": True, "count": 3},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        pages = data.get("data", {}).get("webPages", {}).get("value", [])
        if pages:
            parts = []
            for p in pages:
                parts.append(f"{p.get('name', '')}\n{p.get('snippet', '')}")
            return "\n\n".join(parts)
        return ""
    except Exception as e:
        print(f"  [代言人抓取失败] {e}")
        return ""


def _search_endorsement_news_via_bocha() -> str:
    """Bocha 搜索多条代言人新闻，拼接为富文本供 AI 解析。
    当 Playwright 抓取微信文章失败时，用此方法作为备用。
    """
    from scripts.search import get_api_key
    try:
        api_key = get_api_key()
        queries = [
            "品牌代言人 官宣 2026",
            "明星代言 品牌大使 官宣",
            "中广协 代言人周报",
        ]
        all_parts = []
        for q in queries:
            resp = requests.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": q, "freshness": "week", "summary": True, "count": 10},
                timeout=30,
            )
            resp.raise_for_status()
            pages = resp.json().get("data", {}).get("webPages", {}).get("value", [])
            for p in pages:
                title = p.get("name", "")
                snippet = p.get("snippet", "")
                if any(kw in title + snippet for kw in ["代言", "品牌大使", "品牌挚友", "形象大使", "官宣"]):
                    all_parts.append(f"标题：{title}\n内容：{snippet}")
        if all_parts:
            print(f"  [代言人搜索] Bocha 找到 {len(all_parts)} 条代言相关新闻")
        return "\n\n---\n\n".join(all_parts)
    except Exception as e:
        print(f"  [代言人搜索失败] {e}")
        return ""


def parse_endorsements_from_text(article_text: str, all_industries: list[str]) -> list[dict]:
    """用 MiniMax 从文章中解析代言人信息并匹配行业"""
    import os as _os
    minimax_key = _os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        return []

    industries_str = "、".join(all_industries) if all_industries else "3C数码、新能源汽车、AI科技、食品粮油、护肤美妆"
    prompt = f"""你是分众传媒销售情报分析师。

以下是本周品牌代言人动态的文章内容：

{article_text[:3000]}

请提取所有代言人合作信息，输出 JSON 数组：

[
  {{
    "brand": "品牌名",
    "celebrity": "代言人姓名",
    "industry": "从以下行业中选最匹配的一个：{industries_str}，都不匹配填「其他」",
    "detail": "一句话描述代言合作内容",
    "relevance": "一句话说明分众电梯广告的切入机会",
    "urgency": "🔴（刚官宣，本周跟进）或 🟡（本月关注）"
  }}
]

只输出 JSON 数组，不要其他内容。"""

    try:
        resp = requests.post(
            "https://api.minimax.chat/v1/chat/completions",
            headers={"Authorization": f"Bearer {minimax_key}", "Content-Type": "application/json"},
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 2000,
                "temperature": 0.3,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=40,
        )
        resp.raise_for_status()
        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        # 过滤 <think> 块
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1:
            return json.loads(content[start:end+1])
        return []
    except Exception as e:
        print(f"  [代言人解析失败] {e}")
        return []


def match_endorsements_to_profile(endorsements: list[dict], profile: dict) -> list[dict]:
    """将代言人列表过滤出该档案关注的行业"""
    profile_industries = set()
    for brand in profile.get("brands", []):
        ind = brand.get("industry", "")
        if ind:
            profile_industries.add(ind)
    for ind_cfg in profile.get("industries", []):
        if isinstance(ind_cfg, dict):
            profile_industries.add(ind_cfg.get("name", ""))
        else:
            profile_industries.add(str(ind_cfg))
    return [e for e in endorsements if e.get("industry", "") in profile_industries]


def prompt_and_fetch_endorsements(profiles: list[dict]) -> list[dict]:
    """
    代言人获取流程：
    1. 检查本周缓存
    2. Playwright 抓取微信文章全文（最佳）
    3. Bocha 搜索多条代言新闻拼接（备用）
    4. MiniMax 解析代言人
    返回全量代言人列表（未按档案过滤）
    """
    import sys

    # 先检查本周缓存
    cached = _load_cache()
    if cached:
        print(f"  [代言人] 使用本周缓存，共 {len(cached)} 条")
        return cached

    # 确定文章 URL
    if not sys.stdin.isatty():
        url = ENDORSEMENT_WECHAT_FIXED_URL
        print(f"\n  [代言人] 非交互式环境，自动使用固定 URL: {url[:60]}...")
    else:
        print("\n" + "="*60)
        print("📋 今天是周三，需要录入本周代言人信息")
        print("请粘贴微信文章链接（直接回车跳过）：")
        print("="*60)
        url = input("> ").strip()
        if not url:
            print("  [代言人] 已跳过")
            return []

    # 策略1: Playwright 抓取全文（内容最完整）
    article_text = ""
    if PLAYWRIGHT_AVAILABLE:
        print(f"  [代言人] Playwright 抓取: {url[:60]}...")
        html = _fetch_with_chrome(url, timeout=30000)
        if html:
            parsed = _parse_wechat_page(html, url)
            if parsed and parsed.get("content"):
                article_text = parsed["content"]
                print(f"  [代言人] Playwright 抓取成功，内容长度: {len(article_text)}")

    # 策略2: Bocha 抓取单篇（摘要级别）
    if not article_text:
        print(f"  [代言人] Playwright 未获取到内容，尝试 Bocha 抓取...")
        article_text = fetch_wechat_article_via_bocha(url)

    # 策略3: Bocha 搜索多条代言新闻拼接
    if not article_text or len(article_text) < 200:
        print(f"  [代言人] 单篇内容不足，搜索多条代言新闻补充...")
        search_text = _search_endorsement_news_via_bocha()
        if search_text:
            article_text = (article_text + "\n\n---\n\n" + search_text).strip()

    if not article_text:
        print("  [代言人] 所有抓取方式均失败，已跳过")
        return []

    print(f"  [代言人] 最终内容长度: {len(article_text)}，正在解析...")
    all_industries = collect_all_industries(profiles)
    endorsements = parse_endorsements_from_text(article_text, all_industries)
    print(f"  [代言人] 解析完成，共 {len(endorsements)} 条")

    if endorsements:
        _save_cache(endorsements)

    return endorsements
