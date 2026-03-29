"""
销售情报助手 - 主入口
orchestrates: search → dedup → analyze → report
"""

import os
import sys
import json
import yaml
import argparse
import subprocess
import tempfile
import concurrent.futures
from datetime import datetime

# 添加项目根目录到 path
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

# 默认输出基础目录（可通过环境变量 AI_OUTPUT_DIR 覆盖）
DEFAULT_AI_OUTPUT_DIR = os.getenv("AI_OUTPUT_DIR", "/Users/Joe_1/Desktop/Vibe Working/tools/sales-intel/output")

# 加载 .env（如果存在）
def _load_dotenv():
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()


def md_to_pdf(md_path: str) -> str:
    """将 Markdown 文件转为 PDF（通过 Chrome headless），返回 PDF 路径。"""
    import markdown2

    css = (
        'body { font-family: "PingFang SC","Heiti SC",sans-serif; font-size: 13px; '
        'line-height: 1.8; padding: 30px 40px; color: #333; max-width: 800px; margin: 0 auto; }'
        'h1 { font-size: 20px; border-bottom: 2px solid #2563eb; padding-bottom: 8px; color: #1e3a5f; }'
        'h2 { font-size: 17px; color: #1e40af; margin-top: 28px; }'
        'h3 { font-size: 15px; color: #374151; margin-top: 20px; }'
        'h4 { font-size: 14px; color: #4b5563; }'
        'a { color: #2563eb; text-decoration: none; }'
        'table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }'
        'th, td { border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; }'
        'th { background: #f3f4f6; font-weight: 600; }'
        'blockquote { border-left: 3px solid #2563eb; padding-left: 12px; color: #555; '
        'margin: 12px 0; background: #f8fafc; padding: 8px 12px; }'
        'ul { padding-left: 20px; } li { margin-bottom: 8px; }'
        'hr { border: none; border-top: 1px solid #e5e7eb; margin: 20px 0; }'
    )

    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome):
        print(f"  [PDF] Chrome 未找到，跳过 PDF 生成")
        return ""

    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    html = markdown2.markdown(md_content, extras=["tables", "fenced-code-blocks"])
    full_html = f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head><body>{html}</body></html>'

    tmp_html = tempfile.mktemp(suffix=".html")
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(full_html)

    pdf_path = md_path.rsplit(".", 1)[0] + ".pdf"
    try:
        subprocess.run(
            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
             f"--print-to-pdf={pdf_path}", "--print-to-pdf-no-header", tmp_html],
            capture_output=True, timeout=30,
        )
        if os.path.exists(pdf_path):
            size_kb = os.path.getsize(pdf_path) // 1024
            print(f"  [PDF] 已生成: {os.path.basename(pdf_path)} ({size_kb}KB)")
        else:
            print(f"  [PDF] 生成失败")
            pdf_path = ""
    except Exception as e:
        print(f"  [PDF] 生成异常: {e}")
        pdf_path = ""
    finally:
        if os.path.exists(tmp_html):
            os.unlink(tmp_html)

    return pdf_path


from scripts.search import (
    run_search, get_api_key,
    run_fundraising_search,
    record_source_hits, is_weekly_industry_day,
    is_fundraising_day, set_last_fundraising_date, get_last_fundraising_date,
    is_first_run,
    run_hybrid_search,
)
from scripts.dedup import deduplicate, set_profile as set_dedup_profile, get_profile as get_dedup_profile
from scripts.analyzer import (
    build_analysis_prompt, build_industry_prompt,
    parse_analysis_response, filter_by_score,
    build_fundraising_prompt, parse_fundraising_response,
)
from scripts.report import generate_report, generate_full_report
from scripts.memory import (
    record_interaction, record_feedback, content_hash_from_result,
    set_profile as set_memory_profile, get_profile as get_memory_profile,
)
from scripts.search_pool import (
    collect_all_queries, execute_shared_search,
    distribute_results, collect_single_profile_queries,
)
from scripts.endorsement import (
    prompt_and_fetch_endorsements, match_endorsements_to_profile,
)

# CodeSome 503 熔断：一次 503 后整个 session 跳过，避免每条都重试浪费时间
_codesome_disabled = False


