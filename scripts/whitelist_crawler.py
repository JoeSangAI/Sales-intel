"""
白名单网站直接抓取模块 v5
策略：Bocha site: 搜索 + 精准页面抓取

v4 的通用 URL pattern 猜测成功率极低（中文媒体 URL 结构碎片化）。
v5 改用 Bocha 的 site: 搜索能力，找到白名单域名下匹配关键词的真实文章 URL，
再直接抓取这些 URL，保证成功率。
"""

import os
import json
import requests
import re
import warnings
from typing import List, Dict, Optional, Tuple
from urllib.parse import urljoin, urlparse
from datetime import datetime

# 忽略SSL警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')


# ── 重点垂类媒体精确配置 ──────────────────────────────
# domain -> {name, base_url, list_url, article_pattern, industry, rss}
# 这些网站走首页抓取路线，有精确的 article URL pattern
# rss 字段：如果有官方 RSS feed，则优先使用 RSS 抓取
# 只保留经过验证确实能抓到的媒体
WHITELIST_SOURCES = {
    # ===== 手机3C / 消费电子 =====
    "ithome.com": {
        "name": "IT之家",
        "base_url": "https://www.ithome.com",
        "list_url": "https://www.ithome.com",
        "article_pattern": r'/[\w/]+/\d+\.htm',
        "industry": "3C数码",
        "rss": "https://www.ithome.com/rss/",
    },
    "elecfans.com": {
        "name": "电子发烧友",
        "base_url": "https://www.elecfans.com",
        "list_url": "https://www.elecfans.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "3C数码",
        "rss": None,
    },
    "leiphone.com": {
        "name": "雷锋网",
        "base_url": "https://www.leiphone.com",
        "list_url": "https://www.leiphone.com",
        "article_pattern": r'/banner/homepageUrl/id/\d+',
        "industry": "3C数码",
        "rss": None,
    },

    # ===== 新能源汽车 =====
    "cheshi.com": {
        "name": "网上车市",
        "base_url": "https://www.cheshi.com",
        "list_url": "https://www.cheshi.com",
        "article_pattern": r'/dujia/\d+',
        "industry": "新能源汽车",
        "rss": None,
    },
    "d1ev.com": {
        "name": "第一电动",
        "base_url": "https://www.d1ev.com",
        "list_url": "https://www.d1ev.com/news/shichang",
        "article_pattern": r'/news/shichang/\d+',
        "industry": "新能源汽车",
        "rss": None,
    },
    "chejiahao.autohome.com.cn": {
        "name": "汽车之家自媒体",
        "base_url": "https://chejiahao.autohome.com.cn",
        "list_url": "https://chejiahao.autohome.com.cn",
        "article_pattern": r'/info/\d+',
        "industry": "新能源汽车",
        "rss": None,
    },
    "dongchedi.com": {
        "name": "懂车帝",
        "base_url": "https://www.dongchedi.com",
        "list_url": "https://www.dongchedi.com",
        "article_pattern": r'/article/\d+',
        "industry": "新能源汽车",
        "rss": None,
    },

    # ===== 美妆护肤 =====
    "jumeili.cn": {
        "name": "聚美丽",
        "base_url": "https://www.jumeili.cn",
        "list_url": "https://www.jumeili.cn",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "美妆护肤",
        "rss": None,
    },
    "pinguan.com": {
        "name": "品观网",
        "base_url": "https://www.pinguan.com",
        "list_url": "https://www.pinguan.com",
        "article_pattern": r'/article/\w+/\d+',
        "industry": "美妆护肤",
        "rss": None,
    },
    "chinabeauty.cn": {
        "name": "中国美妆网",
        "base_url": "https://www.chinabeauty.cn",
        "list_url": "https://www.chinabeauty.cn",
        "article_pattern": r'/news/\d+\.html',
        "industry": "美妆护肤",
        "rss": None,
    },

    # ===== 食品粮油 =====
    "news.foodmate.net": {
        "name": "食品伙伴网",
        "base_url": "https://news.foodmate.net",
        "list_url": "https://news.foodmate.net",
        "article_pattern": r'/\d+/\d+\.html',
        "industry": "食品粮油",
        "rss": None,
    },

    # ===== 机器人 / AI =====
    "rgznrb.com": {
        "name": "人工智能日报网",
        "base_url": "https://www.rgznrb.com",
        "list_url": "https://www.rgznrb.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "AI科技",
        "rss": None,
    },

    # ===== 智能硬件 / IoT =====
    "newiot.com": {
        "name": "新物联",
        "base_url": "https://www.newiot.com",
        "list_url": "https://www.newiot.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "智能硬件",
        "rss": None,
    },
    "robot-china.com": {
        "name": "中国机器人网",
        "base_url": "https://www.robot-china.com",
        "list_url": "https://www.robot-china.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "机器人",
        "rss": None,
    },
    "zhidx.com": {
        "name": "智东西",
        "base_url": "https://zhidx.com",
        "list_url": "https://zhidx.com",
        "article_pattern": r'/p/\d+\.html',
        "industry": "AI科技",
        "rss": None,
    },

    # ===== 智能硬件 / IoT =====
    "iotworld.com.cn": {
        "name": "IoT世界网",
        "base_url": "https://www.iotworld.com.cn",
        "list_url": "https://www.iotworld.com.cn",
        "article_pattern": r'/html/News/\d+/\w+\.shtml',
        "industry": "智能硬件",
        "rss": None,
    },

    # ===== 科技/AI =====
    "big-bit.com": {
        "name": "大比特网",
        "base_url": "https://www.big-bit.com",
        "list_url": "https://www.big-bit.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "半导体",
        "rss": None,
    },
    "eetop.cn": {
        "name": "EETOP",
        "base_url": "https://www.eetop.cn",
        "list_url": "https://www.eetop.cn",
        "article_pattern": r'/semi/\d+\.html',
        "industry": "半导体",
        "rss": None,
    },
    "techxun.com": {
        "name": "科技讯",
        "base_url": "http://www.techxun.com",
        "list_url": "http://www.techxun.com",
        "article_pattern": r'/news/it/\d+\.html',
        "industry": "AI科技",
        "rss": None,
    },

    # ===== 企业服务 / SaaS =====
    "saasruanjian.com": {
        "name": "SaaS点评网",
        "base_url": "https://www.saasruanjian.com",
        "list_url": "https://www.saasruanjian.com",
        "article_pattern": r'/[\w/]+/\d+\.html',
        "industry": "企业服务",
        "rss": None,
    },
}


