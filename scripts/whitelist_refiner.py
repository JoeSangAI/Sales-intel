"""
白名单提纯器 — 基于滚动数据自动剔除低质量域名、添加高质量新域名

规则：
- 剔除：滚动7天命中率 < 10% 且 总结果数 >= 10 → 移出白名单
- 添加：Bocha搜索中新出现的域名，连续3天命中率 > 50% → 加入白名单候选
- 候补池：连续3天表现良好的域名，进入候补池，再观察2天 → 正式加入
"""

import os
import json
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
WHITELIST_PATH = os.path.join(PROJECT_ROOT, "data", "domain_whitelist.json")
SUMMARY_PATH = os.path.join(PROJECT_ROOT, "data", "domain_tracking_summary.json")
CANDIDATE_PATH = os.path.join(PROJECT_ROOT, "data", "domain_candidates.json")
ARCHIVE_PATH = os.path.join(PROJECT_ROOT, "data", "domain_archive.json")  # 被剔除域名的存档

# 规则阈值
REMOVAL_HIT_RATE_THRESHOLD = 0.10       # 7天命中率低于此值 → 剔除
REMOVAL_MIN_TOTAL = 10                   # 至少看到10次才考虑剔除
ADDITION_HIT_RATE_THRESHOLD = 0.50       # 连续3天命中率高于此值 → 进入候选
CANDIDATE_DAYS = 3                       # 连续表现良好的天数
FINAL_OBSERVATION_DAYS = 2               # 候选后还需观察2天才能正式加入
ROLLED_OUT_DAYS = 7                      # 被剔除的域名7天内不再考虑加入


def load_whitelist() -> set:
    """加载当前白名单所有域名"""
    if not os.path.exists(WHITELIST_PATH):
        return set()

    with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
        whitelist = json.load(f)

    domains = set()
    for category_domains in whitelist.values():
        for d in category_domains:
            domains.add(d.replace("www.", ""))
    return domains


def save_whitelist(domains: set) -> None:
    """保存域名回白名单（放在'通用'分类）"""
    os.makedirs(os.path.dirname(WHITELIST_PATH), exist_ok=True)

    # 保持其他分类不变，只更新"通用"
    if os.path.exists(WHITELIST_PATH):
        with open(WHITELIST_PATH, "r", encoding="utf-8") as f:
            whitelist = json.load(f)
    else:
        whitelist = {}

    whitelist["通用"] = sorted(list(domains))

    with open(WHITELIST_PATH, "w", encoding="utf-8") as f:
        json.dump(whitelist, f, ensure_ascii=False, indent=2)