def load_config(config_path: str = None) -> dict:
    """加载 config.yaml"""
    if config_path is None:
        config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_profiles(profile_names: list = None) -> list[dict]:
    """
    加载 profiles/ 目录下的所有档案配置。

    profile_names: 指定加载哪些档案，None=加载全部
    返回: [{name, brands, industries, fundraising, _include_industry}, ...]
    """
    profiles_dir = os.path.join(PROJECT_ROOT, "profiles")
    if not os.path.isdir(profiles_dir):
        return []

    profiles = []
    for fname in os.listdir(profiles_dir):
        if not fname.endswith(".yaml"):
            continue
        name = fname[:-5]
        if profile_names and name not in profile_names:
            continue
        fpath = os.path.join(profiles_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if cfg and cfg.get("name"):
                cfg["_include_industry"] = is_weekly_industry_day()
                profiles.append(cfg)
        except Exception as e:
            print(f"[档案加载失败] {fname}: {e}")

    return profiles


def _merge_config_with_profile(shared_config: dict, profile: dict) -> dict:
    """将共享 config 的 search/report 配置与 profile 合并（fundraising 做深度合并）"""
    base_fundraising = shared_config.get("fundraising", {})
    profile_fundraising = profile.get("fundraising", {})
    # 深度合并：base 的 stages/excluded_tracks + profile 的 tracks（后者覆盖前者）
    merged_fundraising = {
        "stages": base_fundraising.get("stages", []),
        "tracks": profile_fundraising.get("tracks", base_fundraising.get("tracks", [])),
        "excluded_tracks": base_fundraising.get("excluded_tracks", []),
    }
    return {
        "brands": profile.get("brands", []),
        "industries": profile.get("industries", []),
        "fundraising": merged_fundraising,
        # search/report 配置沿用 shared config
        "search": shared_config.get("search", {}),
        "report": shared_config.get("report", {}),
    }


def _analyze_single(r: dict, parser_fn) -> dict:
    """对单条结果执行 AI 分析，返回带 analysis 字段的结果。

    省成本策略：先用规则预判，明显低质量的（分数 <= 4）直接用规则结果，
    不调 MiniMax，只把有潜力的结果送 AI 深度分析。
    """
    from scripts.dedup import get_recent_events_for_brand

    brand = r.get("brand", "")
    is_industry = brand.startswith("[行业]")

    # ── 规则预判（品牌新闻）：拦截明显低质量的，省 AI 调用 ──
    if not is_industry:
        pre_check = _fallback_analysis(r)
        if pre_check.get("filter") or pre_check.get("relevance_score", 0) <= 4:
            r["analysis"] = pre_check
            return r

    if is_industry:
        prompt = build_industry_prompt(r)
    else:
        recent_events = get_recent_events_for_brand(brand)
        prompt = build_analysis_prompt(r, recent_events=recent_events)

    if os.environ.get("CODESOME_API_KEY") or os.environ.get("MINIMAX_API_KEY"):
        analysis = _call_openclaw_model(prompt)
    else:
        analysis = _fallback_analysis(r)

    r["analysis"] = analysis
    return r


def _analyze_single_fundraising(r: dict) -> dict:
    """对单条融资结果执行 AI 分析（走 MiniMax，省成本）"""
    track_name = r.get("track_name", "")
    prompt = build_fundraising_prompt(r, track_name)

    if os.environ.get("MINIMAX_API_KEY"):
        analysis = _call_minimax(prompt, parse_analysis_response)
    elif os.environ.get("CODESOME_API_KEY"):
        # MiniMax 不可用时回退到 CodeSome Sonnet
        analysis = _call_llm_api(prompt, parse_analysis_response)
    else:
        analysis = parse_fundraising_response("")

    r["analysis"] = analysis
    return r


def analyze_with_openclaw(results: list[dict]) -> list[dict]:
    """
    使用 AI 模型并行分析每条结果（多线程并发调用 MiniMax）。
    """
    if not results:
        return []

    analyzed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_analyze_single, r, None): r for r in results}
        for future in concurrent.futures.as_completed(futures):
            try:
                analyzed.append(future.result())
            except Exception as e:
                r = futures[future]
                print(f"  [分析失败] {r.get('title', '')[:40]}: {e}", flush=True)
                r["analysis"] = {"filter": True, "filter_reason": f"分析异常: {e}"}
                analyzed.append(r)
    return analyzed