# ── 融资/代言人信号词 ───────────────────────────────
_BRAND_NEWS_KEYWORDS  = ["新品", "发布", "上市", "推出", "首发", "亮相", "问世",
                          "营销", "广告", "投放", "campaign", "品牌升级", "代言人", "比亚迪", "特斯拉", "问界", "蔚来", "小米汽车", "理想汽车", "小鹏", "新车上市"]
_FUNDRAISING_KEYWORDS = ["融资", "获投", "轮融资", "亿元", "投资", "上市", "IPO",
                          "收购", "并购", "估值"]
_ENDORSEMENT_KEYWORDS = ["代言", "代言人", "品牌大使", "形象大使", "官宣"]


def _classify_content_type(title: str, content: str) -> str:
    """判断内容类型：brand_news | fundraising | endorsement"""
    text = (title + " " + content).lower()
    if any(kw in text for kw in _ENDORSEMENT_KEYWORDS):
        return "endorsement"
    if any(kw in text for kw in _FUNDRAISING_KEYWORDS):
        return "fundraising"
    return "brand_news"


def _fetch_page_content(url: str, timeout: int = 10) -> Optional[str]:
    """抓取页面HTML内容（忽略SSL验证，提升成功率）"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = requests.get(url, headers=headers, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return None


def _extract_from_html(html: str, keyword: str) -> Tuple[str, str]:
    """
    从文章页面 HTML 中提取标题和正文摘要。
    尝试多种提取策略，优先使用 meta 描述。
    """
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # 策略1：title 标签
        title = soup.find('title')
        if title:
            title = title.get_text(strip=True)
            # 清理标题中的站名后缀（如 " - 钛媒体"）
            title = re.sub(r'\s*[-_|·]\s*\S+$', '', title)

        # 策略2：og:title
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content'].strip()

        if not title or len(title) < 5:
            title = ""

        # 策略1：og:description / description
        desc = ""
        for meta in [soup.find('meta', property='og:description'),
                     soup.find('meta', attrs={'name': 'description'})]:
            if meta and meta.get('content'):
                desc = meta['content'].strip()
                break

        # 策略2：从 article / main 标签提取首段文字
        if not desc:
            for tag in ['article', 'main', 'div']:
                el = soup.find(tag)
                if el:
                    paragraphs = el.find_all('p')
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if len(text) >= 30:
                            desc = text
                            break
                if desc:
                    break

        # 策略3：直接找 p 标签
        if not desc:
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) >= 30:
                    desc = text
                    break

        # 清理描述
        if desc:
            desc = re.sub(r'\s+', ' ', desc)
            if len(desc) > 500:
                desc = desc[:500] + "..."

        return title, desc

    except Exception:
        return "", ""


def _deduplicate_by_url(articles: List[Dict]) -> List[Dict]:
    """按 URL 去重"""
    seen = set()
    unique = []
    for a in articles:
        url = a.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(a)
    return unique


def _extract_title_from_link(link, soup) -> str:
    """从链接元素提取标题（尝试多种策略）"""
    title = link.get_text(strip=True)
    if title and len(title) >= 10:
        return title
    title = link.get('title', '').strip()
    if title and len(title) >= 10:
        return title
    parent = link.parent
    if parent:
        parent_text = parent.get_text(strip=True)
        if len(parent_text) > len(link.get_text(strip=True)):
            return parent_text
    for sibling in link.find_next_siblings():
        if sibling.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span']:
            title = sibling.get_text(strip=True)
            if title and len(title) >= 10:
                return title
    return ""


def _extract_articles_from_homepage(html: str, base_url: str, article_pattern: str) -> List[Dict]:
    """从首页提取文章链接（基于精确 URL pattern）"""
    from bs4 import BeautifulSoup

    articles = []
    try:
        soup = BeautifulSoup(html, 'html.parser')
        pattern = re.compile(article_pattern)
        seen_urls = set()

        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            if pattern.search(href):
                full_url = urljoin(base_url, href)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                title = _extract_title_from_link(link, soup)
                if title and 10 <= len(title) <= 200:
                    articles.append({
                        "title": title,
                        "url": full_url,
                        "source": "whitelist_crawler",
                    })

    except Exception:
        pass

    return articles


def _fetch_rss_feed(rss_url: str, timeout: int = 15) -> Optional[str]:
    """获取 RSS feed 内容"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        response = requests.get(rss_url, headers=headers, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


