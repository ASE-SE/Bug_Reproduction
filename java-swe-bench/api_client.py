"""
api_client.py
黑盒测试生成大脑。
🚀 进阶修复版：包含动态 JUnit 探测与 Compiler-Feedback Self-Repair (编译器反馈自愈) 机制。
🎯 终极外挂：基于编译器报错的精准源码反查 + AST-Aware Prompting (学术级 AST 多语言语法树解析)。
"""

import json
import re
import requests
from pathlib import Path
from typing import Dict, Any, Optional

from constants import DEFAULT_API_MODEL, logger
# 👇 导入我们独立的 AST 解析引擎
from ast_parser import extract_symbols_from_file


def _extract_json_from_markdown(text: str) -> str:
    text = text.strip()
    # 巧妙避开直接写三个反引号导致的渲染截断
    mark_json = "`" * 3 + "json"
    mark_code = "`" * 3
    if mark_json in text:
        text = text.split(mark_json)[1]
    elif mark_code in text:
        text = text.split(mark_code)[1]
    if mark_code in text:
        text = text.split(mark_code)[0]
    return text.strip()


def _detect_junit_version(repo_dir: Path) -> str:
    """智能探测器：扫描物理仓库的构建文件，判断 JUnit 版本。"""
    pom_path = repo_dir / "pom.xml"
    gradle_path = repo_dir / "build.gradle"

    if pom_path.exists():
        try:
            content = pom_path.read_text(encoding="utf-8").lower()
            if "junit-jupiter" in content or "junit:junit-jupiter" in content:
                return "JUnit 5 (use: import org.junit.jupiter.api.Test; and org.junit.jupiter.api.Assertions.*)"
        except Exception:
            pass
            
    if gradle_path.exists():
        try:
            content = gradle_path.read_text(encoding="utf-8").lower()
            if "junit-jupiter" in content or "usejunitplatform()" in content:
                return "JUnit 5 (use: import org.junit.jupiter.api.Test; and org.junit.jupiter.api.Assertions.*)"
        except Exception:
            pass
            
    return "JUnit 4 (use: import org.junit.Test; and org.junit.Assert.*)"


def _build_repo_context(repo_dir: Path, issue_desc: str) -> str:
    """获取 src/main/java 下的目录树及相关源码。"""
    context = []
    src_main_java = repo_dir / "src" / "main" / "java"
    
    if not src_main_java.exists():
        src_main_java = repo_dir / "src"
        
    if not src_main_java.exists():
        return "Repository source directory not found."

    potential_classes = set(re.findall(r'[A-Z][a-zA-Z0-9_]+', issue_desc))
    
    tree_lines = []
    source_codes = []
    char_limit = 40000
    current_chars = 0
    
    for path in src_main_java.rglob("*.java"):
        rel_path = path.relative_to(repo_dir)
        tree_lines.append(str(rel_path))
        
        if path.stem in potential_classes and current_chars < char_limit:
            try:
                content = path.read_text(encoding="utf-8")
                source_codes.append(f"--- File: {rel_path} ---\n{content}\n")
                current_chars += len(content)
            except Exception:
                pass

    context.append("### REPOSITORY STRUCTURE (Partial src/) ###")
    context.append("\n".join(tree_lines[:100])) 
    if len(tree_lines) > 100:
        context.append("... (truncated)")
        
    if source_codes:
        context.append("\n### RELEVANT SOURCE CODE EXTRACTED FROM REPO ###")
        context.extend(source_codes)
        
    return "\n".join(context)


