"""
Python test execution runner.
"""

import os
from pathlib import Path
from typing import Any

from constants import logger
from repo_utils import run_cmd


def _venv_python(repo_dir: Path) -> str:
    """返回虚拟环境内 python 路径（兼容 Windows/Linux）。"""
    if os.name == "nt":
        return str(repo_dir / ".swe_venv" / "Scripts" / "python.exe")
    return str(repo_dir / ".swe_venv" / "bin" / "python")


def run_single_python_test(repo_dir: Path, test_file: Path, project_type: str) -> dict[str, Any]:
    """
    运行单个 pytest 文件并给出统一状态标签。

    状态映射：
    - file_not_found: 测试文件未成功写入或丢失
    - compilation_error: 代码存在语法错误或缺少依赖无法运行
    - test_passed_not_reproduced: 测试全部通过（未能复现报错）
    - test_failed_reproduced: 测试失败（成功复现了报错）
    - execution_error: 其他执行异常（如超时等）
    """
    # 增加防御性检查：确保测试文件确实存在
    if not test_file.exists():
        logger.error("Test file not found: %s", test_file)
        return {"status": "file_not_found", "output": "Test file does not exist."}

    rel_test = str(test_file.relative_to(repo_dir))
    logger.info("Running pytest for %s (%s)", rel_test, project_type)

    py = _venv_python(repo_dir)
    if not Path(py).exists():
        logger.warning("Virtual environment Python not found, falling back to system 'python'")
        py = "python"

    # 执行 pytest，-q 减少无用输出，--tb=short 缩短回溯栈避免日志过长
    cmd = [py, "-m", "pytest", "-q", "--tb=short", rel_test]
    r = run_cmd(cmd, cwd=repo_dir, timeout=900)
    
    # 合并输出并进行安全截断，防止极端情况下日志撑爆内存 (保留前 20000 字符)
    raw_output = (r.get("stdout", "") or "") + "\n" + (r.get("stderr", "") or "")
    output = raw_output[:20000] 
    rc = r.get("returncode", -1)

    # 启发式状态判定
    if r.get("stderr", "") == "PROCESS TIMEOUT":
        status = "execution_error"
        output = "Process timed out after 900 seconds."
    elif "SyntaxError" in output or "ImportError" in output or "ModuleNotFoundError" in output:
        status = "compilation_error"
    elif rc == 0:
        status = "test_passed_not_reproduced"
    elif "failed" in output.lower() or "error" in output.lower() or rc != 0:
        # 只要 returncode 非 0 且排除了编译错误，基本都可以认为是测试未通过（即成功触发了 Bug）
        status = "test_failed_reproduced"
    else:
        status = "execution_error"

    return {"status": status, "output": output}