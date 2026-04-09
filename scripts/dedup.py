"""
去重机制
1. URL 去重：维护已推送 URL 列表，防止跨日重复推送
2. 标题去重：同一品牌下，标题高度相似的多条报道只保留第一条
3. 事件去重：维护已推送事件摘要，供 AI 判断跨日跟进报道
"""

import fcntl
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta

# 使用统一的档案上下文管理
from scripts.profile_context import get_profile, set_profile, get_profile_data_dir


def _seen_urls_path() -> str:
    return os.path.join(get_profile_data_dir(), "seen_urls.json")

def _seen_events_path() -> str:
    return os.path.join(get_profile_data_dir(), "seen_events.json")

def _seen_urls_lock_path() -> str:
    return os.path.join(get_profile_data_dir(), "seen_urls.json.lock")

@contextmanager
def _locked_seen_urls():
    """对 seen_urls.json 加锁，保证并发读写安全。"""
    lock_path = _seen_urls_lock_path()
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)


def load_seen_urls() -> dict:
    """加载已推送 URL 记录 {url: timestamp}"""
    path = _seen_urls_path()
    if not os.path.exists(path):
        return {}
    with _locked_seen_urls():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def save_seen_urls(seen: dict):
    """保存已推送 URL 记录"""
    path = _seen_urls_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _locked_seen_urls():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)


def _normalize_url(url: str) -> str:
    """
    URL 规范化：
    1. 强制 https://
    2. 移除 www. 前缀
    3. 移除尾部斜杠
    4. 移除 UTM 参数
    5. 验证 URL 格式，无效则返回空字符串
    """
    if not url:
        return url
    from urllib.parse import urlparse, parse_qs

    try:
        parsed = urlparse(url)
        scheme = "https"
        netloc = parsed.netloc

        # 验证：必须有有效的域名（netloc 不能为空，且不能是纯 IP 或包含危险字符）
        if not netloc or not any(c.isalpha() for c in netloc):
            print(f"  [警告] 无效 URL 域名: {url[:60]}...")
            return ""

        # 移除 www. 前缀
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # 移除 UTM 参数
        query_params = parse_qs(parsed.query)
        utm_keys = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id"}
        filtered_params = {k: v for k, v in query_params.items() if k not in utm_keys}
        query = "&".join(f"{k}={v[0]}" for k, v in filtered_params.items())

        # 移除尾部斜杠
        path = parsed.path.rstrip("/")

        result = f"{scheme}://{netloc}{path}"
        if query:
            result += f"?{query}"
        if parsed.fragment:
            result += f"#{parsed.fragment}"
        return result
    except Exception as e:
        print(f"  [警告] URL 解析失败: {url[:60]}... ({e})")
        return ""


def load_seen_events() -> list[dict]:
    """加载已推送事件记录 [{brand, event_key, date}, ...]"""
    path = _seen_events_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen_events(events: list[dict]):
    """保存已推送事件记录"""
    path = _seen_events_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def get_recent_events_for_brand(brand: str, max_age_days: int = 14) -> list[dict]:
    """获取某品牌近期已推过的事件关键词列表"""
    events = load_seen_events()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    return [
        {"brand": e.get("brand", ""), "event_key": e.get("event_key", "")}
        for e in events
        if e.get("brand") == brand and e.get("date", "") >= cutoff
    ]


def record_pushed_events(pushed_events: list[dict]):
    """记录本次推送的新事件（由 main.py 在报告生成后调用）"""
    events = load_seen_events()
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    # 清理过期
    events = [e for e in events if e.get("date", "") >= cutoff]
    today = datetime.now().strftime("%Y-%m-%d")
    for e in pushed_events:
        events.append({**e, "date": today})
    save_seen_events(events)



def _normalize_title(title: str) -> str:
    """标题归一化：去除标点、空格、来源标注，便于相似度比较"""
    # 去掉常见后缀 "- IT之家", "| 36氪", "_腾讯新闻" 等
    title = re.sub(r'[\s]*[|\-_—–·][\s]*[^\s]+$', '', title)
    # 去掉标点和空格
    title = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019「」【】\s\-_|·]', '', title)
    return title.lower()


def _title_similar(t1: str, t2: str) -> bool:
    """判断两个标题是否高度相似（归一化后包含关系或重合度 > 70%）"""
    n1 = _normalize_title(t1)
    n2 = _normalize_title(t2)
    if not n1 or not n2:
        return False
    # 短标题完全包含在长标题中
    if n1 in n2 or n2 in n1:
        return True
    # 字符重合度
    common = set(n1) & set(n2)
    shorter = min(len(set(n1)), len(set(n2)))
    if shorter > 0 and len(common) / shorter > 0.7:
        return True
    return False


