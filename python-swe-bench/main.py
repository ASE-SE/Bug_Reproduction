"""
main.py
Python SWT-Bench 接入版主控。

两阶段命令：

  1) generate  —— 拉数据集 + 调 LLM 生成触发测试 + 输出 SWT-Bench predictions.jsonl
                 并在 results/instances/<id>.json 落"原始缺陷报告 + 生成测试"档。

  2) collect   —— 在 SWT-Bench harness 跑完之后，解析它的 run_instance_swt_logs/，
                 把每条 instance 的执行输出 + 复现状态合并进 results/instances/<id>.json，
                 并写 results/summary.json 汇总。

中间步骤是手动跑 SWT-Bench harness，命令在 generate 完成后会打印出来。
"""

import argparse
import json
import os
from pathlib import Path
from typing import Any

from api_client import call_api_generate_python_test
from collector import collect_all
from constants import (
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    EMBEDDED_API_KEY,
    HF_DATASET_NAME,
    METHOD_NAME,
    PREDICTIONS_FILE,
    SUMMARY_FILE,
    SWT_BENCH_WORKDIR,
    logger,
)
from data_loader import fetch_and_clean_dataset, filter_by_repo, list_repos
from test_writer import (
    build_prediction,
    load_initial_records,
    write_initial_instance_file,
    write_predictions_jsonl,
)


# ---------- generate ----------

def _process_one(entry: dict[str, Any], api_url: str, api_key: str, api_model: str,
                 method_name: str) -> dict[str, Any] | None:
    instance_id = str(entry["instance_id"])
    api_res = call_api_generate_python_test(
        bug_entry=entry, api_url=api_url, api_key=api_key, api_model=api_model
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


def cmd_generate(args: argparse.Namespace) -> int:
    api_key = os.getenv("API_KEY") or EMBEDDED_API_KEY
    if not api_key:
        logger.error("API_KEY missing.")
        return 2

    try:
        entries = fetch_and_clean_dataset(
            force_refresh=args.force_refresh,
            dataset_name=args.dataset_name,
            split=args.split,
        )
    except Exception as e:
        logger.error("Failed to load dataset: %s", e)
        return 2

    if args.repo:
        entries = filter_by_repo(entries, args.repo)
    if args.instance_id:
        entries = [e for e in entries if str(e.get("instance_id")) == args.instance_id]

    if not entries:
        logger.error("No entries match the filters. Use --list-repos to inspect dataset.")
        return 2

    predictions: list[dict[str, Any]] = []
    processed = 0

    try:
        for idx, entry in enumerate(entries):
            if args.limit is not None and processed >= args.limit:
                logger.info("Reached limit of %s tasks.", args.limit)
                break
            iid = str(entry["instance_id"])
            logger.info("=== [%s/%s] %s (%s) ===", idx + 1, len(entries), iid, entry.get("repo"))

            record = _process_one(entry, args.api_url, api_key, args.api_model, args.method_name)
            if record is None:
                continue
            predictions.append(record)
            write_initial_instance_file(entry, record)
            processed += 1
    except KeyboardInterrupt:
        logger.warning("Interrupted; saving partial progress...")

    if not predictions:
        logger.info("No predictions produced.")
        return 1

    out = Path(args.output) if args.output else PREDICTIONS_FILE
    write_predictions_jsonl(predictions, out)

    logger.info("")
    logger.info("✅ Generated %s predictions -> %s", len(predictions), out)
    logger.info("✅ Per-instance archives in results/instances/")
    logger.info("")
    logger.info("Next: run SWT-Bench harness (Docker) to actually execute the tests:")
    logger.info("  cd %s", SWT_BENCH_WORKDIR)
    logger.info(
        "  python -m src.main \\\n"
        "    --dataset_name %s \\\n"
        "    --predictions_path %s \\\n"
        "    --filter_swt --max_workers 4 \\\n"
        "    --run_id %s",
        args.dataset_name or HF_DATASET_NAME, out, args.run_id,
    )
    logger.info("")
    logger.info("Then run: python main.py collect --run-id %s --method-name %s",
                args.run_id, args.method_name)
    return 0


# ---------- collect ----------

def cmd_collect(args: argparse.Namespace) -> int:
    try:
        entries = fetch_and_clean_dataset(
            force_refresh=False,
            dataset_name=args.dataset_name,
            split=args.split,
        )
    except Exception as e:
        logger.error("Failed to load dataset: %s", e)
        return 2

    if args.repo:
        entries = filter_by_repo(entries, args.repo)

    initial = load_initial_records()
    if not initial:
        logger.error("No initial records found in results/instances/. Run `generate` first.")
        return 2

    # 只 collect 我们之前 generate 过的 instance
    entries = [e for e in entries if e["instance_id"] in initial]
    if not entries:
        logger.error("No entries overlap with results/instances/.")
        return 2

    counts = collect_all(entries, initial, args.method_name, args.run_id)
    total = sum(counts.values())

    summary = {
        "method_name": args.method_name,
        "run_id": args.run_id,
        "dataset_name": args.dataset_name or HF_DATASET_NAME,
        "total": total,
        "by_status": counts,
    }
    with SUMMARY_FILE.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("✅ Collected %s instances. Status distribution:", total)
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        logger.info("  %-22s %d", k, v)
    logger.info("Summary written to %s", SUMMARY_FILE)
    logger.info("Per-instance comparison files in results/instances/<id>.json")
    return 0


# ---------- list-repos ----------

def cmd_list_repos(args: argparse.Namespace) -> int:
    entries = fetch_and_clean_dataset(
        force_refresh=args.force_refresh,
        dataset_name=args.dataset_name,
        split=args.split,
    )
    counts = list_repos(entries)
    print(f"Total entries: {len(entries)}; unique repos: {len(counts)}")
    for repo, n in counts.items():
        print(f"  {repo:<40s} {n}")
    return 0


# ---------- argparse ----------

def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--dataset-name", dest="dataset_name", default=None,
                   help=f"HF dataset (default: {HF_DATASET_NAME})")
    p.add_argument("--split", default=None, help="dataset split (default: test)")
    p.add_argument("--repo", default=None,
                   help="filter by repo full name, e.g. sympy/sympy")
    p.add_argument("--method-name", dest="method_name", default=METHOD_NAME)


def main() -> int:
    parser = argparse.ArgumentParser(description="Python SWT-Bench predictions pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate predictions.jsonl from LLM")
    _add_common_args(g)
    g.add_argument("--limit", type=int, default=None)
    g.add_argument("--instance-id", dest="instance_id", default=None)
    g.add_argument("--force-refresh", dest="force_refresh", action="store_true")
    g.add_argument("--api-url", dest="api_url", default=DEFAULT_API_URL)
    g.add_argument("--api-model", dest="api_model", default=DEFAULT_API_MODEL)
    g.add_argument("--output", default=None,
                   help=f"predictions.jsonl path (default: {PREDICTIONS_FILE})")
    g.add_argument("--run-id", dest="run_id", default="local_run",
                   help="convenience: gets printed in the SWT-Bench command hint")
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("collect", help="parse SWT-Bench harness logs into per-instance results")
    _add_common_args(c)
    c.add_argument("--run-id", dest="run_id", required=True,
                   help="must match the --run_id you passed to SWT-Bench harness")
    c.set_defaults(func=cmd_collect)

    lr = sub.add_parser("list-repos", help="list repos and instance counts in the dataset")
    _add_common_args(lr)
    lr.add_argument("--force-refresh", dest="force_refresh", action="store_true")
    lr.set_defaults(func=cmd_list_repos)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
