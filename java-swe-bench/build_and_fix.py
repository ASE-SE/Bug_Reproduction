"""
build_and_fix.py
环境预热控制器。
负责执行防御性构建，目的是触发 Maven/Gradle 下载所有的依赖包至本地缓存（~/.m2 或 ~/.gradle），
而不需要花费额外时间去运行仓库原本就带有的、可能耗时极长的全量测试。
"""

from pathlib import Path
from typing import Dict, Any

from repo_utils import run_cmd
from constants import logger


def prime_build_dependencies(repo_dir: Path, build_system: str, timeout: int = 1800) -> Dict[str, Any]:
    """
    执行项目级编译，预热本地依赖缓存。
    
    Args:
        repo_dir (Path): 目标仓库绝对路径。
        build_system (str): 'maven' 或 'gradle'。
        timeout (int): 依赖下载与编译的最大耗时，默认 30 分钟。
        
    Returns:
        Dict[str, Any]: 包含 success 状态和相关日志流的字典。
    """
    logger.info(f"Priming dependencies for {repo_dir.name} using {build_system}...")
    
    success = False
    stdout, stderr = "", ""

    if build_system == "maven":
        # Maven 构建策略:
        # - test-compile: 编译主代码和测试代码，强制下载所有 pom.xml 中声明的依赖。
        # - -DskipTests: 跳过测试运行阶段，极大地节省时间。
        # - -DfailIfNoTests=false: 如果某些模块完全没有测试代码，防止 Maven 报错退出。
        # - -B: Batch mode，禁用交互式输出，减少日志刷屏。
        # - -e: 打印完整的错误堆栈，便于排查。
        cmd = [
            "mvn", "clean", "test-compile", 
            "-DskipTests", "-DfailIfNoTests=false", 
            "-B", "-e"
        ]
        
        r = run_cmd(cmd, cwd=repo_dir, timeout=timeout)
        success = (r["returncode"] == 0)
        stdout = r["stdout"]
        stderr = r["stderr"]

    elif build_system == "gradle":
        # Gradle 构建策略:
        # - testClasses: 触发主代码和测试代码的编译及依赖解析。
        # - -x test: 排除（Exclude）运行测试的任务。
        # - --no-daemon: 避免在后台启动长驻内存的守护进程（在自动化脚本中推荐）。
        # - --info: 提供适中的日志输出以供追踪。
        cmd = [
            "./gradlew", "clean", "testClasses", 
            "-x", "test", 
            "--no-daemon", "--info"
        ]
        
        r = run_cmd(cmd, cwd=repo_dir, timeout=timeout)
        success = (r["returncode"] == 0)
        stdout = r["stdout"]
        stderr = r["stderr"]

    else:
        logger.error(f"Cannot prime dependencies for unknown build system at {repo_dir}")
        return {"success": False, "stdout": "", "stderr": "Unknown build system."}

    # 结果判定与日志记录
    if success:
        logger.info(f"Successfully primed dependencies for {repo_dir.name}.")
    else:
        # 截取前 1000 个字符以防日志爆炸
        err_snippet = stderr[:1000] if stderr else stdout[:1000]
        logger.warning(f"Failed to prime dependencies for {repo_dir.name}. "
                       f"This may lead to compilation errors later. Snippet: \n{err_snippet}")
        
    return {
        "success": success,
        "stdout": stdout,
        "stderr": stderr
    }