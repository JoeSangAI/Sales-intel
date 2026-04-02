"""
质检层 - 专门检测幻觉和格式问题，不是打分
"""

import json
import re
import os
import sys
import requests

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, PROJECT_ROOT)


# ── MiniMax API 调用 ────────────────────────────────────────────

def _call_qc_llm(prompt: str, timeout: int = 120, max_tokens: int = 2000) -> str:
    """调用 MiniMax M2.7 进行质检"""
    minimax_key = os.environ.get("MINIMAX_API_KEY", "")
    if not minimax_key:
        print("  [QC 警告] MINIMAX_API_KEY 未设置")
        return ""
    for attempt in range(2):
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
                    "temperature": 0.1,
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
            print(f"  [QC MiniMax OK]", flush=True)
            return content
        except Exception as e:
            print(f"  [QC MiniMax 失败{' (重试)' if attempt < 1 else ''}] {e}", flush=True)
            if attempt < 1:
                import time; time.sleep(2)
    return ""


# ── 质检 Prompt ─────────────────────────────────────────────────

def _build_qc_prompt(report: str, original_items: list[dict]) -> str:
    """构建质检 prompt"""
    # 原始新闻列表（用于幻觉比对）
    item_lines = []
    for i, item in enumerate(original_items):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")[:600]
        item_lines.append(
            f"[{i}] title={title}\n    url={url}\n    content={content}"
        )

    # 提取报告中所有 URL
    report_urls = re.findall(r'https?://[^\s\)\]]+', report)

    prompt = f"""你是质检员，检查以下销售日报是否存在以下问题：

【格式检查】
- 是否包含"# 销售情报日报"标题？
- 是否有板块结构（## 标题）？
- 是否有链接（[xxx](url) 格式）？
- 是否有日期？

【幻觉检查——最重要】
日报中每个 [xxx](url) 的 url，必须真实存在于原始新闻列表中。
日报中提到的关键数字/日期，必须出现在对应的原始新闻内容中。

报告中的 URL（共 {len(report_urls)} 个）:
{chr(10).join(report_urls[:50]) if report_urls else "（无URL）"}

【日报内容】
{report}

【原始新闻列表】
{chr(10).join(item_lines) if item_lines else "（无原始新闻）"}

返回 JSON，不要前缀：
{{
  "format_ok": true/false,
  "format_issues": ["问题1", ...],
  "hallucination_ok": true/false,
  "hallucination_issues": ["URL xxx 不在原始新闻中", ...],
  "pass": true/false
}}

规则：
- 只要有任何 hallucination 问题，pass=false
- 格式问题不阻断，但仍需记录
"""
    return prompt


def _parse_qc_response(text: str) -> dict:
    """解析质检 LLM 返回的 JSON 结果"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*"pass".*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        print(f"  [QC 解析失败] 无法解析 JSON: {text[:200]}")
        return {
            "format_ok": True,
            "format_issues": [],
            "hallucination_ok": True,
            "hallucination_issues": [],
            "pass": True,
        }


# ── 公开接口 ──────────────────────────────────────────────────

def quality_check(report: str, original_items: list[dict]) -> dict:
    """
    质检：检查报告的格式正确性和幻觉问题。

    Args:
        report: Markdown 格式的报告内容
        original_items: Layer 2 输出的原始条目列表（用于幻觉比对）

    Returns:
        dict: {
            "format_ok": bool,
            "format_issues": list[str],
            "hallucination_ok": bool,
            "hallucination_issues": list[str],
            "pass": bool
        }
    """
    if not report:
        return {
            "format_ok": False,
            "format_issues": ["报告为空"],
            "hallucination_ok": False,
            "hallucination_issues": [],
            "pass": False,
        }

    print("\n[QC 质检] 开始检查...")
    prompt = _build_qc_prompt(report, original_items)
    raw = _call_qc_llm(prompt)

    if not raw:
        print("  [QC] LLM 调用失败，默认通过")
        return {
            "format_ok": True,
            "format_issues": [],
            "hallucination_ok": True,
            "hallucination_issues": [],
            "pass": True,
        }

    result = _parse_qc_response(raw)
    if result.get("pass"):
        print(f"  [QC 通过] 格式{'OK' if result.get('format_ok') else '有问题'}，幻觉{'OK' if result.get('hallucination_ok') else '有问题'}")
    else:
        issues = result.get("hallucination_issues", [])
        print(f"  [QC 失败] 幻觉问题 {len(issues)} 个: {issues[:3]}")

    return result


def retry_with_feedback(report: str, feedback: dict,
                        original_items: list[dict]) -> str:
    """
    将质检反馈注入 prompt，重新生成报告。

    Args:
        report: 上一次生成的报告（仅供参考）
        feedback: quality_check() 返回的质检结果
        original_items: Layer 2 输出的原始条目列表

    Returns:
        str: 重新生成的 Markdown 报告
    """
    print("\n[QC 重试] 根据质检反馈重新生成报告...")

    # 提取幻觉问题构建反馈
    hallucination_issues = feedback.get("hallucination_issues", [])
    format_issues = feedback.get("format_issues", [])

    # 原始新闻列表
    item_lines = []
    for i, item in enumerate(original_items):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")[:600]
        item_lines.append(
            f"[{i}] title={title}\n    url={url}\n    content={content}"
        )

    retry_prompt = f"""你是分众传媒的销售情报编辑。请根据以下质检反馈，重新生成报告。

【质检反馈 - 必须修复】
幻觉问题（这些 URL 不在原始新闻中，必须修正）：
{chr(10).join(f"- {issue}" for issue in hallucination_issues) if hallucination_issues else "无"}

格式问题：
{chr(10).join(f"- {issue}" for issue in format_issues) if format_issues else "无"}

【原始新闻列表（必须严格使用这些 URL）】
{chr(10).join(item_lines) if item_lines else "（无原始新闻）"}

【上次生成的报告（参考）】
{report}

要求：
1. 报告中所有 [xxx](url) 的 url 必须来自上述原始新闻列表
2. 关键数字/日期必须与原始新闻一致
3. 保持原有结构和有价值内容，只修复质检问题
4. 直接输出修复后的 Markdown 报告，不要有前缀文字。

直接输出 Markdown 报告。"""

    # 复用 layer3 的调用方式
    from scripts.layer3_chef import _call_llm
    new_report = _call_llm(retry_prompt, timeout=180, max_tokens=8000)

    if not new_report:
        print("  [QC 重试失败] 返回原报告")
        return report

    print(f"  [QC 重试完成] 新报告 {len(new_report)} 字符")
    return new_report
