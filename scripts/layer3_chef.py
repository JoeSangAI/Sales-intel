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
    content = call_minimax(prompt, timeout=timeout, max_tokens=max_tokens, retries=5)
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
- 分众卖的不是曝光量，是品牌在主流人群必经场景里把某种认知钉死
- 新闻只是素材，真正要判断的是：这件事释放了什么信号，品牌现在最需要什么
- 创意要有画面感，参考"怕上火喝王老吉"、"送长辈某某酒"这种一句话说清楚定位的路子
- 普通扫描每条结尾必须给具体行动，禁止"跟踪一下"、"持续关注"
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
必须输出 Markdown，严格按以下结构。日报是 sales brief，不是新闻摘要。

# 销售情报日报 · [日期]

## 🔥 重点出击

### 【品牌名】

**新闻摘要**：
这条新闻讲了什么（10-20字，保留关键事实）｜发布时间｜[阅读原文](链接)

**判断**：
一句话说明今天打什么 + 为什么是现在（触发时机）

**切入创意**：

方向1：[大胆的广告片概念 / 整合营销思路 / 新定位切入角度]

方向2：[不超过两个]

**决策人**：
实名 ｜ 职务 ｜ 最关心什么

**一话开场**：
拿起电话第一句（约20字，像人话）

### 【品牌名】
...（每个重点出击的品牌格式同上）

---

## 📡 普通扫描

### 【品牌名】：一句话判断｜今天能做什么｜[阅读原文](链接)

（其余普通品牌格式同上）

---

## 💰 融资（周一/三才出现；其余日期省略此板块）

- **品牌**：一句话判断｜分众机会：[一句话]｜[阅读原文](链接)

【硬规则】
1. 报告必须以 `# 销售情报日报` 开头。
2. 必须包含 `## 🔥 重点出击` 和 `## 📡 普通扫描` 两个板块。
3. 重点出击只放 level=high 的条目，每个品牌选1个最值得打的点。
4. 重点出击每个档案最多选5个品牌，其余全部进普通扫描。
5. 普通扫描每条不超过3行，结尾必须有具体行动，禁止”跟踪一下”、”持续关注”。
6. 切入创意要有画面感，禁止空洞废话，基于真实新闻素材发挥。
7. 客户新闻 section 只能出现注册品牌；融资 section 只能使用原始融资条目。
8. 每条事件必须有来源链接，链接必须来自原始条目。
9. 不得编造数字、日期、产品名、关键人姓名、关键人职务。
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
