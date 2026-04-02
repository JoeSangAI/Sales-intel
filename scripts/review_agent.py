"""
日报 Review Agent
对每篇销售情报日报进行质量评审，输出结构化评分和具体改进建议。

Review 维度：
1. 属实性 - AI 提炼是否准确，有无幻觉
2. 来源质量 - 是否来自高质量媒体（36氪/虎嗅/腾讯/新浪 vs 内容农场）
3. 信息全面性 - 是否漏掉了重要维度和机会点
4. 建议深度 - 给出的分众创意切入是否到位
5. 工作价值 - 是否帮助销售找到跟进点、谈资、机会点

输出格式：
{
  "overall_score": 6.5,        # 0-10 综合分
  "dimensions": {
    "accuracy": 7.0,           # 属实性
    "source_quality": 5.0,     # 来源质量
    "completeness": 4.0,       # 信息全面性
    "advice_depth": 6.0,      # 建议深度
    "work_value": 6.5         # 工作价值
  },
  "findings": [...],           # 具体发现（好的 + 问题的）
  "missed_opportunities": [...], # 漏掉的ope点
  "revision_suggestions": [...],  # 具体修改建议
  "summary": "一句话总结"
}
"""

import re
import json
from typing import Optional


# 高质量媒体白名单
HIGH_QUALITY_DOMAINS = {
    "36kr.com", "虎嗅.com", "huxiu.com", "thepaper.cn", "sina.com.cn",
    "163.com", "qq.com", "sohu.com", "ifeng.com", "caixin.com",
    "yicai.com", "cls.cn", "wallstreetcn.com", "weibo.com",
    "tencent.com", "baidu.com", "xiaohongshu.com", "bilibili.com",
    "weixin.qq.com", "mp.weixin.qq.com",  # 微信公众号
    "toutiao.com", "bytedance.com",
    "leiphone.com", "jiqizhixin.com",  # 雷锋网、机器之心
    "晚点", "深网", "极客公园",
}

# 低质量/内容农场域名黑名单
LOW_QUALITY_DOMAINS = {
    "chinairn.com",     # 中研普华（市场报告内容农场）
    "bst-cert.com",     # 检测认证网站
    "zol.com.cn",       # 中关村在线（商品行情，非新闻）
    "cnyes.com",        # 财经YES
    "irs gov",          # 政府网站，非一手新闻
    "csdn.net",         # 技术博客
    "aliyun.com",       # 云服务官网
    "baidu.com/article", # 百度知道/贴吧
    "sina.cn",          # 非主流sina子站
}


def extract_source_domain(url: str) -> str:
    """从URL提取域名"""
    if not url:
        return ""
    # 去掉协议和www，提取 host 部分（第三段，如 https://news.china.com → news.china.com）
    parts = url.split('/')
    domain = re.sub(r'^(www\.)', '', parts[2] if len(parts) > 2 else '')
    return domain.lower()


def is_high_quality_source(url: str) -> bool:
    """判断是否为高质量信息源"""
    domain = extract_source_domain(url)
    for hq in HIGH_QUALITY_DOMAINS:
        if hq in domain:
            return True
    return False


def is_low_quality_source(url: str) -> bool:
    """判断是否为低质量/内容农场信源"""
    domain = extract_source_domain(url)
    for lq in LOW_QUALITY_DOMAINS:
        if lq in domain:
            return True
    return False


def parse_report_content(report_text: str) -> dict:
    """解析日报文本，提取关键信息"""
    result = {
        "sections": [],
        "client_news": [],   # 客户新闻条目
        "fundraising_news": [],  # 融资新闻条目
        "total_items": 0,
        "high_quality_sources": 0,
        "low_quality_sources": 0,
        "has_insights": 0,      # 有洞察建议的条目
        "lacks_insights": 0,    # 缺乏洞察的条目
        "total_characters": len(report_text),
    }

    # 提取各板块
    current_section = None
    current_subsection = None

    for line in report_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # 一级标题
        if line.startswith('## '):
            current_section = line.replace('## ', '')
            result["sections"].append({"type": "section", "name": current_section, "items": []})
        # 二级标题
        elif line.startswith('### '):
            current_subsection = line.replace('### ', '')
            if current_section:
                result["sections"][-1]["items"].append({"type": "subsection", "name": current_subsection})

        # 提取链接（支持 [text](url) 和 bare http://... 两种格式）
        urls = re.findall(r'https?://[^\s\)\]]+', line)
        for url in urls:
            domain = extract_source_domain(url)
            if is_high_quality_source(url):
                result["high_quality_sources"] += 1
            elif is_low_quality_source(url):
                result["low_quality_sources"] += 1

        # 统计有洞察的条目（有 💡 或 🎨 标记的）
        if '💡' in line or '🎨' in line:
            result["has_insights"] += 1
        elif line.startswith('- **') and len(line) > 20:
            result["lacks_insights"] += 1

        # 提取新闻条目（以 - ** 开头，包含中文括号日期）
        if line.startswith('- **') and '（' in line:
            result["total_items"] += 1

    return result


