"""
白名单网站直接抓取模块 v2
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
        "list_url": "https://www.jumeili.cn",  # 首页
        "article_pattern": r'/news/view/\d+\.html',
        "industry": "美妆",
    },
    "pinguan.com": {
        "name": "品观网",
        "base_url": "https://www.pinguan.com",
        "list_url": "https://www.pinguan.com",  # 首页
        "article_pattern": r'/article/content/\d+',
        "industry": "美妆",
    },
    "c2cc.cn": {
        "name": "C2CC传媒",
        "base_url": "https://www.c2cc.cn",
        "list_url": "https://www.c2cc.cn",  # 首页
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


def _extract_articles_from_homepage(html: str, base_url: str, article_pattern: str) -> List[Dict]:
    """
    从首页提取文章链接（基于实际URL模式）
    """
    from bs4 import BeautifulSoup

    articles = []
    try:
        soup = BeautifulSoup(html, 'html.parser')
        pattern = re.compile(article_pattern)

        # 找出所有匹配模式的链接
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            title = link.get_text(strip=True)

            # 检查是否匹配文章URL模式
            if pattern.search(href):
                # 补全URL
                full_url = urljoin(base_url, href)

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

    # 去重
    seen_urls = set()
    unique_articles = []
    for article in articles:
        if article["url"] not in seen_urls:
            seen_urls.add(article["url"])
            unique_articles.append(article)

    return unique_articles


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

    # 关键词过滤
    if keywords:
        filtered = []
        for article in articles:
            title_lower = article["title"].lower()
            if any(kw.lower() in title_lower for kw in keywords):
                filtered.append(article)
        articles = filtered

    print(f"    找到 {len(articles)} 篇文章")
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
    print("白名单网站直接抓取 v2")
    print("="*60 + "\n")

    # 构建行业关键词
    industry_keywords = {}
    if config.get("industry_research"):
        for industry_config in config["industry_research"]:
            industry_name = industry_config.get("name", "")
            keywords = industry_config.get("keywords", [])
            if industry_name and keywords:
                industry_keywords[industry_name] = keywords

    # 品牌关键词
    brand_keywords = profile_brands or []

    # 只抓取配置的重点网站（不再尝试动态白名单）
    print("  [抓取] 重点垂类媒体...")
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
        "industry_research": [
            {"name": "美妆", "keywords": ["美妆", "化妆品", "护肤", "彩妆"]},
        ]
    }
    test_brands = ["花西子", "完美日记", "珀莱雅"]

    articles = run_whitelist_crawl(test_config, test_brands)
    print(f"\n测试结果: {len(articles)} 篇文章")
    for i, article in enumerate(articles[:10], 1):
        print(f"{i}. {article['title'][:60]}... ({article.get('industry', '未知')})")
        print(f"   {article['url']}")
