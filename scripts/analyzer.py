"""
AI 分析层
对搜索结果进行相关性判断、紧迫度分级、创意传播切入分析
使用 MiniMax-M1 进行分析

新增模块：
- 融资专项分析：从融资事件中识别分众机会
- 行业洞察生成：对高价值赛道进行深度分析（可调用搜索获取最新案例）
"""


ANALYSIS_PROMPT = """你是分众传媒的情报官，专门为销售团队提炼有价值的商机信息。

你的任务：读懂新闻，用销售的眼光提炼情报。不是复述原文，而是说清楚这件事**对分众销售意味着什么**。

# 分众的创意玩法（了解这些，才能看出机会）

## 互动玩法
- **碰一下(NFC)**：用户手机碰屏幕即可领券/互动。三得利用这个做"碰一下抽盲盒"，活跃度提升65%
- **场景文案**：电梯是封闭等待空间，"等电梯的30秒"本身就是创意载体。飘柔做过"上班是女神，下班是路人"精准戳白领痛点
- **社交裂变**：烤匠"#坐电梯被李贝贝馋哭#"话题覆盖1500万人，微信指数飙升650%

## 场景营销
- **时间触发**：节日/时段精准匹配。花西子在情人节、520、七夕抢占"浪漫礼物"心智
- **地点触发**：写字楼vs社区可做完全不同的内容
- **事件借势**：重大赛事/热点事件+电梯媒体=线下版热搜

## 跨界玩法
- **影院+电梯联动**：影院银幕做沉浸式品牌故事，电梯做高频提醒
- **线上线下闭环**：小红书/抖音种草→电梯引爆破圈→碰一下转化
- **终端置换**：分众广告作为空中火力，帮品牌跟渠道谈判换更好的陈列位

# 你的判断标准

1. **商机相关性**（1-10分）：这个新闻是否暗示品牌有广告/营销需求？
   - 9-10：发布会、新品、代言人、品牌升级
   - 7-8：融资、战略合作、进入新市场
   - 5-6：行业动态、竞品变化、财务数据
   - 1-4：弱相关

2. **紧迫度**：🔴本周跟进 / 🟡本月关注 / ⚪留意

3. **是否跟进报道**（is_followup）：
   - 对照下方「近期已推事件」列表，判断这条新闻是否只是对已推过事件的重复跟进
   - 如果是同一事件的后续报道（没有新的实质进展），标记 is_followup: true
   - 如果是全新事件，或有重要新进展（价格公布、新功能确认、代言人官宣等），标记 is_followup: false

4. **情报摘要**（intel_summary）：
   - **控制在1-2句话**，说清楚：发生了什么 + 对销售的意义
   - 不是复述原文，而是情报提炼
   - 举例（好）："vivo X系列发布会定档3月30日，距今11天，现在是接触市场部的黄金期。"
   - 举例（坏）："vivo近期有发布会动态，建议关注。"

5. **创意切入**（focus_media_angle）：
   - **只在你真正有好点子时才写**，没灵感就留空
   - 好的举例："比亚迪4月智驾发布会，电梯里做'你的车比你先到公司'悬念海报，配合碰一下预约试驾"
   - 坏的举例："建议LCD+智能屏饱和攻击"（废话，删掉）

6. **过滤**：纯技术/SEO垃圾/与营销无关的标记过滤

# 近期已推事件（用于判断 is_followup）

{recent_events}

# 输入信息

品牌：{brand}
标题：{title}
内容摘要：{content}
来源：{url}

# 输出格式（严格JSON）

{{
  "relevance_score": 7,
  "urgency": "🟡",
  "is_followup": false,
  "followup_note": "如果 is_followup=true，用一句话说明这是对哪个已知事件的跟进（留空则不显示）",
  "event_type": "从以下选一个最匹配的：新品发布|发布会|代言人|品牌升级|融资|战略合作|营销活动|人事变动|竞品动态|行业趋势",
  "event_key": "用5-10个字概括这个事件，用于后续跟进识别，如'vivo X300发布会'",
  "intel_summary": "1-2句情报提炼，说清楚发生了什么+对销售的意义",
  "focus_media_angle": "创意切入建议（没有好点子就留空字符串）",
  "recommendation_reason": "一句话：为什么现在要跟进这个客户",
  "filter": false,
  "filter_reason": ""
}}
"""


