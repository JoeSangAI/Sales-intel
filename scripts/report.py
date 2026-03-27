"""
日报生成器
1. 将分析后的结果按品牌分组
2. 同品牌内做主题聚合（多条同一事件的报道合并为一条情报）
3. 提炼核心信息 + 附多个来源链接
"""

import re
from datetime import datetime


def _normalize_for_cluster(title: str) -> str:
    """标题归一化，用于聚类比较"""
    # 去掉来源后缀
    title = re.sub(r'[\s]*[|\-_—–·｜]\s*[^\s|_\-—–·｜]{2,10}$', '', title)
    # 去掉标点、空格
    title = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019「」【】\[\]\s\-_|·｜()\(\)\"\']', '', title)
    title = re.sub(r'视频|图片|图赏|快报|简讯', '', title)
    return title


def _extract_ngrams(text: str, n: int = 2) -> set:
    """提取中文字符的 n-gram 集合"""
    chars = re.findall(r'[\u4e00-\u9fff]', text)
    if len(chars) < n:
        return set(chars)
    return {tuple(chars[i:i+n]) for i in range(len(chars) - n + 1)}


def _titles_same_topic(t1: str, t2: str) -> bool:
    """判断两个标题是否属于同一主题

    两层判断：
    1. 严格包含（一标题完全包含另一标题）
    2. bigram Jaccard >= 0.25 + 共享实体词
    """
    n1 = _normalize_for_cluster(t1)
    n2 = _normalize_for_cluster(t2)
    if not n1 or not n2:
        return False

    # 完全包含（允许，但要求长度差异不大，避免"vivo X发布"被"vivo"吸收）
    if n1 in n2 or n2 in n1:
        len_diff = abs(len(n1) - len(n2))
        if len_diff < 8:  # 长度差异太大说明一个是短片段，不是同一主题
            return True

    # bigram Jaccard >= 0.25（阈值从0.15提高到0.25，减少误合并）
    bg1 = _extract_ngrams(n1, 2)
    bg2 = _extract_ngrams(n2, 2)
    if bg1 and bg2:
        intersection = bg1 & bg2
        union = bg1 | bg2
        jaccard = len(intersection) / len(union) if union else 0
        if jaccard >= 0.25:
            # 进一步要求：两条标题在去重后必须仍有至少一个非停用实体词重叠
            e1 = _extract_entities(n1)
            e2 = _extract_entities(n2)
            if e1 & e2:
                return True

    return False


def _extract_entities(text: str) -> set:
    """从归一化标题中提取可能的实体词（2-4字片段）

    对每个连续中文字符块，滑窗提取所有2-4字子串。
    """
    chars = re.findall(r'[\u4e00-\u9fff]+', text)
    entities = set()
    stop_words = {"的是", "是和", "和与", "在了", "将为", "由以",
                  "从到", "让把", "被更", "最不", "也都", "还就",
                  "已能", "会要", "新大", "好多", "看做", "有中"}
    for word in chars:
        for length in range(2, 5):
            for start in range(len(word) - length + 1):
                sub = word[start:start + length]
                if sub not in stop_words:
                    entities.add(sub)
    return entities


def _cluster_items(items: list[dict]) -> list[list[dict]]:
    """将同品牌的条目按主题聚类（链式匹配）

    使用链式聚类：新条目只要和簇中任意一条相似就合并，
    而不仅仅和第一条(代表)比较，避免漏聚。
    """
    clusters = []
    for item in items:
        title = item.get("title", "")
        matched = False
        for cluster in clusters:
            for existing in cluster:
                if _titles_same_topic(title, existing.get("title", "")):
                    cluster.append(item)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            clusters.append([item])
    return clusters


