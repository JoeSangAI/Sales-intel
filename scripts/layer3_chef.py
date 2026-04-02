"""
Layer 3 - 大厨 LLM 模块
通盘看完所有原材料，直接生成完整 Markdown 报告
"""

import os
import re
import sys

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

from scripts.minimax_client import call_minimax


# ── MiniMax API 调用 ────────────────────────────────────────────

def _call_llm(prompt: str, timeout: int = 180, max_tokens: int = 8000) -> str:
    """调用 MiniMax M2.7，返回原始文本内容"""
    print(f"  [Chef] 调用 MiniMax...", flush=True)
    content = call_minimax(prompt, timeout=timeout, max_tokens=max_tokens, retries=3)
    if content:
        print(f"  [Chef MiniMax OK] 生成了 {len(content)} 字符", flush=True)
    return content


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

【格式强制要求】
报告必须严格遵循以下格式，不要做任何改动：

# 销售情报日报

## 📋 客户新闻

### 【品牌名】

#### 🔴 本周跟进
- **{{事件标题}}**（来源 · 日期）
  一句话描述
  来源：[媒体名](链接)
  💡 **为什么现在跟**: 理由
  🎨 **分众机会**: 切入点

（若无本周跟进事件，写"暂无动态"）

### 【品牌名】
...

## 💰 融资速报（如有）

| 赛道 | 品牌 | 融资概况 | 紧迫度 |
|------|------|----------|--------|
| AI大模型 | 深度求索 | B轮 · 10亿 | 🔴 |

### 【赛道名】

#### 🔴 本周跟进
- **{{品牌}}完成{{轮次}}融资**（日期）
  一句话描述
  💡 **为什么现在跟**: 理由
  🎨 **分众机会**: 切入点
  来源：[链接](链接)

---
*由销售情报助手生成*

【格式规则】
1. 报告必须以 `# 销售情报日报` 开头
2. 必须包含 `## 📋 客户新闻` 一级标题
3. 客户新闻下按品牌分，每个品牌用 `### 品牌名`
4. 每个品牌下必须有 `#### 🔴 本周跟进` 或标注"暂无动态"
5. 必须有 `---` 分隔符和 `*由销售情报助手生成*` 页脚
6. 直接输出 Markdown 报告，不要有前缀文字。
7. **来源链接必须在事件一句话描述后立即给出**，格式为 `来源：[媒体名](链接)`，不能放在分析段落之后。
8. **每条事件必须有来源链接**，不允许有无链接的事件描述。
9. **没有来源链接的内容不得出现在报告中**。
10. **客户新闻 section 只能出现【今日配置】中注册的品牌**，禁止将其他品牌（如行业文章中提及的非注册品牌）写入客户新闻。
11. **融资速报 section 只能出现【今日配置】中注册的赛道**，禁止将其他赛道的融资条目写入报告。
12. **行业/赛道融资条目不得混入客户新闻 section**。"""
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