def dedup_by_title(results: list[dict]) -> list[dict]:
    """同一品牌下，标题高度相似的结果只保留第一条"""
    kept = []
    seen_titles_by_brand = {}  # {brand: [title1, title2, ...]}

    for r in results:
        brand = r.get("brand", "")
        title = r.get("title", "")
        if not title:
            kept.append(r)
            continue

        if brand not in seen_titles_by_brand:
            seen_titles_by_brand[brand] = []

        is_dup = False
        for seen_title in seen_titles_by_brand[brand]:
            if _title_similar(title, seen_title):
                is_dup = True
                break

        if not is_dup:
            kept.append(r)
            seen_titles_by_brand[brand].append(title)

    return kept


def deduplicate(results: list[dict], max_age_days: int = 30) -> list[dict]:
    """
    三层去重：
    1. URL 去重（跨日，规范化后比较）
    2. 标题去重（同批次内同品牌）
    3. 事件去重（跨日，基于已推送事件的关键词匹配）
    """
    seen = load_seen_urls()
    now = datetime.now().isoformat()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()

    # 清理过期记录（也做 URL 规范化）
    seen = {_normalize_url(url): ts for url, ts in seen.items() if ts > cutoff}

    # 第1层: URL 去重（使用规范化 URL）
    new_results = []
    url_dup_count = 0
    for r in results:
        url = r.get("url", "")
        normalized = _normalize_url(url)
        if normalized and normalized not in seen:
            new_results.append(r)
            seen[normalized] = now
        else:
            url_dup_count += 1

    save_seen_urls(seen)
    if url_dup_count > 0:
        print(f"  [去重] URL 去重: 过滤 {url_dup_count} 条")

    # 第2层: 标题去重
    before_title = len(new_results)
    new_results = dedup_by_title(new_results)
    title_dup_count = before_title - len(new_results)
    if title_dup_count > 0:
        print(f"  [去重] 标题去重: 过滤 {title_dup_count} 条")

    # 第3层: 事件去重（跨日，基于已推送事件关键词）
    seen_events = load_seen_events()
    if seen_events:
        event_cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        recent_event_keys = {
            (e.get("brand", ""), e.get("event_key", ""))
            for e in seen_events
            if e.get("date", "") >= event_cutoff and e.get("event_key")
        }
        if recent_event_keys:
            before_event = len(new_results)
            filtered = []
            for r in new_results:
                brand = r.get("brand", "")
                title_norm = _normalize_title(r.get("title", ""))
                # 检查标题是否包含已推送事件的关键词
                is_seen_event = False
                for ev_brand, ev_key in recent_event_keys:
                    if ev_brand == brand and ev_key and ev_key in title_norm:
                        is_seen_event = True
                        break
                if not is_seen_event:
                    filtered.append(r)
            event_dup_count = before_event - len(filtered)
            if event_dup_count > 0:
                print(f"  [去重] 事件去重: 过滤 {event_dup_count} 条（已推送过的事件）")
            new_results = filtered

    return new_results


# ── 融资公司名归一化（供 dedup 和 report 共用）──────────────────

def normalize_company(name: str) -> str:
    """归一化公司名，用于去重比较。

    从 report.py 提前到 dedup 阶段，解决"参半"/"小阔科技"/"深圳小阔科技"
    被当作不同公司的问题。
    """
    if not name:
        return ""
    # 提取括号内的品牌名(通常是核心品牌)
    bracket_match = re.search(r'[（(]([^）)]+)[）)]', name)
    if bracket_match:
        brand_name = bracket_match.group(1)
        if len(brand_name) >= 2 and not any(x in brand_name for x in ['有限', '股份', '公司']):
            return brand_name.strip()
    # 去掉公司类型后缀
    name = re.sub(r'(股份)?有限公司$', '', name)
    name = re.sub(r'集团$', '', name)
    # 去掉地区前缀
    name = re.sub(r'^(深圳|北京|上海|广州|杭州|成都|武汉)', '', name)
    # 去掉常见后缀
    for suffix in ["机器人", "科技", "智能", "网络", "数字", "系统"]:
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            name = name[:-len(suffix)]
    return name.strip()


def _extract_company_from_title(title: str) -> str:
    """从融资新闻标题中提取公司名。

    常见模式：'参半完成30亿融资'、'小阔科技获B轮融资'
    """
    # 模式1: X完成/获得/获X轮融资
    m = re.search(r'^(.{2,15}?)(完成|获得|获|宣布)', title)
    if m:
        return m.group(1).strip()
    # 模式2: 标题开头到第一个标点
    m = re.search(r'^([^，。！？、：\s]{2,15})', title)
    if m:
        return m.group(1).strip()
    return ""


