"""
解析 SWT-Bench harness 的输出日志，把每条 instance 的执行结果合并到我们
results/instances/<instance_id>.json 里，得到"原始缺陷报告 + 生成测试 +
执行输出 + 复现类型"四件套，方便人工对比。

复现类型分类（reproduction_status）：
- reproduced          : F→P，buggy 状态 fail，gold patch 后 pass（理想）
- not_reproduced      : P→P，测试在 buggy 状态就 pass，没触发 bug
- runtime_error       : 测试运行时抛异常（非 assert），未真正测到目标
- syntax_error        : Python 语法错（生成的测试编译失败）
- import_error        : import / 模块缺失，多半是 LLM 写错路径
- patch_apply_failed  : SWT-Bench 无法把 diff apply 到仓库
- pending             : harness 还没跑这条
- unknown             : 上面规则都没命中，需要看原始日志
"""

import json
import re
from pathlib import Path
from typing import Any

from constants import INSTANCES_DIR, SWT_BENCH_WORKDIR, logger


# 在 test_output.txt 里识别状态的正则。顺序很重要：先判断更具体的再判断模糊的。
_SYNTAX_PAT = re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")
_IMPORT_PAT = re.compile(r"\b(ModuleNotFoundError|ImportError):")
_RUNTIME_PAT = re.compile(r"\b(RuntimeError|TypeError|AttributeError|KeyError|ValueError|ZeroDivisionError)\b")


def _classify_from_report(report: dict[str, Any]) -> str | None:
    """
    SWT-Bench 的 report.json 字段在不同版本里有差异，这里做尽量兼容的解析：
    - 常见字段：applicability/applicable, resolved, tests_status, fail_to_pass.success/...
    - 命中其一即返回，否则 None。
    """
    if not isinstance(report, dict):
        return None
    instance = next(iter(report.values()), report) if report and isinstance(next(iter(report.values()), None), dict) else report

    if isinstance(instance, dict):
        if instance.get("patch_successfully_applied") is False:
            return "patch_apply_failed"
        if instance.get("applicability") is False or instance.get("applicable") is False:
            return "patch_apply_failed"
        if instance.get("resolved") is True:
            return "reproduced"
        ts = instance.get("tests_status") or {}
        f2p = ts.get("FAIL_TO_PASS") if isinstance(ts, dict) else None
        if isinstance(f2p, dict):
            if f2p.get("success"):
                return "reproduced"
            if f2p.get("failure") and not f2p.get("success"):
                return "not_reproduced"
    return None


def _classify_from_output(text: str) -> str:
    if _SYNTAX_PAT.search(text):
        return "syntax_error"
    if _IMPORT_PAT.search(text):
        return "import_error"
    if "Patch Apply Failed" in text or "patch does not apply" in text:
        return "patch_apply_failed"
    if _RUNTIME_PAT.search(text) and " ERROR " in text:
        return "runtime_error"
    if "passed" in text and "failed" not in text:
        return "not_reproduced"
    if "failed" in text:
        # 没有 report.json 时只能粗略归类
        return "runtime_error"
    return "unknown"


def classify_status(report: dict[str, Any] | None, test_output: str) -> str:
    s = _classify_from_report(report or {})
    if s:
        return s
    return _classify_from_output(test_output or "")


def _read_text_safe(path: Path, limit: int = 200_000) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
        return data[:limit]
    except Exception:
        return ""


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _instance_log_dir(method_name: str, run_id: str, instance_id: str) -> Path:
    """
    SWT-Bench 默认输出布局（基于其 README 与 SWE-bench 同源）：
      <workdir>/run_instance_swt_logs/<method>/<run_id>/<instance_id>/
    """
    return SWT_BENCH_WORKDIR / "run_instance_swt_logs" / method_name / run_id / instance_id


def collect_one(
    entry: dict[str, Any],
    initial_record: dict[str, Any],
    method_name: str,
    run_id: str,
) -> dict[str, Any]:
    """合并单条 instance 的：缺陷报告 + 生成测试 + 执行输出 + 复现类型。"""
    instance_id = entry["instance_id"]
    log_dir = _instance_log_dir(method_name, run_id, instance_id)

    report_path = log_dir / "report.json"
    test_out_path = log_dir / "test_output.txt"
    patch_path = log_dir / "patch.diff"

    report = _read_json_safe(report_path)
    test_output = _read_text_safe(test_out_path)
    applied_patch = _read_text_safe(patch_path) if patch_path.exists() else None

    if not report and not test_output:
        status = "pending"
    else:
        status = classify_status(report, test_output)

    return {
        "instance_id": instance_id,
        "repo": entry["repo"],
        "base_commit": entry["base_commit"],
        "reproduction_status": status,
        "original_bug_report": {
            "problem_description": entry.get("problem_description", ""),
            "hints_text": entry.get("hints_text", ""),
            "fail_to_pass": entry.get("fail_to_pass", []),
        },
        "generated_test": {
            "method_name": method_name,
            "patch": initial_record.get("model_patch", ""),
            "raw_llm_output": initial_record.get("full_output"),
        },
        "execution": {
            "harness_run_id": run_id,
            "harness_log_dir": str(log_dir),
            "applied_patch_present": applied_patch is not None,
            "test_output": test_output,
            "harness_report": report,
        },
    }


def collect_all(
    entries: list[dict[str, Any]],
    initial_records: dict[str, dict[str, Any]],
    method_name: str,
    run_id: str,
) -> dict[str, int]:
    """对全部 entries 跑 collect_one，写到 results/instances/，并返回状态分布。"""
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for entry in entries:
        instance_id = entry["instance_id"]
        initial = initial_records.get(instance_id)
        if not initial:
            logger.warning("Skip %s: no LLM record found in results/instances/", instance_id)
            continue
        merged = collect_one(entry, initial, method_name, run_id)
        out_path = INSTANCES_DIR / f"{instance_id}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        counts[merged["reproduction_status"]] = counts.get(merged["reproduction_status"], 0) + 1
    return counts
