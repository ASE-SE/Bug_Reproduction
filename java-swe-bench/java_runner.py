import os
import re
from pathlib import Path
from typing import Dict, Any

from repo_utils import run_cmd
from constants import logger


def _get_module_cwd(repo_dir: Path, fqn: str) -> Path:
    """寻找测试文件所属的真实子模块目录"""
    class_name = fqn.split('.')[-1]
    test_files = list(repo_dir.rglob(f"{class_name}.java"))
    
    if not test_files:
        return repo_dir
        
    test_file = test_files[0]
    current = test_file.parent
    
    while current != repo_dir and current != current.parent:
        if (current / "pom.xml").exists() or (current / "build.gradle").exists() or (current / "build.gradle.kts").exists():
            return current
        current = current.parent
        
    return repo_dir


def _strip_strict_rules_and_plugins(repo_dir: Path, execute_dir: Path):
    """
    ⚔️ 物理阉割与环境改造：从物理层面消除假阳性，并加速国内网络。
    """
    # === 0. Gradle Wrapper 极速换源 (腾讯云镜像) ===
    wrapper_props = repo_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if wrapper_props.exists():
        try:
            content = wrapper_props.read_text(encoding="utf-8")
            if "services.gradle.org/distributions" in content:
                content = content.replace("services.gradle.org/distributions", "mirrors.cloud.tencent.com/gradle")
                wrapper_props.write_text(content, encoding="utf-8")
                logger.info(f"⚡ Injected Tencent Cloud mirror for Gradle Wrapper: {wrapper_props.relative_to(repo_dir)}")
        except Exception as e:
            logger.warning(f"Failed to inject Gradle mirror: {e}")

    # === 1. Maven 阉割手术 ===
    for target_dir in [repo_dir, execute_dir]:
        pom_file = target_dir / "pom.xml"
        if pom_file.exists():
            try:
                xml_content = pom_file.read_text(encoding="utf-8")
                xml_content = re.sub(
                    r'<plugin>\s*<groupId>org\.apache\.maven\.plugins</groupId>\s*<artifactId>maven-checkstyle-plugin</artifactId>.*?</plugin>', 
                    '', xml_content, flags=re.DOTALL
                )
                xml_content = re.sub(
                    r'<plugin>\s*<groupId>org\.apache\.maven\.plugins</groupId>\s*<artifactId>maven-enforcer-plugin</artifactId>.*?</plugin>', 
                    '', xml_content, flags=re.DOTALL
                )
                pom_file.write_text(xml_content, encoding="utf-8")
            except Exception as e:
                pass

    # === 2. Gradle 阉割手术 ===
    for root, dirs, files in os.walk(repo_dir):
        for file in files:
            if file == "build.gradle" or file == "build.gradle.kts":
                file_path = Path(root) / file
                try:
                    content = file_path.read_text(encoding="utf-8")
                    if "-Werror" in content:
                        content = content.replace("-Werror", "-nowarn")
                        file_path.write_text(content, encoding="utf-8")
                except Exception as e:
                    pass


def run_generated_test(repo_dir: Path, build_system: str, fqn: str, timeout: int = 900) -> Dict[str, Any]:
    """针对指定的类运行单元测试，并解析结果。"""
    execute_dir = _get_module_cwd(repo_dir, fqn)
    
    # 🚀 物理阉割与换源
    _strip_strict_rules_and_plugins(repo_dir, execute_dir)
    
    logger.info(f"Running targeted test for {fqn} using {build_system}...")
    
    # 🚨 终极修改：无论是 Gradle 还是 Maven，全部强制在根目录 (repo_dir) 执行！
    # 坚决不 cd 到子目录，让 Maven/Gradle 的 Reactor 自己去遍历寻找测试类，防止全局变量丢失！
    actual_cwd = repo_dir 
    
    if build_system == "maven":
        # 1. 预编译全局依赖（防缺失）
        logger.info(f"🛠️ [Maven Pre-build] Installing multi-module dependencies at root for {fqn}...")
        pre_build_cmd = [
            "mvn", "install", "-DskipTests", "-B", "-q",
            "-Dcheckstyle.skip=true",
            "-Drat.skip=true",
            "-Denforcer.skip=true",
            "-Djacoco.skip=true"
        ]
        run_cmd(pre_build_cmd, cwd=actual_cwd, timeout=600)

        # 2. 🚨 直接在根目录全局搜索并执行测试，彻底抛弃 -pl 和复杂的路径计算！
        cmd = [
            "mvn", "test", f"-Dtest={fqn}", "-B", "-e",
            "-DfailIfNoTests=false",   # 🛡️ 超级护盾：无视空测试模块，一路通关！
            "-Dcheckstyle.skip=true",
            "-Drat.skip=true",
            "-Denforcer.skip=true",
            "-Djacoco.skip=true"
        ]
    elif build_system == "gradle":
        task_name = "test"
        gradlew_path = repo_dir / "gradlew"
        if gradlew_path.exists():
            cmd = [
                "bash", str(gradlew_path), 
                task_name, "--tests", fqn, 
                "--no-daemon", 
                "--continue", 
                "-x", "check", 
                "-x", "spotlessCheck", 
                "-x", "javadoc"
            ]
        else:
            cmd = [
                "gradle", 
                task_name, "--tests", fqn, 
                "--no-daemon", 
                "--continue",
                "-x", "check", 
                "-x", "spotlessCheck", 
                "-x", "javadoc"
            ]
    else:
        return {"status": "execution_error", "reason": "Unknown build system"}
        
    r = run_cmd(cmd, cwd=actual_cwd, timeout=timeout)
    stdout = r["stdout"]
    stderr = r["stderr"]
    
    combined_output = stdout + "\n" + stderr
    status = "unknown"
    
    # 🚨 精准的基础设施拦截名单 (移除了容易误杀的 Downloading from)
    infra_errors = [
        "Could not transfer artifact", "Failed to collect dependencies",
        "SocketTimeoutException", "Connect timed out", 
        "Connection refused", "Read timed out", 
        "ProjectBuildingException", "UnresolvableModelException", "PluginResolutionException"
    ]
    if any(err in combined_output for err in infra_errors):
        status = "execution_error"
        logger.warning(f"⚠️ Infrastructure/Network error detected for {fqn}")
        
    # 正常的编译与执行判定
    elif "COMPILATION ERROR" in combined_output or "Compilation failed" in combined_output or "compiler error" in combined_output.lower():
        status = "compilation_error"
    elif "BUILD SUCCESS" in combined_output or "SUCCESS: Executed" in combined_output or "BUILD SUCCESSFUL" in combined_output:
        status = "not_reproduced"
    # 🚨 终极判定升级：只要出现了 Maven 测试失败插件的专属报错，或者常规失败字眼，统统算作复现成功！
    elif any(k in combined_output for k in ["BUILD FAILURE", "FAILED", "There were failing tests", "MojoFailureException"]):
        if any(keyword in combined_output for keyword in ["Failures:", "Errors:", "Exception", "AssertionError", "FAILED", "MojoFailureException"]):
            status = "reproduced"
        else:
            status = "execution_error"
    else:
        if r["returncode"] != 0:
            status = "reproduced"
        else:
            status = "not_reproduced"

    logger.info(f"Test Execution Result: [ {status.upper()} ] for {fqn}")
    
    return {
        "status": status,
        "stdout_snippet": stdout[-3000:],
        "stderr_snippet": stderr[-3000:]
    }