def _call_llm_api(prompt: str, parse_response, retries: int = 2):
    """
    通用 LLM API 调用。
    优先级：CodeSome (Sonnet) > MiniMax > fallback
    parse_response: 接收原始文本，返回解析后的结果。
    """
    import requests
    global _codesome_disabled

    # ── 优先使用 CodeSome API (Claude Sonnet) ──
    codesome_key = os.environ.get("CODESOME_API_KEY", "")
    if codesome_key and not _codesome_disabled:
        try:
            resp = requests.post(
                "https://v3.codesome.cn/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {codesome_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 600,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
            else:
                content = ""
            print(f"  [CodeSome Sonnet OK]", flush=True)
            return parse_response(content)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 503:
                _codesome_disabled = True
                print(f"  [CodeSome 503 熔断] 本次运行跳过 CodeSome，直接用 MiniMax", flush=True)
            else:
                print(f"  [CodeSome Sonnet 失败] {e}", flush=True)
        except Exception as e:
            print(f"  [CodeSome Sonnet 失败] {e}", flush=True)

    # ── 回退：MiniMax-Text-2.7 ──
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if minimax_key:
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    "https://api.minimax.chat/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {minimax_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "MiniMax-M2.7",
                        "max_tokens": 600,
                        "temperature": 0.3,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                choices = resp.json().get("choices", [])
                if choices and isinstance(choices[0], dict):
                    msg = choices[0]
                    content = msg.get("message", {}).get("content", "") if isinstance(msg, dict) else str(choices[0])
                else:
                    content = ""
                print(f"  [MiniMax OK]", flush=True)
                return parse_response(content)
            except Exception as e:
                print(f"  [MiniMax 失败{' (重试)' if attempt < retries else ''}] {e}", flush=True)
                if attempt < retries:
                    import time; time.sleep(2)

    return parse_response("")


def _call_llm_raw(prompt: str, retries: int = 2) -> str:
    """直接调用 LLM 返回原始文本"""
    return _call_llm_api(prompt, lambda x: x, retries)


def _call_openclaw_model(prompt: str, retries: int = 2) -> dict:
    """调用 AI 分析（Sonnet 优先）"""
    return _call_llm_api(prompt, parse_analysis_response, retries)


def _call_minimax(prompt: str, parse_response, retries: int = 2):
    """直接调用 MiniMax-Text-2.7（融资分析专用，不走 Sonnet）"""
    import requests
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        return parse_response("")
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                "https://api.minimax.chat/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {minimax_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "MiniMax-M2.7",
                    "max_tokens": 600,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            if choices and isinstance(choices[0], dict):
                content = choices[0].get("message", {}).get("content", "")
            else:
                content = ""
            print(f"  [MiniMax OK]", flush=True)
            return parse_response(content)
        except Exception as e:
            print(f"  [MiniMax 失败{' (重试)' if attempt < retries else ''}] {e}", flush=True)
            if attempt < retries:
                import time; time.sleep(2)
    return parse_response("")



def analyze_fundraising(results: list[dict]) -> list[dict]:
    """并行分析融资结果（多线程）"""
    if not results:
        return []

    analyzed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_analyze_single_fundraising, r): r for r in results}
        for future in concurrent.futures.as_completed(futures):
            try:
                analyzed.append(future.result())
            except Exception as e:
                r = futures[future]
                print(f"  [融资分析失败] {r.get('title', '')[:40]}: {e}", flush=True)
                r["analysis"] = {"filter": True}
                analyzed.append(r)
    return analyzed


