import json
from pathlib import Path
from typing import List, Dict, Any

from constants import ISSUES_FILE, logger

def fetch_and_clean_dataset(force_refresh: bool = False) -> List[Dict[str, Any]]:
    # 1. 检查是否已有清洗好的缓存文件
    if ISSUES_FILE.exists() and not force_refresh:
        logger.info(f"Using cached dataset at {ISSUES_FILE}")
        try:
            with open(ISSUES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.warning("Cached dataset corrupted. Re-parsing local JSONL file.")

    # 2. 替换为 multi_swe_bench 数据集的文件名
    # 首先尝试绝对路径，如果找不到，则尝试在当前目录及 data 目录下寻找
    local_data_file = Path("/root/bug-report2-test-cases2/data/multi_swe_bench_java_verified.jsonl")
    if not local_data_file.exists():
        local_data_file = Path("data/multi_swe_bench_java_verified.jsonl")
    if not local_data_file.exists():
        local_data_file = Path("multi_swe_bench_java_verified.jsonl")
    
    if not local_data_file.exists():
        logger.critical(f"Local file not found at {local_data_file}!")
        raise FileNotFoundError(f"Missing local dataset: {local_data_file}")

    logger.info(f"Loading dataset offline from local JSONL file: {local_data_file.name} (STRICT ZERO-LEAKAGE MODE)...")

    processed_data: List[Dict[str, Any]] = []
    total_items = 0
    
    try:
        with open(local_data_file, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f):
                line = line.strip()
                if not line: continue
                
                total_items += 1
                try:
                    item = json.loads(line)
                    
                    # 提取通用基础字段
                    instance_id = item.get("instance_id")
                    repo_name = item.get("repo")
                    
                    # 提取历史 Commit (Base Commit)
                    base_info = item.get("base", {})
                    base_commit = item.get("base_commit") or base_info.get("sha") or base_info.get("commit")
                    
                    # 提取问题描述：优先提取 problem_statement
                    problem_description = str(item.get("problem_statement", "")).strip()
                    
                    # 如果 problem_statement 为空，尝试回退到普通的 title + body 模式
                    if not problem_description:
                        title = str(item.get("title", ""))
                        body = str(item.get("body", ""))
                        problem_description = f"{title}\n\n{body}".strip()
                    
                    # 校验必要字段是否都提取成功
                    if not all([instance_id, repo_name, base_commit, problem_description]):
                        logger.warning(f"Line {line_num} missing required fields (instance_id, repo, base_commit or description). Skipping.")
                        continue
                        
                    #  核心防漏沙盒：完全剥离 patch，仅保留黑盒复现所需的最少信息
                    entry = {
                        "instance_id": str(instance_id).strip(),
                        "repo": str(repo_name).strip(),
                        "base_commit": str(base_commit).strip(),
                        "problem_description": str(problem_description).strip(),
                        "language": "java"  # 强制标记为 Java 环境
                    }
                    processed_data.append(entry)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"JSON decode error at line {line_num}: {e}")
                    continue
    except Exception as e:
         logger.error(f"Failed to read file {local_data_file}: {e}")
         raise

    logger.info(f"Total valid strict black-box items: {len(processed_data)}")

    # 4. 将清洗后的数据写入缓存文件
    try:
        ISSUES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ISSUES_FILE, "w", encoding="utf-8") as f:
            json.dump(processed_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to write cache file {ISSUES_FILE}: {e}")

    return processed_data