def _parse_rss_and_filter(xml_content: str, base_url: str, keywords: List[str] = None,
                          max_articles: int = 50) -> List[Dict]:
    """
    解析 RSS XML，提取文章列表，按关键词过滤。
    返回格式同 _extract_articles_from_homepage。
    使用 BeautifulSoup 解析 XML，更好的中文媒体兼容性。
    """
    articles = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(xml_content, 'xml')

        # 尝试找 feed/entry 结构
        items = soup.find_all('item') or soup.find_all('entry')
        if not items:
            # 尝试 Atom 格式
            items = soup.find_all('link')

        seen_urls = set()
        for item in items[:max_articles]:
            # 提取 title
            title_elem = item.find('title')
            title = title_elem.get_text(strip=True) if title_elem else ""

            # 提取 link
            link = None
            link_elem = item.find('link')
            if link_elem:
                link = link_elem.get('href') or link_elem.get_text(strip=True) or None
            if not link:
                # 尝试 Atom 格式的 link
                if item.name == 'link':
                    link = item.get('href')
            if not link:
                continue

            # 提取 pubDate
            pub_date = ""
            for date_elem_name in ['pubDate', 'published', 'updated', 'date']:
                date_elem = item.find(date_elem_name)
                if date_elem:
                    pub_date = date_elem.get_text(strip=True)
                    break

            if not title or not link:
                continue

            # 完整化 URL
            full_url = urljoin(base_url, link)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # 关键词过滤
            if keywords:
                matched = any(kw.lower() in title.lower() for kw in keywords)
                if not matched:
                    continue

            articles.append({
                "title": title,
                "url": full_url,
                "source": "whitelist_crawler",
                "published_date": pub_date,
            })

    except Exception as e:
        print(f"    RSS 解析失败: {e}")
    return articles


