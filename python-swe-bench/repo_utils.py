"""
Repo and shell helpers for python-swe-bench.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from constants import logger


def _decode_output(output: Any) -> str:
    """统一处理 subprocess 输出，保证返回 UTF-8 字符串。"""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 600,
    env_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    安全执行 shell 命令并返回结构化结果。
    """
    cmd_str = " ".join(cmd)
    logger.debug("Executing: [%s] at %s", cmd_str, cwd or "current dir")
    
    run_env = os.environ.copy()
    
    # 【重要优化】防止 Git 在遇到私有仓库或不存在的仓库时，卡死在提示输入账号密码的交互界面
    run_env["GIT_TERMINAL_PROMPT"] = "0"
    
    if env_vars:
        run_env.update(env_vars)
        
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": _decode_output(completed.stdout),
            "stderr": _decode_output(completed.stderr),
        }
    except subprocess.TimeoutExpired:
        logger.error("Command timed out after %ss: %s", timeout, cmd_str)
        return {"returncode": -1, "stdout": "", "stderr": "PROCESS TIMEOUT"}
    except Exception as e:
        logger.error("Command execution failed: %s -> %s", cmd_str, e)
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def clone_or_update_repo(repo_fullname: str, clone_dir: Path, commit_sha: str) -> bool:
    """
    克隆并切到目标 commit（幂等）：
    - 首次不存在则 clone；
    - 已存在则 fetch + 彻底清理环境；
    - 最终 checkout 到 base_commit。
    """
    git_url = f"https://github.com/{repo_fullname}.git"
    
    if clone_dir.exists() and not (clone_dir / ".git").exists():
        logger.warning("Invalid git repo found at %s. Purging...", clone_dir)
        shutil.rmtree(clone_dir, ignore_errors=True)

    if not clone_dir.exists():
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Cloning repository %s...", repo_fullname)
        clone_res = run_cmd(["git", "clone", git_url, str(clone_dir)], timeout=1200)
        if clone_res["returncode"] != 0:
            logger.error("Clone failed for %s. Stderr: %s", repo_fullname, clone_res["stderr"][:500])
            return False

    # 【重要优化】增加 reset --hard，防止上一个实例残留的修改或新建文件影响当前实例
    run_cmd(["git", "reset", "--hard"], cwd=clone_dir)
    run_cmd(["git", "clean", "-fdx"], cwd=clone_dir)
    
    checkout_res = run_cmd(["git", "checkout", "-f", commit_sha], cwd=clone_dir)
    
    if checkout_res["returncode"] != 0:
        logger.info("Commit %s not found locally, fetching from origin...", commit_sha)
        run_cmd(["git", "fetch", "origin"], cwd=clone_dir)
        checkout_res = run_cmd(["git", "checkout", "-f", commit_sha], cwd=clone_dir)
        
    if checkout_res["returncode"] != 0:
        logger.info("Fetching all tags and branches as fallback...")
        run_cmd(["git", "fetch", "--all", "--tags"], cwd=clone_dir)
        checkout_res = run_cmd(["git", "checkout", "-f", commit_sha], cwd=clone_dir)
        
    if checkout_res["returncode"] != 0:
        logger.error("Checkout failed for %s. Stderr: %s", commit_sha, checkout_res["stderr"][:500])
        return False
        
    return True


def detect_python_project_layout(repo_dir: Path) -> str:
    """
    粗粒度探测 Python 项目构建形态，用于后续安装策略分支。
    """
    if (repo_dir / "pyproject.toml").exists():
        if (repo_dir / "poetry.lock").exists():
            return "poetry"
        return "pyproject"
    if (repo_dir / "requirements.txt").exists():
        return "requirements"
    # 【优化】补充了对 setup.cfg 的检测，有些老旧库只有 cfg 没有 py
    if (repo_dir / "setup.py").exists() or (repo_dir / "setup.cfg").exists():
        return "setuptools"
    return "plain"


def cleanup_workspace(repo_dir: Path) -> None:
    """清理本地克隆仓库，适合存储受限场景。"""
    if repo_dir.exists():
        logger.info("Deleting repository workspace: %s", repo_dir.name)
        shutil.rmtree(repo_dir, ignore_errors=True)