"""
repo_utils.py
提供底层的 Git 操作、Shell 命令执行探针、Java 构建系统自动识别。
🚀 通用化升级版：适配 Mockito 及任意 Java 数据集。
🚀 新增 智能代理回退机制 (Proxy Fallback)，确保 Clone 万无一失！
🚀 增强 "时空净化器" (reset --hard + clean -fdx)，绝对保证环境纯净！
🚀 终极杀招 "深海打捞" (Deep Fetch)：专门针对隐藏在 PR 或 Detached 状态的幽灵 Commit 进行精准拉取！
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List

from constants import logger


def _decode_output(output: Any) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def run_cmd(
    cmd: List[str], 
    cwd: Optional[Path] = None, 
    timeout: int = 600, 
    env_vars: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """在隔离的子进程中安全执行 Shell 命令。"""
    cmd_str = " ".join(cmd)
    logger.debug(f"Executing: [{cmd_str}] at {cwd or 'current dir'}")
    
    run_env = os.environ.copy()
    if env_vars:
        run_env.update(env_vars)

    try:
        # 规避高版本 Git 的安全目录报错
        if cmd[0] == "git":
            subprocess.run(
                ["git", "config", "--global", "--add", "safe.directory", "*"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False
        )
        
        return {
            "returncode": completed.returncode,
            "stdout": _decode_output(completed.stdout),
            "stderr": _decode_output(completed.stderr)
        }
        
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout}s: {cmd_str}")
        return {"returncode": -1, "stdout": "", "stderr": "PROCESS TIMEOUT"}
    except Exception as e:
        logger.error(f"Command execution failed: {cmd_str} -> {e}")
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def clone_and_checkout(repo_fullname: str, clone_dir: Path, commit_sha: str) -> bool:
    """
    克隆仓库并强制切换到指定的历史 Commit 状态。
    🚀 通用化：自动尝试国内加速代理，失败则回退到官方 GitHub。
    """
    github_url = f"https://github.com/{repo_fullname}.git"
    proxy_url = f"https://gitclone.com/github.com/{repo_fullname}.git"
    
    # 1. 环境净化检查
    if clone_dir.exists():
        if not (clone_dir / ".git").exists():
            logger.warning(f"Invalid git repo found at {clone_dir}. Purging...")
            shutil.rmtree(clone_dir, ignore_errors=True)
            
    # 2. 执行 Clone (智能回退机制)
    if not clone_dir.exists():
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"🔄 Trying to clone {repo_fullname} via Proxy ({proxy_url})...")
        clone_res = run_cmd(["git", "clone", proxy_url, str(clone_dir)])
        
        if clone_res["returncode"] != 0:
            logger.warning(f"⚠️ Proxy clone failed. Falling back to official GitHub: {github_url}")
            # 清理刚才代理失败可能残留的空目录
            shutil.rmtree(clone_dir, ignore_errors=True)
            clone_res = run_cmd(["git", "clone", github_url, str(clone_dir)])
            
            if clone_res["returncode"] != 0:
                logger.error(f"❌ Clone failed for {repo_fullname}. Stderr: {clone_res['stderr'][:500]}")
                return False

    # 3. 强制 Checkout 到 Bug 发生时的状态
    logger.info(f"Checking out historical commit: {commit_sha[:8]}...")
    
    # 先硬重置抹除本地所有瞎改，再删除所有幽灵未追踪文件
    run_cmd(["git", "reset", "--hard", "HEAD"], cwd=clone_dir)
    run_cmd(["git", "clean", "-fdx"], cwd=clone_dir)
    
    run_cmd(["git", "fetch", "--all"], cwd=clone_dir)
    checkout_res = run_cmd(["git", "checkout", "-f", commit_sha], cwd=clone_dir)
    
    # 🚀 核心修复：深海打捞 (Deep Fetch)
    if checkout_res["returncode"] != 0:
        logger.warning(f"⚠️ Commit {commit_sha[:8]} not found in standard branches. Attempting Deep Fetch from official GitHub...")
        # 强行向官方源请求这个游离的 commit hash
        run_cmd(["git", "fetch", github_url, commit_sha], cwd=clone_dir)
        checkout_res = run_cmd(["git", "checkout", "-f", commit_sha], cwd=clone_dir)
        
        if checkout_res["returncode"] != 0:
            logger.error(f"❌ Checkout completely failed for {commit_sha}. Stderr: {checkout_res['stderr'][:500]}")
            return False
            
    return True


def detect_build_system(repo_dir: Path) -> str:
    """探测 Java 仓库的构建系统，并自动修复可执行权限。"""
    if (repo_dir / "pom.xml").exists():
        return "maven"
        
    if (repo_dir / "build.gradle").exists() or (repo_dir / "build.gradle.kts").exists():
        gradlew = repo_dir / "gradlew"
        if gradlew.exists():
            try:
                # 自动为 gradle 包装器赋予运行权限，防止 Linux 下 Permission Denied
                os.chmod(str(gradlew), 0o755)
            except OSError as e:
                pass
        return "gradle"
        
    return "unknown"


def cleanup_workspace(repo_dir: Path, clean_maven_cache: bool = False):
    """阅后即焚 (Run-and-Burn)：清理占用大量磁盘空间的本地代码仓库。"""
    if repo_dir.exists():
        logger.info(f"🧹 [Run-and-Burn] Deleting repository workspace: {repo_dir.name} to free up space...")
        try:
            shutil.rmtree(repo_dir, ignore_errors=True)
            logger.info(f"✅ Workspace {repo_dir.name} completely removed.")
        except Exception as e:
            logger.error(f"Failed to delete workspace {repo_dir}: {e}")