def _clean_title(title: str) -> str:
    """清理标题中的网站名后缀和噪音"""
    import re
    # 第一遍：反复去掉末尾的 |xxx / _xxx / -xxx / —xxx 后缀（网站名、栏目名）
    for _ in range(5):
        cleaned = re.sub(r'\s*[|_\-—–·]\s*[^|_\-—–·]{1,20}$', '', title)
        if cleaned == title:
            break
        title = cleaned
    # 第二遍：处理多级栏目残留，如 _亓言纪_旗舰_影像 / _频道_子频道_名称
    # 匹配模式：至少2段，每段2+中文字符，段间用_或-分隔，整体在末尾
    # 不影响 "4月" 这类单独时间词
    for _ in range(3):
        cleaned = re.sub(
            r'[_\-]\s*([\u4e00-\u9fff]{2,4}\s*[_\-]\s*){1,}[\u4e00-\u9fff]{2,4}\s*$',
            '', title
        )
        if cleaned == title:
            break
        title = cleaned
    # 去掉开头的 - 或空格
    title = re.sub(r'^[\-\s]+', '', title)
    # 如果标题只剩很短的网站名残留，返回空让 _pick_best_title 跳过
    if len(title.strip()) < 6:
        return ""
    return title.strip()


def _pick_best_title(cluster: list[dict]) -> str:
    """从簇中选最佳标题（有实质信息、不截断、不是视频标题）"""
    titles = [it.get("title", "") for it in cluster]
    # 先用 _clean_title 过滤，跳过清理后为空的标题
    clean = [
        t for t in titles
        if _clean_title(t)
        and not re.match(r'^[\[【]', t)
        and "..." not in t
        and "…" not in t
    ]
    candidates = clean or titles
    # 优先选 20-40 字的标题（太短信息不足，太长有噪音）
    candidates.sort(key=lambda t: abs(len(t) - 28))
    return candidates[0] if candidates else titles[0]


def _extract_summary(cluster: list[dict]) -> str:
    """从簇中找质量最好的一条 content 作摘要，不拼接"""
    # 噪音特征：视频页、股吧、内容重复
    noise_patterns = [
        "0播放", "不代表", "创作者", "股吧", "投资交流",
        "点击查看", "查看更多", "加载中", "街访", "现场采访",
        "路人怎么看", "图片 | 参数", "询价",
    ]

    def content_score(text: str) -> int:
        """内容质量分：越高越好"""
        if not text or len(text) < 30:
            return -1
        score = len(text)
        for p in noise_patterns:
            if p in text:
                score -= 500
        return score

    best_content = ""
    best_score = -1
    for it in cluster:
        c = it.get("content", "")[:600]
        s = content_score(c)
        if s > best_score:
            best_score = s
            best_content = c

    if not best_content.strip():
        return ""

    # 截取前 220 字符，断到最后一个完整句子
    summary = best_content[:220]
    for sep in ["。", "；"]:
        idx = summary.rfind(sep)
        if idx > 60:
            summary = summary[:idx + 1]
            break
    return summary.strip()


def _cluster_urgency(cluster: list[dict]) -> str:
    """取簇内最高紧迫度"""
    priority = {"🔴": 3, "🟡": 2, "⚪": 1}
    best = "⚪"
    for item in cluster:
        u = item.get("analysis", {}).get("urgency", "⚪")
        if priority.get(u, 0) > priority.get(best, 0):
            best = u
    return best


def _cluster_score(cluster: list[dict]) -> int:
    """取簇内最高相关度分数"""
    return max(
        (it.get("analysis", {}).get("relevance_score", 0) for it in cluster),
        default=0,
    )


def _is_cluster_followup(cluster: list[dict]) -> tuple[bool, str]:
    """判断整个簇是否为跟进报道，返回 (is_followup, followup_note)"""
    # 簇内所有条目都是 followup 才算 followup
    notes = []
    for it in cluster:
        a = it.get("analysis", {})
        if not a.get("is_followup", False):
            return False, ""
        note = a.get("followup_note", "")
        if note:
            notes.append(note)
    return True, notes[0] if notes else ""


def _get_cluster_date(cluster: list[dict]) -> str:
    """从簇中取最新的 published_date，格式化为 MM-DD"""
    dates = []
    for it in cluster:
        d = it.get("published_date", "")
        if d and len(d) >= 10:
            dates.append(d[:10])
    if not dates:
        return ""
    latest = sorted(dates)[-1]
    # 只显示 MM-DD
    parts = latest.split("-")
    if len(parts) == 3:
        return f"{parts[1]}-{parts[2]}"
    return latest


