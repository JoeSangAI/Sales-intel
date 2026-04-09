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

# 赛道-业务关键词映射表（用于校验公司是否属于该赛道）
_TRACK_BUSINESS_KEYWORDS = {
    "AI大模型": ["大模型", "LLM", "人工智能", "AI公司", "AI创业", "AI独角兽", "生成式AI", "AGI", "AIGC", "ChatGPT", "GPT", "文心", "通义", "豆包", "DeepSeek", "Kimi", "月之暗面", "智谱", "百川", "零一万物", "阶跃", "元象", "深度求索", "OpenAI", "Anthropic", "Mistral"],
    "机器人/具身智能": ["机器人", "具身智能", "人形机器人", "机械臂", "自动驾驶", "AGV", "ROS", " Robotics", " Embodied", "灵巧手", "四足机器人", "机械狗", "千寻智能", "宇树", "追觅", "傅利叶", "智元", "星动纪元"],
    "新能源汽车/智能汽车": ["新能源汽车", "电动车", "锂电池", "动力电池", "充电桩", "电驱", "智驾", "辅助驾驶", "宁德时代", "比亚迪", "蔚来", "小鹏", "理想", "广汽埃安", "华为智驾", "鸿蒙智行", "问界", "智界", "享界", "尊界", "尚界", "启境", "华境", "奕境"],
    "智能硬件/IoT": ["智能硬件", "IoT", "物联网", "智能家居", "可穿戴", "智能音箱", "智能手表", "AR", "VR", "MR", "元宇宙硬件", "智能门锁", "扫地机器人"],
    "企业服务/SaaS": ["SaaS", "企业服务", "云服务", "CRM", "ERP", "OA", "HR SaaS", "财税", "数据分析", "BI", "低代码"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "封装测试", "IC设计", "处理器", "GPU", "CPU", "ASIC"],
    "消费品": ["消费", "零售", "电商", "品牌", "渠道", "门店"],
    "食品/粮油": ["食品", "粮油", "饮料", "乳制品", "调味品", "零食", "餐饮"],
    "日用洗护": ["日化", "洗护", "护肤品", "化妆品", "美妆", "个护", "卫生用品"],
    "服装": ["服装", "服饰", "鞋", "箱包", "面料", "纺织"],
    "家居用品": ["家居", "家电", "家具", "厨卫", "家装"],
    "休闲食品": ["零食", "糖果", "巧克力", "烘焙", "饮品"],
    "儿童服饰": ["童装", "儿童服装", "母婴", "玩具", "儿童用品"],
    "保健品": ["保健", "营养品", "维生素", "鱼油", "膳食补充", "功能性食品", "益生菌"],
    "户外服装": ["户外", "登山", "露营", "徒步", "运动装备", "冲锋衣", "帐篷", "探路者", "北面", "始祖鸟", "迪卡侬"],
    "教育": ["教育", "培训", "学习", "课程", "学校", "K12", "职业教育", "留学"],
}

# 反向映射：哪些公司不应该出现在哪些赛道（AI公司被错误归类时使用）
_ANTI_TRACK污染表 = {
    # AI公司不应该出现在这些非AI赛道
    "AI大模型": ["日用洗护", "消费品", "休闲食品", "儿童服饰", "家居用品", "服装", "美妆", "护肤", "食品/粮油"],
}


def _is_noise_domain(url: str) -> bool:
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in _NOISE_DOMAINS_QC)