def _build_ast_aware_api_dictionary(repo_dir: Path, issue_desc: str) -> str:
    """
    🎯 核心黑科技：AST-Aware Retrieval (AST 感知检索)
    通过真实的 Tree-sitter 语法树解析，提取 Bug 描述中涉及到的类的精确本地结构。
    """
    potential_classes = set(re.findall(r'[A-Z][a-zA-Z0-9_]+', issue_desc))
    if not potential_classes:
        return "No specific API constraints found."

    api_dictionary = []
    
    # 扫描相关的 Java 和 Ruby 文件
    for ext in ["*.java", "*.rb"]:
        for path in repo_dir.rglob(ext):
            # 宽泛匹配：如果文件名在潜在的类名列表中
            if path.stem.replace('_', '').lower() in [c.lower() for c in potential_classes] or path.stem in potential_classes:
                
                # 调用底层的 AST 引擎提取符号
                ast_symbols = extract_symbols_from_file(path)
                
                if ast_symbols and ast_symbols.get("classes"):
                    rel_path = path.relative_to(repo_dir)
                    lang_label = "Java" if ext == ".java" else "Ruby - JRuby Interop Required"
                    
                    api_dictionary.append(f"📁 Source: {rel_path} ({lang_label})")
                    
                    for cls in set(ast_symbols["classes"]):
                        api_dictionary.append(f"  📦 Class/Module: {cls}")
                        
                    for method in set(ast_symbols["methods"]):
                        if method not in ast_symbols["classes"]:  # 排除同名构造函数减少干扰
                            api_dictionary.append(f"    ⚙️ Exposed Method: {method}")
                            
    if not api_dictionary:
        return "No AST-parsed local API definitions found for the mentioned classes."
        
    logger.info(f"🌳 Successfully extracted AST context for: {potential_classes}")
    return "\n".join(api_dictionary)


def _extract_and_read_missing_classes(compiler_error: str, repo_dir: Path) -> str:
    """
    🎯 核心黑科技 2：精准错误自愈
    分析编译器错误日志，提取找不到的类名，并在本地物理仓库中检索真实源码。
    """
    if not compiler_error:
        return ""
        
    missing_symbols = set()
    class_matches = re.findall(r'symbol:\s+class\s+([A-Z][a-zA-Z0-9_]*)', compiler_error)
    missing_symbols.update(class_matches)
    location_matches = re.findall(r'location:\s+(?:class|interface|variable)\s+([A-Z][a-zA-Z0-9_]*)', compiler_error)
    missing_symbols.update(location_matches)
    
    missing_symbols = {sym for sym in missing_symbols if not sym.startswith("GeneratedTriggerTest")}
    
    if not missing_symbols:
        return "No specific missing classes identified in the compiler error."
        
    logger.info(f"🔍 AI API Hallucination Detected! Fetching real source for missing symbols: {missing_symbols}")
    
    extracted_code = []
    chars_limit_per_file = 2000 
    
    for symbol in missing_symbols:
        found_files = list(repo_dir.rglob(f"{symbol}.java"))
        if not found_files:
            continue
            
        target_file = found_files[0]
        try:
            content = target_file.read_text(encoding="utf-8")
            if len(content) > chars_limit_per_file:
                content = content[:chars_limit_per_file] + "\n... [TRUNCATED FOR LENGTH. CORE SIGNATURES ABOVE] ..."
            extracted_code.append(f"--- Real Source Code for: {symbol} (Path: {target_file.relative_to(repo_dir)}) ---\n{content}\n")
        except Exception:
            pass
            
    if not extracted_code:
        return f"Could not find local source code for missing symbols: {', '.join(missing_symbols)}"
        
    return "\n".join(extracted_code)


