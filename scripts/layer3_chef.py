"""
Layer 3 - 大厨 LLM 模块
通盘看完所有原材料，直接生成完整 Markdown 报告
"""

import os
import sys

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

from scripts.minimax_client import call_minimax


def _call_llm(prompt: str, timeout: int = 180, max_tokens: int = 8000) -> str:
    """调用 MiniMax M2.7，返回原始文本内容"""
    print("  [Chef] 调用 MiniMax...", flush=True)
    content = call_minimax(prompt, timeout=timeout, max_tokens=max_tokens, retries=3)
    if content:
        print(f"  [Chef MiniMax OK] 生成了 {len(content)} 字符", flush=True)
    return content


def _build_chef_prompt(
    items: list[dict],
    config: dict,
    schedule: dict,
    recent_events: list[str],
    endorsement_items: list[dict],
    decision_makers_map: dict[str, list[dict]],
) -> str:
    brand_lines = []
    for brand in config.get("brands", []):
        brand_lines.append(f"- {brand.get('name', '')}（行业: {brand.get('industry', '')}）")

    industry_lines = [f"- {ind.get('name', '')}" for ind in config.get("industries", [])]
    track_lines = [f"- {t.get('name', '')}" for t in config.get("fundraising", {}).get("tracks", [])]

    today_mode = "客户新闻"
    if schedule.get("include_industry"):
        today_mode = "客户新闻+融资"
    if schedule.get("include_endorsement"):
        today_mode = "含代言人速报"

    event_str = chr(10).join(f"- {e}" for e in recent_events) if recent_events else "今日首次运行，无历史记录"

    news_lines = []
    for i, item in enumerate(items):
        news_lines.append(
            f"[{i}] brand={item.get('brand', '')} [signal={item.get('signal_type', '其他')}] [level={item.get('opportunity_level', 'normal')}]"
            f" [赛道: {item.get('track_name', '')}] [跟进: {item.get('followup_note', '')}]\n"
            f"    title={item.get('title', '')}\n"
            f"    url={item.get('url', '')}\n"
            f"    content={item.get('content', '')[:800]}"
        )

    maker_lines = []
    for brand, makers in (decision_makers_map or {}).items():
        if not makers:
            maker_lines.append(f"- {brand}: 未检索到公开实名")
            continue
        maker_lines.append(f"- {brand}:")
        for maker in makers:
            maker_lines.append(
                f"  - {maker.get('name', '')} | {maker.get('title', '')} | 优先级:{maker.get('priority', '')} | "
                f"关注:{maker.get('focus', '')} | 原因:{maker.get('reason', '')} | 来源:{maker.get('source_url', '')}"
            )

    end_lines = []
    for i, item in enumerate(endorsement_items or []):
        end_lines.append(
            f"[{i}] 代言人={item.get('endorser_name', '')} | 品牌={item.get('brand', '')} | 行业={item.get('industry', '')} | 事件={item.get('endorsement_event', '')}"
        )

    return f"""你不是新闻摘要助手，而是分众传媒销售团队的销售实战参谋。

你的目标不是复述新闻，而是输出销售可以直接拿去打客户的日报。

【分众判断原则】
- 分众卖的不是普通曝光，而是主流人群、必经场景、高频重复、集中压强
- 新闻只是触发器，真正要判断的是品牌当前最值得切入的增长问题
- 每条重点商机只能抓一个主攻点，不要平均用力
- 高潜商机要输出销售打法；普通新闻保持简洁，不要写成研究报告

【今日配置】
关注品牌：{chr(10).join(brand_lines) if brand_lines else '无'}
关注行业：{chr(10).join(industry_lines) if industry_lines else '无'}
融资赛道：{chr(10).join(track_lines) if track_lines else '无'}
今日模式：{today_mode}

【近期已推事件】
{event_str}

【今日新闻条目】
{chr(10).join(news_lines) if news_lines else '今日无品牌新闻'}

【关键决策人实名结果】
{chr(10).join(maker_lines) if maker_lines else '无'}

{'【代言人速报】' if end_lines else ''}
{chr(10).join(end_lines) if end_lines else ''}

【输出要求】
必须输出 Markdown，并严格用下面结构：

# 销售情报日报

## 📋 客户新闻

### 【品牌名】

#### 🔴 重点商机
仅当该品牌存在 level=high 的条目时输出。每个重点商机按下面格式：
- **事件标题**
  一句话判断：不是复述新闻，而是指出这件事为什么值得销售现在介入。
  来源：[媒体名](链接)
  品牌当前主线：只写一个最值得打的增长主线。
  为什么现在跟：解释当前窗口期。
  消费者/市场洞察：简要说明消费者矛盾、竞争态势、市场背景。
  分众主攻切口：只写一个首推切口，不能泛泛写提升曝光。
  关键决策人：
    - 姓名｜职务｜优先级｜最关心什么｜为什么先找他
    - 若未检索到公开实名，明确写“未检索到公开实名，建议先从创始人/CEO/集团市场线切入”
  销售开场话术：给一句可以直接联系客户的话。
  心智表达方向：给一句适合分众场景的表达方向。
  反对意见预判：客户最可能的一句反对话 + 简短回击逻辑。
  风险提醒：指出商机可能落空的原因。

#### 🟡 普通关注
普通新闻放这里，简版输出：
- **事件标题**
  一句话判断
  来源：[媒体名](链接)
  当前建议：一句简短建议，避免空话。

若某品牌无任何条目，写“暂无动态”。

## 💰 融资速报
如无融资条目可省略整个板块。
同一家公司的多条融资新闻必须合并为一条，不能重复出现。
融资条目仍保持简洁：
- **品牌/事件标题**
  一句话判断
  来源：[媒体名](链接)
  为什么现在跟：一句话
  分众机会：一句话

---
*由销售情报助手生成*

【硬规则】
1. 报告必须以 `# 销售情报日报` 开头。
2. 必须包含 `## 📋 客户新闻`。
3. 客户新闻 section 只能出现注册品牌。
4. 融资 section 只能使用原始融资条目。
5. 每条事件必须有来源链接，且链接 url 必须来自原始条目或关键决策人实名结果中的 source_url。
6. 不得编造数字、日期、产品名、关键人姓名、关键人职务。
7. 没有原始条目支撑时只能写“暂无动态”。
8. 只有 level=high 的品牌新闻才能输出“重点商机”；普通条目必须进入“普通关注”。
9. 如果关键决策人实名结果为空，不能编造，只能写“未检索到公开实名”。
10. 直接输出 Markdown，不要前缀文字。"""


def chef_report(
    items: list[dict],
    config: dict,
    schedule: dict,
    recent_events: list[str],
    endorsement_items: list[dict] = None,
    decision_makers_map: dict[str, list[dict]] = None,
) -> str:
    if not items:
        print("[Chef] 无预处理结果，跳过报告生成")
        return ""

    print(f"\n[Chef] 开始生成报告，输入 {len(items)} 条...")
    prompt = _build_chef_prompt(
        items,
        config,
        schedule,
        recent_events,
        endorsement_items or [],
        decision_makers_map or {},
    )
    report = _call_llm(prompt)

    if not report:
        print("[Chef] 报告生成失败，返回空字符串")
        return ""

    print(f"  [Chef 完成] 报告 {len(report)} 字符")
    return report