def review_report(report_text: str, profile_name: str = "") -> dict:
    """
    对日报进行质量评审
    """
    parsed = parse_report_content(report_text)

    # ── 维度1：属实性（AI提炼准确性）────────────────────
    # 检查点：
    # - 是否有"暂无动态"过多（说明搜索质量差）
    # - 是否有融资数字异常（幻觉检测）
    # - 标题和内容是否匹配
    accuracy_issues = []
    accuracy_score = 8.0  # 默认高分，如果有明确问题再扣

    no_news_count = report_text.count('暂无动态')
    if no_news_count >= 5:
        accuracy_issues.append(f"有 {no_news_count} 个品牌显示'暂无动态'，可能是搜索质量差而非真实无动态")
        accuracy_score -= 1.5

    # 检查融资数字是否可疑（太大/太小/格式异常）
    suspicious_numbers = re.findall(r'(\d+[\d亿万]+元|\d+[\d亿万]+美元)', report_text)
    for num in suspicious_numbers:
        if '亿万' in num or num.count('0') > 5:
            accuracy_issues.append(f"融资数字可疑: {num}")
            accuracy_score -= 0.5

    if accuracy_score < 0:
        accuracy_score = 0

    # ── 维度2：来源质量────────────────────────────────
    # 计算高质量信源比例
    total_sources = parsed["high_quality_sources"] + parsed["low_quality_sources"]
    if total_sources > 0:
        hq_ratio = parsed["high_quality_sources"] / total_sources
    else:
        hq_ratio = 0

    source_quality_score = round(hq_ratio * 10, 1)
    source_issues = []
    if hq_ratio < 0.3:
        source_issues.append(f"高质量信源比例仅 {hq_ratio:.0%}，大部分来自内容农场或商品行情站")
    if parsed["low_quality_sources"] > 0:
        source_issues.append(f"发现 {parsed['low_quality_sources']} 条来自低质量信源（如中关村在线、中研普华等）")

    # ── 维度3：信息全面性────────────────────────────
    completeness_score = 6.0
    completeness_issues = []

    if parsed["total_items"] == 0 and no_news_count == 0:
        completeness_issues.append("日报为空，无任何新闻条目")
        completeness_score = 2.0
    elif parsed["total_items"] == 0 and no_news_count >= 3:
        completeness_issues.append(f"所有 {no_news_count} 个品牌均无动态，可能是搜索覆盖不足")
        completeness_score = 3.0

    # 检查是否有融资新闻（融资档案）
    has_fundraising = any('融资' in s["name"] for s in parsed["sections"])
    if not has_fundraising:
        completeness_issues.append("未包含融资新闻板块（如有融资赛道配置则此项扣分）")

    # ── 维度4：建议深度─────────────────────────────
    advice_depth_score = 5.0
    advice_issues = []

    insight_count = parsed["has_insights"]
    insight_ratio = insight_count / max(parsed["total_items"], 1)
    if insight_ratio < 0.3:
        advice_issues.append(f"仅 {insight_count}/{parsed['total_items']} 条有洞察建议 ({insight_ratio:.0%})，大部分条目缺乏分众切入分析")
        advice_depth_score = 4.0

    # 检查建议是否有实质内容（不只是"值得关注"）
    generic_advice = re.findall(r'值得关注', report_text)
    if len(generic_advice) >= 3:
        advice_issues.append(f"有 {len(generic_advice)} 处'值得关注'类泛泛之谈，缺乏具体分析")
        advice_depth_score -= 1.0

    # ── 维度5：工作价值─────────────────────────────
    work_value_score = 6.0
    work_value_issues = []

    if parsed["total_items"] >= 5:
        work_value_score = 8.0
        work_value_issues.append("新闻条目充足，对销售有潜在参考价值")
    elif parsed["total_items"] >= 1:
        work_value_score = 6.0
    elif no_news_count >= 3:
        work_value_score = 3.0
        work_value_issues.append("大部分品牌无动态，对销售工作价值有限")
    else:
        work_value_score = 4.0
        work_value_issues.append("日报内容不足以支撑销售日常跟进")

    # ── 综合评分 ────────────────────────────────────
    dimensions = {
        "accuracy": round(accuracy_score, 1),
        "source_quality": round(source_quality_score, 1),
        "completeness": round(completeness_score, 1),
        "advice_depth": round(max(advice_depth_score, 0), 1),
        "work_value": round(max(work_value_score, 0), 1),
    }

    # 加权平均（工作价值权重最高）
    weights = {
        "accuracy": 0.15,
        "source_quality": 0.20,
        "completeness": 0.20,
        "advice_depth": 0.20,
        "work_value": 0.25,
    }
    overall_score = sum(dimensions[k] * weights[k] for k in weights)

    # ── 总结发现 ───────────────────────────────────
    findings = []

    # 好的方面
    if dimensions["source_quality"] >= 7.0:
        findings.append("✅ 信源质量良好，以36氪、虎嗅等一手媒体为主")
    if dimensions["advice_depth"] >= 6.0:
        findings.append("✅ 洞察建议有深度，能给出具体分众创意方向")
    if dimensions["completeness"] >= 7.0:
        findings.append("✅ 新闻覆盖全面，包含客户动态和融资信息")
    if dimensions["work_value"] >= 7.0:
        findings.append("✅ 日报内容充实，能为销售提供实质性跟进参考")

    # 问题方面
    for issue in accuracy_issues:
        findings.append(f"⚠️ 属实性: {issue}")
    for issue in source_issues:
        findings.append(f"⚠️ 来源质量: {issue}")
    for issue in completeness_issues:
        findings.append(f"⚠️ 全面性: {issue}")
    for issue in advice_issues:
        findings.append(f"⚠️ 建议深度: {issue}")
    for issue in work_value_issues:
        findings.append(f"⚠️ 工作价值: {issue}")

    # ── 漏掉的ope点 ──────────────────────────────
    missed = []

    # 如果"暂无动态"过多，提示应该扩大搜索
    if no_news_count >= 3:
        missed.append({
            "type": "搜索覆盖不足",
            "detail": f"{no_news_count} 个品牌无动态，建议：1) 扩大搜索关键词；2) 切换至微信公众号/Toutiao信源；3) 降低min_score阈值"
        })

    # 如果来源质量差，提示应该用高质量信源
    if dimensions["source_quality"] < 5.0:
        missed.append({
            "type": "信息源质量差",
            "detail": "当前大量来自内容农场，建议接入微信公众号搜索API或今日头条API替代Bocha"
        })

    # 如果洞察不足
    if dimensions["advice_depth"] < 5.0:
        missed.append({
            "type": "洞察不够深度",
            "detail": "大部分条目缺乏分众创意建议，分析只停留在'值得关注'层面"
        })

    # ── 改进建议 ──────────────────────────────────
    suggestions = []

    if dimensions["source_quality"] < 6.0:
        suggestions.append("【高优先级】切换信息源：接入微信公众号搜狗搜索 + 今日头条高质量频道，大幅提升信源质量")
    if dimensions["completeness"] < 5.0:
        suggestions.append("【高优先级】扩大搜索：检查品牌关键词配置，确保覆盖子品牌/产品线名称")
    if dimensions["advice_depth"] < 5.0:
        suggestions.append("【中优先级】深化分析：在prompt中要求AI必须给出至少一条具体分众创意切入方向")
    if dimensions["accuracy"] < 6.0:
        suggestions.append("【中优先级】幻觉检测：对融资数字和公司名称增加二次核实环节")

    # ── 生成一句话总结 ──────────────────────────────
    if overall_score >= 8.0:
        summary = f"日报质量优秀（{overall_score:.1f}/10），信源可靠，洞察有深度，能有效支持销售工作"
    elif overall_score >= 6.0:
        summary = f"日报质量良好（{overall_score:.1f}/10），但{source_issues[0] if source_issues else '仍有改进空间'}"
    elif overall_score >= 4.0:
        summary = f"日报质量一般（{overall_score:.1f}/10），{source_issues[0] if source_issues else completeness_issues[0] if completeness_issues else '需要重点改善信源质量'}"
    else:
        summary = f"日报质量堪忧（{overall_score:.1f}/10），{source_issues[0] if source_issues else '信息源质量差，建议彻底重构搜索策略'}"

    # ── 判断返工类型 ──────────────────────────────────
    rework_type = "none"
    rework_reasons = []

    if overall_score < 7.0:
        # Type A: 原材料问题 — 需要重新搜索
        if dimensions["source_quality"] < 5.0:
            rework_type = "material"
            rework_reasons.append("信源质量差，需换关键词重新搜索")
        if dimensions["completeness"] < 5.0:
            rework_type = "material"
            rework_reasons.append("信息覆盖不足，需扩大搜索范围")

        # Type B: 炒菜问题 — 需要重新分析（不覆盖 Type A）
        if rework_type == "none":
            if dimensions["advice_depth"] < 5.0:
                rework_type = "cooking"
                rework_reasons.append("洞察建议不够深度")
            if dimensions["accuracy"] < 5.0:
                rework_type = "cooking"
                rework_reasons.append("分析准确性不足")
            if dimensions["work_value"] < 5.0:
                rework_type = "cooking"
                rework_reasons.append("对销售工作价值有限")

    return {
        "overall_score": round(overall_score, 1),
        "dimensions": dimensions,
        "findings": findings,
        "missed_opportunities": missed,
        "revision_suggestions": suggestions,
        "rework_type": rework_type,
        "rework_reasons": rework_reasons,
        "summary": summary,
        "stats": {
            "total_items": parsed["total_items"],
            "no_news_count": no_news_count,
            "high_quality_sources": parsed["high_quality_sources"],
            "low_quality_sources": parsed["low_quality_sources"],
            "insight_count": parsed["has_insights"],
            "total_characters": parsed["total_characters"],
        }
    }


