"""
Layer 2 - 预处理模块
纯规则去重 + LLM 快速分类标引（品牌归属确认 + 赛道标注 + 跟进判断）
"""

import json
import re
import os
import sys

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

from scripts.dedup import deduplicate, _normalize_url, _normalize_title
from scripts.minimax_client import call_minimax


# ── 噪音域名过滤 ────────────────────────────────────────────────

_NOISE_DOMAINS = [
    r'tieba\.baidu\.com',
    r'bbs\.',
    r'forum\.',
    r'club\.',
]


def _is_noise_url(url: str) -> bool:
    """判断 URL 是否为噪音来源（论坛、贴吧、百科、内容农场）"""
    if not url:
        return False
    for pattern in _NOISE_DOMAINS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


# ── 规则去重（内部） ────────────────────────────────────────────

def _rule_dedup(results: list[dict]) -> list[dict]:
    """
    纯规则预处理：
    1. URL 去重（复用 dedup.py 的规范化逻辑）
    2. 噪音过滤（论坛、贴吧等直接丢弃）
    3. 标题去重（复用 dedup.py）
    """
    # 第1步：噪音过滤
    before_noise = len(results)
    results = [r for r in results if not _is_noise_url(r.get("url", ""))]
    noise_count = before_noise - len(results)
    if noise_count > 0:
        print(f"  [Layer2 规则] 噪音过滤: 过滤 {noise_count} 条")

    # 第2步：复用 dedup.py 的去重逻辑
    deduped = deduplicate(results)
    return deduped


# ── _rule_prefilter：移自 main.py，不改逻辑 ────────────────────

def _rule_prefilter(result: dict) -> bool:
    """规则粗筛：快速判断结果是否值得保留（Layer 1）。

    只做 True/False 判断，不打分。用于在 AI 分类之前过滤明显垃圾。
    返回 True = 保留，False = 过滤。
    """
    title = result.get("title", "")
    content = result.get("content", "")[:300]
    brand = result.get("brand", "")
    brand_names = result.get("brand_names", [brand])
    text = f"{title} {content}"

    # 行业/融资类结果不做品牌粗筛
    if brand.startswith("[行业]") or brand.startswith("[融资]"):
        return True

    # 品牌名（含子品牌）必须出现在标题或内容前300字
    if not any(bn and bn in text for bn in brand_names):
        return False

    # 品牌是配角的过滤（联合/携手/与X合作）
    matched_brand = ""
    for bn in brand_names:
        if bn and bn in title:
            matched_brand = bn
            break

    if matched_brand:
        peripheral_patterns = [
            rf'联合{re.escape(matched_brand)}',
            rf'携手{re.escape(matched_brand)}',
            rf'与{re.escape(matched_brand)}',
            rf'和{re.escape(matched_brand)}',
            rf'跨界{re.escape(matched_brand)}',
            rf'{re.escape(matched_brand)}联名',
            rf'{re.escape(matched_brand)}送',
            rf'{re.escape(matched_brand)}合作',
        ]
        if any(re.search(p, title) for p in peripheral_patterns):
            return False

    return True


# ── 分离融资 vs 品牌/行业 ───────────────────────────────────────

