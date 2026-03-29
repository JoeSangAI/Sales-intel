"""
域名质量分析器
分析过去7天的搜索结果,评估各域名质量,生成白名单更新建议
"""

import os
import json
import glob
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from collections import defaultdict
import concurrent.futures

# MiniMax API 配置
def _ensure_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_ensure_env()

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")


def load_search_results(days: int = 7) -> List[Dict]:
    """加载过去N天的搜索结果"""
    cache_dir = os.path.join(os.path.dirname(__file__), "..", "data", "search_cache")
    all_results = []

    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        cache_file = os.path.join(cache_dir, f"bocha_{date}.json")

        if not os.path.exists(cache_file):
            continue

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                # cache_data 格式: {query: [results]}
                for query, results in cache_data.items():
                    for result in results:
                        result["_query"] = query
                        result["_date"] = date
                        all_results.append(result)
        except Exception as e:
            print(f"  [警告] 加载缓存失败 {cache_file}: {e}")

    print(f"  [分析] 加载了过去{days}天的搜索结果: {len(all_results)} 条")
    return all_results


def evaluate_result_value(result: Dict) -> Tuple[bool, str, str]:
    """
    用关键词匹配评估单条搜索结果是否包含广告投放需求信号
    返回: (是否有价值, 信号类型, 理由)
    """
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    content = (title + " " + snippet).lower()

    # 高价值信号关键词
    signal_patterns = {
        "新品发布": ["新品", "发布", "上市", "推出", "首发", "亮相", "问世"],
        "代言人": ["代言", "代言人", "品牌大使", "形象大使", "官宣"],
        "CMO换人": ["cmo", "首席营销官", "品牌总监", "市场总监", "营销副总裁"],
        "融资": ["融资", "获投", "完成", "轮融资", "亿元", "投资"],
        "新市场": ["进入", "布局", "开拓", "扩张", "新市场", "新赛道"],
        "大规模投放": ["投放", "广告", "营销", "campaign", "传播"],
    }

    # 检查是否命中高价值信号
    for signal_type, keywords in signal_patterns.items():
        matched_keywords = [kw for kw in keywords if kw in content]
        if matched_keywords:
            return True, signal_type, f"命中: {matched_keywords[0]}"

    # 低价值信号（排除）
    noise_keywords = ["股价", "涨停", "跌停", "财报", "年会", "招聘"]
    if any(kw in content for kw in noise_keywords):
        return False, "", "低价值信号"

    return False, "", "无明显信号"


def sample_results_by_domain(results: List[Dict], max_per_domain: int = 5) -> List[Dict]:
    """对每个域名采样,避免评估过多结果"""
    from urllib.parse import urlparse
    from collections import defaultdict

    domain_results = defaultdict(list)

    # 按域名分组
    for result in results:
        url = result.get("url", "")
        if not url:
            continue
        domain = urlparse(url).netloc.replace("www.", "")
        domain_results[domain].append(result)

    # 每个域名最多取N条
    sampled = []
    for domain, domain_res in domain_results.items():
        sampled.extend(domain_res[:max_per_domain])

    print(f"  [采样] 从 {len(results)} 条结果中采样 {len(sampled)} 条 (每域名最多{max_per_domain}条)")
    return sampled


def batch_evaluate_results(results: List[Dict], max_workers: int = 8, sample: bool = True) -> List[Dict]:
    """批量评估搜索结果(并发)"""

    # 采样以减少评估量
    if sample and len(results) > 3000:
        results = sample_results_by_domain(results, max_per_domain=5)

    print(f"  [评估] 开始批量评估 {len(results)} 条结果...")

    evaluated = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(evaluate_result_value, r): r for r in results}

        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = futures[future]
            try:
                is_valuable, signal_type, reasoning = future.result()
                result["_is_valuable"] = is_valuable
                result["_signal_type"] = signal_type
                result["_reasoning"] = reasoning
                evaluated.append(result)

                if i % 50 == 0:
                    print(f"    进度: {i}/{len(results)}")

            except Exception as e:
                print(f"    评估失败: {e}")
                result["_is_valuable"] = False
                result["_signal_type"] = ""
                result["_reasoning"] = str(e)
                evaluated.append(result)

    valuable_count = sum(1 for r in evaluated if r["_is_valuable"])
    print(f"  [评估] 完成! 高价值结果: {valuable_count}/{len(evaluated)}")
    return evaluated


