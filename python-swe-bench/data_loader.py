"""
Dataset loader for python-swe-bench.

Produces a normalized `issues_commits.json` with Python-only entries.
"""

import json
from typing import Any

from datasets import load_dataset

# 这里引入修改后的 LOCAL_PARQUET_PATH
from constants import LOCAL_PARQUET_PATH, ISSUES_FILE, logger


def _normalize_entry(item: dict[str, Any]) -> dict[str, str] | None:
    """
    将原始数据集条目归一化为主流程可消费的最小结构。

    过滤策略：
    - 仅保留 language 包含 python 的样本；
    - 必须有 instance_id/repo/base_commit/problem_description。
    """
    lang = str(item.get("language", "")).strip().lower()
    if lang and "python" not in lang:
        return None

    instance_id = item.get("instance_id")
    repo_name = item.get("repo")
    base_commit = item.get("base_commit")
    problem_description = (
        item.get("problem_statement")
        or item.get("problem_description")
        or item.get("description")
        or item.get("issue_body", "")
    )
    if not all([instance_id, repo_name, base_commit, problem_description]):
        return None

    fail_to_pass = item.get("FAIL_TO_PASS", [])
    if isinstance(fail_to_pass, str):
        try:
            fail_to_pass = json.loads(fail_to_pass)
        except Exception:
            fail_to_pass = [x.strip() for x in fail_to_pass.split(",") if x.strip()]

    return {
        "instance_id": str(instance_id).strip(),
        "repo": str(repo_name).strip(),
        "base_commit": str(base_commit).strip(),
        "problem_description": str(problem_description).strip(),
        "hints_text": str(item.get("hints_text", "")).strip(),
        "test_patch": str(item.get("test_patch", "")).strip(),
        "fail_to_pass": fail_to_pass if isinstance(fail_to_pass, list) else [],
        "language": "python",
    }


def fetch_and_clean_dataset(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    获取并缓存 Python 样本：
    - 默认优先读本地 issues_commits.json；
    - force_refresh=True 时重新从本地 Parquet 拉取并覆盖缓存。
    """
    if ISSUES_FILE.exists() and not force_refresh:
        logger.info("Using cached dataset at %s", ISSUES_FILE)
        with ISSUES_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)

    # 【修改点】加载本地 Parquet 文件
    logger.info("Loading dataset from local Parquet: %s", LOCAL_PARQUET_PATH)
    # 使用 parquet 引擎，并通过 data_files 指定路径，映射到 "test" split
    ds = load_dataset("parquet", data_files={"test": LOCAL_PARQUET_PATH}, split="test")

    processed_data: list[dict[str, Any]] = []
    total = 0
    skipped = 0
    for item in ds:
        total += 1
        normalized = _normalize_entry(dict(item))
        if normalized is None:
            skipped += 1
            continue
        processed_data.append(normalized)

    if not processed_data:
        raise ValueError("No valid Python entries found in dataset.")

    with ISSUES_FILE.open("w", encoding="utf-8") as f:
        json.dump(processed_data, f, indent=2, ensure_ascii=False)

    logger.info("Dataset normalized: total=%s, skipped=%s, python_items=%s", total, skipped, len(processed_data))
    return processed_data


def download_and_process_dataset(force_refresh: bool = False) -> list[dict[str, Any]]:
    return fetch_and_clean_dataset(force_refresh=force_refresh)


if __name__ == "__main__":
    data = fetch_and_clean_dataset()
    logger.info("Success! Total Python records extracted: %s", len(data))