def _crawl_via_rss(domain: str, config: Dict, keywords: List[str] = None,
                   max_articles: int = 50) -> List[Dict]:
    """通过 RSS Feed 抓取文章（优先使用）"""
    rss_url = config.get("rss")
    if not rss_url:
        return []

    print(f"  [RSS] {config['name']} ({domain})")

    xml_content = _fetch_rss_feed(rss_url)
    if not xml_content:
        print(f"    ✗ RSS 获取失败，尝试首页")
        return []

    articles = _parse_rss_and_filter(xml_content, config["base_url"], keywords, max_articles)
    print(f"    RSS 文章: {len(articles)} 篇")

    # 精准抓取每篇文章获取摘要
    for a in articles[:max_articles]:
        full_html = _fetch_page_content(a["url"], timeout=8)
        if full_html:
            title, content = _extract_from_html(full_html, "")
            if title:
                a["title"] = title
            a["content"] = content
        a["content_type"] = _classify_content_type(a.get("title", ""), a.get("content", ""))

    return articles
    """从首页提取文章链接（基于精确 URL pattern）"""
    from bs4 import BeautifulSoup

    articles = []
    try:
        soup = BeautifulSoup(html, 'html.parser')
        pattern = re.compile(article_pattern)
        seen_urls = set()

        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            if pattern.search(href):
                full_url = urljoin(base_url, href)
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)
                title = _extract_title_from_link(link, soup)
                if title and 10 <= len(title) <= 200:
                    articles.append({
                        "title": title,
                        "url": full_url,
                        "source": "whitelist_crawler",
                    })

    except Exception:
        pass

    return articles


def crawl_whitelist_source(domain: str, config: Dict, keywords: List[str] = None) -> List[Dict]:
    """抓取单个精确配置的垂类网站（优先 RSS，fallback 到首页）"""
    list_url = config.get("list_url")
    base_url = config.get("base_url")
    article_pattern = config.get("article_pattern")
    rss_url = config.get("rss")

    if not list_url:
        return []

    print(f"  [垂媒] {config['name']} ({domain})")

    articles = []

    # 策略1：优先使用 RSS 抓取
    if rss_url:
        articles = _crawl_via_rss(domain, config, keywords, max_articles=30)
        if articles:
            print(f"    RSS 最终: {len(articles)} 篇")
            return articles
        # RSS 失败，继续用首页

    # 策略2：首页抓取
    if not article_pattern:
        print(f"    ✗ 无 article_pattern，跳过")
        return []

    html = _fetch_page_content(list_url)
    if not html:
        print(f"    ✗ 首页抓取失败")
        return []

    articles = _extract_articles_from_homepage(html, base_url, article_pattern)

    # 关键词过滤
    if keywords:
        before = len(articles)
        articles = [
            a for a in articles
            if any(kw.lower() in a["title"].lower() for kw in keywords)
        ]
        print(f"    首页文章 {before} → 关键词过滤 {len(articles)}")
    else:
        print(f"    首页文章 {len(articles)}")

    # 精准抓取每篇文章获取摘要
    for a in articles:
        full_html = _fetch_page_content(a["url"], timeout=8)
        if full_html:
            title, content = _extract_from_html(full_html, "")
            if title:
                a["title"] = title
            a["content"] = content
        a["content_type"] = _classify_content_type(a.get("title", ""), a.get("content", ""))

    print(f"    最终: {len(articles)} 篇")
    return articles


def crawl_all_whitelist_sources(industry_keywords: Dict = None,
                                 brand_keywords: List[str] = None) -> List[Dict]:
    """抓取所有精确配置的垂类媒体"""
    all_articles = []

    for domain, config in WHITELIST_SOURCES.items():
        industry = config.get("industry", "")
        keywords = list(brand_keywords) if brand_keywords else []
        if industry_keywords and industry in industry_keywords:
            keywords.extend(industry_keywords[industry])

        articles = crawl_whitelist_source(domain, config, keywords)
        for a in articles:
            a["industry"] = industry
        all_articles.extend(articles)

    return all_articles


# Bocha site: 查询的硬性资源上限（每次 run_whitelist_crawl 调用）
_BOCHA_SITE_QUERY_LIMIT = 30  # 最多 30 次 site: 查询
_BOCHA_SITE_MAX_PER_DOMAIN = 5  # 每域名最多 5 篇文章
_BOCHA_SITE_TOP_DOMAINS = [  # 只对这些高价值域名做 site: 查询
    "36kr.com", "jiemian.com", "sohu.com", "163.com",
    "qq.com", "ithome.com", "toutiao.com", "sina.com.cn",
]
_BOCHA_SITE_CACHE_KEY_PREFIX = "site:"  # 缓存 key 前缀，site: 查询走独立缓存池


