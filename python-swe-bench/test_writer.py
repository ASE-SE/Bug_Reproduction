"""
将 LLM 生成的测试代码包装成 SWT-Bench / SWE-bench harness 可消费的
unified diff（git patch），以及把 predictions 写成 JSONL。

历史逻辑（直接落盘到 repo_dir）已不需要——SWT-Bench 在 Docker 内
处理 clone / apply / run，宿主机只负责生产 predictions。
"""

import json
from pathlib import Path
from typing import Any, Iterable

from constants import logger


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
    # 末尾的 split 会产出一个空字符串元素（因为以 \n 结尾），剔除
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
    return record


def write_predictions_jsonl(records: Iterable[dict[str, Any]], output_path: Path) -> int:
    """落盘 predictions.jsonl，返回写入条数。"""
    count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            count += 1
    logger.info("Wrote %s predictions to %s", count, output_path)
    return count
