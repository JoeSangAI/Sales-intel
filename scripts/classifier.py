"""
LLM 分类 Agent — 搜索结果的智能预处理

在搜索结果进入 AI 分析之前，用 LLM 做一次批量分类：
1. 判断每条新闻是否与 profile 相关
2. 判断品牌归属（解决品牌别名问题）
3. 判断行业分类（解决宇树出现在错误行业的问题）
4. 识别同一事件的多条报道（信息归拢）

设计原则：批量处理，一次 LLM 调用处理多条结果，降低成本。
"""

import os
import json
import requests
from typing import Optional


PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")

CLASSIFY_PROMPT = """你是一个新闻分类器，负责对搜索结果进行预处理。

## 任务

对以下 {count} 条新闻进行分类，返回 JSON 数组。

## 该销售关注的品牌
{brands}

## 该销售关注的行业
{industries}

## 搜索结果

{results_text}

## 输出要求

返回一个 JSON 数组，每个元素对应一条新闻：
```json
[
  {{
    "id": 0,
    "relevant": true,
    "reason": "与 profile 注册品牌 PMPM 直接相关",
    "correct_brand": "PMPM",
    "correct_industry": "护肤美妆",
    "group_id": 1
  }}
]
```

字段说明：
- id: 新闻序号（从0开始）
- relevant: 是否与该销售的关注范围相关（品牌或行业匹配）
- reason: 一句话说明为什么相关/不相关
- correct_brand: 该新闻实际属于哪个品牌（从上方品牌列表选，或写"无"）
- correct_industry: 该新闻实际属于哪个行业
- group_id: 同一事件的多条报道用相同的 group_id（从1开始编号）

## 关键规则
- 只有 profile 注册的品牌和行业才算"相关"
- 品牌别名要识别（如 iQOO = vivo 子品牌，金龙鱼 = 益海嘉里）
- 同一事件的不同媒体报道，group_id 相同
- 严格输出 JSON，不要多余文字
"""


def _call_minimax_classify(prompt: str) -> str:
    """调用 MiniMax 进行分类，返回原始文本"""
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        return ""
    try:
        resp = requests.post(
            "https://api.minimax.chat/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {minimax_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "MiniMax-M2.7",
                "max_tokens": 2000,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        resp.raise_for_status()
        choices = resp.json().get("choices", [])
        if choices and isinstance(choices[0], dict):
            return choices[0].get("message", {}).get("content", "")
        return ""
    except Exception as e:
        print(f"  [分类 Agent 异常] {e}")
        return ""


def _parse_classify_response(text: str) -> list[dict]:
    """解析分类结果 JSON"""
    if not text:
        return []
    # 尝试提取 JSON 数组
    text = text.strip()
    # 去掉 markdown 代码块
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        # 尝试找到 JSON 数组
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return []


def classify_results(
    results: list[dict],
    profile_brands: list[dict],
    profile_industries: list[dict],
    batch_size: int = 15,
) -> list[dict]:
    """
    对搜索结果进行 LLM 分类。

    返回过滤后的结果列表（只保留相关的），并附加分类信息。
    同一事件的多条报道会被标记相同的 _group_id。

    batch_size: 每批处理的结果数（控制 prompt 长度）
    """
    if not results:
        return []

    # 构建品牌和行业描述
    brands_text = "\n".join(
        f"- {b.get('name', '')}（子品牌: {', '.join(b.get('sub_brands', []))}，行业: {b.get('industry', '')}）"
        for b in profile_brands
    )
    industries_text = "\n".join(
        f"- {i.get('name', '')}" for i in profile_industries
    )

    classified = []
    # 分批处理
    for batch_start in range(0, len(results), batch_size):
        batch = results[batch_start:batch_start + batch_size]

        # 构建结果文本
        results_lines = []
        for i, r in enumerate(batch):
            results_lines.append(
                f"[{i}] 品牌: {r.get('brand', '')} | 标题: {r.get('title', '')} | "
                f"内容: {r.get('content', '')[:150]}"
            )
        results_text = "\n".join(results_lines)

        prompt = CLASSIFY_PROMPT.format(
            count=len(batch),
            brands=brands_text or "（无注册品牌）",
            industries=industries_text or "（无注册行业）",
            results_text=results_text,
        )

        print(f"  [分类 Agent] 处理第 {batch_start+1}-{batch_start+len(batch)} 条...")
        raw = _call_minimax_classify(prompt)
        classifications = _parse_classify_response(raw)

        if not classifications:
            # LLM 调用失败，保留所有结果（降级为不过滤）
            print(f"  [分类 Agent] 解析失败，保留全部 {len(batch)} 条")
            for r in batch:
                r["_classified"] = False
                classified.append(r)
            continue

        # 应用分类结果
        relevant_count = 0
        filtered_count = 0
        for cls in classifications:
            idx = cls.get("id", -1)
            if 0 <= idx < len(batch):
                r = batch[idx]
                if cls.get("relevant", True):
                    r["_group_id"] = cls.get("group_id", 0)
                    r["_correct_brand"] = cls.get("correct_brand", "")
                    r["_correct_industry"] = cls.get("correct_industry", "")
                    r["_classified"] = True
                    classified.append(r)
                    relevant_count += 1
                else:
                    filtered_count += 1

        # 处理未被分类覆盖的结果（保留）
        classified_ids = {cls.get("id", -1) for cls in classifications}
        for i, r in enumerate(batch):
            if i not in classified_ids:
                r["_classified"] = False
                classified.append(r)

        print(f"  [分类 Agent] 相关: {relevant_count}, 过滤: {filtered_count}")

    return classified


def deduplicate_by_group(results: list[dict]) -> list[dict]:
    """
    根据 _group_id 对同一事件的多条报道进行归拢。
    每组只保留内容最丰富的一条，其余的 URL 作为"更多来源"附加。
    """
    if not results:
        return []

    # 分组
    groups = {}
    ungrouped = []
    for r in results:
        gid = r.get("_group_id", 0)
        if gid and gid > 0:
            groups.setdefault(gid, []).append(r)
        else:
            ungrouped.append(r)

    # 每组选最佳
    deduped = []
    for gid, group in groups.items():
        if len(group) == 1:
            deduped.append(group[0])
            continue

        # 选内容最长的作为主条目
        group.sort(key=lambda r: len(r.get("content", "")), reverse=True)
        best = group[0]
        # 附加其他来源
        extra_sources = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in group[1:]
            if r.get("url")
        ]
        if extra_sources:
            best["_extra_sources"] = extra_sources
        deduped.append(best)
        print(f"  [归拢] 组{gid}: {len(group)} 条 → 1 条（+{len(extra_sources)} 个来源）")

    return deduped + ungrouped
