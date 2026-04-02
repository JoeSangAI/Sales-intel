"""
Layer 3 - 大厨 LLM 模块
通盘看完所有原材料，直接生成完整 Markdown 报告
"""

import json
import os
import sys
import requests

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)


# ── MiniMax API 调用 ────────────────────────────────────────────

def _call_llm(prompt: str, timeout: int = 180, max_tokens: int = 8000) -> str:
    """调用 MiniMax M2.7，返回原始文本内容"""
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        print("  [Chef 警告] MINIMAX_API_KEY 未设置")
        return ""
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {minimax_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-M2.7",
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
            else:
                content = ""
            print(f"  [Chef MiniMax OK] 生成了 {len(content)} 字符", flush=True)
            # 去掉 MiniMax 思考块
            content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
            return content
        except Exception as e:
            print(f"  [Chef MiniMax 失败{' (重试)' if attempt < 2 else ''}] {e}", flush=True)
            if attempt < 2:
                import time; time.sleep(3)
    return ""


# ── Prompt 构建 ─────────────────────────────────────────────────

def _build_chef_prompt(items: list[dict], config: dict, schedule: dict,
                       recent_events: list[str], endorsement_items: list[dict]) -> str:
    """构建大厨 LLM 的 prompt"""

    # 品牌列表
    brand_configs = config.get("brands", [])
    brand_lines = []
    for b in brand_configs:
        name = b.get("name", "")
        industry = b.get("industry", "")
        brand_lines.append(f"- {name}（行业: {industry}）")

    # 行业列表
    industry_configs = config.get("industries", [])
    industry_lines = [f"- {ind.get('name', '')}" for ind in industry_configs]

    # 赛道列表
    fundraising = config.get("fundraising", {})
    track_configs = fundraising.get("tracks", [])
    track_lines = [f"- {t.get('name', '')}" for t in track_configs]

    # 今日模式判断
    today_mode = "客户新闻"
    if schedule.get("include_industry"):
        today_mode = "客户新闻+融资"
    if schedule.get("include_endorsement"):
        today_mode = "含代言人速报"

    # 近期事件
    if recent_events:
        event_str = chr(10).join(f"- {e}" for e in recent_events)
    else:
        event_str = "今日首次运行，无历史记录"

    # 新闻条目
    news_lines = []
    for i, item in enumerate(items):
        brand = item.get("brand", "")
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")[:800]
        track = item.get("track_name", "")
        is_followup = item.get("is_followup", False)
        followup_note = item.get("followup_note", "")
        followup_tag = f" [跟进报道: {followup_note}]" if is_followup and followup_note else ""
        track_tag = f" [赛道: {track}]" if track else ""
        news_lines.append(
            f"[{i}] brand={brand}{track_tag}{followup_tag}\n"
            f"    title={title}\n"
            f"    url={url}\n"
            f"    content={content}"
        )

    # 代言人条目
    end_lines = []
    for i, e in enumerate(endorsement_items or []):
        name = e.get("endorser_name", "")
        brand = e.get("brand", "")
        industry = e.get("industry", "")
        event = e.get("endorsement_event", "")
        end_lines.append(
            f"[{i}] 代言人={name} | 品牌={brand} | 行业={industry} | 事件={event}"
        )

    prompt = f"""你是分众传媒的销售情报编辑。

今天有一批经过预处理（去重+标引）的新闻，等待整合成日报。
你的任务是把它们整合成一份清晰、有价值的销售情报日报。

【分众能帮客户做什么】
- 帮助品牌在关键人群（白领/社区居民）的必经场景建立认知
- 在重大节点（发布会/融资/代言人）前后快速建立心智
- 通过创意形式（互动/场景文案/社交裂变）实现破圈
- 融资后的品牌建设是黄金窗口期

【判断什么是值得推的新闻】
- 这个品牌/事件是否暗示了广告/营销需求？
- 现在是不是接触该品牌市场部的黄金窗口？
- 分众有没有独特的切入点？（时间/地点/事件/跨界...）

不要把这些当作公式套用，而是作为思考背景，
让你的判断更贴合分众销售的实际需求。

【今日配置】
关注品牌：{chr(10).join(brand_lines) if brand_lines else "无"}
关注行业：{chr(10).join(industry_lines) if industry_lines else "无"}
融资赛道：{chr(10).join(track_lines) if track_lines else "无"}
今日模式：{today_mode}

【近期已推事件（用于判断跟进报道）】
{event_str}

【今日新闻条目】
{chr(10).join(news_lines) if news_lines else "今日无品牌新闻"}

{'【代言人速报】' if end_lines else ''}
{chr(10).join(end_lines) if end_lines else ''}

你的输出要求：
1. 客户新闻：按品牌分组，🔴标签留给最值得本周行动的事件
2. 每个品牌只呈现最重要的事件，不要罗列
3. 融资速报：按赛道分组，先汇总表再详情
4. 代言人人报（周三专属）：按行业分组
5. 关键引用必须标注来源 URL（幻觉是严重问题）
6. 如果某品牌今天无重要新闻，直接标注"暂无动态"
7. 创意切入建议：有真正好点子才写，不要为了有建议而写

🔴 紧迫度只保留这一档，判断标准：
- 发布会/活动在 2 周内
- 融资刚刚宣布（1 个月内）
- CMO/市场负责人刚换
- 重大政策节点（如补贴窗口期）

直接输出 Markdown 报告，不要有前缀文字。"""
    return prompt


# ── 公开接口 ──────────────────────────────────────────────────

def chef_report(items: list[dict], config: dict, schedule: dict,
                recent_events: list[str], endorsement_items: list[dict] = None) -> str:
    """
    Layer 3 大厨 LLM 入口。

    通盘看完所有原材料后，直接生成完整 Markdown 报告。

    Args:
        items: Layer 2 预处理后的条目列表
        config: 档案配置（brands/industries/fundraising）
        schedule: 今日调度标志（include_industry/include_endorsement）
        recent_events: 近期已推送事件列表（字符串列表）
        endorsement_items: 代言人速报条目列表

    Returns:
        str: Markdown 格式的销售情报日报
    """
    if not items:
        print("[Chef] 无预处理结果，跳过报告生成")
        return ""

    print(f"\n[Chef] 开始生成报告，输入 {len(items)} 条...")
    prompt = _build_chef_prompt(items, config, schedule, recent_events, endorsement_items or [])
    report = _call_llm(prompt)

    if not report:
        print("[Chef] 报告生成失败，返回空字符串")
        return ""

    print(f"  [Chef 完成] 报告 {len(report)} 字符")
    return report
