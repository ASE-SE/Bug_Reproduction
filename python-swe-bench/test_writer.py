"""
将 LLM 生成的测试代码包装成 SWT-Bench / SWE-bench harness 可消费的
unified diff（git patch），以及把 predictions 写成 JSONL。

每条 instance 还会落一个"初始档"到 results/instances/<instance_id>.json，
里面包含原始缺陷报告 + 生成测试代码（执行结果在 collect 阶段补齐）。
"""

import json
from pathlib import Path
from typing import Any, Iterable

from constants import INSTANCES_DIR, logger


def _normalize_rel_path(path_hint: str | None, file_name: str | None, instance_id: str) -> str:
    """决定测试文件在仓库内的相对路径。优先用 path_hint，否则放 tests/ 下。"""
    if path_hint:
        rel = path_hint.lstrip("/").lstrip("./")
    else:
        name = file_name or f"test_generated_trigger_{instance_id}.py"
        rel = f"tests/{name}"
    if not rel.endswith(".py"):
        rel += ".py"
    return rel


def build_new_file_patch(rel_path: str, file_content: str) -> str:
    """
    生成新增文件的 unified diff。git apply 接受没有 index 行的形式。

    注意点：
    - 文件末尾必须有换行，否则要追加 `\\ No newline at end of file`，统一补 \\n 最稳；
    - hunk header 中的行数 = 实际加号行数；
    - 路径以 a/ b/ 前缀，符合 git diff 习惯。
    """
    if not file_content.endswith("\n"):
        file_content += "\n"
    lines = file_content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    body = "".join(f"+{l}\n" for l in lines)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}"
    )


def build_prediction(
    instance_id: str,
    test_code: str,
    method_name: str,
    path_hint: str | None = None,
    file_name: str | None = None,
    full_output: str | None = None,
) -> dict[str, Any]:
    """生成一条 SWT-Bench predictions JSONL 记录。"""
    rel_path = _normalize_rel_path(path_hint, file_name, instance_id)
    patch = build_new_file_patch(rel_path, test_code)
    record: dict[str, Any] = {
        "instance_id": instance_id,
        "model_name_or_path": method_name,
        "model_patch": patch,
    }
    if full_output is not None:
        record["full_output"] = full_output
    record["_test_rel_path"] = rel_path
    record["_test_code"] = test_code
    return record


def write_initial_instance_file(entry: dict[str, Any], record: dict[str, Any]) -> Path:
    """
    在 results/instances/<instance_id>.json 落一个初始档：
    包含原始缺陷报告 + 生成的测试代码 + 待补齐的执行结果占位。
    collect 阶段会把执行输出和 reproduction_status 合并进来。
    """
    instance_id = entry["instance_id"]
    out = {
        "instance_id": instance_id,
        "repo": entry["repo"],
        "base_commit": entry["base_commit"],
        "reproduction_status": "pending",
        "original_bug_report": {
            "problem_description": entry.get("problem_description", ""),
            "hints_text": entry.get("hints_text", ""),
            "fail_to_pass": entry.get("fail_to_pass", []),
        },
        "generated_test": {
            "method_name": record["model_name_or_path"],
            "file_path": record.get("_test_rel_path"),
            "code": record.get("_test_code"),
            "patch": record["model_patch"],
            "raw_llm_output": record.get("full_output"),
        },
        "execution": None,
    }
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    path = INSTANCES_DIR / f"{instance_id}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return path


def write_predictions_jsonl(records: Iterable[dict[str, Any]], output_path: Path) -> int:
    """落盘 predictions.jsonl，返回写入条数。剥离内部 _test_* 字段。"""
    count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for rec in records:
            clean = {k: v for k, v in rec.items() if not k.startswith("_")}
            f.write(json.dumps(clean, ensure_ascii=False) + "\n")
            count += 1
    logger.info("Wrote %s predictions to %s", count, output_path)
    return count


def load_initial_records() -> dict[str, dict[str, Any]]:
    """读取 results/instances/ 下所有初始档，给 collect 阶段查 LLM 测试用。"""
    out: dict[str, dict[str, Any]] = {}
    if not INSTANCES_DIR.exists():
        return out
    for p in INSTANCES_DIR.glob("*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            iid = data.get("instance_id") or p.stem
            out[iid] = {
                "model_patch": data.get("generated_test", {}).get("patch", ""),
                "full_output": data.get("generated_test", {}).get("raw_llm_output"),
            }
        except Exception:
            continue
    return out
