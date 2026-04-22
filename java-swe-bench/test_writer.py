"""
test_writer.py
代码落盘层。
支持多模块 (Multi-Module) 智能探测，精准锁定真实的测试代码存放目录，
彻底粉碎 Maven 找不到测试用例的假阴性问题！
"""

import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from constants import logger


def _find_best_test_dir(repo_dir: Path, package_path: str) -> Path:
    """
    🔍 智能探测雷达：在多模块项目中寻找最匹配的 src/test/java 目录
    """
    candidate_dirs = list(repo_dir.rglob("src/test/java"))
    
    if not candidate_dirs:
        # 兜底：如果整个项目连一个 src/test/java 都没有，只能硬建一个根目录的了
        return repo_dir / "src" / "test" / "java"
        
    if len(candidate_dirs) == 1:
        # 如果只有一个，直接命中
        return candidate_dirs[0]
        
    # 如果有多个（典型的多模块项目），通过包路径去匹配最合适的模块打分
    best_dir = candidate_dirs[0]
    max_score = -1
    
    for d in candidate_dirs:
        score = 0
        
        # 1. 强特征：如果这个模块的测试目录里已经有这个包路径了，说明找对地方了
        if (d / package_path).exists():
            score += 10
            
        # 2. 次强特征：看看对应的 src/main/java 里有没有这个包
        main_dir = d.parent.parent / "main" / "java"
        if main_dir.exists() and (main_dir / package_path).exists():
            score += 5
            
        # 3. 弱特征：模块名包含 core/logstash 等通常是主干业务模块
        if "logstash-core" in d.parts or "core" in str(d).lower() or "logstash" in d.parts:
            score += 2
            
        if score > max_score:
            max_score = score
            best_dir = d
            
    return best_dir


def write_test_to_repo(repo_dir: Path, test_json: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[Path]]:
    """
    将 LLM 生成的测试代码写入本地仓库的正确路径。
    """
    test_code = test_json.get("test_code", "")
    class_name = test_json.get("class_name", "")

    if not test_code or not class_name:
        logger.error("Test code or class name is missing from the parsed LLM output.")
        return False, None, None

    # 1. 智能解析 package 名称
    package_match = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", test_code, re.MULTILINE)
    package_name = package_match.group(1) if package_match else ""
    package_path = package_name.replace(".", "/") if package_name else ""

    # 2. 🚀 呼叫智能雷达，锁定真实的模块目录
    base_test_dir = _find_best_test_dir(repo_dir, package_path)
    
    if package_path:
        target_dir = base_test_dir / package_path
    else:
        target_dir = base_test_dir

    # 3. 递归创建深层级目录
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{class_name}.java"

    # 4. 执行代码落盘
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(test_code)
        
        fqn = f"{package_name}.{class_name}" if package_name else class_name
        logger.info(f"Successfully wrote test {fqn} to {file_path}")
        return True, fqn, file_path
        
    except IOError as e:
        logger.error(f"Failed to write test file to {file_path}: {e}")
        return False, None, None