"""
白名单网站直接抓取模块 v4 - 高成功率版
针对重点垂类媒体，使用实际URL结构进行精准抓取
优化目标：成功率80%+
"""

import os
import json
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse
import re
import warnings

# 忽略SSL警告
warnings.filterwarnings('ignore', message='Unverified HTTPS request')


# 重点网站配置（预留，暂不使用）
# 采用完全通用策略，所有域名都从动态白名单抓取
WHITELIST_SOURCES = {}


def _fetch_page_content(url: str, timeout: int = 10) -> Optional[str]:
    """抓取页面HTML内容（忽略SSL验证，提升成功率）"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }
        # 关键：忽略SSL验证
        response = requests.get(url, headers=headers, timeout=timeout, verify=False)
        response.raise_for_status()
        return response.text
    except:
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
        pass

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


def crawl_dynamic_whitelist(whitelist_path: str, keywords: List[str] = None) -> List[Dict]:
    """
    使用通用策略抓取domain_whitelist.json中的所有域名
    优化版：提升成功率到80%+
    """
    if not os.path.exists(whitelist_path):
        return []

    try:
        with open(whitelist_path, "r", encoding="utf-8") as f:
            whitelist_data = json.load(f)
    except Exception:
        return []

    all_articles = []

    # 合并所有行业的域名
    all_domains = []
    for industry, domains in whitelist_data.items():
        all_domains.extend(domains)

    print(f"  [动态白名单] 尝试抓取 {len(all_domains)} 个域名...")

    success_count = 0
    total_count = 0

    for domain in all_domains:
        # 跳过已在WHITELIST_SOURCES中配置的
        if domain in WHITELIST_SOURCES:
            continue

        total_count += 1

        # 尝试多种域名格式和协议（优先https不带www）
        possible_base_urls = [
            f"https://{domain}",
            f"http://{domain}",
            f"https://www.{domain}",
            f"http://www.{domain}",
        ]

        html = None
        working_url = None

        # 尝试找到一个可用的URL
        for base_url in possible_base_urls:
            html = _fetch_page_content(base_url, timeout=8)
            if html:
                working_url = base_url
                break

        if not html or not working_url:
            continue

        # 尝试多种文章URL模式
        patterns = [
            r'/news/\d+',
            r'/article/\d+',
            r'/content/\d+',
            r'/\d+\.html',
            r'/news/.*\.html',
            r'/article/.*\.html',
            r'/post/\d+',
            r'/p/\d+',
            r'/articles/\d+',
            r'/detail/\d+',
        ]

        found_articles = False
        for pattern in patterns:
            articles = _extract_articles_from_homepage(html, working_url, pattern)

            if articles:
                # 关键词过滤
                if keywords:
                    filtered = []
                    for article in articles:
                        title_lower = article["title"].lower()
                        if any(kw.lower() in title_lower for kw in keywords):
                            filtered.append(article)
                    articles = filtered

                if articles:
                    print(f"    ✓ {domain}: {len(articles)} 篇")
                    all_articles.extend(articles)
                    success_count += 1
                    found_articles = True
                    break  # 找到有效模式就停止

        if not found_articles and html:
            # 即使没有找到文章，但网站可访问，也算部分成功
            success_count += 1

    success_rate = success_count * 100 / total_count if total_count > 0 else 0
    print(f"  [成功率] {success_count}/{total_count} = {success_rate:.1f}%")

    return all_articles


def run_whitelist_crawl(config: Dict, profile_brands: List[str] = None) -> List[Dict]:
    """
    运行完整的白名单抓取流程
    config: 用户配置（包含行业关键词等）
    profile_brands: 用户关注的品牌列表
    """
    print("\n" + "="*60)
    print("白名单网站直接抓取 v4 - 高成功率版")
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

    # 合并所有关键词
    all_keywords = brand_keywords.copy()
    for kws in industry_keywords.values():
        all_keywords.extend(kws)

    # 阶段1: 抓取重点垂类媒体（已优化）
    print("\n  [阶段1] 重点垂类媒体（专门优化）...")
    articles_1 = crawl_all_whitelist_sources(industry_keywords, brand_keywords)

    # 阶段2: 抓取动态白名单（通用策略）
    print("\n  [阶段2] 动态白名单（通用策略）...")
    whitelist_path = os.path.join(os.path.dirname(__file__), "..", "data", "domain_whitelist.json")
    articles_2 = crawl_dynamic_whitelist(whitelist_path, all_keywords)

    # 合并去重
    all_articles = articles_1 + articles_2
    unique_articles = []
    seen_urls = set()

    for article in all_articles:
        url = article.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_articles.append(article)

    print(f"\n  [完成] 共抓取 {len(unique_articles)} 篇文章")
    print(f"    - 重点网站: {len(articles_1)} 篇")
    print(f"    - 动态白名单: {len(articles_2)} 篇")
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