def call_api_generate_java_test(
    bug_entry: Dict[str, Any], 
    repo_dir: Path,
    api_url: str, 
    api_key: str, 
    api_model: Optional[str] = None,
    previous_code: Optional[str] = None,
    compiler_error: Optional[str] = None
) -> Dict[str, Any]:
    
    model = api_model or DEFAULT_API_MODEL
    raw_issue_id = str(bug_entry.get("instance_id", "000"))
    safe_issue_id = raw_issue_id.replace("-", "_").replace(".", "_")
    class_name = f"GeneratedTriggerTest_{safe_issue_id}"
    
    problem_desc = bug_entry.get("problem_description", "")
    repo_context = _build_repo_context(repo_dir, problem_desc)
    junit_instruction = _detect_junit_version(repo_dir)
    
    # 🚀 状态机：判断是“初始生成”还是“自我修复”
    if previous_code and compiler_error:
        logger.info(f"🛠️ Entering SELF-REPAIR mode for {raw_issue_id}...")
        
        # 触发精准源码反查！
        targeted_source_code = _extract_and_read_missing_classes(compiler_error, repo_dir)
        
        system_instruction = f"""
<Instruction>
You are an expert software vulnerability researcher. 
You previously wrote a JUnit test to reproduce a bug, but it FAILED TO COMPILE.
Your task is to analyze the compiler error, fix the syntax or import issues, and output the corrected JUnit test.
</Instruction>

<Strategy>
1. Analyze the [COMPILER ERROR] to identify missing symbols, wrong imports, or syntax mistakes.
2. CRITICAL: Review the [TARGETED SOURCE CODE FOR MISSING SYMBOLS] provided below. This contains the REAL local source code for the classes you used incorrectly. Use the correct package imports and real method signatures from this context to fix your API hallucinations.
3. Write the corrected, self-contained test class named EXACTLY `{class_name}`.
4. IMPORTANT FRAMEWORK RULE: The target project uses {junit_instruction}.
</Strategy>

<Format>
OUTPUT STRICTLY A VALID JSON OBJECT ONLY.
{{
  "localization_reasoning": "Briefly explain how you fixed the compilation error using the real source code.",
  "language": "java",
  "test_code": "/* Corrected Java code */",
  "class_name": "{class_name}"
}}
</Format>
"""
        user_prompt = f"""
[BUG REPORT]
{problem_desc}

[PREVIOUS CODE THAT FAILED TO COMPILE]
---- JAVA CODE START ----
{previous_code}
---- JAVA CODE END ----

[COMPILER ERROR LOG]
{compiler_error}

[TARGETED SOURCE CODE FOR MISSING SYMBOLS]
{targeted_source_code}
"""
    else:
        logger.info(f"✨ Generating initial test for {raw_issue_id}...")
        
        # 🌳 触发 AST 知识检索！
        ast_dictionary = _build_ast_aware_api_dictionary(repo_dir, problem_desc)
        
        system_instruction = f"""
<Instruction>
You are an expert software vulnerability researcher. 
Your task is Black-Box Bug Reproduction. You MUST write a JUnit test to reproduce the exact issue described in the Bug Report.
You DO NOT have the developer's patch. You must rely on the Bug Report and the provided Repository Context.
</Instruction>

<Strategy>
1. Read the Bug Report to understand the expected vs actual behavior.
2. Review the [LOCAL API DICTIONARY] to understand the EXACT available classes and methods. Pay special attention to language boundaries (Java vs Ruby).
3. Write a self-contained test class named EXACTLY `{class_name}`.
4. IMPORTANT FRAMEWORK RULE: The target project uses {junit_instruction}. You MUST strictly use the corresponding imports and annotations.
5. The test MUST FAIL on this current (buggy) codebase by throwing the exception or failing the assertion described in the issue.
</Strategy>

<Format>
OUTPUT STRICTLY A VALID JSON OBJECT ONLY.
{{
  "localization_reasoning": "Briefly explain how you identified the bug location.",
  "language": "java",
  "test_code": "/* Complete Java code with package and imports */",
  "class_name": "{class_name}"
}}
</Format>
"""
        # 👇 核心：把 AST 字典喂给大模型
        user_prompt = f"""
[BUG REPORT]
{problem_desc}

[LOCAL API DICTIONARY (AST EXTRACTED)]
The following are the EXACT class and method names extracted via Tree-sitter from this repository. 
DO NOT hallucinate APIs. If a class is listed as (Ruby), you MUST use JRuby engine API to interact with it from Java.
{ast_dictionary}

[PRE-PATCH REPOSITORY CONTEXT]
{repo_context}
"""

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=500)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:100]}"}

        raw_content = resp.json()["choices"][0]["message"]["content"]
        clean_json_str = _extract_json_from_markdown(raw_content)
        parsed_data = json.loads(clean_json_str)
        
        if "class_name" not in parsed_data:
            parsed_data["class_name"] = class_name
            
        return {"ok": True, "data": parsed_data}
        
    except Exception as e:
        logger.error(f"Error during API call: {str(e)}")
        return {"ok": False, "error": str(e)}