INDUSTRY_ANALYSIS_PROMPT = """你是分众传媒的行业情报官，专门从行业新闻中挖掘销售线索。

你的任务：从这条行业新闻中，判断对分众销售的价值，并直接点出**值得拜访的具体客户**。

# 分众能帮行业客户做什么

- **新品牌/新品类破圈**：刚融资或刚发布的品牌，最需要快速建立大众认知，电梯媒体是最快触达白领决策人群的渠道
- **发布会后扩散**：发布会制造了话题，但只有核心圈层知道，电梯媒体把声量从垂类扩散到大众
- **竞争防御**：行业格局生变时，领先品牌需要加大心智投入防止被追赶
- **节点营销**：行业政策利好（补贴、国补）带来消费窗口，品牌需要抢占心智

# 你的判断标准

1. **行业商机价值**（1-10分）：
   - 9-10：有具体品牌融资/发布会/上市，可直接转化为销售线索
   - 7-8：行业大事件，多个品牌受益，有批量拜访价值
   - 5-6：行业趋势，中长期机会
   - 1-4：纯政策/技术信息，与广告投放无关

2. **紧迫度**：🔴本周跟进 / 🟡本月关注

3. **情报摘要**（intel_summary）：
   - 1-2句话说清楚：发生了什么 + 这个行业为什么现在需要广告投放

4. **潜在客户线索**（prospect_leads）：
   - 从新闻中提取**具体的品牌/公司名称**，说明为什么现在值得拜访
   - 格式：[{{"name": "品牌名", "reason": "一句话说明拜访理由"}}]
   - 如果新闻没有提到具体品牌，可以基于行业逻辑推断（如"该赛道头部玩家"）
   - 最多列 3 个，宁缺毋滥

5. **过滤**：纯政策解读/学术研究/与广告无关的内容标记过滤

# 输入信息

行业：{brand}
标题：{title}
内容摘要：{content}
来源：{url}

# 输出格式（严格JSON）

{{
  "relevance_score": 7,
  "urgency": "🟡",
  "event_key": "5-10字概括事件",
  "intel_summary": "1-2句：发生了什么+行业广告投放机会",
  "prospect_leads": [
    {{"name": "品牌A", "reason": "刚完成B轮融资，正在全国扩张，需要品牌破圈"}},
    {{"name": "品牌B", "reason": "新品发布在即，市场部预算窗口期"}}
  ],
  "filter": false,
  "filter_reason": ""
}}
"""


def build_analysis_prompt(result: dict, recent_events: list[str] = None) -> str:
    """构建客户分析 prompt"""
    if recent_events:
        recent_str = "\n".join(f"- {e}" for e in recent_events)
    else:
        recent_str = "（今日首次运行，无历史记录）"
    return ANALYSIS_PROMPT.format(
        brand=result.get("brand", ""),
        title=result.get("title", ""),
        content=result.get("content", "")[:800],
        url=result.get("url", ""),
        recent_events=recent_str,
    )


def build_industry_prompt(result: dict) -> str:
    """构建行业分析 prompt"""
    return INDUSTRY_ANALYSIS_PROMPT.format(
        brand=result.get("brand", "").replace("[行业]", ""),
        title=result.get("title", ""),
        content=result.get("content", "")[:800],
        url=result.get("url", ""),
    )


# ── 通用 Prompt 构建（减少重复）──────────────────────────────

def _build_prompt(template: str, result: dict, **fmt_kwargs) -> str:
    """通用 prompt 构建，减少模板代码"""
    base = {
        "brand": result.get("brand", ""),
        "title": result.get("title", ""),
        "content": result.get("content", "")[:800],
        "url": result.get("url", ""),
    }
    base.update(fmt_kwargs)
    return template.format(**base)


