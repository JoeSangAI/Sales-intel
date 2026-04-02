"""
高质量域名自动发现与管理
当 Bocha 返回某域名的高质量内容时，自动将其加入白名单。

策略：
1. 每次搜索结果进来时，分析返回的 URL 所属域名
2. 对未知域名，检查其内容质量（标题/摘要相关性）
3. 高质量新域名 → 加入 good_domains.json 持久化
4. 下次搜索时，已知好域名直接放行
"""

import os
import json
import re
from typing import Optional

_DISCOVERY_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "good_domains.json"
)

# 初始高质量域名白名单（与 ZH_NEWS_DOMAINS 一致）
_INITIAL_DOMAINS = {
    # 核心高质量媒体
    "36kr.com", "jiemian.com", "thepaper.cn", "sina.com.cn",
    "163.com", "qq.com", "sohu.com", "ifeng.com",
    "caixin.com", "yicai.com", "cls.cn", "wallstreetcn.com",
    "huxiu.com", "tmtpost.com", "geekpark.net", "leiphone.com",
    "ithome.com", "cnbeta.com.tw", "cnr.cn", "xinhuanet.com",
    "autohome.com.cn", "pcauto.com.cn", "dongchedi.com",
    "k.sina.com.cn",
    # 微信公众号
    "mp.weixin.qq.com", "weixin.qq.com",
    # 今日头条
    "toutiao.com", "bytedance.com", "toutiao.cn",
    # 社交媒体
    "weibo.com", "xiaohongshu.com", "bilibili.com",
    # 新闻门户
    "news.qq.com", "news.163.com", "news.sina.com.cn",
    # 行业垂直媒体（已知高质量）
    "woshipm.com",      # 人人都是产品经理
    "smzdm.com",         # 什么值得买
    "alibaba.com",       # 阿里官方
    "jd.com",            # 京东
    "amazon.cn",         # 亚马逊
}

# 已知内容农场域名（直接黑名单）
_KNOWN_CONTENT_FARM = {
    "chinairn.com", "bst-cert.com", "zol.com.cn", "cnyes.com",
    "cnblogs.com", "csdn.net", "aliyun.com", "bearst.cn",
    "iDonews.com", "zhuolihaichuang.com", "cnpp.cn", "donewn.com",
    "rugod.com", "ppt101.com", "pptjia.com", "ypppt.com",
}


def _load_discovered_domains() -> set:
    """加载已发现的高质量域名"""
    if os.path.exists(_DISCOVERY_FILE):
        try:
            with open(_DISCOVERY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                domains = set(data.get("domains", []))
                # 合并初始域名
                return domains | _INITIAL_DOMAINS
        except Exception as e:
            print(f"  [警告] 加载域名发现缓存失败: {e}")
    return _INITIAL_DOMAINS.copy()


def _save_discovered_domains(domains: set) -> None:
    """保存新发现的高质量域名"""
    os.makedirs(os.path.dirname(_DISCOVERY_FILE), exist_ok=True)
    data = {"domains": sorted(list(domains))}
    with open(_DISCOVERY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_domain(url: str) -> str:
    """从 URL 提取域名"""
    if not url:
        return ""
    domain = re.sub(r"^https?://(www\.)?", "", url.split("/")[0])
    return domain.lower()


def is_content_farm(domain: str) -> bool:
    """判断是否为已知内容农场"""
    for farm in _KNOWN_CONTENT_FARM:
        if farm in domain:
            return True
    return False


def analyze_result_quality(title: str, content: str, brand_names: list[str]) -> float:
    """
    分析单条搜索结果的质量分数（0-10）
    考虑因素：
    - 标题是否包含品牌名（强相关）
    - 内容摘要是否包含品牌名
    - 标题是否为完整的文章标题（非列表页/标签页）
    - 内容长度（过短可能是低质量摘要）
    """
    score = 5.0  # 默认基础分

    title_lower = title.lower()
    content_lower = content[:200].lower() if content else ""

    # 标题包含品牌名：+2分
    if any(bn.lower() in title_lower for bn in brand_names if bn):
        score += 2.0

    # 内容包含品牌名：+1分
    if any(bn.lower() in content_lower for bn in brand_names if bn):
        score += 1.0

    # 标题是完整文章标题特征（有一定长度且有明确主题）：+1分
    if len(title) >= 10 and any(c in title for c in "，、。："):
        score += 1.0

    # 内容过短（<20字）：-2分（可能是标题党或低质量摘要）
    if len(content) < 20:
        score -= 2.0

    # 内容包含"暂无动态"/"暂无资讯"等词：-3分
    if any(kw in content_lower for kw in ["暂无动态", "暂无资讯", "暂无更新", "暂无新闻"]):
        score -= 3.0

    return max(0.0, min(10.0, score))


def discover_good_domains(results: list[dict], brand_names: list[str], min_quality: float = 6.0) -> set:
    """
    从搜索结果中发现新的高质量域名

    返回：在本批结果中出现的高质量新域名（不在白名单中的）
    """
    domain_scores: dict = {}  # domain -> (count, avg_quality)

    for r in results:
        url = r.get("url", "")
        domain = extract_domain(url)
        if not domain or is_content_farm(domain):
            continue

        title = r.get("title", "")
        content = r.get("content", "")
        quality = analyze_result_quality(title, content, brand_names)

        if domain not in domain_scores:
            domain_scores[domain] = [0, 0.0]
        domain_scores[domain][0] += 1
        domain_scores[domain][1] += quality

    # 找出平均质量达标的新域名
    current_whitelist = _load_discovered_domains()
    new_good_domains = set()

    for domain, (count, total_quality) in domain_scores.items():
        if domain in current_whitelist:
            continue
        avg_quality = total_quality / count if count > 0 else 0
        # 至少返回2条结果，且平均质量 >= min_quality
        if count >= 2 and avg_quality >= min_quality:
            new_good_domains.add(domain)

    return new_good_domains


def register_new_domains(new_domains: set) -> None:
    """将新发现的高质量域名注册到白名单"""
    if not new_domains:
        return
    current = _load_discovered_domains()
    updated = current | new_domains
    _save_discovered_domains(updated)
    print(f"  [域名发现] 新增 {len(new_domains)} 个高质量域名: {sorted(new_domains)}")


def get_good_domains() -> set:
    """获取当前完整的高质量域名白名单"""
    return _load_discovered_domains()


def is_good_domain(url: str, good_domains: set = None) -> bool:
    """判断URL是否来自高质量域名"""
    if not url:
        return False
    domain = extract_domain(url)
    if not domain:
        return False
    if is_content_farm(domain):
        return False
    if good_domains is None:
        good_domains = _load_discovered_domains()
    return any(d in domain for d in good_domains)
