"""
ast_parser.py
基于 Tree-sitter 的多语言抽象语法树解析引擎。
为 LLM 提供精确的、语义级别的本地 API 字典，消除跨语言 API 幻觉。
🚀 修复版：兼容 Tree-sitter 0.22+ 最新字典格式 API。
"""

import tree_sitter_java as tsjava
import tree_sitter_ruby as tsruby
from tree_sitter import Language, Parser
from pathlib import Path
from constants import logger

# 1. 初始化多语言的 AST 引擎
JAVA_LANG = Language(tsjava.language())
RUBY_LANG = Language(tsruby.language())

# 2. 预编译 AST 查询语句
JAVA_QUERY = JAVA_LANG.query("""
(class_declaration name: (identifier) @class.name)
(method_declaration 
    type: (_) @method.return_type
    name: (identifier) @method.name 
    parameters: (formal_parameters) @method.params)
""")

RUBY_QUERY = RUBY_LANG.query("""
(class name: [ (constant) (scope_resolution name: (constant)) ] @class.name)
(module name: [ (constant) (scope_resolution name: (constant)) ] @module.name)
(method name: (_) @method.name parameters: (method_parameters)? @method.params)
""")

def extract_symbols_from_file(file_path: Path) -> dict:
    """解析单个文件，返回格式化的类与方法签名字典"""
    parser = Parser()
    ext = file_path.suffix
    
    if ext == ".java":
        parser.language = JAVA_LANG
        query = JAVA_QUERY
    elif ext == ".rb":
        parser.language = RUBY_LANG
        query = RUBY_QUERY
    else:
        return {}

    try:
        source_code = file_path.read_bytes()
        tree = parser.parse(source_code)
        captures = query.captures(tree.root_node)
        
        results = {"classes": [], "methods": []}
        
        # 🚀 核心修复：兼容 Tree-sitter 0.22+ (字典格式) 与旧版本 (列表格式)
        if isinstance(captures, dict):
            # 新版 API 逻辑
            for capture_name, nodes in captures.items():
                for node in nodes:
                    # 安全解码
                    text = node.text.decode('utf-8') if isinstance(node.text, bytes) else node.text
                    
                    if capture_name in ["class.name", "module.name"]:
                        results["classes"].append(text)
                    elif capture_name == "method.name":
                        results["methods"].append(text)
        else:
            # 老版 API 逻辑
            for node, capture_name in captures:
                text = node.text.decode('utf-8') if isinstance(node.text, bytes) else node.text
                
                if capture_name in ["class.name", "module.name"]:
                    results["classes"].append(text)
                elif capture_name == "method.name":
                    results["methods"].append(text)
                    
        return results
    except Exception as e:
        logger.warning(f"AST Parsing failed for {file_path.name}: {e}")
        return {}