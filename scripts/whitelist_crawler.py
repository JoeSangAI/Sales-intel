"""
白名单网站直接抓取模块 v3 - 优化版
针对重点垂类媒体，使用实际URL结构进行精准抓取
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
import re


# 高质量垂类媒体配置（基于实际URL结构）
WHITELIST_SOURCES = {
    "jumeili.cn": {
        "name": "聚美丽",
        "base_url": "https://www.jumeili.cn",
        "list_url": "https://www.jumeili.cn",
        "article_pattern": r'/news/view/\d+\.html',
        "industry": "美妆",
    },
    "pinguan.com": {
        "name": "品观网",
        "base_url": "https://www.pinguan.com",
        "list_url": "https://www.pinguan.com",
        "article_pattern": r'/article/content/\d+',
        "industry": "美妆",
    },
    "c2cc.cn": {
        "name": "C2CC传媒",
        "base_url": "https://www.c2cc.cn",
        "list_url": "https://www.c2cc.cn",
        "article_pattern": r'/news/\d+\.html',
        "industry": "美妆",
    },
}


def _fetch_page_content(url: str, timeout: int = 10) -> Optional[str]:
    """抓取页面HTML内容"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"  [抓取失败] {url}: {e}")
        return None


def _extract_title_from_link(link, soup) -> str:
    """从链接元素提取标题（尝试多种策略）"""
    # 策略1: 链接本身的文字
    title = link.get_text(strip=True)
    if title and len(title) >= 10:
        return title

    # 策略2: 链接的title属性
    title = link.get('title', '').strip()
    if title and len(title) >= 10:
        return title

    # 策略3: 父元素的文字（排除链接本身）
    parent = link.parent
    if parent:
        # 获取父元素的所有文字
        parent_text = parent.get_text(strip=True)
        # 如果父元素文字比链接文字长，使用父元素文字
        if len(parent_text) > len(link.get_text(strip=True)):
            return parent_text

    # 策略4: 查找相邻的标题元素
    for sibling in link.find_next_siblings():
        if sibling.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'span']:
            title = sibling.get_text(strip=True)
            if title and len(title) >= 10:
                return title

    return ""


def _extract_articles_from_homepage(html: str, base_url: str, article_pattern: str) -> List[Dict]:
    """从首页提取文章链接（基于实际URL模式）"""
    from bs4 import BeautifulSoup

    articles = []
    try:
        soup = BeautifulSoup(html, 'html.parser')
        pattern = re.compile(article_pattern)

        # 找出所有匹配模式的链接
        seen_urls = set()
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')

            # 检查是否匹配文章URL模式
            if pattern.search(href):
                # 补全URL
                full_url = urljoin(base_url, href)

                # 去重
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # 提取标题（尝试多种策略）
                title = _extract_title_from_link(link, soup)

                # 过滤标题
                if title and len(title) >= 10 and len(title) <= 200:
                    articles.append({
                        "title": title,
                        "url": full_url,
                        "snippet": "",
                        "published_date": "",
                        "source": "whitelist_crawler"
                    })

    except Exception as e:
        print(f"  [解析失败] {e}")

    return articles


def crawl_whitelist_source(domain: str, config: Dict, keywords: List[str] = None) -> List[Dict]:
    """
    抓取单个白名单网站的最新文章
    keywords: 用于过滤的关键词列表
    """
    list_url = config.get("list_url")
    base_url = config.get("base_url")
    article_pattern = config.get("article_pattern")

    if not list_url or not article_pattern:
        return []

    print(f"  [抓取] {config['name']} ({domain})")

    # 抓取首页
    html = _fetch_page_content(list_url)
    if not html:
        return []

    # 提取文章链接
    articles = _extract_articles_from_homepage(html, base_url, article_pattern)

    print(f"    原始文章数: {len(articles)}")

    # 关键词过滤
    if keywords:
        filtered = []
        for article in articles:
            title_lower = article["title"].lower()
            if any(kw.lower() in title_lower for kw in keywords):
                filtered.append(article)
        print(f"    关键词过滤后: {len(filtered)}")
        articles = filtered
    else:
        print(f"    无关键词过滤")

    print(f"    最终结果: {len(articles)} 篇文章")
    return articles


def crawl_all_whitelist_sources(industry_keywords: Dict[str, List[str]] = None,
                                  brand_keywords: List[str] = None) -> List[Dict]:
    """
    抓取所有配置的白名单网站
    industry_keywords: {行业名: [关键词列表]}
    brand_keywords: 品牌关键词列表
    """
    all_articles = []

    for domain, config in WHITELIST_SOURCES.items():
        industry = config.get("industry", "")

        # 构建关键词列表
        keywords = []
        if industry_keywords and industry in industry_keywords:
            keywords.extend(industry_keywords[industry])
        if brand_keywords:
            keywords.extend(brand_keywords)

        # 抓取
        articles = crawl_whitelist_source(domain, config, keywords)

        # 标记行业
        for article in articles:
            article["industry"] = industry

        all_articles.extend(articles)

    return all_articles


def run_whitelist_crawl(config: Dict, profile_brands: List[str] = None) -> List[Dict]:
    """
    运行完整的白名单抓取流程（仅抓取重点垂类媒体）
    config: 用户配置（包含行业关键词等）
    profile_brands: 用户关注的品牌列表
    """
    print("\n" + "="*60)
    print("白名单网站直接抓取 v3")
    print("="*60 + "\n")

    # 构建行业关键词（从industries配置中提取）
    industry_keywords = {}
    if config.get("industries"):
        for industry_config in config["industries"]:
            industry_name = industry_config.get("name", "")
            keywords = industry_config.get("keywords", [])
            if industry_name and keywords:
                # 将关键词字符串拆分成单个关键词
                all_keywords = []
                for kw_str in keywords:
                    # 按空格拆分，提取单个关键词
                    all_keywords.extend(kw_str.split())
                industry_keywords[industry_name] = all_keywords
                print(f"  [行业关键词] {industry_name}: {len(all_keywords)}个关键词")

    # 品牌关键词
    brand_keywords = profile_brands or []
    print(f"  [品牌关键词] {len(brand_keywords)}个品牌")

    # 只抓取配置的重点网站
    print("\n  [抓取] 重点垂类媒体...")
    articles = crawl_all_whitelist_sources(industry_keywords, brand_keywords)

    # 去重
    unique_articles = []
    seen_urls = set()

    for article in articles:
        url = article.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_articles.append(article)

    print(f"\n  [完成] 共抓取 {len(unique_articles)} 篇文章")
    print("="*60 + "\n")

    return unique_articles


if __name__ == "__main__":
    # 测试
    test_config = {
        "industries": [
            {
                "name": "美妆",
                "keywords": [
                    "PMPM 芭妮兰 毕生之研 新品发布",
                    "功效护肤 成分党 品牌升级",
                    "护肤品 融资 亿元"
                ]
            }
        ]
    }
    test_brands = ["PMPM", "芭妮兰", "毕生之研"]

    articles = run_whitelist_crawl(test_config, test_brands)
    print(f"\n测试结果: {len(articles)} 篇文章")
    for i, article in enumerate(articles[:10], 1):
        print(f"{i}. {article['title'][:60]}... ({article.get('industry', '未知')})")
        print(f"   {article['url']}")