def _parse_response(response_text: str, defaults: dict) -> dict:
    """通用 JSON 解析，支持嵌套 JSON"""
    import json
    start = response_text.find('{')
    if start != -1:
        depth = 0
        for i, ch in enumerate(response_text[start:], start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(response_text[start:i+1])
                    except json.JSONDecodeError:
                        break
    return defaults


def parse_analysis_response(response_text: str) -> dict:
    """解析模型返回的 JSON 分析结果"""
    return _parse_response(response_text, {
        "relevance_score": 5,
        "urgency": "⚪",
        "is_followup": False,
        "followup_note": "",
        "event_key": "",
        "intel_summary": "",
        "focus_media_angle": "",
        "recommendation_reason": "",
        "filter": False,
        "filter_reason": "分析解析失败",
    })


def filter_by_score(analyzed_results: list[dict], min_score: int = 6) -> list[dict]:
    """过滤低分和标记过滤的结果"""
    return [
        r for r in analyzed_results
        if not r.get("analysis", {}).get("filter", False)
        and r.get("analysis", {}).get("relevance_score", 0) >= min_score
    ]


# ── 融资专项分析 ──────────────────────────────────────────

FUNDRAISING_ANALYSIS_PROMPT = """你是分众传媒的销售情报专家，专门从融资事件中识别分众广告投放机会。

你的核心判断逻辑：
融资 → 有钱 → 会花钱 → 什么时候花、花在哪里？

# 融资后品牌营销的一般规律

- **A轮**：产品刚跑通，营销预算开始有，但以验证渠道为主，分众介入需快速建立信任
- **B轮**：规模化扩张，市场预算大幅增加，品牌开始有破圈需求，分众介入黄金期
- **C轮及以上**：弹药充足，广告凶猛，直接抢大单
- **战略融资（腾讯/阿里/字节战投）**：不仅有钱，还有生态资源，品牌推广意愿强

# 判断分众机会的三个维度

1. **赛道匹配度**：这个行业的品牌是否需要打C端心智？
   - 强匹配：AI大模型、机器人、智能硬件、新消费
   - 中匹配：企业服务SaaS（有B+C混合需求时）
   - 弱匹配：纯B端技术公司、医疗设备

2. **融资规模**：金额越大，营销预算越充裕
   - 亿元以下：观察为主
   - 亿元以上：重点跟进
   - 十亿元以上：立即锁定

3. **介入时机**：融资后1-3个月是黄金窗口
   - 刚宣布融资：市场部正在规划年度预算，分众有机会进入媒介组合
   - 融资后1个月：品牌声量战开始，分众是高频触达利器
   - 融资后3个月+：预算已分配，错过需等下次产品发布

# 你的输出要求

不是描述融资事件本身，而是回答：**这个融资事件对分众销售意味着什么？**

# 输入信息

赛道：{track_name}
品牌（如有具体公司名）：{brand}（注：如为公司名请直接使用，如为赛道分类名则从内容中提取真实公司名）
融资事件：{title}
内容摘要：{content}
来源：{url}

**重要**：如果标题是英文，请先用中文概括事件，再填入 event_key。

# 输出格式（严格JSON）

{{
  "relevance_score": 7,
  "urgency": "🔴",
  "event_key": "5-10字概括融资事件",
  "intel_summary": "1-2句：融资背景+分众机会点",
  "focus_media_angle": "如果有好切入角度则写（没有则空字符串）",
  "recommendation_reason": "一句话：为什么现在要跟进这个客户",
  "prospect_leads": [
    {{"name": "实际公司名（从内容中提取，不要用赛道分类名）", "reason": "一句话拜访理由"}}
  ],
  "filter": false,
  "filter_reason": ""
}}
"""


INDUSTRY_INSIGHT_PROMPT = """你是分众传媒的首席战略顾问，对中国媒体市场和品牌营销有深刻洞察。

你的任务：当某行业在一周内出现多条高相关度信号时，生成一份深度行业洞察报告，
帮助销售团队理解为什么这个行业值得重点跟进，以及如何跟进。

# 分众的核心价值主张（你在推理时应该用到）

1. **封闭空间高触达**：电梯是城市人群每天必经的封闭空间，周均4-7次强制触达
2. **白领决策链**：写字楼是品牌最想影响的决策人群聚集地
3. **从垂类到大众的破圈**：新品类/新品牌需要从极客圈扩散到大众，分众是最低成本的破圈路径
4. **心智占领**：在消费者做决策之前就植入品牌认知，比信息流更聚焦、比央视更精准

# 你的分析框架（每份报告必须包含）

1. **市场格局**：这个行业的竞争格局是怎样的？头部、腰部玩家分别是谁？
2. **消费趋势**：这个行业的目标消费者是谁？他们的媒体消费习惯是什么？
3. **营销诉求**：这个行业品牌当前最核心的营销挑战是什么？
4. **分众机会**：分众在这个行业中为什么不可或缺？什么场景下品牌一定会选分众？
5. **介入时机**：什么节点是最佳介入时机？
6. **案例参照**：尽可能搜索并给出分众在这个行业或相关行业的最新案例（如果没有则基于逻辑推理）

# 已知信息（供你推理）

行业：{industry_name}
近期信号：{recent_signals}
分析角度要求：{analysis_perspective}

# 输出格式

## {industry_name} 行业洞察报告

### 📊 市场格局
（200字以内，精准描述竞争态势）

### 🎯 消费趋势
（150字以内，说清楚目标人群和媒体习惯）

### 💡 营销诉求
（150字以内，行业当前最核心的营销挑战）

### 🚀 分众机会
（200字以内，为什么分众不可或缺，包含具体的场景描述）

### ⏰ 介入时机
（100字以内，什么节点是最佳时机）

### 📋 案例参照
（尽量给出分众在该行业或相关行业的案例，包含品牌名+场景描述+效果数据（如果有））
（如果没有真实案例，给出基于逻辑的参照）

# 注意事项

- 案例部分请根据{industry_name}自行判断是否需要调用搜索获取最新信息
- 如果需要搜索，请使用以下格式声明：
  [需要搜索：关键词1 | 关键词2]
- 报告语言要接地气，像销售在微信里给老板汇报的口吻，不要学术腔
"""


def build_fundraising_prompt(result: dict, track_name: str) -> str:
    """构建融资分析 prompt"""
    return FUNDRAISING_ANALYSIS_PROMPT.format(
        track_name=track_name,
        brand=result.get("brand", ""),
        title=result.get("title", ""),
        content=result.get("content", "")[:800],
        url=result.get("url", ""),
    )


def parse_fundraising_response(response_text: str) -> dict:
    """解析融资分析 JSON 结果"""
    return _parse_response(response_text, {
        "relevance_score": 5,
        "urgency": "⚪",
        "event_key": "",
        "intel_summary": "",
        "focus_media_angle": "",
        "recommendation_reason": "",
        "prospect_leads": [],
        "filter": False,
        "filter_reason": "分析解析失败",
    })


def group_results_by_track(results: list[dict]) -> dict:
    """将融资结果按赛道分组"""
    tracks = {}
    for r in results:
        track = r.get("track_name", "其他")
        if track not in tracks:
            tracks[track] = []
        tracks[track].append(r)
    return tracks


def identify_high_value_tracks(fundraising_results: list[dict], min_score: int = 7) -> list[str]:
    """识别高价值赛道（该赛道有 >= 1 条高相关度融资事件）"""
    score_by_track = {}
    for r in fundraising_results:
        analysis = r.get("analysis", {})
        score = analysis.get("relevance_score", 0)
        track = r.get("track_name", "其他")
        if score >= min_score:
            if track not in score_by_track or score > score_by_track[track]:
                score_by_track[track] = score

    return list(score_by_track.keys())


def build_industry_insight_prompt(
    industry_name: str,
    recent_signals: list[dict],
    analysis_perspective: str,
) -> str:
    """构建行业洞察报告 prompt"""
    # 格式化近期信号
    signals_str = "\n".join([
        f"- {s.get('title', '')}（相关度 {s.get('analysis', {}).get('relevance_score', 0)}/10）"
        for s in recent_signals[:5]
    ])
    if not signals_str:
        signals_str = "（本期无高相关度信号，基于行业整体趋势分析）"

    return INDUSTRY_INSIGHT_PROMPT.format(
        industry_name=industry_name,
        recent_signals=signals_str,
        analysis_perspective=analysis_perspective,
    )


def generate_industry_insight(
    industry_name: str,
    recent_signals: list[dict],
    analysis_perspective: str,
    call_search_fn=None,
    raw_llm_call_fn=None,
) -> dict:
    """
    生成行业洞察报告

    call_search_fn: 可选，搜索函数，用于获取最新分众案例
    raw_llm_call_fn: 可选，直接返回文本的 LLM 调用函数（而非 parse_analysis_response）
    """
    prompt = build_industry_insight_prompt(industry_name, recent_signals, analysis_perspective)

    # 如果 prompt 中声明需要搜索，先搜索再补充
    if call_search_fn and "[需要搜索：" in prompt:
        import re
        search_matches = re.findall(r'\[需要搜索：(.*?)\]', prompt)
        for keywords in search_matches:
            keyword_list = [k.strip() for k in keywords.split("|")]
            search_results = []
            for kw in keyword_list:
                try:
                    results = call_search_fn(f"分众 {kw}")
                    search_results.extend(results)
                except Exception:
                    pass
            if search_results:
                cases_text = "\n".join([
                    f"- {s.get('title', '')}: {s.get('content', '')[:200]}"
                    for s in search_results[:3]
                ])
                prompt = prompt.replace(
                    f"[需要搜索：{keywords}]",
                    f"\n【最新分众案例搜索结果】\n{cases_text}\n"
                )
            else:
                prompt = prompt.replace(f"[需要搜索：{keywords}]", "（未搜索到相关案例）")

    # 调用 LLM 生成洞察内容
    content = ""
    if raw_llm_call_fn:
        try:
            # raw_llm_call_fn 直接返回文本字符串
            content = raw_llm_call_fn(prompt)
        except Exception:
            pass

    # 如果未生成内容，显示提示
    if not content:
        content = f"*正在分析 {industry_name} 赛道...*"

    return {"content": content, "industry": industry_name}


def enrich_with_focus_media_cases(insight_text: str, industry_name: str, call_search_fn) -> str:
    """
    对已生成的洞察报告进行案例补充
    如果洞察中有"案例参照"部分为空或薄弱，调用搜索补充
    """
    if "案例参照" not in insight_text:
        return insight_text

    # 检查案例参照部分是否为空
    lines = insight_text.split("\n")
    in_case_section = False
    case_lines = []
    for line in lines:
        if "案例参照" in line:
            in_case_section = True
            continue
        if in_case_section:
            if line.startswith("##") or line.startswith("#"):
                break
            case_lines.append(line)

    case_content = "\n".join(case_lines).strip()
    if case_content and len(case_content) > 20:
        # 案例部分已有内容，不需要补充
        return insight_text

    # 搜索分众在该行业的案例
    try:
        search_kw = f"分众 {industry_name} 案例"
        results = call_search_fn(search_kw)
        if results:
            cases_text = "\n".join([
                f"- **{s.get('title', '')}**：{s.get('content', '')[:150]}"
                for s in results[:3]
            ])
            # 替换空的案例参照部分
            # 找到案例参照部分并补充
            new_lines = []
            in_case = False
            for line in lines:
                new_lines.append(line)
                if "案例参照" in line:
                    in_case = True
                    continue
                if in_case and (line.startswith("##") or line.startswith("#")):
                    new_lines.append(f"\n{cases_text}\n")
                    in_case = False
            return "\n".join(new_lines)
    except Exception:
        pass

    return insight_text