def _separate_by_type(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    分离融资结果 vs 品牌/行业结果。
    融资：[brand startswith '[融资]']
    """
    financing = [r for r in results if r.get("brand", "").startswith("[融资]")]
    brand_industry = [r for r in results if not r.get("brand", "").startswith("[融资]")]
    return brand_industry, financing


# ── MiniMax API 调用 ────────────────────────────────────────────

def _call_minimax_raw(prompt: str, timeout: int = 120, max_tokens: int = 4000, retries: int = 3) -> str:
    """调用 MiniMax M2.7，返回原始文本内容"""
    return call_minimax(prompt, timeout=timeout, max_tokens=max_tokens, retries=retries)


# ── Prompt 构建与解析 ──────────────────────────────────────────

def _build_classify_prompt(items: list[dict], brand_configs: list[dict],
                            industry_configs: list[dict], recent_events: list[str]) -> str:
    """构建 Layer2 LLM 分类标引 prompt"""
    brand_lines = []
    for b in brand_configs:
        name = b.get("name", "")
        subs = ", ".join(b.get("sub_brands", [])) or "无"
        industry = b.get("industry", "")
        brand_lines.append(f"- {name}（子品牌: {subs}，行业: {industry}）")

    industry_lines = [f"- {ind.get('name', '')}" for ind in industry_configs]

    # 赛道关键词映射（帮助 LLM 准确归类）
    from scripts.quality_rules import _TRACK_BUSINESS_KEYWORDS
    track_hint_lines = []
    for track, keywords in _TRACK_BUSINESS_KEYWORDS.items():
        track_hint_lines.append(f"- {track}: {', '.join(keywords[:8])}")

    event_lines = recent_events if recent_events else ["今日首次运行，无历史记录"]

    news_lines = []
    for i, item in enumerate(items):
        brand = item.get("brand", "")
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")[:500]
        news_lines.append(
            f"[{i}] brand={brand}\n    title={title}\n    url={url}\n    content={content[:200]}..."
        )

    prompt = f"""对以下 {len(items)} 条新闻进行快速分类，返回 JSON 数组。
每条只需要返回：
- correct_brand：新闻实际属于哪个品牌（来自上方品牌列表，或"无"）
- correct_track：新闻属于哪个赛道（来自上方赛道列表，或"无"）
- is_followup：是否为近期已推事件的跟进报道（true/false）
- followup_note：如果 is_followup=true，用一句话说明是对哪个已知事件的跟进
- signal_type：事件类型，只能是 新品/融资/品牌升级/代言人/渠道扩张/合作/其他 之一
- opportunity_level：商机级别，只能是 high 或 normal

【品牌列表】
{chr(10).join(brand_lines) if brand_lines else "无"}

【行业列表】
{chr(10).join(industry_lines) if industry_lines else "无"}

【赛道关键词参考（判断 correct_track 时使用）】
{chr(10).join(track_hint_lines) if track_hint_lines else "无"}

【近期已推事件】
{chr(10).join(event_lines)}

【新闻列表】
{chr(10).join(news_lines)}

【关键判断规则】
判断 correct_brand 时，必须同时满足：
1. 品牌名必须在标题的前半部分出现（前30个字符内）
2. 品牌必须是新闻的主语（新闻是"关于"这个品牌的，而不是"提到"这个品牌的）
3. 如果标题中出现"联合X"、"与X合作"、"携手X"、"X联名"、"据X报道"，则X不是主语，correct_brand 应为"无"或实际主语品牌
4. 如果新闻内容主要是行业综述（"X品牌在YY领域的市场份额提升"），即使提到某品牌，该品牌也不是主语
5. 以下信号优先判为 high：新品发布/融资到账/品牌升级/代言人官宣/渠道扩张/进入新市场
6. 普通报道、弱相关合作、泛行业提及，判为 normal

直接返回 JSON 数组，不要前缀文字，数组长度必须恰好是 {len(items)}。

输出格式：
[
  {{"id": 0, "correct_brand": "vivo", "correct_track": "", "is_followup": false, "followup_note": "", "signal_type": "新品", "opportunity_level": "high"}},
  ...
]"""
    return prompt


def _parse_classify_response(text: str, items: list[dict]) -> list[dict]:
    """解析 Layer2 LLM 返回的 JSON 分类结果"""
    # 尝试提取 JSON 数组
    text = text.strip()
    # 去掉 markdown 代码块
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 去掉 MiniMax 思考块
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # 尝试找 JSON 数组
        match = re.search(r'\[\s*\{.*\}\s*\]', text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                print(f"  [Layer2 解析失败] LLM 返回非 JSON，fallback 到原始值")
                parsed = []
        else:
            print(f"  [Layer2 解析失败] 无法从响应中提取 JSON: {text[:200]}")
            parsed = []

    # 构建 id→结果映射
    result_map = {}
    for item in parsed:
        if isinstance(item, dict) and "id" in item:
            result_map[item["id"]] = item

    classified = []
    for i, item in enumerate(items):
        info = result_map.get(i, {})
        classified.append({
            "brand": info.get("correct_brand", item.get("brand", "")),
            "track_name": info.get("correct_track", ""),
            "is_followup": info.get("is_followup", False),
            "followup_note": info.get("followup_note", ""),
            "signal_type": info.get("signal_type", "其他") or "其他",
            "opportunity_level": info.get("opportunity_level", "normal") or "normal",
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        })
    return classified


# ── LLM 分类（内部） ────────────────────────────────────────────

def _call_classify_llm(items: list[dict], brand_configs: list[dict],
                       industry_configs: list[dict], recent_events: list[str]) -> list[dict]:
    """调用 Layer2 LLM 进行快速分类标引"""
    if not items:
        return []

    prompt = _build_classify_prompt(items, brand_configs, industry_configs, recent_events)
    raw = _call_minimax_raw(prompt, timeout=120, max_tokens=4000)
    if not raw:
        # LLM 调用失败时，返回原始数据（不做分类）
        return [{
            "brand": item.get("brand", ""),
            "track_name": "",
            "is_followup": False,
            "followup_note": "",
            "signal_type": "其他",
            "opportunity_level": "normal",
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "content": item.get("content", ""),
        } for item in items]

    return _parse_classify_response(raw, items)


# ── 公开接口 ──────────────────────────────────────────────────

def preprocess(
    raw_results: list[dict],
    brand_configs: list[dict],
    industry_configs: list[dict],
    profile_fundraising_tracks: list[dict],
    recent_events: list[str] = None,
    skip_dedup: bool = False,
) -> list[dict]:
    """
    Layer 2 预处理入口（deduplicate 并入内部，不对外暴露）。

    流程：
    1. 纯规则去重（URL/标题/事件 + 噪音过滤）
    2. 规则粗筛（品牌相关性，移自 main.py）
    3. 分离融资 vs 品牌/行业
    4. 品牌/行业 → LLM 分类标引（品牌归属纠正 + 赛道标注 + 跟进判断）
    5. 融资结果透传（track_name 已标注，不调用 LLM）

    Args:
        raw_results: 原始搜索结果列表
        brand_configs: 品牌配置列表
        industry_configs: 行业配置列表
        profile_fundraising_tracks: 融资赛道配置列表（当前未使用，保留参数位）
        recent_events: 近期已推送事件字符串列表（"brand - event_key"），None=自动收集

    Returns:
        list[dict]: 预处理后的条目，每条带 brand/track_name/is_followup/title/url/content
    """
    print(f"\n[Layer2 预处理] 输入 {len(raw_results)} 条")

    # 第1步：纯规则去重（cache 模式跳过，因为数据已被推送过）
    if skip_dedup:
        deduped = raw_results
        print(f"  [Layer2 跳过去重] cache 模式")
    else:
        deduped = _rule_dedup(raw_results)
        print(f"  [Layer2 规则去重] {len(raw_results)} → {len(deduped)} 条")

    # 第2步：规则粗筛（品牌相关性，移自 main.py）
    before_rule = len(deduped)
    filtered = [r for r in deduped if _rule_prefilter(r)]
    rule_removed = before_rule - len(filtered)
    if rule_removed > 0:
        print(f"  [Layer2 规则粗筛] 过滤 {rule_removed} 条")

    # 第3步：分离融资 vs 品牌/行业
    brand_industry, financing = _separate_by_type(filtered)
    print(f"  [Layer2 分离] 品牌/行业 {len(brand_industry)} 条，融资 {len(financing)} 条")

    # 第4步：品牌/行业 → LLM 分类标引（每批最多15条）
    # recent_events 由 main.py 收集后传入，避免重复查询
    if recent_events is None:
        from scripts.dedup import get_recent_events_for_brand
        recent_events = []
        for cfg in brand_configs:
            recent = get_recent_events_for_brand(cfg.get("name", ""))
            recent_events.extend([f"{e.get('brand', '')} - {e.get('event_key', '')}"
                                  for e in recent if e.get("event_key")])

    BATCH_SIZE = 15
    classified = []
    for i in range(0, len(brand_industry), BATCH_SIZE):
        batch = brand_industry[i:i + BATCH_SIZE]
        print(f"  [Layer2 LLM 分类] 批次 {i // BATCH_SIZE + 1}，处理 {len(batch)} 条...")
        batch_result = _call_classify_llm(batch, brand_configs, industry_configs, recent_events)
        classified.extend(batch_result)

    # 第5步：融资结果透传（track_name 在搜索时已标注，直接透传）
    # 融资不做 LLM 分类标引

    result = classified + financing
    print(f"  [Layer2 完成] 输出 {len(result)} 条预处理结果")
    return result