def _fallback_analysis(result: dict) -> dict:
    """
    独立运行时的简易分析（无 AI 模型）。
    核心逻辑：品牌名必须出现在标题或内容中，否则直接过滤。
    fallback 模式不生成切入建议（避免模板化套话）。
    """
    title = result.get("title", "")
    content = result.get("content", "")[:500]
    brand = result.get("brand", "")
    brand_names = result.get("brand_names", [brand])
    text = f"{title} {content}"

    # ---- 第一关：品牌相关性（硬门槛）----
    matched_brand = ""
    for bn in brand_names:
        if bn and bn in text:
            matched_brand = bn
            break

    if not matched_brand:
        # 品牌名未在文本中出现：不直接过滤，交给 AI 进一步判断
        return {
            "relevance_score": 4,
            "urgency": "⚪",
            "intel_summary": "",
            "focus_media_angle": "",
            "recommendation_reason": "",
            "talk_track": "",
            "filter": False,
            "filter_reason": f"内容未明确提及{brand}，待 AI 判断",
        }

    # ---- 第 1.5 关：标题主体检查（品牌是配角则降级过滤）----
    brand_in_title = any(bn and bn in title for bn in brand_names)
    if brand_in_title:
        # 检查标题中是否有其他更突出的品牌/主体
        # 模式："联合X""携手X""与X合作" 等 — 品牌出现在介词/动词宾语位置，不是主体
        import re
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
        is_peripheral = any(re.search(p, title) for p in peripheral_patterns)

        # 额外检查：标题前半段是否由其他品牌/实体主导
        # 如果品牌名只出现在标题后半段的从属结构中，也视为配角
        if not is_peripheral:
            brand_pos = title.find(matched_brand)
            title_mid = len(title) // 2
            # 品牌在后半段，且前半段有明确的其他主体（含中文品牌名模式）
            if brand_pos > title_mid:
                front_half = title[:brand_pos]
                # 如果前半段包含其他产品/品牌关键词，品牌可能是配角
                other_subject_signals = ["新车", "新品", "车型", "上市", "宠粉"]
                if any(s in front_half for s in other_subject_signals):
                    is_peripheral = True

        if is_peripheral:
            return {
                "relevance_score": 2,
                "urgency": "⚪",
                "intel_summary": "",
                "focus_media_angle": "",
                "recommendation_reason": "",
                "talk_track": "",
                "filter": True,
                "filter_reason": f"{matched_brand}在该文章中仅为配角/联合方，非主体报道",
            }
    elif not any(bn and bn in content[:300] for bn in brand_names):
        # 品牌名不在标题中，也不在内容前300字中 → 弱相关
        return {
            "relevance_score": 2,
            "urgency": "⚪",
            "intel_summary": "",
            "focus_media_angle": "",
            "recommendation_reason": "",
            "talk_track": "",
            "filter": True,
            "filter_reason": f"{matched_brand}仅在内容深处提及，非主体报道",
        }

    # ---- 第二关：信号词打分 ----
    score = 4
    urgency = "⚪"
    reason = ""

    high_signals = {
        "发布会": "近期有发布会动态，可能有品牌曝光需求",
        "新品发布": "新品发布期，品牌推广预算释放窗口",
        "代言人": "代言人动态，品牌正在加大传播投入",
        "品牌升级": "品牌升级期，需要大规模心智刷新",
        "广告投放": "有明确的广告投放动作",
        "营销战役": "正在策划或执行营销战役",
        "品牌发布": "品牌层面有重大发布",
        "全球首发": "全球首发产品，高传播价值窗口",
    }
    mid_signals = {
        "新品": "有新品动态，可能伴随推广需求",
        "融资": "获得融资，品牌投放预算可能增加",
        "上市": "新品上市期，传播需求集中",
        "合作": "有品牌合作动态",
        "签约": "签约合作，可能有联合推广",
        "CMO": "营销高管变动，决策链可能调整",
        "品牌总监": "品牌负责人变动，值得关注",
        "市场总监": "市场负责人变动，值得关注",
    }
    low_signals = {
        "销量": "销量数据变化",
        "市场份额": "市场格局变化",
        "补贴": "补贴政策变化",
        "竞争": "竞争格局变化",
        "政策": "行业政策变化",
    }

    for kw, r in high_signals.items():
        if kw in text:
            score = 9
            urgency = "🔴"
            reason = r
            break

    if score < 9:
        for kw, r in mid_signals.items():
            if kw in text:
                score = 7
                urgency = "🟡"
                reason = r
                break

    if score < 7:
        for kw, r in low_signals.items():
            if kw in text:
                score = 5
                urgency = "⚪"
                reason = r
                break

    if not reason:
        reason = f"{matched_brand}近期有行业动态"

    return {
        "relevance_score": score,
        "urgency": urgency,
        "intel_summary": "",
        "focus_media_angle": "",  # fallback 模式不生成建议
        "recommendation_reason": reason,
        "talk_track": "",
        "filter": False,
        "filter_reason": "",
    }


def run_pipeline(
    config: dict = None,
    include_industry: bool = None,
    min_score: int = 7,
    dry_run: bool = False,
    profile_name: str = None,
    search_pool: dict = None,
    fundraising_results_raw: list = None,
    use_cache: bool = False,
    date_str: str = None,
    time_range: str = "day",
    first_run: bool = False,
    endorsement_items: list = None,
) -> str:
    """
    执行完整 pipeline
    返回: Markdown 格式的日报字符串

    profile_name: 当前档案名（用于数据目录隔离和报告标题）
    search_pool: 可选，来自 search_pool.execute_shared_search() 的结果，
                 传入则跳过独立搜索，走共享池分发
    fundraising_results_raw: 可选，预取的融资搜索结果（多档案模式下由主流程统一预取）
    use_cache: 为 True 时，跳过搜索，从存档加载结果重新分析（用于重新生成报告）
    date_str: 可选，指定存档日期（YYYY-MM-DD），默认为当天
    time_range: 搜索时间窗口，"day"=今日，"month"=近3个月（首次运行使用）
    first_run: 是否为首次运行（由 run_single_profile_pipeline 在存档保存前计算后传入）
    """
    if config is None:
        config = load_config()

    # ── Profile 数据目录切换 ────────────────────────────────
    old_dedup_profile = get_dedup_profile()
    old_memory_profile = get_memory_profile()
    if profile_name:
        set_dedup_profile(profile_name)
        set_memory_profile(profile_name)
        print(f"  [档案] 切换到: {profile_name}")

    try:
        return _run_pipeline_inner(
            config, include_industry, min_score, dry_run,
            profile_name, search_pool, fundraising_results_raw, use_cache,
            date_str=date_str,
            time_range=time_range,
            first_run=first_run,
            endorsement_items=endorsement_items,
        )
    finally:
        # 恢复默认 profile
        set_dedup_profile("default" if profile_name else None)
        set_memory_profile("default" if profile_name else None)


def _run_pipeline_inner(
    config, include_industry, min_score, dry_run, profile_name, search_pool,
    fundraising_results_raw=None, use_cache=False, date_str: str = None,
    time_range: str = "day",
    first_run: bool = False,
    endorsement_items: list = None,
) -> str:
    """
    run_pipeline 的核心逻辑（放在 try/finally 外层）。
    不做 profile 切换，只管业务逻辑。

    use_cache: True 时跳过搜索，从 SQLite 存档加载结果重新分析。
    date_str: 存档日期，默认为当天。
    time_range: 搜索时间窗口，"day"=今日，"month"=近3个月（首次运行使用）。
    first_run: 是否为首次运行（由 run_single_profile_pipeline 在存档保存前计算后传入）。
    """
    brand_configs = config.get("brands", [])
    industry_configs = config.get("industries", [])
    fundraising_config = config.get("fundraising", {})

    if include_industry is None:
        include_industry = False  # 行业搜索默认关闭，需要时用 --industry 手动开启

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"[{date_str}] 开始销售情报生成...")
    print(f"  品牌监控: {len(brand_configs)} 个")
    print(f"  行业搜索: {'是' if include_industry else '否'}")
    print(f"  融资专项: 是（{len(fundraising_config.get('tracks', []))} 个赛道）")

    if dry_run:
        print("  [DRY RUN] 跳过实际搜索")
        return ""

    # ── Step 1: 搜索（或从存档加载 + 增量搜索）─────────────
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    if use_cache:
        # 纯缓存模式：从存档加载所有数据，不再进行增量搜索
        from scripts.search_archive import (
            load_results, has_archive, save_results as archive_save,
        )
        pf = profile_name or "default"

        if not has_archive(date_str, pf):
            print(f"\n[Step 1] 存档为空，请先正常搜索")
            return ""

        all_raw = load_results(date_str, pf)
        print(f"\n[Step 1] 从存档加载（纯缓存模式）: {len(all_raw)} 条")
    elif search_pool is not None:
        # 来自共享池：直接分发，跳过独立搜索
        print("\n[Step 1] 从搜索共享池分发结果...")
        all_raw = distribute_results(search_pool, {
            "name": profile_name or "default",
            "brands": brand_configs,
            "industries": industry_configs,
            "fundraising": fundraising_config,
        })
        print(f"  分发结果: {len(all_raw)} 条")
    else:
        # 独立搜索 - 使用混合搜索模式
        print("\n[Step 1] 混合搜索（Bocha + 白名单直接抓取）...")

        # 品牌搜索：使用混合模式
        brand_results = run_hybrid_search(brand_configs, config)

        # 行业搜索：保持原有逻辑
        if include_industry:
            from scripts.search import run_industry_search
            industry_results = run_industry_search(industry_configs)
            raw_results = brand_results + industry_results
        else:
            raw_results = brand_results

        print(f"  混合搜索结果: {len(raw_results)} 条")

        # 融资专项搜索：优先使用预取结果（多档案模式统一预取），否则按需运行
        if fundraising_results_raw is not None:
            fr_label = "共享（已预取）"
            fr_count = len(fundraising_results_raw)
        elif is_fundraising_day():
            fundraising_results_raw = run_fundraising_search(fundraising_config)
            fr_label = "是"
            fr_count = len(fundraising_results_raw)
            set_last_fundraising_date(datetime.now().date().isoformat())
        else:
            fundraising_results_raw = []
            fr_label = "否（非周一/周三）"
            fr_count = 0

        print(f"\n[Step 2] 融资专项搜索: {fr_label}")
        if fr_count > 0:
            print(f"  融资搜索结果: {fr_count} 条")

        all_raw = raw_results + fundraising_results_raw
    if not all_raw:
        print("\n  无搜索结果，跳过日报")
        return ""

    # 分离品牌/行业结果 和 融资结果
    brand_industry_results = [r for r in all_raw if not r.get("brand", "").startswith("[融资]")]
    fundraising_results = [r for r in all_raw if r.get("brand", "").startswith("[融资]")]

    if use_cache:
        new_results = all_raw
        print(f"\n[Step 2] 从存档加载（跳过重复检查）: {len(brand_industry_results)} 条客户新闻 + {len(fundraising_results)} 条融资新闻")
    else:
        # 品牌/行业去重（融资结果不需要去重，因为每次都是全量搜索）
        brand_industry_deduped = deduplicate(brand_industry_results)
        new_results = brand_industry_deduped + fundraising_results
        print(f"\n[Step 2] 去重后: {len(new_results)} 条新结果")
        # 存档搜索结果（支持后续重新生成报告）
        if new_results:
            from scripts.search_archive import save_results as archive_save
            archive_save(new_results, date_str, profile_name or "default", profile_config={
                "brands": brand_configs, "industries": industry_configs})
            print(f"  [存档] 已保存 {len(new_results)} 条到数据库")

    if not new_results:
        print("  无新结果，跳过日报")
        return ""

    # 重新从 new_results 分离（去重后结果可能变化）
    brand_industry_results = [r for r in new_results if not r.get("brand", "").startswith("[融资]")]
    fundraising_results = [r for r in new_results if r.get("brand", "").startswith("[融资]")]

    # ── Step 3: AI 分析 ────────────────────────────────────
    print("\n[Step 3] 开始 AI 分析...")

    # 品牌/行业结果分析
    analyzed_brand = analyze_with_openclaw(brand_industry_results) if brand_industry_results else []
    # 融资结果分析
    analyzed_fr = analyze_fundraising(fundraising_results) if fundraising_results else []

    analyzed = analyzed_brand + analyzed_fr

    # ── Step 4: 过滤低分 + 信息源追踪 ────────────────────────
    filtered = filter_by_score(analyzed, min_score=min_score)
    print(f"  过滤后: {len(filtered)} 条有效情报")

    # 信息源质量追踪（高分结果计入 source_quality.json）
    try:
        record_source_hits(analyzed, min_score=7)
    except Exception as e:
        print(f"  [信息来源追踪失败] {e}")

    # ── Step 5: 生成日报 ───────────────────────────────────
    print("\n[Step 5] 生成日报...")
    # first_run 已由 run_single_profile_pipeline 在存档保存前计算并传入
    report = generate_full_report(
        analyzed_results=filtered,
        fundraising_results=[r for r in analyzed if r.get("brand", "").startswith("[融资]") and r.get("analysis", {}).get("relevance_score", 0) >= 7],
        date_str=date_str,
        profile_name=profile_name,
        brand_configs=brand_configs,
        is_first_run=first_run,
        endorsement_items=endorsement_items or [],
    )
    print(f"  日报生成完成，共 {len(report)} 字符")

    # ── Step 6: 记录本次推送 ───────────────────────────────
    from scripts.dedup import record_pushed_events, _normalize_title
    pushed_events = []
    for r in filtered:
        analysis = r.get("analysis", {})
        if analysis.get("is_followup", False):
            continue
        event_key = analysis.get("event_key", "")
        if not event_key:
            # AI 返回空 event_key 时，使用标题前20字作为 fallback
            event_key = _normalize_title(r.get("title", ""))[:20]
        pushed_events.append({
            "brand": r.get("brand", ""),
            "event_key": event_key,
        })
    if pushed_events:
        record_pushed_events(pushed_events)

    return report


def run_single_profile_pipeline(
    profile_name: str,
    shared_config: dict = None,
    search_pool: dict = None,
    include_industry: bool = None,
    min_score: int = 7,
    dry_run: bool = False,
    output_dir: str = None,
    fundraising_results_raw: list = None,
    use_cache: bool = False,
    date_str: str = None,
    endorsement_items: list = None,
) -> str:
    """
    运行单个档案的完整 pipeline。

    shared_config: 共享的 search/report 配置（从 config.yaml 加载）
    search_pool: 搜索共享池结果（来自 run_multi_profile_pipeline）
    fundraising_results_raw: 融资搜索预取结果（多档案模式下统一预取一次）
    output_dir: 报告输出目录，None 则 stdout
    date_str: 存档日期（YYYY-MM-DD），默认为当天
    返回: Markdown 报告字符串
    """
    from scripts.dedup import set_profile as set_dedup, get_profile as get_dedup
    from scripts.memory import set_profile as set_mem, get_profile as get_mem

    profile_path = os.path.join(PROJECT_ROOT, "profiles", f"{profile_name}.yaml")
    if not os.path.exists(profile_path):
        print(f"[档案不存在] {profile_path}")
        return ""

    with open(profile_path, "r", encoding="utf-8") as f:
        profile_cfg = yaml.safe_load(f)

    config = _merge_config_with_profile(shared_config, profile_cfg)

    print(f"\n{'='*60}")
    print(f"[档案] {profile_name}")
    print(f"{'='*60}")

    # 首次运行判断（必须在 run_pipeline 调用前完成，以便传入 generate_full_report）
    first_run = is_first_run(profile_name) if profile_name and not use_cache else False

    # 首次运行：查近3个月；后续查今日
    if first_run and not use_cache:
        time_range = "month"
        print(f"  [首次运行] 扩展时间窗口为近3个月")
    else:
        time_range = "day"

    report = run_pipeline(
        config=config,
        include_industry=include_industry,
        min_score=min_score,
        dry_run=dry_run,
        profile_name=profile_name,
        search_pool=search_pool,
        fundraising_results_raw=fundraising_results_raw,
        use_cache=use_cache,
        date_str=date_str,
        time_range=time_range,
        first_run=first_run,
        endorsement_items=endorsement_items,
    )

    if report:
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            fname = os.path.join(output_dir, f"{profile_name}-销售情报-{date_str}.md")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n日报已保存: {fname}")
            md_to_pdf(fname)
        else:
            print("\n" + "=" * 60)
            print(report)
    else:
        print("\n今日无新情报，日报已跳过。")

    return report


def run_multi_profile_pipeline(profile_names: list = None, dry_run: bool = False) -> list[str]:
    """
    运行多个档案（使用搜索共享池优化 Bocha 消耗）。

    profile_names: 指定档案列表，None=运行全部
    返回: 各档案报告字符串列表
    """
    profiles = load_profiles(profile_names)
    if not profiles:
        print("[错误] 未找到任何档案配置，请先在 profiles/ 目录下创建 .yaml 文件")
        return []

    print(f"[多档案模式] 共 {len(profiles)} 个档案: {[p.get('name') for p in profiles]}")
    print(f"[多档案模式] 开始收集所有查询...")

    # Phase 1: 共享搜索
    shared_config = load_config()
    all_queries = collect_all_queries(profiles)
    unique_queries = len(set(f"{q['query']}|{q.get('lang','zh')}" for q in all_queries))
    print(f"[多档案模式] 共 {len(all_queries)} 条查询（去重后 {unique_queries} 条唯一查询）")

    if dry_run:
        print("  [DRY RUN] 跳过实际搜索")
        return []

    search_pool = execute_shared_search(all_queries)

    # Phase 1b: 融资专项预取（只查一次，全局共享）
    if is_fundraising_day():
        print(f"\n[多档案模式] 融资专项搜索（每周一、三）...")
        fr_config = shared_config.get("fundraising", {})
        pre_fr_results = run_fundraising_search(fr_config)
        print(f"  融资搜索结果: {len(pre_fr_results)} 条")
        set_last_fundraising_date(datetime.now().date().isoformat())
    else:
        last_fr_date = get_last_fundraising_date()
        print(f"\n[多档案模式] 融资专项跳过（上次: {last_fr_date}）")
        pre_fr_results = []

    # Phase 1c: 周三代言人速报（交互式，暂停等待用户粘贴微信链接）
    all_endorsements = []
    if datetime.now().weekday() == 2:  # 周三
        all_endorsements = prompt_and_fetch_endorsements(profiles)

    # Phase 2: 按档案独立处理
    reports = []
    for profile in profiles:
        pname = profile.get("name", "default")
        # 设置数据目录
        set_dedup_profile(pname)
        set_memory_profile(pname)

        # 按行业过滤代言人
        profile_endorsements = match_endorsements_to_profile(all_endorsements, profile) if all_endorsements else []

        output_dir = os.path.join(DEFAULT_AI_OUTPUT_DIR, pname)
        report = run_single_profile_pipeline(
            profile_name=pname,
            shared_config=shared_config,
            search_pool=search_pool,
            include_industry=profile.get("_include_industry"),
            output_dir=output_dir,
            dry_run=dry_run,
            fundraising_results_raw=pre_fr_results,
            endorsement_items=profile_endorsements,
        )
        reports.append(report)

    print(f"\n[多档案模式] 完成，共处理 {len(profiles)} 个档案")
    return reports


