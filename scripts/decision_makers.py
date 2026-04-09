"""
高潜商机关键决策人补强。
只对 Layer2 标记为 high 的品牌做额外搜索与结构化抽取。
"""

import json
import os
import re
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

from scripts.minimax_client import call_minimax
from scripts.search import search_tavily, get_api_key


def _collect_high_priority_brands(items: list[dict]) -> dict[str, list[dict]]:
    grouped = defaultdict(list)
    for item in items:
        brand = item.get("brand", "")
        if not brand or brand.startswith("[融资]") or brand.startswith("[行业]"):
            continue
        if item.get("opportunity_level") != "high":
            continue
        grouped[brand].append(item)
    return dict(grouped)


def _build_queries(brand: str, item: dict) -> list[str]:
    """为同一品牌生成 2-3 条短查询，提高命中率"""
    queries = [
        f'"{brand}" 董事长 OR 创始人',
        f'"{brand}" CEO OR 总经理 OR 总裁',
    ]
    signal_type = item.get("signal_type", "其他")
    if signal_type in ("新品", "品牌升级", "代言人"):
        queries.append(f'"{brand}" CMO OR 市场总监 OR 品牌负责人')
    elif signal_type in ("渠道扩张",):
        queries.append(f'"{brand}" 销售总监 OR 渠道负责人')
    else:
        queries.append(f'"{brand}" CMO OR 市场负责人')
    return queries


def _build_extract_prompt(brand: str, items: list[dict], search_results: list[dict]) -> str:
    item_lines = []
    for item in items[:3]:
        item_lines.append(
            f"- 标题: {item.get('title', '')}\n  信号类型: {item.get('signal_type', '其他')}\n  内容: {item.get('content', '')[:300]}"
        )

    result_lines = []
    for idx, result in enumerate(search_results[:8]):
        result_lines.append(
            f"[{idx}] title={result.get('title', '')}\n    url={result.get('url', '')}\n    content={result.get('content', '')[:400]}"
        )

    return f"""你是销售情报研究员。请基于搜索结果，为品牌提取公开可识别的关键决策人。

品牌：{brand}
相关商机：
{chr(10).join(item_lines) if item_lines else '无'}

搜索结果：
{chr(10).join(result_lines) if result_lines else '无'}

请只提取有搜索结果支撑的关键人，优先以下角色：
1. 创始人/董事长
2. CEO/总经理/总裁
3. CMO/市场负责人/品牌负责人
4. 销售负责人/渠道负责人

关键规则：
- 提取的人必须是该品牌的直属高管，不能是其他公司的人
- 如果品牌属于集团（如千问属于阿里），关键人必须是该品牌或其母公司的高管，不能是无关公司的人
- 姓名和职务必须在搜索结果中有明确出处
- 没有把握就不要输出，宁少勿错

返回 JSON，不要前缀：
{{
  "decision_makers": [
    {{
      "name": "姓名",
      "title": "职务",
      "priority": "高/中/低",
      "focus": "该角色最关心什么",
      "reason": "为什么建议优先找他",
      "source_url": "必须来自搜索结果中的 url"
    }}
  ]
}}

规则：
- 不得编造姓名或职务
- 没有把握就不要输出
- 最多返回 4 人
- 如果搜索结果不足，返回空数组"""


def _parse_extract_response(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    makers = parsed.get("decision_makers", []) if isinstance(parsed, dict) else []
    cleaned = []
    for maker in makers:
        if not isinstance(maker, dict):
            continue
        if not maker.get("name") or not maker.get("source_url"):
            continue
        cleaned.append({
            "name": maker.get("name", "").strip(),
            "title": maker.get("title", "").strip(),
            "priority": maker.get("priority", "中").strip() or "中",
            "focus": maker.get("focus", "").strip(),
            "reason": maker.get("reason", "").strip(),
            "source_url": maker.get("source_url", "").strip(),
        })
    return cleaned[:4]


def enrich_decision_makers(items: list[dict], config: dict) -> dict[str, list[dict]]:
    api_key = get_api_key()
    if not api_key:
        print("  [关键人] API key 缺失，跳过关键人搜索")
        return {}

    brand_map = _collect_high_priority_brands(items)
    if not brand_map:
        return {}

    enriched = {}
    for brand, brand_items in brand_map.items():
        queries = _build_queries(brand, brand_items[0])
        print(f"  [关键人] 搜索 {brand}（{len(queries)} 条查询）...")
        all_results = []
        seen_urls = set()
        for q in queries:
            results = search_tavily(
                query=q,
                api_key=api_key,
                topic="general",
                time_range="month",
                max_results=5,
            )
            for r in results:
                url = r.get("url", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append(r)
        if not all_results:
            enriched[brand] = []
            continue

        prompt = _build_extract_prompt(brand, brand_items, all_results)
        raw = call_minimax(prompt, timeout=120, max_tokens=2000, retries=3)
        enriched[brand] = _parse_extract_response(raw) if raw else []

    return enriched
