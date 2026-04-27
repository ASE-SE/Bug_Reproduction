"""
Dataset loader for python-swe-bench.

主路径：从 Hugging Face 拉取 SWE-bench / SWT-Bench 数据集
（默认 princeton-nlp/SWE-bench_Lite），instance_id 与 SWT-Bench
官方 Docker 镜像一一对应，是后续接入评测的前提。

回退路径：HF 不可用时改读本地 Parquet。
"""

import json
from typing import Any

from datasets import load_dataset

from constants import (
    HF_DATASET_NAME,
    HF_DATASET_SPLIT,
    ISSUES_FILE,
    LOCAL_PARQUET_PATH,
    logger,
)


def _normalize_entry(item: dict[str, Any]) -> dict[str, str] | None:
    """
    将原始数据集条目归一化为主流程可消费的最小结构。

    SWE-bench 系列数据集没有 language 字段（默认全是 Python），所以这里只在
    字段存在时做过滤；对自建 Parquet 才会真正生效。
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


def _load_from_huggingface() -> list[dict[str, Any]]:
    logger.info("Loading dataset from Hugging Face: %s [%s]", HF_DATASET_NAME, HF_DATASET_SPLIT)
    ds = load_dataset(HF_DATASET_NAME, split=HF_DATASET_SPLIT)
    return [dict(item) for item in ds]


def _load_from_local_parquet() -> list[dict[str, Any]]:
    logger.info("Loading dataset from local Parquet: %s", LOCAL_PARQUET_PATH)
    ds = load_dataset("parquet", data_files={"test": LOCAL_PARQUET_PATH}, split="test")
    return [dict(item) for item in ds]


def fetch_and_clean_dataset(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    优先级：缓存（issues_commits.json） → Hugging Face → 本地 Parquet。
    force_refresh=True 时跳过缓存，重新拉取并覆盖。
    """
    if ISSUES_FILE.exists() and not force_refresh:
        logger.info("Using cached dataset at %s", ISSUES_FILE)
        with ISSUES_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)

    raw_items: list[dict[str, Any]]
    try:
        raw_items = _load_from_huggingface()
    except Exception as e:
        logger.warning("Hugging Face load failed (%s); falling back to local Parquet.", e)
        raw_items = _load_from_local_parquet()

    processed_data: list[dict[str, Any]] = []
    skipped = 0
    for item in raw_items:
        normalized = _normalize_entry(item)
        if normalized is None:
            skipped += 1
            continue
        processed_data.append(normalized)

    if not processed_data:
        raise ValueError("No valid Python entries found in dataset.")

    with ISSUES_FILE.open("w", encoding="utf-8") as f:
        json.dump(processed_data, f, indent=2, ensure_ascii=False)

    logger.info(
        "Dataset normalized: total=%s, skipped=%s, kept=%s",
        len(raw_items),
        skipped,
        len(processed_data),
    )
    return processed_data


def download_and_process_dataset(force_refresh: bool = False) -> list[dict[str, Any]]:
    return fetch_and_clean_dataset(force_refresh=force_refresh)


if __name__ == "__main__":
    data = fetch_and_clean_dataset()
    logger.info("Success! Total Python records extracted: %s", len(data))