def _format_cluster(cluster: list[dict], date_str: str = "") -> list[str]:
    """格式化一个主题簇为 Markdown — 清晰分行版"""
    raw_title = _clean_title(_pick_best_title(cluster))
    score = _cluster_score(cluster)

    # 跟进报道：只显示一行简短提醒
    is_followup, followup_note = _is_cluster_followup(cluster)
    if is_followup:
        note = followup_note or raw_title or "跟进报道"
        return [f"- 📌 持续关注：{note}", ""]

    # AI 提炼的核心内容
    intel_summary = ""
    for it in cluster:
        s = it.get("analysis", {}).get("intel_summary", "")
        if s:
            intel_summary = s
            break
    if not intel_summary:
        intel_summary = _extract_summary(cluster)

    # 标题为空时，用 intel_summary 第一句话
    title = raw_title
    if not title and intel_summary:
        import re
        first_sentence = re.split(r'[，。；！？,;!?]', intel_summary)[0]
        title = first_sentence if len(first_sentence) >= 8 else intel_summary[:50]
    if not title:
        title = "新动态"

    # 事件类型标签
    event_type = ""
    for it in cluster:
        et = it.get("analysis", {}).get("event_type", "")
        if et:
            event_type = et
            break

    # 推荐跟进理由
    rec_reason = ""
    for it in cluster:
        rr = it.get("analysis", {}).get("recommendation_reason", "")
        if rr:
            rec_reason = rr
            break

    # 创意切入
    focus_angle = ""
    for it in cluster:
        fa = it.get("analysis", {}).get("focus_media_angle", "")
        if fa and fa != _DEFAULT_ANGLE:
            focus_angle = fa
            break

    lines = []
    # 标题行：事件类型 · 核心信息 · 日期
    type_tag = f"【{event_type}】" if event_type else ""
    pub_date = _get_cluster_date(cluster)
    date_tag = f" · {pub_date}" if pub_date else ""
    lines.append(f"- {type_tag}**{title}**（{score}/10{date_tag}）")
    lines.append("")
    # 核心内容
    if intel_summary:
        lines.append(f"  {intel_summary}")
        lines.append("")
    # 来源链接
    seen_urls = set()
    sources = []
    for it in cluster:
        url = it.get("url", "")
        t = it.get("title", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            source_name = _extract_source_name(url, t)
            sources.append(f"[{source_name}]({url})")

    if len(sources) == 1:
        lines.append(f"  {sources[0]}")
    elif len(sources) <= 3:
        lines.append(f"  来源：{'｜'.join(sources)}")
    else:
        lines.append(f"  来源：{'｜'.join(sources[:3])} +{len(sources)-3} 篇")

    lines.append("")
    # 跟进理由
    if rec_reason:
        lines.append(f"  💡 **为什么现在跟**: {rec_reason}")
        lines.append("")
    # 创意切入
    if focus_angle:
        lines.append(f"  🎨 **分众创意**: {focus_angle}")
        lines.append("")

    return lines


# fallback 模板化建议的标记，用于在报告中过滤掉
_DEFAULT_ANGLE = ""


def _format_industry_cluster(cluster: list[dict]) -> list[str]:
    """格式化行业情报簇：含客户线索"""
    title = _clean_title(_pick_best_title(cluster))
    score = _cluster_score(cluster)

    intel_summary = ""
    for it in cluster:
        s = it.get("analysis", {}).get("intel_summary", "")
        if s:
            intel_summary = s
            break
    if not intel_summary:
        intel_summary = _extract_summary(cluster)

    # 汇总所有条目的 prospect_leads，去重
    seen_leads = set()
    all_leads = []
    for it in cluster:
        for lead in it.get("analysis", {}).get("prospect_leads", []):
            name = lead.get("name", "")
            if name and name not in seen_leads:
                seen_leads.add(name)
                all_leads.append(lead)

    lines = []
    if len(cluster) > 1:
        lines.append(f"- **{title}**（{score}/10，{len(cluster)} 篇）")
    else:
        lines.append(f"- **{title}**（{score}/10）")
    if intel_summary:
        lines.append(f"  {intel_summary}")

    if all_leads:
        lines.append(f"  **🎯 值得拜访：**")
        for lead in all_leads[:3]:
            lines.append(f"  - **{lead['name']}**：{lead.get('reason', '')}")

    # 来源
    seen_urls = set()
    sources = []
    for it in cluster:
        url = it.get("url", "")
        t = it.get("title", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(f"[{_extract_source_name(url, t)}]({url})")
    if len(sources) == 1:
        lines.append(f"  {sources[0]}")
    elif len(sources) <= 3:
        lines.append(f"  来源：{'｜'.join(sources)}")
    else:
        lines.append(f"  来源：{'｜'.join(sources[:3])} +{len(sources)-3} 篇")

    lines.append("")
    return lines


def _extract_source_name(url: str, title: str) -> str:
    """从 URL 或标题中提取来源名称"""
    # 从标题末尾提取 "- 来源名" 或 "| 来源名"
    m = re.search(r'[\-|_—–·]\s*([^\-|_—–·]+)$', title)
    if m:
        name = m.group(1).strip()
        if 2 <= len(name) <= 15:
            return name

    # 从 URL 提取域名简称
    domain_map = {
        "sina.com": "新浪", "sohu.com": "搜狐", "163.com": "网易",
        "qq.com": "腾讯", "ifeng.com": "凤凰网", "36kr.com": "36氪",
        "ithome.com": "IT之家", "autohome.com": "汽车之家",
        "xinhuanet.com": "新华网", "thepaper.cn": "澎湃",
        "huxiu.com": "虎嗅", "leiphone.com": "雷锋网",
        "cnbeta.com": "cnBeta", "caixin.com": "财新",
    }
    for domain, name in domain_map.items():
        if domain in url:
            return name
    return "原文链接"


def generate_report(analyzed_results: list[dict], date_str: str = None) -> str:
    """生成主题聚合的 Markdown 日报"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    if not analyzed_results:
        return ""

    # 按品牌分组
    by_brand = {}
    for r in analyzed_results:
        brand = r.get("brand", "未知")
        if brand not in by_brand:
            by_brand[brand] = []
        by_brand[brand].append(r)

    lines = [f"# 销售情报日报 · {date_str}", ""]

    brand_items = {k: v for k, v in by_brand.items() if not k.startswith("[行业]")}
    industry_items = {k: v for k, v in by_brand.items() if k.startswith("[行业]")}

    # ── 客户动态 ──
    if brand_items:
        lines.append("## 📋 客户动态")
        lines.append("")

    for brand, items in brand_items.items():
        clusters = _cluster_items(items)

        urgency_groups = {"🔴": [], "🟡": [], "⚪": []}
        for cluster in clusters:
            u = _cluster_urgency(cluster)
            urgency_groups[u].append(cluster)

        if not urgency_groups["🔴"] and not urgency_groups["🟡"]:
            continue

        lines.append("")
        lines.append(f"### {brand}")
        lines.append("")

        if urgency_groups["🔴"]:
            lines.append("#### 🔴 本周跟进")
            lines.append("")
            for cluster in urgency_groups["🔴"]:
                lines.extend(_format_cluster(cluster, date_str))
                lines.append("")

        if urgency_groups["🟡"]:
            lines.append("#### 🟡 本月关注")
            lines.append("")
            for cluster in urgency_groups["🟡"]:
                lines.extend(_format_cluster(cluster, date_str))
                lines.append("")

    # ── 行业动态 ──
    if industry_items:
        lines.append("## 🏭 行业动态")
        lines.append("")
        for industry_name, items in industry_items.items():
            display_name = industry_name.replace("[行业]", "")
            lines.append(f"### {display_name}")
            lines.append("")
            clusters = _cluster_items(items)
            for cluster in clusters:
                lines.extend(_format_industry_cluster(cluster))

    lines.append("---")
    lines.append(f"*由销售情报助手自动生成 · {date_str}*")

    return "\n".join(lines)


# ── 融资速报板块 ──────────────────────────────────────────

def _format_fundraising_cluster(cluster: list[dict], date_str: str = "") -> list[str]:
    """格式化融资情报簇"""
    raw_title = _clean_title(_pick_best_title(cluster))
    score = _cluster_score(cluster)
    track_name = cluster[0].get("track_name", "")

    # 跟进报道：只显示简短提醒
    is_followup, followup_note = _is_cluster_followup(cluster)
    if is_followup:
        note = followup_note or raw_title or "跟进报道"
        return [f"- 📌 持续关注：{note}", ""]

    # 优先用 AI 生成的 intel_summary
    intel_summary = ""
    for it in cluster:
        s = it.get("analysis", {}).get("intel_summary", "")
        if s:
            intel_summary = s
            break
    if not intel_summary:
        intel_summary = _extract_summary(cluster)

    # 标题为空时，用 intel_summary 第一句话
    title = raw_title
    if not title and intel_summary:
        import re
        first_sentence = re.split(r'[，。；！？,;!?]', intel_summary)[0]
        title = first_sentence if len(first_sentence) >= 8 else intel_summary[:50]
    if not title:
        title = "融资动态"

    # 创意切入
    focus_angle = ""
    for it in cluster:
        fa = it.get("analysis", {}).get("focus_media_angle", "")
        if fa and fa != _DEFAULT_ANGLE:
            focus_angle = fa
            break

    # 推荐理由
    rec_reason = ""
    for it in cluster:
        rr = it.get("analysis", {}).get("recommendation_reason", "")
        if rr:
            rec_reason = rr
            break

    lines = []
    count_str = f"，{len(cluster)} 篇报道" if len(cluster) > 1 else ""
    date_suffix = f" · {date_str}" if date_str else ""
    # 品牌名独占一行加粗，与新闻标题分层
    brand_label = ""
    for it in cluster:
        company = _get_fundraising_company(it)
        if company:
            brand_label = company
            break
    if brand_label:
        lines.append(f"**{brand_label}**")
        lines.append("")
    pub_date = _get_cluster_date(cluster)
    pub_tag = f" · {pub_date}" if pub_date else ""
    lines.append(f"- {title}（{score}/10{count_str}{pub_tag}）")
    lines.append("")
    if intel_summary:
        lines.append(f"  {intel_summary}")
        lines.append("")

    # 来源
    seen_urls = set()
    sources = []
    for it in cluster:
        url = it.get("url", "")
        t = it.get("title", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append(f"[{_extract_source_name(url, t)}]({url})")
    if len(sources) == 1:
        lines.append(f"  {sources[0]}")
    elif len(sources) <= 3:
        lines.append(f"  来源：{'｜'.join(sources)}")
    else:
        lines.append(f"  来源：{'｜'.join(sources[:3])} +{len(sources)-3} 篇")

    lines.append("")
    if rec_reason:
        lines.append(f"  💡 **为什么现在跟**: {rec_reason}")
        lines.append("")
    if focus_angle:
        lines.append(f"  🎨 **分众创意**: {focus_angle}")
        lines.append("")

    return lines


def _group_fundraising_by_track(results: list[dict]) -> dict:
    """按赛道分组融资结果"""
    tracks = {}
    for r in results:
        track = r.get("track_name", "其他")
        if track not in tracks:
            tracks[track] = []
        tracks[track].append(r)
    return tracks


def _get_fundraising_company(item: dict) -> str:
    """从融资条目中提取实际公司名（用于聚类合并）"""
    leads = item.get("analysis", {}).get("prospect_leads", [])
    if leads and leads[0].get("name"):
        return leads[0]["name"].strip()
    return ""


def _cluster_fundraising_items(items: list[dict]) -> list[list[dict]]:
    """融资条目聚类：先按公司名合并，再对剩余条目做标题聚类。

    解决同一公司出现在不同标题文章中未被合并的问题。
    """
    # 第一轮：按公司名分组
    by_company = {}
    no_company = []
    for item in items:
        company = _get_fundraising_company(item)
        if company:
            if company not in by_company:
                by_company[company] = []
            by_company[company].append(item)
        else:
            no_company.append(item)

    clusters = list(by_company.values())

    # 第二轮：无公司名的条目用标题聚类
    if no_company:
        clusters.extend(_cluster_items(no_company))

    return clusters





def generate_fundraising_section(fundraising_results: list[dict], date_str: str) -> str:
    """生成融资速报板块"""
    if not fundraising_results:
        return ""

    by_track = _group_fundraising_by_track(fundraising_results)

    # ── 第一步：按公司名去重，避免同一公司出现在多个赛道 ──
    # 每个公司只保留一条（取紧迫度最高的）
    # 公司名归一化：去掉"机器人"等后缀，避免同一公司不同命名被当作不同公司
    def _normalize_company(name: str) -> str:
        """归一化公司名，用于去重比较"""
        if not name:
            return ""
        import re
        # 去掉常见后缀
        for suffix in ["机器人", "科技", "智能", "网络", "数字", "系统"]:
            if name.endswith(suffix) and len(name) > len(suffix) + 2:
                name = name[:-len(suffix)]
        return name.strip()

    seen_companies = {}  # normalized_name -> (original_name, best_item)
    for item in fundraising_results:
        company = _get_fundraising_company(item)
        if not company:
            company = item.get("brand", "").replace("[融资]", "")
        normalized = _normalize_company(company)
        if not normalized:
            continue
        if normalized not in seen_companies:
            seen_companies[normalized] = (company, item)
        else:
            # 取紧迫度更高的
            existing_item = seen_companies[normalized][1]
            existing_u = existing_item.get("analysis", {}).get("urgency", "⚪")
            new_u = item.get("analysis", {}).get("urgency", "⚪")
            u_priority = {"🔴": 3, "🟡": 2, "⚪": 1}
            if u_priority.get(new_u, 0) > u_priority.get(existing_u, 0):
                seen_companies[normalized] = (company, item)

    deduped_results = [item for _, item in seen_companies.values()]

    # 汇总总览表
    red_count = sum(
        1 for r in deduped_results
        if r.get("analysis", {}).get("urgency") == "🔴"
    )
    yellow_count = sum(
        1 for r in deduped_results
        if r.get("analysis", {}).get("urgency") == "🟡"
    )

    lines = ["## 💰 融资新闻", ""]
    lines.append(f"> **本周期关注赛道融资动态一览** · 共 {len(deduped_results)} 品牌获融资，其中 🔴 本周跟进 {red_count} 家，🟡 本月关注 {yellow_count} 家")
    lines.append("")
    lines.append("| 赛道 | 品牌 | 融资概况 | 紧迫度 |")
    lines.append("|------|------|----------|--------|")

    # 汇总表按公司去重后展示
    seen_brands_in_table = set()
    for item in deduped_results:
        u = item.get("analysis", {}).get("urgency", "⚪")
        if u not in ("🔴", "🟡"):
            continue
        prospect_leads = item.get("analysis", {}).get("prospect_leads", [])
        if prospect_leads and prospect_leads[0].get("name"):
            brand = prospect_leads[0]["name"]
        else:
            brand = item.get("brand", "").replace("[融资]", "")
        normalized_brand = _normalize_company(brand)
        if normalized_brand in seen_brands_in_table:
            continue
        seen_brands_in_table.add(normalized_brand)
        track_name = item.get("track_name", "其他")
        summary = item.get("analysis", {}).get("intel_summary", "")
        amount = ""
        import re
        m = re.search(r"([0-9]+[亿万元])(?:.*?融资|币)", summary)
        if not m:
            m = re.search(r"([0-9]+[亿万元])", summary)
        amount = m.group(1) if m else "—"
        round_match = re.search(r"(A|B|C|D|S)[轮+]", summary)
        round_str = f"{round_match.group(1)}轮 · " if round_match else ""
        lines.append(f"| {track_name} | {brand} | {round_str}{amount} | {u} |")

    # ── 第二步：赛道详情（每个公司只出现一次） ──
    # 按公司名聚类，取代按赛道聚类
    all_clusters = _cluster_fundraising_items(deduped_results)

    # 按紧迫度分组
    urgency_groups = {"🔴": [], "🟡": [], "⚪": []}
    for cluster in all_clusters:
        u = _cluster_urgency(cluster)
        urgency_groups[u].append(cluster)

    if not urgency_groups["🔴"] and not urgency_groups["🟡"]:
        return "\n".join(lines)

    # 按赛道组织详情：先聚类再按赛道分组展示
    # 每个公司只归属一个赛道（取第一个出现的赛道）
    track_for_company = {}  # company -> track_name
    company_track_assigned = set()
    for track_name, items in by_track.items():
        for item in items:
            company = _get_fundraising_company(item)
            if not company:
                company = item.get("brand", "").replace("[融资]", "")
            if company not in company_track_assigned:
                track_for_company[company] = track_name
                company_track_assigned.add(company)

    # 按赛道分组输出
    for track_name, items in by_track.items():
        track_clusters = []
        for cluster in all_clusters:
            # 取簇中任意一条的公司名
            sample_item = cluster[0]
            company = _get_fundraising_company(sample_item)
            if not company:
                company = sample_item.get("brand", "").replace("[融资]", "")
            if track_for_company.get(company) == track_name:
                track_clusters.append(cluster)

        track_urgency_groups = {"🔴": [], "🟡": [], "⚪": []}
        for cluster in track_clusters:
            u = _cluster_urgency(cluster)
            track_urgency_groups[u].append(cluster)

        if not track_urgency_groups["🔴"] and not track_urgency_groups["🟡"]:
            continue

        lines.append("")
        lines.append(f"### {track_name}")
        lines.append("")

        if track_urgency_groups["🔴"]:
            lines.append("#### 🔴 本周跟进")
            lines.append("")
            for cluster in track_urgency_groups["🔴"]:
                lines.extend(_format_fundraising_cluster(cluster, date_str))
                lines.append("")

        if track_urgency_groups["🟡"]:
            lines.append("#### 🟡 本月关注")
            lines.append("")
            for cluster in track_urgency_groups["🟡"]:
                lines.extend(_format_fundraising_cluster(cluster, date_str))
                lines.append("")

    return "\n".join(lines)


# ── 行业洞察板块 ──────────────────────────────────────────


def generate_full_report(
    analyzed_results: list[dict],
    fundraising_results: list[dict] = None,
    date_str: str = None,
    profile_name: str = None,
    brand_configs: list[dict] = None,
    is_first_run: bool = False,
    endorsement_items: list[dict] = None,
) -> str:
    """
    生成完整日报（整合所有板块）

    板块顺序：
    1. 客户动态（品牌监控）
    2. 融资速报

    brand_configs: 品牌配置列表，用于在无新闻时显示提示
    is_first_run: 是否为首次运行（影响无新闻时的提示文案）
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    if not analyzed_results and not fundraising_results:
        return ""

    title = f"# 销售情报日报 · {date_str}"
    if profile_name:
        title += f" · {profile_name}"
    lines = [title, ""]

    # ── 顶部速览 ──
    brand_items_all = [r for r in analyzed_results if not r.get("brand", "").startswith("[")]
    red_brand = sum(1 for r in brand_items_all if r.get("analysis", {}).get("urgency") == "🔴")
    yellow_brand = sum(1 for r in brand_items_all if r.get("analysis", {}).get("urgency") == "🟡")
    red_fr = sum(1 for r in (fundraising_results or []) if r.get("analysis", {}).get("urgency") == "🔴")
    yellow_fr = sum(1 for r in (fundraising_results or []) if r.get("analysis", {}).get("urgency") == "🟡")
    # 有新闻的品牌（不含行业/融资标签）
    brands_active = sorted(set(
        r.get("brand", "") for r in analyzed_results
        if not r.get("brand", "").startswith("[")
        and r.get("analysis", {}).get("urgency") in ("🔴", "🟡")
    ))
    summary_parts = []
    news_count = red_brand + yellow_brand
    if news_count:
        summary_parts.append(f"客户新闻 {news_count} 条")
    fr_count = red_fr + yellow_fr
    if fr_count:
        summary_parts.append(f"融资线索 {fr_count} 条")
    if brands_active:
        summary_parts.append(f"涉及：{'、'.join(brands_active)}")
    if summary_parts:
        lines.append(f"> {' ｜ '.join(summary_parts)}")
        lines.append("")

    # ── 1. 客户动态（仅品牌监控，不含行业） ──
    brand_items = [r for r in analyzed_results if not r.get("brand", "").startswith("[")]

    if brand_items:
        lines.append("## 📋 客户新闻")
        lines.append("")

    # 品牌客户
    brand_by_name = {}
    for r in brand_items:
        brand = r.get("brand", "未知")
        if brand not in brand_by_name:
            brand_by_name[brand] = []
        brand_by_name[brand].append(r)

    for brand, items in brand_by_name.items():
        clusters = _cluster_items(items)
        urgency_groups = {"🔴": [], "🟡": [], "⚪": []}
        for cluster in clusters:
            u = _cluster_urgency(cluster)
            urgency_groups[u].append(cluster)

        if not urgency_groups["🔴"] and not urgency_groups["🟡"]:
            continue

        lines.append("")
        lines.append(f"### {brand}")
        lines.append("")

        if urgency_groups["🔴"]:
            lines.append("#### 🔴 本周跟进")
            lines.append("")
            for cluster in urgency_groups["🔴"]:
                lines.extend(_format_cluster(cluster, date_str))
                lines.append("")

        if urgency_groups["🟡"]:
            lines.append("#### 🟡 本月关注")
            lines.append("")
            for cluster in urgency_groups["🟡"]:
                lines.extend(_format_cluster(cluster, date_str))
                lines.append("")

    # ── 无新闻品牌提示（压缩为一行） ──
    if brand_configs:
        brands_with_news = set(brand_by_name.keys())
        brands_monitored = {cfg["name"] for cfg in brand_configs}
        brands_no_news = brands_monitored - brands_with_news
        if brands_no_news:
            lines.append("")
            lines.append(f"暂无动态：{'、'.join(sorted(brands_no_news))}")
            lines.append("")

    # ── 2. 融资速报 ──
    if fundraising_results:
        fr_section = generate_fundraising_section(fundraising_results, date_str)
        if fr_section:
            lines.append("")
            lines.append(fr_section)

    # ── 3. 本周代言人速报 ──
    if endorsement_items:
        end_section = generate_endorsement_section(endorsement_items, date_str)
        if end_section:
            lines.append("")
            lines.append(end_section)

    lines.append("---")
    lines.append(f"*由销售情报助手自动生成 · {date_str}*")

    return "\n".join(lines)


def generate_endorsement_section(endorsement_items: list[dict], date_str: str = "") -> str:
    """
    生成本周代言人速报板块。

    endorsement_items: [{
        "brand": "品牌名",
        "celebrity": "代言人名",
        "industry": "行业",
        "detail": "代言详情",
        "relevance": "与分众的关联分析",
        "urgency": "🔴/🟡/⚪",
    }, ...]
    """
    if not endorsement_items:
        return ""

    lines = ["## 🌟 本周代言人速报", ""]
    lines.append(f"> 本周共 {len(endorsement_items)} 条代言人动态，已按行业匹配")
    lines.append("")

    by_industry = {}
    for item in endorsement_items:
        ind = item.get("industry", "其他")
        if ind not in by_industry:
            by_industry[ind] = []
        by_industry[ind].append(item)

    for industry, items in by_industry.items():
        lines.append(f"### {industry}")
        lines.append("")
        for item in items:
            brand = item.get("brand", "")
            celebrity = item.get("celebrity", "")
            detail = item.get("detail", "")
            relevance = item.get("relevance", "")
            urgency = item.get("urgency", "🟡")
            lines.append(f"- {urgency} **{brand} × {celebrity}**")
            if detail:
                lines.append(f"  {detail}")
            if relevance:
                lines.append(f"  💡 **分众机会**: {relevance}")
            lines.append("")

    return "\n".join(lines)
