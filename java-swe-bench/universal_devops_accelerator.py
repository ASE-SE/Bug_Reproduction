"""
universal_devops_accelerator.py
独立的 DevOps 通用加速模块。
负责全局拦截 Maven/Gradle 流量，以及智能/物理替换仓库内的国外依赖源。
"""

import os
from pathlib import Path
import requests

from constants import logger, DEFAULT_API_URL, EMBEDDED_API_KEY

def setup_global_mirrors():
    """在宿主机全局劫持 Maven 和 Gradle 下载请求，永久指向阿里云"""
    logger.info("⚡ [Accelerator] Injecting global Maven/Gradle mirrors (Aliyun) into host system...")
    
    # 1. 劫持 Maven
    m2_dir = Path.home() / ".m2"
    m2_dir.mkdir(parents=True, exist_ok=True)
    settings_file = m2_dir / "settings.xml"
    
    # 🚀 核心修复1：修改为 central，放行第三方插件仓库，并规范 XML 格式，去除 Markdown 干扰
    settings_content = """<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.0.0
                              https://maven.apache.org/xsd/settings-1.0.0.xsd">
  <mirrors>
    <mirror>
      <id>aliyunmaven</id>
      <mirrorOf>central</mirrorOf>
      <name>阿里云公共仓库</name>
      <url>https://maven.aliyun.com/repository/public</url>
    </mirror>
  </mirrors>
</settings>"""
    settings_file.write_text(settings_content, encoding="utf-8")

    # 2. 劫持 Gradle
    gradle_dir = Path.home() / ".gradle"
    gradle_dir.mkdir(parents=True, exist_ok=True)
    init_script = gradle_dir / "init.gradle"
    
    # 🚀 核心修复2：去除 URL 中的 Markdown 括号，确保 Gradle 能正确解析纯文本 URL
    init_content = """
allprojects {
    buildscript {
        repositories {
            maven { url 'https://maven.aliyun.com/repository/public/' }
            maven { url 'https://maven.aliyun.com/repository/spring/' }
            maven { url 'https://maven.aliyun.com/repository/google/' }
            maven { url 'https://maven.aliyun.com/repository/gradle-plugin/' }
        }
    }
    repositories {
        maven { url 'https://maven.aliyun.com/repository/public/' }
        maven { url 'https://maven.aliyun.com/repository/spring/' }
        maven { url 'https://maven.aliyun.com/repository/google/' }
    }
}"""
    init_script.write_text(init_content, encoding="utf-8")


def _llm_intelligent_patch(file_path: Path):
    """【大模型智能打补丁】遇到复杂的构建文件，调用 LLM 智能重写国内源"""
    logger.info(f"🧠 [LLM Patcher] Analyzing and patching {file_path.name}...")
    original_code = file_path.read_text(encoding="utf-8")
    
    system_prompt = (
        "You are an expert DevOps engineer. Your task is to modify the provided build configuration file "
        "(e.g., Gemfile, build.gradle, package.json). "
        "You must find any foreign package registry URLs (like rubygems.org, npmjs.org) and replace them with "
        "their fast Chinese mirror equivalents (e.g., gems.ruby-china.com, registry.npmmirror.com). "
        "DO NOT change any other logic, dependencies, or versions. "
        "Output ONLY the raw modified code without any markdown formatting, markdown blocks (```), or explanations."
    )
    
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Here is the file content:\n\n{original_code}"}
        ],
        "temperature": 0.1,
        "max_tokens": 4000
    }
    
    headers = {
        "Authorization": f"Bearer {EMBEDDED_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(DEFAULT_API_URL, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        modified_code = response.json()["choices"][0]["message"]["content"].strip()
        
        # 🚀 修复点：更稳健的 Markdown 代码块清洗逻辑，杜绝乱码截断
        if modified_code.startswith("```"):
            lines = modified_code.split("\n")
            if len(lines) > 1:
                # 如果最后一行也是 ``` 结尾，掐头去尾
                if lines[-1].strip().startswith("```"):
                    modified_code = "\n".join(lines[1:-1])
                # 否则只去头
                else:
                    modified_code = "\n".join(lines[1:])
            
        file_path.write_text(modified_code, encoding="utf-8")
        logger.info(f"✅ [LLM Patcher] Successfully rewrote {file_path.name} with Chinese mirrors!")
    except Exception as e:
        logger.error(f"❌ [LLM Patcher] Failed to patch {file_path.name}: {e}")


def accelerate_repository(repo_dir: Path):
    """主控入口：遍历项目，物理替换或智能替换依赖源"""
    logger.info(f"🚀 [Accelerator] Deploying project-level mirror patches to {repo_dir.name}...")
    patched_count = 0
    for root, dirs, files in os.walk(repo_dir):
        for file in files:
            file_path = Path(root) / file
            
            # Ruby 项目换源
            if file == "Gemfile":
                try:
                    content = file_path.read_text(encoding="utf-8")
                    # 🚀 核心修复3：去除这里的 Markdown 语法，使用纯文本替换
                    if "rubygems.org" in content:
                        content = content.replace("https://rubygems.org/", "https://gems.ruby-china.com/")
                        content = content.replace("https://rubygems.org", "https://gems.ruby-china.com/")
                        content = content.replace("http://rubygems.org/", "https://gems.ruby-china.com/")
                        content = content.replace("http://rubygems.org", "https://gems.ruby-china.com/")
                        file_path.write_text(content, encoding="utf-8")
                        patched_count += 1
                        logger.info(f"💉 Patched RubyGems source in: {file_path.relative_to(repo_dir)}")
                except Exception as e:
                    logger.warning(f"Failed to patch {file_path}: {e}")
            
            # 预留给未来其他语言扩展 (遇到复杂的可以直接扔给 _llm_intelligent_patch)
            elif file == ".npmrc" or file == "requirements.txt":
                # 未来如果是 Nodejs 或 Python 项目，可以在这里扩展
                pass
                    
    if patched_count > 0:
        logger.info(f"🎉 [Accelerator] Successfully patched {patched_count} file(s) to use fast mirrors!")
    else:
        logger.info("⚡ [Accelerator] No foreign sources needed patching.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target_path = Path(sys.argv[1])
        if target_path.exists():
            setup_global_mirrors()
            accelerate_repository(target_path)
        else:
            print(f"❌ Target path does not exist: {target_path}")
    else:
        print("💡 Usage: python universal_devops_accelerator.py <path_to_repository>")