def crawl_via_bocha_site_search(
    domains: List[str],
    keywords: List[str],
    api_key: str,
    max_per_domain: int = 5,
) -> List[Dict]:
    """
    Bocha site: 辅助抓取（资源受限版）。

    策略：只对 Top 8 高价值域名做 site: 查询，且严格限制总调用量。
    所有 site: 查询结果走独立缓存，避免重复消耗 API credits。

    domains: 白名单域名列表（仅作参考，实际只用 TOP_DOMAINS）
    keywords: 关键词列表（取前 5 个最相关的）
    api_key: Bocha API key
    max_per_domain: 每域名最多抓取的文章数
    """
    from scripts.search_core import _get_cached, _set_cached

    results = []
    seen_urls = set()

    # 只处理 Top 高价值域名
    target_domains = [d for d in _BOCHA_SITE_TOP_DOMAINS if d in domains]
    if not target_domains:
        # 兜底：从 domains 中取前 3 个
        target_domains = domains[:3]

    # 取最相关的关键词（取前 5 个）
    top_keywords = keywords[:5]

    # 预计算总查询量，超过上限则采样
    total_queries = len(target_domains) * len(top_keywords)
    if total_queries > _BOCHA_SITE_QUERY_LIMIT:
        # 按域名采样：每个域名取最相关的 2-3 个关键词
        queries_per_domain = max(2, _BOCHA_SITE_QUERY_LIMIT // len(target_domains))
        top_keywords = keywords[:queries_per_domain * len(target_domains)][:queries_per_domain]

    print(f"  [Bocha辅助] 目标域名: {target_domains}")
    print(f"  [Bocha辅助] 关键词: {top_keywords}")
    print(f"  [Bocha辅助] 预计查询: {len(target_domains) * len(top_keywords)} 次（上限 {_BOCHA_SITE_QUERY_LIMIT}）")

    bocha_calls = 0

    for domain in target_domains:
        domain_lower = domain.lower()
        if bocha_calls >= _BOCHA_SITE_QUERY_LIMIT:
            print(f"  [Bocha辅助] 达到查询上限 {_BOCHA_SITE_QUERY_LIMIT}，停止")
            break

        domain_articles = 0

        for kw in top_keywords:
            if bocha_calls >= _BOCHA_SITE_QUERY_LIMIT:
                break
            if domain_articles >= max_per_domain:
                break

            cache_key = f"site:{domain}:{kw}"
            cached = _get_cached(cache_key)
            if cached is not None:
                bocha_results = cached
                print(f"  [Bocha辅助] {domain} '{kw}': 缓存命中 {len(bocha_results)} 条")
            else:
                bocha_calls += 1
                query = f"site:{domain} {kw} 2026"
                try:
                    bocha_results = search_tavily(
                        query=query,
                        api_key=api_key,
                        time_range="month",
                        max_results=8,
                    )
                    _set_cached(cache_key, bocha_results)
                    print(f"  [Bocha辅助] {domain} '{kw}': {len(bocha_results)} 条（#{bocha_calls}）")
                except Exception as e:
                    print(f"  [Bocha辅助] {domain} '{kw}': 查询失败 {e}")
                    bocha_results = []

            for r in bocha_results:
                url = r.get("url", "")
                if not url or domain_lower not in url.lower():
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # 直接抓取真实文章 URL
                html = _fetch_page_content(url, timeout=8)
                if not html:
                    # 回退：使用 Bocha 返回的标题和摘要
                    title = r.get("title", "")
                    content = r.get("content", "")
                else:
                    title, content = _extract_from_html(html, kw)
                    if not title:
                        title = r.get("title", "")

                if not title or len(title) < 5:
                    continue

                # 关键词二次校验
                text = (title + " " + content).lower()
                if kw.lower() not in text:
                    continue

                content_type = _classify_content_type(title, content)
                results.append({
                    "title": title,
                    "url": url,
                    "content": content or r.get("content", ""),
                    "source": "whitelist_crawler",
                    "content_type": content_type,
                })
                domain_articles += 1

                if domain_articles >= max_per_domain:
                    break

        print(f"  [Bocha辅助] {domain}: 抓取 {domain_articles} 篇（累计 {len(results)} 篇）")

    print(f"  [Bocha辅助] 实际 Bocha 调用: {bocha_calls} 次（上限 {_BOCHA_SITE_QUERY_LIMIT}）")
    return results


def run_whitelist_crawl(config: Dict, profile_brands: List[str] = None,
                          bocha_api_key: str = None) -> List[Dict]:
    """
    运行完整的白名单抓取流程 v5。

    config: 用户配置（包含行业关键词等）
    profile_brands: 用户关注的品牌列表
    bocha_api_key: Bocha API key（可选，默认从环境变量读取）
    """
    print("\n" + "="*60)
    print("白名单网站直接抓取 v5 - Bocha site: 辅助策略")
    print("="*60 + "\n")

    # 加载 Bocha API key
    if not bocha_api_key:
        _ensure_env()
        bocha_api_key = os.environ.get("BOCHA_API_KEY", "")

    # ── 构建关键词列表 ───────────────────────────────
    industry_keywords = {}
    if config.get("industries"):
        for industry_config in config["industries"]:
            name = industry_config.get("name", "")
            kws = industry_config.get("keywords", [])
            if name and kws:
                # 关键词字符串拆分成单个词，提高 Bocha site: 查询精度
                words = []
                for kw_str in kws:
                    words.extend(kw_str.split())
                industry_keywords[name] = words
                print(f"  [行业关键词] {name}: {len(words)} 个")

    brand_keywords = list(profile_brands) if profile_brands else []
    print(f"  [品牌关键词] {len(brand_keywords)} 个: {', '.join(brand_keywords[:5])}{'...' if len(brand_keywords) > 5 else ''}")

    # 合并所有关键词（用于 Bocha site: 查询）
    all_keywords = brand_keywords.copy()
    for kws in industry_keywords.values():
        all_keywords.extend(kws)
    # 去重
    all_keywords = list(dict.fromkeys(all_keywords))

    # ── 阶段1: 精确垂类媒体 ───────────────────────────
    print("\n  [阶段1] 精确垂类媒体抓取...")
    articles_1 = crawl_all_whitelist_sources(industry_keywords, brand_keywords)

    # ── 阶段2: Bocha site: 辅助抓取 ──────────────────
    print("\n  [阶段2] Bocha site: 辅助精准抓取...")

    # 加载白名单域名
    whitelist_path = os.path.join(os.path.dirname(__file__), "..", "data", "domain_whitelist.json")
    whitelist_domains = []
    if os.path.exists(whitelist_path):
        try:
            with open(whitelist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for domains in data.values():
                    whitelist_domains.extend(domains)
            whitelist_domains = list(dict.fromkeys(whitelist_domains))
        except Exception:
            pass

    # 排除已有精确配置的域名（避免重复抓取）
    configured_domains = set(WHITELIST_SOURCES.keys())
    dynamic_domains = [d for d in whitelist_domains if d not in configured_domains]

    print(f"  动态域名: {len(dynamic_domains)} 个（如已有精确配置则跳过）")

    if dynamic_domains and all_keywords and bocha_api_key:
        articles_2 = crawl_via_bocha_site_search(
            domains=dynamic_domains,
            keywords=all_keywords,
            api_key=bocha_api_key,
            max_per_domain=_BOCHA_SITE_MAX_PER_DOMAIN,
        )
    else:
        print("  跳过（无 API key 或无关键词）")
        articles_2 = []

    # ── 合并去重 ────────────────────────────────────
    all_articles = articles_1 + articles_2
    unique_articles = _deduplicate_by_url(all_articles)

    # 内容类型统计
    type_counts = {}
    for a in unique_articles:
        ct = a.get("content_type", "brand_news")
        type_counts[ct] = type_counts.get(ct, 0) + 1

    print(f"\n  [完成] 共抓取 {len(unique_articles)} 篇文章")
    print(f"    - 垂类媒体: {len(articles_1)} 篇")
    print(f"    - Bocha辅助: {len(articles_2)} 篇")
    if type_counts:
        print(f"    - 内容类型: 客户新闻 {type_counts.get('brand_news', 0)}, "
              f"融资 {type_counts.get('fundraising', 0)}, "
              f"代言人 {type_counts.get('endorsement', 0)}")
    print("="*60 + "\n")

    return unique_articles


def _ensure_env():
    """加载 .env"""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    # 测试
    test_config = {
        "industries": [
            {
                "name": "新能源汽车",
                "keywords": [
                    "小米 OPPO 荣耀 vivo 新品发布",
                    "比亚迪 新车 发布",
                    "消费电子 融资 亿元",
                ]
            }
        ]
    }
    test_brands = ["问界", "智界", "比亚迪", "小米汽车"]

    articles = run_whitelist_crawl(test_config, test_brands)
    print(f"\n测试结果: {len(articles)} 篇文章")
    for i, a in enumerate(articles[:10], 1):
        ct = a.get("content_type", "?")
        print(f"{i}. [{ct}] {a['title'][:60]}")
        print(f"   {a['url']}")
