"""
质检第一层：规则校验（确定性，零 LLM 调用）
"""

import re
from datetime import datetime


_NOISE_DOMAINS_QC = [
    "chinairn.com", "chinabgao.com", "askci.com",
    "stockstar.com", "stock.sohu.com",
    "trustexporter.com", "globalimporter.net",
    "topnews.cn", "bbs.q.sina.com.cn", "winshang.com",
    "zutong.cn", "ztock.cn", "stockway.cn",
]


def _is_noise_domain(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in _NOISE_DOMAINS_QC)


def run_rules_check(report: str, original_items: list, profile: dict) -> dict:
    """
    第一层规则校验。

    返回:
        {
            "pass": bool,        # True = 全部通过
            "issues": list,      # 致命问题（阻断）
            "warnings": list,    # 非致命警告
        }
    """
    issues = []
    warnings = []

    # ── 构建白名单集合 ──
    brand_names = set()
    for b in profile.get("brands", []):
        brand_names.add(b.get("name", "").lower())
        for sub in b.get("sub_brands", []):
            brand_names.add(sub.lower())

    track_names = {t.get("name", "") for t in profile.get("fundraising", {}).get("tracks", [])}
    industry_names = {i.get("name", "") for i in profile.get("industries", [])}
    allowed_sectors = track_names | industry_names

    # ── 提取报告中的所有 URL ──
    report_urls = re.findall(r'\[([^\]]+)\]\((https?://[^\)]+)\)', report)

    # ── 构建原始数据 URL 集合 ──
    valid_urls = {item.get("url", "") for item in original_items}

    # ── R1: URL 必须在原始数据中 ──
    for text, url in report_urls:
        if url not in valid_urls:
            issues.append(f"[R1 阻断] 链接不在原始数据中: {url}")

    # ── R3: 日期真实性（超过7天警告） ──
    date_pattern = re.compile(r'20\d{2}[-/]\d{2}[-/]\d{2}')
    for text, url in report_urls:
        dates_in_report = date_pattern.findall(text)
        for d in dates_in_report:
            try:
                date_str = d.replace('/', '-')
                pub = datetime.strptime(date_str, "%Y-%m-%d")
                diff = (datetime.now() - pub).days
                if diff > 7:
                    warnings.append(f"[R3 警告] 日期过早（{diff}天前）: {d}，链接: {url}")
            except:
                pass

    # ── R5: 噪音域名 ──
    for text, url in report_urls:
        if _is_noise_domain(url):
            issues.append(f"[R5 阻断] 噪音域名: {url}")

    return {
        "pass": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }
