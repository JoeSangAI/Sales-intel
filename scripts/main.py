"""
销售情报助手 - 主入口
架构：search → dedup → layer2_preprocess → layer3_chef → quality_check

板块划分：
- 调度层 (scheduler.py): 周规则判断、profile 加载
- 采集层 (search.py / search_pool.py): 搜索执行、结果收集
- 预处理层 (layer2_preprocessor.py): 规则粗筛 + LLM 分类标引
- 报告层 (layer3_chef.py): 大厨 LLM 生成完整日报
- 质检层 (quality_check.py): 幻觉检测 + 格式质检
"""

import os
import sys
import json
import yaml
import argparse
import subprocess
import tempfile
import re
import traceback
from datetime import datetime

# 添加项目根目录到 path
PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)

# 默认输出基础目录（可通过环境变量 AI_OUTPUT_DIR 覆盖）
DEFAULT_AI_OUTPUT_DIR = os.getenv("AI_OUTPUT_DIR", "/Users/Joe_1/Desktop/AI output/sales-intel")

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
        'body { font-family: "PingFang SC","Heiti SC","Microsoft YaHei","Apple Color Emoji","Segoe UI Emoji",sans-serif; font-size: 13px; '
        'line-height: 1.8; padding: 30px 40px; color: #333; max-width: 800px; margin: 0 auto; '
        'word-break: break-all; overflow-wrap: break-word; }'
        'h1 { font-size: 22px; font-weight: 700; border-bottom: 3px solid #1e40af; padding-bottom: 10px; color: #1e3a5f; margin-bottom: 6px; }'
        'h2 { font-size: 16px; font-weight: 700; color: #fff; background: #1e40af; padding: 8px 14px; border-radius: 4px; margin-top: 32px; margin-bottom: 12px; }'
        'h3 { font-size: 14px; font-weight: 700; color: #1e3a5f; border-left: 4px solid #2563eb; padding-left: 10px; margin-top: 20px; margin-bottom: 6px; }'
        'h4 { font-size: 13px; color: #4b5563; }'
        'strong { font-weight: 700; color: #1e3a5f; }'
        'a { color: #2563eb; text-decoration: none; }'
        'table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }'
        'th, td { border: 1px solid #d1d5db; padding: 6px 10px; text-align: left; }'
        'th { background: #f3f4f6; font-weight: 600; }'
        'blockquote { border-left: 3px solid #2563eb; padding-left: 12px; color: #555; '
        'margin: 12px 0; background: #f8fafc; padding: 8px 12px; }'
        'ul { display: block; padding-left: 24px; margin: 8px 0; list-style: disc; }'
        'li { display: block; margin-bottom: 10px; word-break: break-all; overflow-wrap: break-word; }'
        'p { margin: 6px 0; }'
        'hr { border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }'
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
            try:
                os.unlink(tmp_html)
            except Exception as e:
                print(f"  [警告] 清理临时文件失败: {e}")

    return pdf_path


from scripts.search import (
    run_search, get_api_key,
    run_fundraising_search,
    record_source_hits,
    is_first_run,
    run_hybrid_search,
)
from scripts.profile_context import set_profile, get_profile
from scripts.dedup import deduplicate, dedup_fundraising_by_company, _normalize_title
from scripts.layer2_preprocessor import preprocess
from scripts.layer3_chef import chef_report
from scripts.decision_makers import enrich_decision_makers
from scripts.quality_check import quality_check, retry_with_feedback
from scripts.memory import (
    record_interaction, record_feedback, content_hash_from_result,
)
from scripts.search_pool import (
    collect_all_queries, execute_shared_search,
    distribute_results, collect_single_profile_queries,
)
from scripts.endorsement import (
    prompt_and_fetch_endorsements, match_endorsements_to_profile,
)

# 调度层：从 scheduler.py 导入
from scripts.scheduler import (
    load_config, load_profiles, get_schedule_flags,
    _merge_config_with_profile,
)


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
    old_profile = get_profile()
    if profile_name:
        set_profile(profile_name)
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
        set_profile("default" if profile_name else None)


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

        # 跳过客户新闻（只跑行业和代言人）
        skip_customer_news = config.get("skip_customer_news", False)
        if skip_customer_news:
            print("  [特殊规则] 跳过客户新闻，只保留行业和代言人")
            all_raw = [r for r in all_raw if r.get("brand", "").startswith("[行业]")]

        print(f"  分发结果: {len(all_raw)} 条")
    else:
        # 独立搜索 - 使用混合搜索模式
        print("\n[Step 1] 混合搜索（Bocha + 白名单直接抓取）...")

        # 检查是否跳过客户新闻（只跑行业和代言人）
        skip_customer_news = config.get("skip_customer_news", False)
        if skip_customer_news:
            print("  [特殊规则] 跳过客户新闻，只保留行业和代言人")
            brand_results = []
        else:
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

        # 融资专项搜索：优先使用预取结果（多档案模式统一预取），否则直接搜索
        if fundraising_results_raw is not None:
            fr_label = "共享（已预取）"
            fr_count = len(fundraising_results_raw)
        else:
            fundraising_results_raw = run_fundraising_search(fundraising_config)
            fr_label = "是"
            fr_count = len(fundraising_results_raw)

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

    # ── 融资结果按 profile 过滤：只保留该销售关注的行业/赛道 ──
    if fundraising_results and (industry_configs or fundraising_config.get("tracks")):
        profile_industries = {ind.get("name", "") for ind in industry_configs}
        profile_tracks = {t.get("name", "") for t in fundraising_config.get("tracks", [])}
        allowed_sectors = profile_industries | profile_tracks
        before_count = len(fundraising_results)
        fundraising_results = [
            r for r in fundraising_results
            if _fundraising_matches_profile(r, allowed_sectors)
        ]
        filtered_count = before_count - len(fundraising_results)
        if filtered_count > 0:
            print(f"  [Profile 过滤] 融资结果: {before_count} → {len(fundraising_results)} 条（过滤 {filtered_count} 条不相关行业）")

    if use_cache:
        # 融资结果去重（cache 模式下 brand_industry 也交给 preprocess 去重）
        fundraising_deduped = deduplicate(fundraising_results)
        fundraising_deduped = dedup_fundraising_by_company(fundraising_deduped)
        if len(fundraising_deduped) < len(fundraising_results):
            print(f"  [融资去重] {len(fundraising_results)} → {len(fundraising_deduped)} 条")
        new_results = brand_industry_results + fundraising_deduped
        print(f"\n[Step 2] 从存档加载: {len(new_results)} 条（融资 {len(fundraising_deduped)} 条已去重）")
    else:
        # 融资结果去重：URL/标题去重 + 公司名归一化去重（融资不经过 layer2 的 LLM）
        fundraising_deduped = deduplicate(fundraising_results)
        fundraising_deduped = dedup_fundraising_by_company(fundraising_deduped)
        if len(fundraising_deduped) < len(fundraising_results):
            print(f"  [融资去重] {len(fundraising_results)} → {len(fundraising_deduped)} 条（过滤 {len(fundraising_results) - len(fundraising_deduped)} 条重复）")
        # 品牌/行业去重并入 layer2 preprocess() 内部（统一处理）
        new_results = brand_industry_results + fundraising_deduped
        print(f"\n[Step 2] 预处理前: {len(new_results)} 条（融资 {len(fundraising_deduped)} 条已去重）")
        # 存档搜索结果（支持后续重新生成报告）
        if new_results:
            from scripts.search_archive import save_results as archive_save
            archive_save(new_results, date_str, profile_name or "default", profile_config={
                "brands": brand_configs, "industries": industry_configs})
            print(f"  [存档] 已保存 {len(new_results)} 条到数据库")

    if not new_results:
        print("  无新结果，跳过日报")
        return ""

    # ── Step 2: Layer 2 预处理 ───────────────────────────────
    print("\n[Step 2] Layer 2 预处理...")
    from scripts.dedup import get_recent_events_for_brand

    # 收集所有品牌的近期事件（用于 LLM 判断跟进），转为字符串格式
    all_recent = []
    for cfg in brand_configs:
        recent = get_recent_events_for_brand(cfg.get("name", ""))
        all_recent.extend([f"{e.get('brand', '')} - {e.get('event_key', '')}"
                          for e in recent if e.get("event_key")])

    # 融资结果不参与 LLM 分类（只在搜索时标注 track_name）
    brand_industry_raw = [r for r in new_results
                          if not r.get("brand", "").startswith("[融资]")]
    fundraising_raw = [r for r in new_results
                       if r.get("brand", "").startswith("[融资]")]

    # 品牌/行业：去重并入 preprocess 内部；融资：统一在这里去重
    if brand_industry_raw:
        brand_industry_clean = preprocess(
            raw_results=brand_industry_raw,
            brand_configs=brand_configs,
            industry_configs=industry_configs,
            profile_fundraising_tracks=fundraising_config.get("tracks", []),
            recent_events=all_recent,
            skip_dedup=use_cache,
        )
    else:
        brand_industry_clean = brand_industry_raw

    # 融资去重：fresh 模式在 Step 1 已做，cache 模式需要单独做
    if use_cache:
        fundraising_clean = dedup_fundraising_by_company(fundraising_raw)
    else:
        fundraising_clean = fundraising_raw

    all_items = brand_industry_clean + fundraising_clean

    # ── Step 2b: 白名单硬过滤（规则级，不依赖 LLM）─────────────
    # 融资条目：只保留 profile 注册赛道内的
    profile_industries = {ind.get("name", "") for ind in industry_configs}
    profile_tracks = {t.get("name", "") for t in fundraising_config.get("tracks", [])}
    allowed_sectors = profile_industries | profile_tracks
    if allowed_sectors:
        before_whitelist = len(all_items)
        all_items = [
            item for item in all_items
            if not item.get("brand", "").startswith("[融资]")
            or _fundraising_matches_profile(item, allowed_sectors)
        ]
        wl_filtered = before_whitelist - len(all_items)
        if wl_filtered > 0:
            print(f"  [白名单硬过滤] 移除 {wl_filtered} 条不属于注册赛道的融资条目")

    # ── Step 2c: 融资公司名归一化去重 ──
    fundraising_items = [i for i in all_items if i.get("brand", "").startswith("[融资]")]
    if fundraising_items:
        fundraising_deduped = dedup_fundraising_by_company(fundraising_items)
        non_fundraising = [i for i in all_items if not i.get("brand", "").startswith("[融资]")]
        all_items = non_fundraising + fundraising_deduped
        removed = len(fundraising_items) - len(fundraising_deduped)
        if removed > 0:
            print(f"  [融资去重] 同公司合并: {len(fundraising_items)} → {len(fundraising_deduped)} 条")

    print(f"  Layer 2 预处理后: {len(all_items)} 条")

    decision_makers_map = enrich_decision_makers(all_items, config)

    if not all_items:
        print("  无有效条目，跳过日报")
        return ""

    # ── Step 3: Layer 3 大厨 LLM ─────────────────────────────
    print("\n[Step 3] Layer 3 大厨 LLM...")
    from scripts.dedup import load_seen_events
    from scripts.scheduler import get_schedule_flags

    recent_events_list = load_seen_events()
    schedule = get_schedule_flags()

    report = chef_report(
        items=all_items,
        config=config,
        schedule=schedule,
        recent_events=[f"{e.get('brand', '')} - {e.get('event_key', '')}"
                       for e in recent_events_list if e.get("event_key")],
        endorsement_items=endorsement_items or [],
        decision_makers_map=decision_makers_map,
    )
    print(f"  报告生成完成，共 {len(report)} 字符")

    # ── Step 3b: 质检 ─────────────────────────────────────
    if report and not dry_run:
        print("\n[Step 3b] 质检...")
        qc_result = quality_check(report, all_items, profile=config, decision_makers_map=decision_makers_map)

        if not qc_result["pass"]:
            print(f"  [质检首次] 发现问题: {qc_result.get('hallucination_issues', qc_result.get('format_issues', []))}")
            # 首次失败：注入反馈，重新生成
            report = retry_with_feedback(report, qc_result, all_items, config=config, decision_makers_map=decision_makers_map)
            qc_result2 = quality_check(report, all_items, profile=config, decision_makers_map=decision_makers_map)
            if not qc_result2["pass"]:
                print(f"  [质检重试后] 仍有问题，标记人工审核")
                report = "⚠️ 需要人工审核\n\n" + report
            else:
                print(f"  [质检] 已自动修复")
        else:
            print(f"  [质检通过]")

    # ── Step 4: 记录本次推送 ───────────────────────────────
    from scripts.dedup import record_pushed_events
    pushed_events = []
    for item in all_items:
        if item.get("is_followup", False):
            continue
        event_key = item.get("event_key", "")
        if not event_key:
            # 没有 event_key 时，使用标题前20字作为 fallback
            event_key = _normalize_title(item.get("title", ""))[:20]
        pushed_events.append({
            "brand": item.get("brand", ""),
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

    # 防止路径穿越攻击，强制只取 basename
    safe_name = os.path.basename(str(profile_name)) if profile_name else "default"
    profile_path = os.path.join(PROJECT_ROOT, "profiles", f"{safe_name}.yaml")
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


def _fundraising_matches_profile(result: dict, allowed_sectors: set) -> bool:
    """判断融资结果是否属于该 profile 关注的行业/赛道。

    融资结果的 brand 字段格式为 "[融资] AI大模型" 或 "[融资] 机器人/具身智能"。
    从中提取赛道名，与 profile 的行业列表和融资赛道列表做模糊匹配。
    """
    brand = result.get("brand", "")
    # 提取赛道名：去掉 "[融资]" 前缀
    track_name = brand.replace("[融资]", "").strip()
    if not track_name:
        return True  # 无法判断时保留

    # 精确匹配
    if track_name in allowed_sectors:
        return True

    # 模糊匹配：赛道名包含在 allowed 中，或 allowed 包含在赛道名中
    for sector in allowed_sectors:
        if sector in track_name or track_name in sector:
            return True

    return False


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

    # 周规则：决定今天搜索什么
    schedule = get_schedule_flags()
    include_industry = schedule["include_industry"]
    include_endorsement = schedule["include_endorsement"]

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

    # Phase 1b: 融资专项预取（仅周一、周三运行）
    pre_fr_results = []
    if include_industry:
        print(f"\n[多档案模式] 融资专项搜索...")
        fr_config = shared_config.get("fundraising", {})
        pre_fr_results = run_fundraising_search(fr_config)
        print(f"  融资搜索结果: {len(pre_fr_results)} 条")
    else:
        print(f"\n[多档案模式] 今日跳过融资专项搜索（非周一/周三）")

    # Phase 1c: 代言人速报（仅周三运行）
    all_endorsements = []
    if include_endorsement:
        all_endorsements = prompt_and_fetch_endorsements(profiles)
    else:
        print(f"[多档案模式] 今日跳过代言人速报（非周三）")

    # Phase 2: 按档案独立处理（并发）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _run_one_profile(profile: dict) -> tuple:
        pname = profile.get("name", "default")
        set_profile(pname)
        profile_endorsements = match_endorsements_to_profile(all_endorsements, profile) if all_endorsements else []
        output_dir = os.path.join(DEFAULT_AI_OUTPUT_DIR, pname)
        report = run_single_profile_pipeline(
            profile_name=pname,
            shared_config=shared_config,
            search_pool=search_pool,
            include_industry=include_industry,
            output_dir=output_dir,
            dry_run=dry_run,
            fundraising_results_raw=pre_fr_results if include_industry else None,
            endorsement_items=profile_endorsements,
        )
        return pname, report

    reports = []
    # 最多3个档案并发跑，MiniMax API 承受得住
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_run_one_profile, profile): profile.get("name") for profile in profiles}
        for future in as_completed(futures):
            pname = futures[future]
            try:
                _, report = future.result()
                reports.append(report)
            except Exception as e:
                print(f"  [档案异常] {pname}: {e}")
                reports.append("")
                traceback.print_exc()

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
                        set_profile(d)
                        save_seen_urls({})
                        save_seen_events([])
                        print(f"[RESET] 已清空档案 {d} 的去重记录")
        elif args.profile:
            from scripts.dedup import save_seen_urls, save_seen_events
            set_profile(args.profile)
            save_seen_urls({})
            save_seen_events([])
            print(f"[RESET] 已清空档案 {args.profile} 的去重记录")
        else:
            # 清空默认 data/profiles/default
            from scripts.dedup import save_seen_urls, save_seen_events
            set_profile("default")
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
        # 防止路径穿越攻击，强制只取 basename
        safe_name = os.path.basename(str(profile_name)) if profile_name else "default"
        profile_path = os.path.join(PROJECT_ROOT, "profiles", f"{safe_name}.yaml")
        if not os.path.exists(profile_path):
            print(f"[错误] 档案不存在: {profile_path}")
            return
        shared_config = load_config(args.config)
        output_dir = args.output or os.path.join(DEFAULT_AI_OUTPUT_DIR, profile_name)
        run_single_profile_pipeline(
            profile_name=profile_name,
            shared_config=shared_config,
            include_industry=True if not args.no_industry else False,
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