def main():
    parser = argparse.ArgumentParser(description="销售情报助手")
    parser.add_argument("--config", type=str, help="配置文件路径（向后兼容）")
    parser.add_argument("--profile", type=str, default=None,
                        help="指定运行某个档案（默认使用 config.yaml）")
    parser.add_argument("--profile-all", action="store_true",
                        help="运行 profiles/ 下所有档案（使用搜索共享池）")
    parser.add_argument("--reset-dedup", action="store_true",
                        help="清空去重记录（周日测试用）")
    parser.add_argument("--industry", action="store_true", help="强制包含行业搜索")
    parser.add_argument("--no-industry", action="store_true", help="强制排除行业搜索")
    parser.add_argument("--min-score", type=int, default=7, help="最低相关度分数（默认7，精选模式）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印配置，不实际执行")
    parser.add_argument("--output", type=str, help="输出文件路径（默认 stdout）")
    parser.add_argument("--force", action="store_true", help="强制忽略去重记录，重新推送（用于测试）")
    parser.add_argument("--regenerate", action="store_true", help="从存档加载结果重新生成报告（不重新搜索）")
    parser.add_argument("--date", type=str, default=None, help="指定存档日期（YYYY-MM-DD，默认今天）")
    parser.add_argument("--analyze-domains", action="store_true", help="运行域名质量分析（分析过去7天搜索结果，生成白名单更新建议）")

    args = parser.parse_args()

    # ── 域名质量分析 ──────────────────────────────────────
    if args.analyze_domains:
        from scripts.domain_analyzer import run_analysis
        run_analysis(days=7)
        return

    # ── 去重重置 ──────────────────────────────────────────
    if args.reset_dedup:
        if args.profile_all:
            # 清空所有档案的去重记录
            from scripts.dedup import save_seen_urls, save_seen_events
            profiles_dir = os.path.join(PROJECT_ROOT, "data", "profiles")
            if os.path.exists(profiles_dir):
                for d in os.listdir(profiles_dir):
                    dp = os.path.join(profiles_dir, d)
                    if os.path.isdir(dp):
                        import json
                        # 清空该档案的去重
                        set_dedup_profile(d)
                        save_seen_urls({})
                        save_seen_events([])
                        print(f"[RESET] 已清空档案 {d} 的去重记录")
        elif args.profile:
            from scripts.dedup import save_seen_urls, save_seen_events
            set_dedup_profile(args.profile)
            save_seen_urls({})
            save_seen_events([])
            print(f"[RESET] 已清空档案 {args.profile} 的去重记录")
        else:
            # 清空默认 data/profiles/default
            from scripts.dedup import save_seen_urls, save_seen_events
            set_dedup_profile("default")
            save_seen_urls({})
            save_seen_events([])
            print("[RESET] 已清空默认去重记录（profiles/default）")

    # ── 强制模式 ──────────────────────────────────────────
    if args.force:
        from scripts.dedup import load_seen_urls, save_seen_urls
        seen = load_seen_urls()
        today = datetime.now().date().isoformat()
        seen = {url: ts for url, ts in seen.items() if not ts.startswith(today)}
        save_seen_urls(seen)
        print(f"[FORCE 模式] 已清除今日去重记录，当前保留 {len(seen)} 条历史记录")

    # ── 多档案模式 ────────────────────────────────────────
    if args.profile_all:
        run_multi_profile_pipeline(dry_run=args.dry_run)
        return

    if args.profile:
        profile_name = args.profile
        profile_path = os.path.join(PROJECT_ROOT, "profiles", f"{profile_name}.yaml")
        if not os.path.exists(profile_path):
            print(f"[错误] 档案不存在: {profile_path}")
            return
        shared_config = load_config(args.config)
        output_dir = args.output or os.path.join(DEFAULT_AI_OUTPUT_DIR, profile_name)
        run_single_profile_pipeline(
            profile_name=profile_name,
            shared_config=shared_config,
            include_industry=is_weekly_industry_day() if not args.industry and not args.no_industry else args.industry,
            min_score=args.min_score,
            dry_run=args.dry_run,
            output_dir=output_dir,
            use_cache=args.regenerate,
            date_str=args.date,
        )
        return

    # ── 向后兼容模式（使用 config.yaml）───────────────────
    config = load_config(args.config)

    include_industry = None
    if args.industry:
        include_industry = True
    elif args.no_industry:
        include_industry = False

    report = run_pipeline(
        config=config,
        include_industry=include_industry,
        min_score=args.min_score,
        dry_run=args.dry_run,
        profile_name="default",
        use_cache=args.regenerate,
        date_str=args.date,
    )

    if report:
        if args.output:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\n日报已保存: {args.output}")
            md_to_pdf(args.output)
        else:
            print("\n" + "=" * 60)
            print(report)
    else:
        print("\n今日无新情报，日报已跳过。")


if __name__ == "__main__":
    main()
