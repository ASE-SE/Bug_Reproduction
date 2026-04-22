"""
main.py
Python 专用主控调度器。

目标：
1) 读取本地 Parquet 并标准化 Python SWE-bench 数据；
2) checkout 到 base_commit；
3) 让 LLM 生成 pytest 触发测试；
4) 写入测试文件并执行；
5) 将每条实例结果落盘到 results/{instance_id}.json。
"""

import argparse
import json
import os
from pathlib import Path

from api_client import call_api_generate_python_test
from build_and_fix import attempt_python_build
from constants import (
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    EMBEDDED_API_KEY,
    REPOS_DIR,
    RESULTS_DIR,
    logger,
)
from data_loader import fetch_and_clean_dataset
from python_runner import run_single_python_test
from repo_utils import clone_or_update_repo, detect_python_project_layout
from test_writer import write_python_test


def main() -> None:
    # CLI 参数设计偏向“先小规模验证，再批量跑”
    parser = argparse.ArgumentParser(description="Python-only SWE-bench trigger test pipeline")
    parser.add_argument("--limit", type=int, default=None, help="Max tasks to process")
    parser.add_argument("--instance-id", type=str, default=None, help="Only run one instance_id")
    parser.add_argument("--force-refresh", action="store_true", help="Re-parse local parquet and overwrite cache")
    parser.add_argument("--api-url", type=str, default=DEFAULT_API_URL, help="LLM API URL")
    parser.add_argument("--api-model", type=str, default=DEFAULT_API_MODEL, help="LLM model name")
    args = parser.parse_args()

    api_key = os.getenv("API_KEY") or EMBEDDED_API_KEY
    if not api_key:
        logger.error("API_KEY missing.")
        return

    # 1) 数据准备（优先读取本地缓存，强制刷新则重新解析 Parquet）
    try:
        entries = fetch_and_clean_dataset(force_refresh=args.force_refresh)
    except Exception as e:
        logger.error("Failed to load dataset: %s", e)
        return

    summary: list[dict] = []
    processed = 0

    try:
        for idx, entry in enumerate(entries):
            if args.limit is not None and processed >= args.limit:
                logger.info("Reached limit of %s tasks. Stopping.", args.limit)
                break

            instance_id = str(entry.get("instance_id", f"idx-{idx}"))
            if args.instance_id and instance_id != args.instance_id:
                continue

            repo = entry.get("repo")
            commit = entry.get("base_commit")
            if not repo or not commit:
                logger.warning("Skip %s: missing repo/base_commit", instance_id)
                continue

            logger.info("=== Processing [%s/%s] %s (%s) ===", idx + 1, len(entries), instance_id, repo)

            result_path = RESULTS_DIR / f"{instance_id}.json"
            if result_path.exists():
                logger.info("Skip %s: result already exists", instance_id)
                # 即使跳过，也把它加入 summary，方便最终统计
                with result_path.open("r", encoding="utf-8") as f:
                    summary.append(json.load(f))
                processed += 1
                continue

            # 2) 仓库准备：clone/fetch/checkout 到 base_commit
            repo_dir = REPOS_DIR / str(repo).replace("/", "_")
            if not clone_or_update_repo(str(repo), repo_dir, str(commit)):
                continue

            # 3) 探测项目布局并预热环境（venv + install）
            project_type = detect_python_project_layout(repo_dir)
            build_res = attempt_python_build(repo_dir, project_type)
            if not build_res["success"]:
                logger.warning("Prep failed for %s, generation continues.", instance_id)

            # 4) 调用 LLM 生成 pytest 触发测试
            api_res = call_api_generate_python_test(
                bug_entry=entry,
                api_url=args.api_url,
                api_key=api_key,
                api_model=args.api_model,
            )
            if not api_res.get("ok"):
                logger.error("Generation error for %s: %s", instance_id, api_res.get("error"))
                continue

            data = api_res["data"]
            test_code = data.get("test_code", "")
            path_hint = data.get("path_hint")
            test_file_name = data.get("file_name")

            # 5) 落盘测试代码
            written_file = write_python_test(
                repo_dir=repo_dir,
                test_code=test_code,
                file_name=test_file_name,
                path_hint=path_hint,
                instance_id=instance_id,
            )
            if not written_file:
                continue

            # 6) 执行单测并判断状态
            run_res = run_single_python_test(repo_dir, written_file, project_type)
            status = run_res.get("status", "unknown")
            logger.info("Status for %s: %s", instance_id, status)

            # 7) 保存实例级结果
            result = {
                "instance_id": instance_id,
                "repo": repo,
                "base_commit": commit,
                "project_type": project_type,
                "build_success": build_res.get("success", False),
                "status": status,
                "test_file": str(written_file.relative_to(repo_dir)),
                "code": test_code,
                "output": run_res.get("output", ""),
            }
            with result_path.open("w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            summary.append(result)
            processed += 1

    except KeyboardInterrupt:
        logger.warning("\nProcess interrupted by user (Ctrl+C). Saving progress...")
    except Exception as e:
        logger.error("Unexpected error in main loop: %s", e)
    finally:
        # 8) 生成汇总文件，便于后续统计分析（即使中途报错或中断也会执行）
        if summary:
            summary_path = RESULTS_DIR / "summary.json"
            with summary_path.open("w", encoding="utf-8") as f:
                json.dump({"processed": processed, "total_in_summary": len(summary), "results": summary}, f, indent=2, ensure_ascii=False)
            logger.info("Done. Summary saved to %s. Processed %s new entries in this run.", summary_path, processed)
        else:
            logger.info("No entries processed. Exiting.")


if __name__ == "__main__":
    main()