def _check_brand_content_alignment(item: dict) -> tuple[bool, str]:
    """
    R6: 品牌-内容一致性检查
    品牌应该是文章的主语或核心话题，而非只是被提及。
    检查品牌名是否出现在内容的靠前位置（前300字）。
    返回 (pass, reason)
    """
    brand = item.get("brand", "")
    title = item.get("title", "")
    content = item.get("content", "")

    # 融资/行业类不做品牌-内容检查
    if brand.startswith("[融资]") or brand.startswith("[行业]"):
        return True, ""

    # 提取品牌名（去掉前缀）
    brand_name = brand
    for prefix in ["[品牌]", "[融资]", "[行业]"]:
        if brand.startswith(prefix):
            brand_name = brand[len(prefix):].strip()
            break

    if not brand_name:
        return True, ""

    # 品牌必须在标题中出现（品牌新闻的标题必须有品牌名）
    if brand_name not in title:
        return False, f"品牌'{brand_name}'未出现在标题中，可能不是文章主语"

    # 品牌必须在内容前300字中出现
    content_prefix = content[:300]
    if brand_name not in content_prefix:
        # 检查是否是"配角"模式（联合/携手/与X合作）
        peripheral_patterns = [
            rf'联合{re.escape(brand_name)}',
            rf'携手{re.escape(brand_name)}',
            rf'与{re.escape(brand_name)}\s*(?:合作|携手|联合)',
            rf'和{re.escape(brand_name)}\s*(?:合作|携手|联合)',
            rf'{re.escape(brand_name)}\s*联名',
            rf'跨界{re.escape(brand_name)}',
            rf'{re.escape(brand_name)}\s*称',
            rf'据{re.escape(brand_name)}',
        ]
        for p in peripheral_patterns:
            if re.search(p, content_prefix):
                return False, f"品牌'{brand_name}'在内容中处于配角位置（联合/携手/合作等），不是主语"

        return False, f"品牌'{brand_name}'未出现在内容前300字，可能不是主要话题"

    return True, ""


def _check_track_company_alignment(item: dict) -> tuple[bool, str]:
    """
    R7: 赛道-公司一致性检查
    融资条目中，公司实际业务应与赛道匹配。
    通过内容关键词判断公司真实业务，防止赛道标记错误。
    返回 (pass, reason)
    """
    brand = item.get("brand", "")
    track = item.get("track_name", "")
    title = item.get("title", "")
    content = item.get("content", "")[:500]

    if not brand.startswith("[融资]"):
        return True, ""

    # 提取公司名
    company_name = brand[len("[融资]"):].strip()
    if not company_name:
        return True, ""

    # 如果赛道有业务关键词表，检查一致性
    if track in _TRACK_BUSINESS_KEYWORDS:
        keywords = _TRACK_BUSINESS_KEYWORDS[track]
        found_in_content = any(kw in content for kw in keywords)
        found_in_title = any(kw in title for kw in keywords)

        if not found_in_content and not found_in_title:
            # 公司在赛道关键词表中查不到，可能是错误归类
            return False, f"公司'{company_name}'被归入'{track}'赛道，但内容中未出现该赛道相关关键词，可能归类错误"

    # 检查AI公司是否被错误归入非AI赛道
    ai_keywords_in_name = ["AI", "人工智能", "大模型", "智能", "DeepSeek", "OpenAI", "Kimi", "文心", "通义", "豆包", "智谱", "百川", "阶跃", "元象", "深度求索", "月之暗面", "零一", "千寻"]
    is_likely_ai_company = any(kw in company_name for kw in ai_keywords_in_name)

    if is_likely_ai_company and track in _ANTI_TRACK污染表.get("AI大模型", []):
        return False, f"AI公司'{company_name}'被归入'{track}'赛道，这是错误的赛道归类"

    return True, ""


def _check_number_source(item: dict) -> tuple[bool, str]:
    """
    R9: 数字/金额溯源检查
    新闻标题或摘要中的关键数字（融资金额、产品数量等）必须出现在内容中。
    返回 (pass, reason)
    """
    title = item.get("title", "")
    content = item.get("content", "")

    # 从标题中提取数字
    numbers_in_title = re.findall(r'\d+\.?\d*\s*[亿万元]', title)
    for num_str in numbers_in_title:
        # 去掉"亿"等单位后在内容中查找
        num_digits = re.sub(r'[亿万元]', '', num_str).strip()
        if num_digits and num_digits not in content[:500]:
            return False, f"标题中的数字'{num_str}'未出现在内容前500字，来源存疑"

    return True, ""