def format_review_output(review_result: dict, profile_name: str = "") -> str:
    """将评审结果格式化为易读的文本输出"""
    output = []
    score = review_result["overall_score"]
    dims = review_result["dimensions"]

    output.append(f"{'='*50}")
    output.append(f"📋 日报 Review Report {' | ' + profile_name if profile_name else ''}")
    output.append(f"{'='*50}")
    output.append(f"综合评分: {score:.1f}/10")
    output.append("")

    # 维度雷达
    dim_labels = {
        "accuracy": "属实性",
        "source_quality": "来源质量",
        "completeness": "信息全面",
        "advice_depth": "建议深度",
        "work_value": "工作价值",
    }
    output.append("📊 维度评分:")
    for k, label in dim_labels.items():
        bar = "█" * int(dims[k]) + "░" * (10 - int(dims[k]))
        output.append(f"  {label:12s}: {bar} {dims[k]:.1f}")

    output.append("")

    # 统计数据
    stats = review_result["stats"]
    output.append(f"📈 统计:")
    output.append(f"  新闻条目: {stats['total_items']} | 无动态品牌: {stats['no_news_count']}")
    output.append(f"  高质量信源: {stats['high_quality_sources']} | 低质量信源: {stats['low_quality_sources']}")
    output.append(f"  有洞察条目: {stats['insight_count']} | 日报字数: {stats['total_characters']}")
    output.append("")

    # 发现
    if review_result["findings"]:
        output.append("🔍 评审发现:")
        for f in review_result["findings"]:
            output.append(f"  {f}")
        output.append("")

    # 漏掉的ope点
    if review_result["missed_opportunities"]:
        output.append("🚨 漏掉的ope点:")
        for m in review_result["missed_opportunities"]:
            output.append(f"  [{m['type']}] {m['detail']}")
        output.append("")

    # 改进建议
    if review_result["revision_suggestions"]:
        output.append("💡 改进建议:")
        for s in review_result["revision_suggestions"]:
            output.append(f"  {s}")
        output.append("")

    # 一句话总结
    output.append(f"📝 总结: {review_result['summary']}")
    output.append("=" * 50)

    return "\n".join(output)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python review_agent.py <report_file> [profile_name]")
        sys.exit(1)

    report_path = sys.argv[1]
    profile_name = sys.argv[2] if len(sys.argv) > 2 else ""

    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    result = review_report(report_text, profile_name)
    output = format_review_output(result, profile_name)
    print(output)

    # 同时输出 JSON
    print("\n--- JSON OUTPUT ---")
    print(json.dumps(result, ensure_ascii=False, indent=2))