# 赛道-业务关键词映射（与 quality_rules.py 保持一致）
_TRACK_BUSINESS_KW = {
    "AI大模型": ["大模型", "LLM", "人工智能", "AI公司", "AI创业", "AI独角兽", "生成式AI", "AGI", "AIGC", "ChatGPT", "GPT", "文心", "通义", "豆包", "DeepSeek", "Kimi", "月之暗面", "智谱", "百川", "零一万物", "阶跃", "元象", "深度求索", "OpenAI", "Anthropic", "Mistral", "AI助手", "AI写作", "AI图像", "AI视频"],
    "机器人/具身智能": ["机器人", "具身智能", "人形机器人", "机械臂", "自动驾驶", "AGV", "ROS", "Robotics", "Embodied", "灵巧手", "四足机器人", "机械狗", "千寻智能", "宇树", "追觅", "傅利叶", "智元", "星动纪元", "具身", "机器人关节", "智能搬运"],
    "新能源汽车/智能汽车": ["新能源汽车", "电动车", "锂电池", "动力电池", "充电桩", "电驱", "智驾", "辅助驾驶", "宁德时代", "比亚迪", "蔚来", "小鹏", "理想", "广汽埃安", "华为智驾", "鸿蒙智行", "问界", "智界", "享界", "尊界", "尚界", "启境", "华境", "奕境", "纯电动", "增程式", "混动"],
    "智能硬件/IoT": ["智能硬件", "IoT", "物联网", "智能家居", "可穿戴", "智能音箱", "智能手表", "AR", "VR", "MR", "元宇宙硬件", "智能门锁", "扫地机器人", "智能眼镜"],
    "企业服务/SaaS": ["SaaS", "企业服务", "云服务", "CRM", "ERP", "OA", "HR SaaS", "财税", "数据分析", "BI", "低代码", "B2B", "企业软件"],
    "半导体": ["半导体", "芯片", "晶圆", "光刻", "封装测试", "IC设计", "处理器", "GPU", "CPU", "ASIC", "AI芯片", "集成电路"],
    "消费品": ["消费", "零售", "电商", "品牌", "渠道", "门店", "快消"],
    "食品/粮油": ["食品", "粮油", "饮料", "乳制品", "调味品", "零食", "餐饮", "乳业", "奶粉", "低温奶"],
    "日用洗护": ["日化", "洗护", "护肤品", "化妆品", "美妆", "个护", "卫生用品", "洗衣液", "洗洁精"],
    "服装": ["服装", "服饰", "鞋", "箱包", "面料", "纺织", "运动服", "童装"],
    "家居用品": ["家居", "家电", "家具", "厨卫", "家装", "照明", "床垫"],
    "休闲食品": ["零食", "糖果", "巧克力", "烘焙", "饮品", "奶茶", "咖啡", "气泡水"],
    "儿童服饰": ["童装", "儿童服装", "母婴", "玩具", "儿童用品", "婴儿", "尿裤"],
    "保健品": ["保健", "营养品", "维生素", "鱼油", "膳食补充", "功能性食品", "益生菌", "氨糖", "钙片", "燕窝", "阿胶"],
    "户外服装": ["户外", "登山", "露营", "徒步", "运动装备", "冲锋衣", "帐篷", "探路者", "北面", "始祖鸟", "迪卡侬", "凯乐石", "防晒衣", "登山鞋"],
    "教育": ["教育", "培训", "学习", "课程", "学校", "K12", "职业教育", "留学", "家教", "课外辅导", "早教", "少儿英语"],
    "医疗健康": ["医疗", "医院", "制药", "生物医药", "医疗器械", "体检", "齿科", "眼科", "中医", "医保"],
}

# AI公司关键词（出现在公司名中则大概率是AI公司）
_AI_COMPANY_KW = ["AI", "人工智能", "大模型", "智能科技", "DeepSeek", "OpenAI", "Kimi", "文心", "通义", "豆包", "智谱", "百川", "阶跃", "元象", "深度求索", "月之暗面", "零一", "千寻", "万物代码"]


def _score_track_alignment(company_name: str, track_name: str, content: str) -> float:
    """
    计算公司名+内容与赛道的匹配度。
    返回 0.0~1.0，0表示完全不匹配，1表示高度匹配。
    """
    if not track_name or track_name not in _TRACK_BUSINESS_KW:
        return 0.5  # 未知赛道，不降权也不加分

    text = f"{company_name} {content[:500]}".lower()
    keywords = _TRACK_BUSINESS_KW.get(track_name, [])
    score = 0.0

    # 公司名中包含赛道关键词
    for kw in keywords:
        if kw.lower() in company_name.lower():
            score += 0.4

    # 内容中出现赛道关键词
    matches = sum(1 for kw in keywords if kw.lower() in text)
    if matches > 0:
        score += min(0.6, matches * 0.15)

    return min(1.0, score)


