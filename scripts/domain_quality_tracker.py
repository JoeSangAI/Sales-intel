"""
域名质量追踪器 — 每日记录每个域名的搜索命中表现

每天运行时：
1. 读取当天的搜索缓存（bocha_{date}.json）
2. 对每条结果评估是否有价值（关键词匹配）
3. 按域名聚合，写入 data/domain_tracking/tracking_{date}.json
4. 更新 data/domain_tracking_summary.json（滚动7天统计）
"""

import os
import json
from datetime import datetime, timedelta
from collections import defaultdict
from urllib.parse import urlparse

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
TRACKING_DIR = os.path.join(PROJECT_ROOT, "data", "domain_tracking")
SUMMARY_PATH = os.path.join(PROJECT_ROOT, "data", "domain_tracking_summary.json")


def _ensure_env():
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_ensure_env()


# ============================================================
# 评估逻辑（与 domain_analyzer.py 保持一致）
# ============================================================

SIGNAL_PATTERNS = {
    "新品发布": ["新品", "发布", "上市", "推出", "首发", "亮相", "问世"],
    "代言人": ["代言", "代言人", "品牌大使", "形象大使", "官宣"],
    "CMO换人": ["cmo", "首席营销官", "品牌总监", "市场总监", "营销副总裁"],
    "融资": ["融资", "获投", "完成", "轮融资", "亿元", "投资"],
    "新市场": ["进入", "布局", "开拓", "扩张", "新市场", "新赛道"],
    "大规模投放": ["投放", "广告", "营销", "campaign", "传播"],
}

NOISE_KEYWORDS = ["股价", "涨停", "跌停", "财报", "年会", "招聘"]


def evaluate_result(result: dict) -> tuple[bool, str]:
    """评估单条搜索结果是否有价值。返回 (有价值, 信号类型)"""
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    content = (title + " " + snippet).lower()

    for signal_type, keywords in SIGNAL_PATTERNS.items():
        if any(kw in content for kw in keywords):
            return True, signal_type

    if any(kw in content for kw in NOISE_KEYWORDS):
        return False, ""

    return False, ""


def extract_domain(url: str) -> str:
    """从 URL 提取域名"""
    if not url:
        return ""
    domain = urlparse(url).netloc
    # 去掉 www. 前缀便于匹配白名单
    return domain.replace("www.", "")


# ============================================================
# 每日快照
# ============================================================

def record_daily(date_str: str = None) -> dict:
    """
    对指定日期的搜索结果进行域名质量评估，写入每日快照。
    返回该日的统计数据。
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    cache_file = os.path.join(PROJECT_ROOT, "data", "search_cache", f"bocha_{date_str}.json")
    if not os.path.exists(cache_file):
        print(f"  [追踪] 找不到缓存: {cache_file}")
        return {}

    with open(cache_file, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    # 按域名聚合
    domain_stats = defaultdict(lambda: {"total": 0, "valuable": 0, "signals": defaultdict(int)})

    for query, results in cache_data.items():
        for result in results:
            url = result.get("url", "")
            domain = extract_domain(url)
            if not domain:
                continue

            is_valuable, signal_type = evaluate_result(result)
            domain_stats[domain]["total"] += 1
            if is_valuable:
                domain_stats[domain]["valuable"] += 1
                if signal_type:
                    domain_stats[domain]["signals"][signal_type] += 1

    # 写入每日快照
    tracking_file = os.path.join(TRACKING_DIR, f"tracking_{date_str}.json")
    os.makedirs(TRACKING_DIR, exist_ok=True)

    # 转换为可序列化格式
    serializable = {}
    for domain, stats in domain_stats.items():
        serializable[domain] = {
            "total": stats["total"],
            "valuable": stats["valuable"],
            "hit_rate": stats["valuable"] / stats["total"] if stats["total"] > 0 else 0,
            "signals": dict(stats["signals"]),
            "date": date_str,
        }

    with open(tracking_file, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

    print(f"  [追踪] {date_str} 写入 {len(serializable)} 个域名的数据 → {tracking_file}")
    return serializable


# ============================================================
# 滚动统计（过去7天）
# ============================================================

def compute_rolling_summary(days: int = 7) -> dict:
    """
    计算过去N天的滚动统计数据。
    返回: {domain: {total_7d, valuable_7d, hit_rate_7d, days_seen, signal_breakdown}}
    """
    today = datetime.now()
    domain_agg = defaultdict(lambda: {
        "total": 0,
        "valuable": 0,
        "days_seen": 0,
        "signals": defaultdict(int),
        "daily_hit_rates": [],  # 每天的命中率，用于观察波动
    })

    for i in range(days):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        tracking_file = os.path.join(TRACKING_DIR, f"tracking_{date_str}.json")

        if not os.path.exists(tracking_file):
            continue

        with open(tracking_file, "r", encoding="utf-8") as f:
            daily_data = json.load(f)

        for domain, stats in daily_data.items():
            agg = domain_agg[domain]
            agg["total"] += stats["total"]
            agg["valuable"] += stats["valuable"]
            agg["days_seen"] += 1
            agg["daily_hit_rates"].append(stats["hit_rate"])
            for sig, cnt in stats.get("signals", {}).items():
                agg["signals"][sig] += cnt

    # 计算最终统计
    summary = {}
    for domain, agg in domain_agg.items():
        total = agg["total"]
        valuable = agg["valuable"]
        summary[domain] = {
            "total_7d": total,
            "valuable_7d": valuable,
            "hit_rate_7d": valuable / total if total > 0 else 0,
            "days_seen": agg["days_seen"],
            "signals": dict(agg["signals"]),
            "avg_daily_hit_rate": sum(agg["daily_hit_rates"]) / len(agg["daily_hit_rates"])
                if agg["daily_hit_rates"] else 0,
            "last_updated": today.strftime("%Y-%m-%d"),
        }

    return summary


def save_summary(summary: dict) -> None:
    """保存滚动统计到文件"""
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  [追踪] 滚动统计已更新: {len(summary)} 个域名")


# ============================================================
# 主流程
# ============================================================

def run_tracker(date_str: str = None) -> dict:
    """
    每日追踪主流程：
    1. 记录当日快照
    2. 更新滚动统计
    3. 返回当日统计
    """
    print("\n" + "=" * 50)
    print("域名质量追踪器")
    print("=" * 50 + "\n")

    target_date = date_str or datetime.now().strftime("%Y-%m-%d")

    # Step 1: 记录当日数据
    daily_stats = record_daily(target_date)

    # Step 2: 更新滚动7天统计
    summary = compute_rolling_summary(days=7)
    save_summary(summary)

    # 打印当日 TOP 域名
    if daily_stats:
        sorted_domains = sorted(daily_stats.items(), key=lambda x: x[1]["valuable"], reverse=True)
        print(f"\n  [TOP] {target_date} 高价值域名 TOP10:")
        for domain, stats in sorted_domains[:10]:
            print(f"    {domain}: {stats['valuable']}/{stats['total']} ({stats['hit_rate']:.0%})")

    print("\n" + "=" * 50)
    print("追踪完成")
    print("=" * 50 + "\n")

    return daily_stats


if __name__ == "__main__":
    import sys
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_tracker(date_arg)
