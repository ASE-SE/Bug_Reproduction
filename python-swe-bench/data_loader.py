"""
Dataset loader for python-swe-bench.

从 Hugging Face 拉 SWE-bench / SWT-Bench 数据集（默认
princeton-nlp/SWE-bench_Lite），缓存到 data/<dataset>__<split>.json。

支持按 repo 过滤（--repo sympy/sympy），方便针对单仓库快速验证。
"""

import json
from pathlib import Path
from typing import Any

from datasets import load_dataset

from constants import (
    DATA_DIR,
    HF_DATASET_NAME,
    HF_DATASET_SPLIT,
    logger,
)


def _safe_name(s: str) -> str:
    return s.replace("/", "_").replace(":", "_")


def _cache_path(dataset_name: str, split: str) -> Path:
    return DATA_DIR / f"{_safe_name(dataset_name)}__{split}.json"


def _normalize_entry(item: dict[str, Any]) -> dict[str, str] | None:
    """
    将原始数据集条目归一化为主流程可消费的最小结构。

    SWE-bench 系列没有 language 字段（默认全是 Python），所以这里只在
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


def _load_from_huggingface(dataset_name: str, split: str) -> list[dict[str, Any]]:
    logger.info("Loading dataset from Hugging Face: %s [%s]", dataset_name, split)
    ds = load_dataset(dataset_name, split=split)
    return [dict(item) for item in ds]


def fetch_and_clean_dataset(
    force_refresh: bool = False,
    dataset_name: str | None = None,
    split: str | None = None,
) -> list[dict[str, Any]]:
    """
    优先级：data/<dataset>__<split>.json 缓存 → Hugging Face。
    force_refresh=True 时跳过缓存，重新拉取并覆盖。
    """
    name = dataset_name or HF_DATASET_NAME
    sp = split or HF_DATASET_SPLIT
    cache = _cache_path(name, sp)

    if cache.exists() and not force_refresh:
        logger.info("Using cached dataset at %s", cache)
        with cache.open("r", encoding="utf-8") as f:
            return json.load(f)

    raw_items = _load_from_huggingface(name, sp)

    processed: list[dict[str, Any]] = []
    skipped = 0
    for item in raw_items:
        normalized = _normalize_entry(item)
        if normalized is None:
            skipped += 1
            continue
        processed.append(normalized)

    if not processed:
        raise ValueError("No valid Python entries found in dataset.")

    with cache.open("w", encoding="utf-8") as f:
        json.dump(processed, f, indent=2, ensure_ascii=False)

    logger.info(
        "Dataset cached to %s: total=%s, skipped=%s, kept=%s",
        cache, len(raw_items), skipped, len(processed),
    )
    return processed


def filter_by_repo(entries: list[dict[str, Any]], repo: str) -> list[dict[str, Any]]:
    """按 repo 全名过滤，例如 'sympy/sympy'。大小写不敏感。"""
    target = repo.strip().lower()
    if not target:
        return entries
    out = [e for e in entries if str(e.get("repo", "")).lower() == target]
    logger.info("Filter repo=%s: %s -> %s entries", repo, len(entries), len(out))
    return out


def list_repos(entries: list[dict[str, Any]]) -> dict[str, int]:
    """返回数据集中每个 repo 的 instance 数，用于 --list-repos。"""
    counts: dict[str, int] = {}
    for e in entries:
        r = str(e.get("repo", ""))
        counts[r] = counts.get(r, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    data = fetch_and_clean_dataset()
    logger.info("Total Python records: %s", len(data))
    for repo, n in list_repos(data).items():
        logger.info("  %-40s %d", repo, n)
