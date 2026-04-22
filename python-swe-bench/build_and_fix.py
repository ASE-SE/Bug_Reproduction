"""
Pre-run dependency setup for Python repositories.
"""

import os
import sys
from pathlib import Path
from typing import Any

from constants import logger
from repo_utils import run_cmd


def attempt_python_build(repo_dir: Path, project_type: str, timeout: int = 1800) -> dict[str, Any]:
    """
    预热 Python 项目运行环境（并不运行完整测试套件）：
    1) 建立虚拟环境；
    2) 根据项目类型安装依赖；
    3) 执行 pytest --collect-only 做快速可执行性检查。
    """
    logger.info("Preparing Python environment for %s as %s...", repo_dir.name, project_type)
    venv_dir = repo_dir / ".swe_venv"
    python_bin = sys.executable

    create_venv = run_cmd([python_bin, "-m", "venv", str(venv_dir)], cwd=repo_dir, timeout=300)
    if create_venv["returncode"] != 0 and not venv_dir.exists():
        return {"success": False, "stdout": create_venv["stdout"], "stderr": create_venv["stderr"]}

    pip = str(venv_dir / "Scripts" / "pip.exe") if os.name == "nt" else str(venv_dir / "bin" / "pip")
    py = str(venv_dir / "Scripts" / "python.exe") if os.name == "nt" else str(venv_dir / "bin" / "python")

    cmds: list[list[str]] = []
    # 根据项目布局选择安装策略
    if project_type == "requirements":
        cmds.append([pip, "install", "-r", "requirements.txt"])
    elif project_type in ("pyproject", "setuptools", "poetry"):
        cmds.append([pip, "install", "-e", "."])
    else:
        cmds.append([pip, "install", "pytest"])

    # 确保 runner 一定可调用 pytest
    cmds.append([pip, "install", "pytest"])
    cmds.append([py, "-m", "pytest", "--collect-only", "-q"])

    all_stdout: list[str] = []
    all_stderr: list[str] = []
    for cmd in cmds:
        r = run_cmd(cmd, cwd=repo_dir, timeout=timeout)
        all_stdout.append(r["stdout"])
        all_stderr.append(r["stderr"])
        # collect-only 失败通常是“测试本身存在问题”，不一定代表依赖安装失败
        if r["returncode"] != 0 and cmd[-3:] != ["--collect-only", "-q"]:
            return {"success": False, "stdout": "\n".join(all_stdout), "stderr": "\n".join(all_stderr)}

    return {"success": True, "stdout": "\n".join(all_stdout), "stderr": "\n".join(all_stderr)}