def load_candidates() -> dict:
    """加载候选池数据"""
    if not os.path.exists(CANDIDATE_PATH):
        return {}
    with open(CANDIDATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_candidates(candidates: dict) -> None:
    """保存候选池"""
    with open(CANDIDATE_PATH, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)


def load_archive() -> dict:
    """加载被剔除域名的存档（记录剔除时间和原因）"""
    if not os.path.exists(ARCHIVE_PATH):
        return {}
    with open(ARCHIVE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_archive(archive: dict) -> None:
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)


def get_daily_domain_stats(date_str: str) -> dict:
    """读取某日的域名统计数据"""
    tracking_file = os.path.join(PROJECT_ROOT, "data", "domain_tracking", f"tracking_{date_str}.json")
    if not os.path.exists(tracking_file):
        return {}
    with open(tracking_file, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 剔除规则
# ============================================================

def find_domains_to_remove(whitelist: set, summary: dict, archive: dict) -> list[tuple[str, str]]:
    """
    找出应该从白名单移除的域名。
    返回: [(domain, reason), ...]
    """
    to_remove = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    for domain in whitelist:
        if domain not in summary:
            # 白名单里有，但过去7天没出现过 → 降权但不立即删
            continue

        stats = summary[domain]
        hit_rate = stats["hit_rate_7d"]
        total = stats["total_7d"]

        if total < REMOVAL_MIN_TOTAL:
            continue

        if hit_rate < REMOVAL_HIT_RATE_THRESHOLD:
            to_remove.append((domain, f"7天命中率 {hit_rate:.0%}（{total}次出现），低于{REMOVAL_HIT_RATE_THRESHOLD:.0%}阈值"))

    return to_remove


def apply_removals(whitelist: set, to_remove: list[tuple[str, str]]) -> set:
    """从白名单移除低质量域名"""
    if not to_remove:
        return whitelist

    removed = set()
    today_str = datetime.now().strftime("%Y-%m-%d")

    archive = load_archive()

    for domain, reason in to_remove:
        whitelist.discard(domain)
        removed.add(domain)
        archive[domain] = {
            "removed_at": today_str,
            "reason": reason,
        }

    save_archive(archive)
    save_whitelist(whitelist)

    print(f"\n  [剔除] 移除 {len(removed)} 个低质量域名:")
    for domain, reason in to_remove:
        print(f"    ❌ {domain}: {reason}")

    return whitelist


# ============================================================
# 添加规则
# ============================================================

def find_domains_to_candidate(summary: dict, whitelist: set, archive: dict) -> list[str]:
    """
    发现高质量新域名（不在白名单中）：
    - 过去7天出现 >= 3天
    - 每日命中率都 > 50%
    - 且不在 archive（最近7天内被剔除的不考虑）
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    candidates = []

    for domain, stats in summary.items():
        if domain in whitelist:
            continue
        if domain in archive:
            removed_at = datetime.strptime(archive[domain]["removed_at"], "%Y-%m-%d")
            if (datetime.now() - removed_at).days < ROLLED_OUT_DAYS:
                continue  # 7天内被剔除的暂不考虑

        if stats["days_seen"] < CANDIDATE_DAYS:
            continue

        # 检查是否连续几天都有好表现
        # 通过 daily_hit_rates 检查（需要从原始数据重建，这里用 avg_daily_hit_rate 粗略判断）
        # 更准确：用每日快照检查
        if stats["avg_daily_hit_rate"] < ADDITION_HIT_RATE_THRESHOLD:
            continue

        candidates.append(domain)

    return candidates


def update_candidate_pool(existing_candidates: dict, new_candidates: list[str]) -> dict:
    """
    更新候选池：
    - 新候选加入
    - 已有候选若连续 CANDIDATE_DAYS + FINAL_OBSERVATION_DAYS 天保持高命中率 → 正式加入
    - 已有候选若某天命中率低 → 移除
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 初始化新候选
    for domain in new_candidates:
        if domain not in existing_candidates:
            existing_candidates[domain] = {
                "first_seen": today_str,
                "high_hit_rate_days": 1,
                "last_seen": today_str,
                "status": "new",  # new → observing → ready → added
            }

    # 更新已有候选的每日状态
    updated = {}
    for domain, info in existing_candidates.items():
        daily_stats = get_daily_domain_stats(today_str)
        today_domain_stats = daily_stats.get(domain, {})

        if today_domain_stats:
            hit_rate = today_domain_stats.get("hit_rate", 0)
            if hit_rate >= ADDITION_HIT_RATE_THRESHOLD:
                info["high_hit_rate_days"] += 1
            else:
                info["high_hit_rate_days"] = 0  # 哪天掉队就清零
            info["last_seen"] = today_str

        # 判断状态升级
        if info["status"] == "new" and info["high_hit_rate_days"] >= CANDIDATE_DAYS:
            info["status"] = "observing"
        elif info["status"] == "observing":
            # 需要额外观察 FINAL_OBSERVATION_DAYS 天
            days_since_first = (datetime.now() - datetime.strptime(info["first_seen"], "%Y-%m-%d")).days
            if days_since_first >= CANDIDATE_DAYS + FINAL_OBSERVATION_DAYS:
                info["status"] = "ready"

        updated[domain] = info

    return updated


def apply_additions(whitelist: set, candidates: dict) -> set:
    """将状态为 ready 的候选域名正式加入白名单"""
    ready = [d for d, info in candidates.items() if info["status"] == "ready"]

    if not ready:
        print(f"\n  [添加] 暂无域名达到加入条件（需连续{CANDIDATE_DAYS}天高命中率 + 再观察{FINAL_OBSERVATION_DAYS}天）")
        return whitelist

    for domain in ready:
        whitelist.add(domain)
        del candidates[domain]  # 从候选池移除
        print(f"    ✅ 新增: {domain}")

    save_whitelist(whitelist)
    save_candidates(candidates)

    print(f"\n  [添加] 正式加入 {len(ready)} 个域名到白名单")
    return whitelist


# ============================================================
# 每周报告
# ============================================================

def generate_weekly_report(
    whitelist: set,
    summary: dict,
    removed: list,
    added: list,
    candidates: dict,
) -> str:
    today_str = datetime.now().strftime("%Y-%m-%d")

    report = []
    report.append(f"# 白名单提纯报告 — {today_str}\n")
    report.append(f"## 当前状态\n")
    report.append(f"- 白名单域名总数: {len(whitelist)}\n")
    report.append(f"- 追踪域名总数: {len(summary)}\n")
    report.append(f"- 当前候选池: {len(candidates)} 个\n")

    if removed:
        report.append(f"\n## ❌ 剔除域名（{len(removed)} 个）\n")
        for domain, reason in removed:
            report.append(f"- **{domain}**: {reason}\n")

    if added:
        report.append(f"\n## ✅ 新增域名（{len(added)} 个）\n")
        for domain in added:
            report.append(f"- {domain}\n")

    if candidates:
        report.append(f"\n## 🔄 候选池状态（{len(candidates)} 个）\n")
        report.append("| 域名 | 状态 | 高命中率天数 | 首次出现 |\n")
        report.append("|------|------|------------|----------|\n")
        for domain, info in candidates.items():
            report.append(f"| {domain} | {info['status']} | {info['high_hit_rate_days']} | {info['first_seen']} |\n")

    # TOP 域名列表
    if summary:
        top = sorted(summary.items(), key=lambda x: x[1]["valuable_7d"], reverse=True)[:15]
        report.append(f"\n## 🏆 白名单域名 TOP15（7天表现）\n")
        report.append("| 域名 | 命中率 | 高价值文章 | 总出现 | 天数 |\n")
        report.append("|------|--------|-----------|--------|------|\n")
        for domain, stats in top:
            if domain in whitelist:
                report.append(f"| {domain} | {stats['hit_rate_7d']:.0%} | {stats['valuable_7d']} | {stats['total_7d']} | {stats['days_seen']} |\n")

    return "".join(report)


# ============================================================
# 主流程
# ============================================================

def run_refiner() -> None:
    """
    提纯器主流程（每周日运行一次）：
    1. 加载当前白名单和追踪数据
    2. 找出低质量域名 → 剔除
    3. 发现高质量新域名 → 进入候选池
    4. 候选池中达标者 → 正式加入
    5. 生成报告
    """
    print("\n" + "=" * 50)
    print("白名单提纯器")
    print("=" * 50 + "\n")

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 加载数据
    whitelist = load_whitelist()
    archive = load_archive()
    candidates = load_candidates()

    if not os.path.exists(SUMMARY_PATH):
        print("  [错误] 找不到追踪统计数据，请先运行 domain_quality_tracker.py")
        return

    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        summary = json.load(f)

    print(f"  [状态] 白名单: {len(whitelist)} 个域名, 追踪数据: {len(summary)} 个域名")

    # Step 1: 剔除低质量域名
    to_remove = find_domains_to_remove(whitelist, summary, archive)
    whitelist = apply_removals(whitelist, to_remove)

    # Step 2: 发现新候选
    new_candidates = find_domains_to_candidate(summary, whitelist, archive)
    if new_candidates:
        print(f"\n  [发现] {len(new_candidates)} 个高质量新域名进入候选池:")
        for d in new_candidates:
            print(f"    → {d}")

    # Step 3: 更新候选池
    candidates = update_candidate_pool(candidates, new_candidates)

    # 移除不达标的候选（连续0天高命中率且已观察足够长时间）
    stale = [d for d, info in candidates.items()
             if info["high_hit_rate_days"] == 0
             and info["status"] in ("observing", "ready")]
    for d in stale:
        del candidates[d]
    if stale:
        print(f"\n  [候选] 移除 {len(stale)} 个不达标候选")

    save_candidates(candidates)

    # Step 4: 正式加入达标的候选
    ready = [d for d, info in candidates.items() if info["status"] == "ready"]
    if ready:
        for domain in ready:
            whitelist.add(domain)
            del candidates[domain]
            print(f"    ✅ 正式加入: {domain}")
        save_whitelist(whitelist)
        save_candidates(candidates)
        print(f"\n  [添加] {len(ready)} 个域名正式加入白名单")

    # Step 5: 生成报告
    report = generate_weekly_report(
        whitelist, summary,
        [(d, r) for d, r in to_remove],
        ready,
        candidates,
    )

    report_path = os.path.join(PROJECT_ROOT, "data", "whitelist_refine_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  [报告] 已生成: {report_path}")

    # 打印报告内容
    print("\n" + "-" * 40)
    print(report)

    print("\n" + "=" * 50)
    print("提纯完成")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    run_refiner()
