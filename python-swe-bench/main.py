"""
main.py
Python 专用主控调度器（SWT-Bench 接入版）。

流程：
1) 从 Hugging Face 拉 SWE-bench / SWT-Bench 标准数据集；
2) 让 LLM 为每条 instance 生成 pytest 触发测试；
3) 把测试代码包装成 unified diff，组成 predictions JSONL；
4) 把 predictions.jsonl 交给 SWT-Bench harness 跑（参见仓库 README）。

宿主机不再做 clone / venv / pytest——这些由 SWT-Bench 在 Docker 内完成，
彻底消除"本地环境差异 vs 测试用例本身错"的歧义。
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

from api_client import call_api_generate_python_test
from constants import (
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    EMBEDDED_API_KEY,
    METHOD_NAME,
    PREDICTIONS_FILE,
    RESULTS_DIR,
    logger,
)
from data_loader import fetch_and_clean_dataset
from test_writer import build_prediction, write_predictions_jsonl


def _process_one(entry: dict[str, Any], api_url: str, api_key: str, api_model: str,
                 method_name: str) -> dict[str, Any] | None:
    """对单条 instance 调 LLM 并组装为 prediction 记录。失败返回 None。"""
    instance_id = str(entry["instance_id"])
    api_res = call_api_generate_python_test(
        bug_entry=entry,
        api_url=api_url,
        api_key=api_key,
        api_model=api_model,
    )
    if not api_res.get("ok"):
        logger.error("Generation error for %s: %s", instance_id, api_res.get("error"))
        return None

    data = api_res["data"]
    test_code = data.get("test_code", "")
    if not test_code.strip():
        logger.warning("Empty test_code for %s, skip.", instance_id)
        return None

    return build_prediction(
        instance_id=instance_id,
        test_code=test_code,
        method_name=method_name,
        path_hint=data.get("path_hint"),
        file_name=data.get("file_name"),
        full_output=json.dumps(data, ensure_ascii=False),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Python SWT-Bench predictions generator")
    parser.add_argument("--limit", type=int, default=None, help="Max tasks to process")
    parser.add_argument("--instance-id", type=str, default=None, help="Only run one instance_id")
    parser.add_argument("--force-refresh", action="store_true", help="Re-pull dataset, ignore cache")
    parser.add_argument("--api-url", type=str, default=DEFAULT_API_URL)
    parser.add_argument("--api-model", type=str, default=DEFAULT_API_MODEL)
    parser.add_argument("--method-name", type=str, default=METHOD_NAME,
                        help="Identifier written into predictions.model_name_or_path")
    parser.add_argument("--output", type=str, default=str(PREDICTIONS_FILE),
                        help="Path to write predictions.jsonl (SWT-Bench input)")
    args = parser.parse_args()

    api_key = os.getenv("API_KEY") or EMBEDDED_API_KEY
    if not api_key:
        logger.error("API_KEY missing.")
        return

    try:
        entries = fetch_and_clean_dataset(force_refresh=args.force_refresh)
    except Exception as e:
        logger.error("Failed to load dataset: %s", e)
        return

    predictions: list[dict[str, Any]] = []
    processed = 0

    try:
        for idx, entry in enumerate(entries):
            if args.limit is not None and processed >= args.limit:
                logger.info("Reached limit of %s tasks. Stopping.", args.limit)
                break

            instance_id = str(entry.get("instance_id", f"idx-{idx}"))
            if args.instance_id and instance_id != args.instance_id:
                continue

            logger.info("=== [%s/%s] %s (%s) ===", idx + 1, len(entries), instance_id, entry.get("repo"))

            record = _process_one(entry, args.api_url, api_key, args.api_model, args.method_name)
            if record is None:
                continue
            predictions.append(record)

            # 单条留档，方便排查 LLM 输出
            per_file = RESULTS_DIR / f"{instance_id}.json"
            with per_file.open("w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            processed += 1

    except KeyboardInterrupt:
        logger.warning("\nProcess interrupted by user (Ctrl+C). Saving progress...")
    except Exception as e:
        logger.error("Unexpected error in main loop: %s", e)
    finally:
        if predictions:
            write_predictions_jsonl(predictions, Path(args.output))
            logger.info(
                "Done. %s predictions ready. Next step: feed %s to SWT-Bench harness.",
                len(predictions), args.output,
            )
            logger.info(
                "Example: python -m src.main --dataset_name princeton-nlp/SWE-bench_Lite "
                "--predictions_path %s --filter_swt --max_workers 4 --run_id my_run",
                args.output,
            )
        else:
            logger.info("No predictions produced. Exiting.")


if __name__ == "__main__":
    main()