def _check_peripheral_brand(item: dict) -> tuple[bool, str]:
    """
    R10: 品牌主角检查
    品牌条目中，如果标题中出现"联合X"、"与X合作"等模式，说明该品牌不是主角。
    返回 (pass, reason)
    """
    brand = item.get("brand", "")
    title = item.get("title", "")
    content = item.get("content", "")

    if brand.startswith("[融资]") or brand.startswith("[行业]"):
        return True, ""

    brand_name = brand
    for prefix in ["[品牌]", "[融资]", "[行业]"]:
        if brand.startswith(prefix):
            brand_name = brand[len(prefix):].strip()
            break

    if not brand_name:
        return True, ""

    # 检查品牌是否是配角
    if brand_name in title:
        peripheral_patterns = [
            rf'联合{re.escape(brand_name)}',
            rf'携手{re.escape(brand_name)}',
            rf'与{re.escape(brand_name)}\s*(?:合作|携手|联合)',
            rf'和{re.escape(brand_name)}\s*(?:合作|携手|联合)',
            rf'{re.escape(brand_name)}\s*联名',
            rf'跨界{re.escape(brand_name)}',
            rf'{re.escape(brand_name)}\s*称',
            rf'据{re.escape(brand_name)}',
        ]
        for p in peripheral_patterns:
            if re.search(p, title):
                return False, f"品牌'{brand_name}'在标题中处于配角位置（联合/携手/与X合作），不是主语"

    return True, ""


def run_rules_check(report: str, original_items: list, profile: dict) -> dict:
    """
    第一层规则校验（增强版）。

    新增检查：
    - R6: 品牌-内容一致性（品牌必须是内容主语）
    - R7: 赛道-公司一致性（融资公司业务必须匹配赛道）
    - R9: 数字溯源（关键数字必须出现在内容中）
    - R10: 品牌主角检查

    返回:
        {
            "pass": bool,
            "issues": list,           # 致命问题（阻断）
            "warnings": list,         # 非致命警告
            "item_issues": list,      # 条目级问题（返回给 Layer2 重新处理）
        }
    """
    issues = []
    warnings = []
    item_issues = []

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

    # ── R6: 品牌-内容一致性（对原始条目逐条检查） ──
    for item in original_items:
        passed, reason = _check_brand_content_alignment(item)
        if not passed:
            item_url = item.get("url", "")
            item_title = item.get("title", "")[:50]
            item_issues.append({
                "url": item_url,
                "issue_type": "R6",
                "reason": reason,
                "title": item_title,
                "action": "remove_or_fix",
            })
            warnings.append(f"[R6 警告] {reason}，条目: {item_title}...，URL: {item_url}")

    # ── R7: 赛道-公司一致性（对融资条目逐条检查） ──
    for item in original_items:
        passed, reason = _check_track_company_alignment(item)
        if not passed:
            item_url = item.get("url", "")
            item_brand = item.get("brand", "")
            item_title = item.get("title", "")[:50]
            item_issues.append({
                "url": item_url,
                "issue_type": "R7",
                "reason": reason,
                "brand": item_brand,
                "title": item_title,
                "action": "remove_or_retrack",
            })
            warnings.append(f"[R7 警告] {reason}，条目: {item_title}...，URL: {item_url}")

    # ── R9: 数字溯源检查 ──
    for item in original_items:
        passed, reason = _check_number_source(item)
        if not passed:
            item_url = item.get("url", "")
            item_title = item.get("title", "")[:50]
            item_issues.append({
                "url": item_url,
                "issue_type": "R9",
                "reason": reason,
                "title": item_title,
                "action": "flag",
            })
            warnings.append(f"[R9 警告] {reason}，条目: {item_title}...")

    # ── R10: 品牌主角检查 ──
    for item in original_items:
        passed, reason = _check_peripheral_brand(item)
        if not passed:
            item_url = item.get("url", "")
            item_title = item.get("title", "")[:50]
            item_issues.append({
                "url": item_url,
                "issue_type": "R10",
                "reason": reason,
                "title": item_title,
                "action": "remove_or_downgrade",
            })
            warnings.append(f"[R10 警告] {reason}，条目: {item_title}...，URL: {item_url}")

    # ── R11: 关键人实名降级文案检查 ──
    if "关键决策人" in report and "未检索到公开实名" not in report:
        if "CMO" in report or "市场负责人" in report or "董事长" in report or "CEO" in report:
            warnings.append("[R11 警告] 报告包含关键决策人描述，请确认姓名/职务来自前置搜索结果")

    return {
        "pass": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "item_issues": item_issues,
    }