def aggregate_by_domain(results: List[Dict]) -> Dict[str, Dict]:
    """按域名聚合统计"""
    domain_stats = defaultdict(lambda: {
        "total": 0,
        "valuable": 0,
        "signal_types": defaultdict(int),
        "examples": []
    })

    for result in results:
        url = result.get("url", "")
        if not url:
            continue

        # 提取域名
        from urllib.parse import urlparse
        domain = urlparse(url).netloc.replace("www.", "")

        stats = domain_stats[domain]
        stats["total"] += 1

        if result.get("_is_valuable"):
            stats["valuable"] += 1
            signal_type = result.get("_signal_type", "未知")
            stats["signal_types"][signal_type] += 1

            # 保存示例(最多3个)
            if len(stats["examples"]) < 3:
                stats["examples"].append({
                    "title": result.get("title", ""),
                    "url": url,
                    "signal": signal_type,
                    "reasoning": result.get("_reasoning", "")
                })

    # 计算命中率
    for domain, stats in domain_stats.items():
        stats["hit_rate"] = stats["valuable"] / stats["total"] if stats["total"] > 0 else 0

    return dict(domain_stats)


def generate_report(domain_stats: Dict[str, Dict], output_path: str) -> None:
    """生成Markdown格式的域名质量报告"""

    # 按命中率分类
    high_quality = []  # 命中率 >= 50% 且结果数 >= 3
    medium_quality = []  # 命中率 30-50%
    low_quality = []  # 命中率 < 30%

    for domain, stats in domain_stats.items():
        if stats["total"] < 3:  # 样本太少,不纳入统计
            continue

        if stats["hit_rate"] >= 0.5:
            high_quality.append((domain, stats))
        elif stats["hit_rate"] >= 0.3:
            medium_quality.append((domain, stats))
        else:
            low_quality.append((domain, stats))

    # 按命中率排序
    high_quality.sort(key=lambda x: x[1]["hit_rate"], reverse=True)
    medium_quality.sort(key=lambda x: x[1]["hit_rate"], reverse=True)
    low_quality.sort(key=lambda x: x[1]["hit_rate"], reverse=True)

    # 生成报告
    report = []
    report.append("# 域名质量分析报告\n")
    report.append(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    report.append(f"数据范围: 过去7天\n")
    report.append(f"总域名数: {len(domain_stats)}\n")
    report.append("\n---\n\n")

    # 高质量域名
    report.append("## 🌟 高质量域名 (建议加入白名单)\n\n")
    report.append("| 域名 | 命中率 | 总结果数 | 高价值数 | 主要信号类型 |\n")
    report.append("|------|--------|----------|----------|-------------|\n")

    for domain, stats in high_quality[:20]:  # 最多显示20个
        signal_summary = ", ".join([
            f"{sig}({cnt})"
            for sig, cnt in sorted(stats["signal_types"].items(), key=lambda x: x[1], reverse=True)[:3]
        ])
        report.append(
            f"| {domain} | {stats['hit_rate']:.0%} | {stats['total']} | "
            f"{stats['valuable']} | {signal_summary} |\n"
        )

    report.append("\n")

    # 表现一般
    report.append("## 📊 表现一般 (保持观察)\n\n")
    report.append("| 域名 | 命中率 | 总结果数 | 高价值数 |\n")
    report.append("|------|--------|----------|----------|\n")

    for domain, stats in medium_quality[:15]:
        report.append(
            f"| {domain} | {stats['hit_rate']:.0%} | {stats['total']} | {stats['valuable']} |\n"
        )

    report.append("\n")

    # 低质量域名
    report.append("## ⚠️ 低质量域名 (建议移除)\n\n")
    report.append("| 域名 | 命中率 | 总结果数 | 高价值数 |\n")
    report.append("|------|--------|----------|----------|\n")

    for domain, stats in low_quality[:15]:
        report.append(
            f"| {domain} | {stats['hit_rate']:.0%} | {stats['total']} | {stats['valuable']} |\n"
        )

    report.append("\n")

    # 白名单更新建议
    report.append("## 💡 白名单更新建议\n\n")

    new_domains = [d for d, s in high_quality if s["hit_rate"] >= 0.6]
    remove_domains = [d for d, s in low_quality if s["hit_rate"] < 0.2 and s["total"] >= 5]

    if new_domains:
        report.append("### 建议新增:\n")
        for domain in new_domains[:10]:
            report.append(f"- ✅ {domain}\n")
        report.append("\n")

    if remove_domains:
        report.append("### 建议移除:\n")
        for domain in remove_domains[:10]:
            report.append(f"- ❌ {domain}\n")
        report.append("\n")

    # 写入文件
    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(report)

    print(f"\n  [报告] 已生成: {output_path}")
    print(f"    - 高质量域名: {len(high_quality)}")
    print(f"    - 表现一般: {len(medium_quality)}")
    print(f"    - 低质量域名: {len(low_quality)}")
    print(f"    - 建议新增: {len(new_domains)}")
    print(f"    - 建议移除: {len(remove_domains)}")


def update_whitelist(domain_stats: Dict[str, Dict]) -> None:
    """自动更新域名白名单配置"""
    whitelist_path = os.path.join(os.path.dirname(__file__), "..", "data", "domain_whitelist.json")

    # 加载现有白名单
    if os.path.exists(whitelist_path):
        with open(whitelist_path, "r", encoding="utf-8") as f:
            whitelist = json.load(f)
    else:
        whitelist = {"通用": []}

    # 找出高质量新域名
    new_domains = []
    for domain, stats in domain_stats.items():
        if stats["total"] >= 3 and stats["hit_rate"] >= 0.6:
            # 检查是否已在白名单中
            already_exists = any(domain in domains for domains in whitelist.values())
            if not already_exists:
                new_domains.append(domain)

    # 添加到"通用"分类
    if new_domains:
        if "通用" not in whitelist:
            whitelist["通用"] = []
        whitelist["通用"].extend(new_domains)
        whitelist["通用"] = list(set(whitelist["通用"]))  # 去重

        # 保存
        os.makedirs(os.path.dirname(whitelist_path), exist_ok=True)
        with open(whitelist_path, "w", encoding="utf-8") as f:
            json.dump(whitelist, f, ensure_ascii=False, indent=2)

        print(f"\n  [白名单] 已自动添加 {len(new_domains)} 个高质量域名")
    else:
        print(f"\n  [白名单] 无需更新")


def run_analysis(days: int = 7) -> None:
    """运行完整的域名质量分析流程"""
    print("\n" + "="*60)
    print("域名质量分析器")
    print("="*60 + "\n")

    # Step 1: 加载搜索结果
    results = load_search_results(days)
    if not results:
        print("  [错误] 没有找到搜索结果,请先运行日常搜索")
        return

    # Step 2: 批量评估
    evaluated = batch_evaluate_results(results)

    # Step 3: 按域名聚合
    print("\n  [聚合] 按域名统计...")
    domain_stats = aggregate_by_domain(evaluated)

    # Step 4: 生成报告
    report_path = os.path.join(os.path.dirname(__file__), "..", "data", "domain_quality_report.md")
    generate_report(domain_stats, report_path)

    # Step 5: 更新白名单
    update_whitelist(domain_stats)

    print("\n" + "="*60)
    print("分析完成!")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_analysis()