def _is_likely_ai_company(company_name: str) -> bool:
    """判断公司名是否像AI公司"""
    name_lower = company_name.lower()
    return any(kw.lower() in name_lower for kw in _AI_COMPANY_KW)


def _anti_track_pollution() -> dict:
    """返回 AI 赛道反污染表：AI公司不应出现在这些赛道"""
    return {
        "AI大模型": ["日用洗护", "消费品", "休闲食品", "儿童服饰", "家居用品", "服装", "美妆", "护肤", "食品/粮油"],
    }


def dedup_fundraising_by_company(results: list[dict]) -> list[dict]:
    """融资结果按公司名归一化去重，同一公司只保留内容最丰富的一条。"""
    if not results:
        return results

    by_company = {}  # normalized_name -> list of results
    no_company = []

    for r in results:
        # 先从标题提取公司名
        title = r.get("title", "")
        company = _extract_company_from_title(title)
        if not company:
            company = r.get("brand", "").replace("[融资]", "").strip()

        normalized = normalize_company(company)
        if not normalized:
            no_company.append(r)
            continue

        by_company.setdefault(normalized, []).append(r)

    # 每个公司只保留内容最长的一条（同时解决跨赛道冲突）
    deduped = []
    company_dup_count = 0
    track_conflict_count = 0
    pollution_filtered_count = 0

    anti_pollution = _anti_track_pollution()

    for normalized, items in by_company.items():
        # ── 第一步：检测跨赛道冲突 ──
        track_names_seen = set(r.get("track_name", "") for r in items if r.get("track_name"))
        is_ai = _is_likely_ai_company(normalized)

        if len(track_names_seen) > 1:
            track_conflict_count += 1
            # 同一公司被多个赛道捕获，打日志
            print(f"  [赛道冲突] 公司'{normalized}'出现在多个赛道: {track_names_seen}")

        # ── 第二步：AI公司反污染检查 + 赛道匹配度评分 ──
        filtered_items = []
        for r in items:
            track = r.get("track_name", "")
            title = r.get("title", "")
            content = r.get("content", "")[:500]

            # AI公司被错误归入非AI赛道 → 过滤
            if is_ai and track in anti_pollution.get("AI大模型", []):
                pollution_filtered_count += 1
                print(f"  [反污染] 过滤AI公司'{normalized}'的错误赛道'{track}'")
                continue

            # 计算赛道匹配度
            alignment_score = _score_track_alignment(normalized, track, content)
            r["_alignment_score"] = alignment_score
            filtered_items.append(r)

        if not filtered_items:
            continue

        # ── 第三步：按内容长度+匹配度排序，保留最佳 ──
        # 优先选匹配度高、内容丰富的
        def sort_key(r):
            score = r.get("_alignment_score", 0.5)
            content_len = len(r.get("content", ""))
            return (score, content_len)

        filtered_items.sort(key=sort_key, reverse=True)
        best = filtered_items[0]

        # ── 第四步：赛道一致性修正 ──
        # 如果当前赛道匹配度很低（< 0.3），且有更高分的赛道，修正 track_name
        best_track = best.get("track_name", "")
        best_score = best.get("_alignment_score", 0.0)

        if best_score < 0.3:
            # 寻找内容最匹配的赛道
            content = best.get("content", "")[:500]
            correct_track = None
            correct_score = 0.0
            for t, kws in _TRACK_BUSINESS_KW.items():
                score = _score_track_alignment(normalized, t, content)
                if score > correct_score:
                    correct_score = score
                    correct_track = t

            if correct_track and (correct_score - best_score) >= 0.2:
                print(f"  [赛道修正 dedup] '{normalized}': {best_track}({best_score:.2f}) → {correct_track}({correct_score:.2f})")
                best = dict(best)
                best["track_name"] = correct_track

        deduped.append(best)
        company_dup_count += len(items) - 1

    if company_dup_count > 0:
        print(f"  [去重] 融资公司名去重: 过滤 {company_dup_count} 条（同一公司不同表述）")
    if track_conflict_count > 0:
        print(f"  [去重] 跨赛道冲突: {track_conflict_count} 家公司被多赛道捕获，已修正赛道")
    if pollution_filtered_count > 0:
        print(f"  [去重] AI反污染过滤: {pollution_filtered_count} 条AI公司错误赛道条目被移除")

    return deduped + no_company
