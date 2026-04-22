#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bug Report to Test Case Generator
自动化框架：从bug report生成测试用例
"""

import json
import os
import re
import subprocess
import tempfile
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
from dataclasses import dataclass, asdict

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('test_case_generator.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class BugReport:
    """Bug Report数据结构"""
    issue_id: int
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    repo_fullname: str


@dataclass
class MethodSignature:
    """方法签名数据结构"""
    file_path: str
    signatures: List[str]


@dataclass
class TestCaseResult:
    """测试用例生成结果"""
    issue_number: int
    test_case_code: str
    compilation_success: bool
    compilation_error: Optional[str] = None
    api_response_time: Optional[float] = None


class IssueParser:
    """解析issue_body，提取关键信息"""
    
    @staticmethod
    def extract_version(text: str) -> str:
        """提取版本信息"""
        # 查找版本号模式
        patterns = [
            r'version[:\s]+([0-9]+\.[0-9]+(?:\.[0-9]+)?)',
            r'Version[:\s]+([0-9]+\.[0-9]+(?:\.[0-9]+)?)',
            r'v([0-9]+\.[0-9]+(?:\.[0-9]+)?)',
            r'JDK[:\s]+([0-9]+)',
            r'Java[:\s]+version[:\s]+([0-9]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return "未知版本"
    
    @staticmethod
    def extract_steps(text: str) -> List[str]:
        """提取重现步骤"""
        steps = []
        # 查找步骤列表
        step_patterns = [
            r'(?:步骤|Step|Reproduction|重现步骤)[:\s]*\n((?:\d+[\.\)]\s*[^\n]+\n?)+)',
            r'(\d+[\.\)]\s*[^\n]+)',
        ]
        for pattern in step_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            if matches:
                if isinstance(matches[0], tuple):
                    steps = [m.strip() for m in matches[0].split('\n') if m.strip()]
                else:
                    steps = [m.strip() for m in matches if m.strip()]
                if steps:
                    break
        
        # 如果没有找到，尝试从markdown列表提取
        if not steps:
            lines = text.split('\n')
            for line in lines:
                if re.match(r'^\d+[\.\)]\s+', line):
                    steps.append(line.strip())
        
        return steps[:10] if steps else ["未提供具体步骤"]
    
    @staticmethod
    def extract_error_info(text: str) -> Tuple[str, str]:
        """提取错误信息和错误日志"""
        error_info = ""
        error_log = ""
        
        # 查找错误信息
        error_patterns = [
            r'(?:错误|Error|Exception|错误信息)[:\s]*\n?([^\n]+)',
            r'(java\.lang\.[A-Za-z]+Exception[^\n]*)',
            r'(Illegal[^\n]*)',
        ]
        for pattern in error_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                error_info = match.group(1).strip()
                break
        
        # 查找错误日志（通常在代码块中）
        log_patterns = [
            r'```(?:log|txt|error)?\n(.*?)```',
            r'Caused by:.*?(?=\n\n|\n[A-Z])',
            r'at\s+[^\n]+(?:\n\s+at\s+[^\n]+)*',
        ]
        for pattern in log_patterns:
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if matches:
                error_log = '\n'.join(matches[:3])  # 取前3个匹配
                break
        
        return error_info or "未提供错误信息", error_log or "未提供错误日志"
    
    @staticmethod
    def extract_expected_actual(text: str) -> Tuple[str, str]:
        """提取预期行为和实际行为"""
        expected = ""
        actual = ""
        
        # 查找预期行为
        expected_patterns = [
            r'(?:预期|Expected|期望)[:\s]*\n?([^\n]+(?:\n[^\n]+)*?)(?=\n(?:实际|Actual|###)|$)',
            r'Expected behavior[:\s]*\n?([^\n]+(?:\n[^\n]+)*?)(?=\nActual|$)',
        ]
        for pattern in expected_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                expected = match.group(1).strip()
                break
        
        # 查找实际行为
        actual_patterns = [
            r'(?:实际|Actual)[:\s]*\n?([^\n]+(?:\n[^\n]+)*?)(?=\n(?:###|$))',
            r'Actual behavior[:\s]*\n?([^\n]+(?:\n[^\n]+)*?)(?=\n(?:###|$))',
        ]
        for pattern in actual_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                actual = match.group(1).strip()
                break
        
        return expected or "未提供预期行为", actual or "未提供实际行为"
    
    @staticmethod
    def extract_user_operations(text: str) -> str:
        """提取用户操作"""
        # 查找用户操作相关描述
        operation_patterns = [
            r'(?:操作|Operation|用户操作|Steps to reproduce)[:\s]*\n?([^\n]+(?:\n[^\n]+)*?)(?=\n(?:###|重现|Reproduction|$))',
        ]
        for pattern in operation_patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        
        # 如果没有找到，从重现步骤中提取
        steps = IssueParser.extract_steps(text)
        if steps and steps[0] != "未提供具体步骤":
            return "; ".join(steps[:3])
        
        return "未提供用户操作"


class PromptBuilder:
    """构建符合prompt.txt格式的prompt"""
    
    def __init__(self, prompt_template_path: str):
        with open(prompt_template_path, 'r', encoding='utf-8') as f:
            self.template = f.read()
    
    def build(self, bug_report: BugReport, method_signatures: List[MethodSignature]) -> str:
        """构建完整的prompt"""
        parser = IssueParser()
        
        # 提取信息
        version = parser.extract_version(bug_report.issue_body)
        steps = parser.extract_steps(bug_report.issue_body)
        error_info, error_log = parser.extract_error_info(bug_report.issue_body)
        expected, actual = parser.extract_expected_actual(bug_report.issue_body)
        user_ops = parser.extract_user_operations(bug_report.issue_body)
        
        # 构建方法签名信息
        method_info = ""
        if method_signatures:
            method_info = "\n相关方法签名：\n"
            for ms in method_signatures:
                method_info += f"文件: {ms.file_path}\n"
                for sig in ms.signatures:
                    if sig != "UNKNOWN":
                        method_info += f"  - {sig}\n"
        
        # 构建Input部分 - 严格按照prompt.txt格式
        input_content = f"""问题标题：{bug_report.issue_title}
                            问题描述：{bug_report.issue_body[:2000]}{'...' if len(bug_report.issue_body) > 2000 else ''}
                            受影响版本：{version}
                            用户操作：{user_ops}
                            重现步骤：{', '.join(steps)}
                            错误信息：{error_info}
                            错误日志：{error_log[:1000]}{'...' if len(error_log) > 1000 else ''}
                            预期行为：{expected}
                            实际行为：{actual}"""
        
        if method_info:
            input_content += method_info
        
        # 查找并替换Input部分
        # prompt.txt包含中英文两个Input部分，我们替换中文部分
        prompt = self.template
        
        # 查找中文Input部分（第一个）
        chinese_input_pattern = r'(<Input>\s*Bug Report关键信息[：:]*\s*\n)(.*?)(\n\s*</Input>)'
        match = re.search(chinese_input_pattern, prompt, re.DOTALL)
        
        if match:
            # 替换中文Input部分的内容
            prompt = re.sub(
                chinese_input_pattern,
                r'\1' + input_content + r'\3',
                prompt,
                count=1,  # 只替换第一个匹配
                flags=re.DOTALL
            )
        else:
            # 如果没有找到，尝试在第一个</Input>之前插入
            first_input_end = prompt.find('</Input>')
            if first_input_end != -1:
                # 在第一个</Input>之前插入
                prompt = prompt[:first_input_end] + f'\n{input_content}\n' + prompt[first_input_end:]
            else:
                # 如果完全没有Input标签，在文件末尾添加
                prompt += f"\n\n<Input>\nBug Report关键信息：\n{input_content}\n\n</Input>"
        
        return prompt


class DeepSeekAPIClient:
    """DeepSeek API客户端"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com", default_model: str = "deepseek-chat"):
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        })
    
    def generate_test_case(self, prompt: str, model: Optional[str] = None) -> Tuple[str, float]:
        """调用API生成测试用例"""
        if model is None:
            model = self.default_model
        
        url = f"{self.base_url}/v1/chat/completions"
        
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 4000
        }
        
        start_time = time.time()
        try:
            response = self.session.post(url, json=payload, timeout=120)
            response.raise_for_status()
            
            result = response.json()
            elapsed_time = time.time() - start_time
            
            # 提取生成的代码
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                # 尝试提取代码块
                code_match = re.search(r'```(?:java)?\n?(.*?)```', content, re.DOTALL)
                if code_match:
                    return code_match.group(1).strip(), elapsed_time
                return content.strip(), elapsed_time
            else:
                raise Exception("API响应中没有choices字段")
        except requests.exceptions.RequestException as e:
            elapsed_time = time.time() - start_time
            raise Exception(f"API调用失败: {str(e)}")


class JavaCompiler:
    """Java代码编译检查器"""
    
    def __init__(self, java_home: Optional[str] = None):
        self.java_home = java_home
        self.javac_path = self._find_javac()
    
    def _find_javac(self) -> str:
        """查找javac路径"""
        if self.java_home:
            javac = Path(self.java_home) / "bin" / "javac"
            if javac.exists():
                return str(javac)
        
        # 尝试从PATH中查找
        try:
            result = subprocess.run(
                ["where" if os.name == "nt" else "which", "javac"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip().split('\n')[0]
        except:
            pass
        
        return "javac"  # 默认使用PATH中的javac
    
    def _extract_class_name(self, code: str) -> Optional[str]:
        """从Java代码中提取public类名"""
        # 查找public class声明
        pattern = r'public\s+class\s+(\w+)'
        match = re.search(pattern, code)
        if match:
            return match.group(1)
        
        # 如果没有public class，查找任何class声明
        pattern = r'class\s+(\w+)'
        match = re.search(pattern, code)
        if match:
            return match.group(1)
        
        return None
    
    def _fix_compilation_errors_with_ai(self, code: str, error_msg: str, api_client) -> str:
        """使用AI修复编译错误
        
        Args:
            code: 原始Java代码
            error_msg: 编译错误信息
            api_client: DeepSeek API客户端
            
        Returns:
            修复后的代码
        """
        prompt = f"""你是一个Java编译错误修复专家。请修复以下Java代码的编译错误。

                    原始代码：
                    ```java
                    {code}
                    ```

                    编译错误信息：
                    ```
                    {error_msg}
                    ```

                    请修复代码，使其能够成功编译。要求：
                    1. 保持代码的主要逻辑和功能
                    2. 如果缺少外部依赖，可以移除或注释相关代码
                    3. 确保修复后的代码可以独立编译
                    4. 只返回修复后的Java代码，不要包含任何解释或markdown格式

                    修复后的代码："""

        try:
            # 使用默认模型
            fixed_code, _ = api_client.generate_test_case(prompt)
            
            # 清理返回的代码（移除可能的markdown标记）
            fixed_code = re.sub(r'```(?:java)?\s*\n?', '', fixed_code)
            fixed_code = re.sub(r'```\s*$', '', fixed_code)
            fixed_code = fixed_code.strip()
            
            logger.info(f"  AI修复完成，代码长度: {len(fixed_code)} 字符")
            return fixed_code
        except Exception as e:
            logger.warning(f"  AI修复失败: {str(e)}，返回原始代码")
            return code
    
    def check_compilation(
        self,
        code: str,
        classpath: Optional[str] = None,
        try_fix: bool = True,
        api_client=None,
        max_fix_iterations: int = 5,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """检查代码能否编译

        Args:
            code: Java源代码
            classpath: Java类路径
            try_fix: 是否尝试修复编译错误（默认True）
            api_client: API客户端（用于AI修复，如果提供则使用AI修复）
            max_fix_iterations: 最多尝试修复的次数（防止死循环）

        Returns:
            (编译成功, 错误信息, 最终代码)
        """
        original_code = code

        with tempfile.TemporaryDirectory() as tmpdir:
            for iteration in range(max_fix_iterations if try_fix else 1):
                # 提取类名
                class_name = self._extract_class_name(code)

                # 如果找到了类名，使用类名作为文件名；否则使用默认名称
                if class_name:
                    java_filename = f"{class_name}.java"
                else:
                    # 如果没有找到类名，尝试将代码包装在一个类中
                    java_filename = "TestCase.java"
                    # 如果代码中没有类声明，包装它
                    if "class" not in code:
                        code = f"public class TestCase {{\n{code}\n}}"

                # 创建临时Java文件
                test_file = Path(tmpdir) / java_filename
                test_file.write_text(code, encoding="utf-8")

                # 构建编译命令
                cmd = [self.javac_path, "-encoding", "UTF-8"]
                if classpath:
                    cmd.extend(["-cp", classpath])
                cmd.append(str(test_file))

                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        cwd=tmpdir,
                    )

                    if result.returncode == 0:
                        # 编译成功
                        if iteration > 0:
                            logger.info(
                                "  ✓ 编译错误已修复（共尝试 %d 次）", iteration
                            )
                        return True, None, code

                    # 编译失败
                    error_msg = result.stderr or result.stdout

                    # 如果不需要或不能再修复，直接返回
                    if not try_fix or iteration == max_fix_iterations - 1:
                        return False, error_msg, code

                    # 使用AI或简单规则修复
                    if api_client:
                        logger.debug(
                            "  使用AI第 %d 次修复编译错误...", iteration + 1
                        )
                        new_code = self._fix_compilation_errors_with_ai(
                            code, error_msg, api_client
                        )
                    else:
                        new_code = self._fix_compilation_errors_simple(
                            code, error_msg
                        )

                    # 如果AI没有真正修改代码，就没有继续尝试的意义，直接返回
                    if new_code.strip() == code.strip():
                        logger.debug("  修复未改变代码，停止进一步尝试")
                        return False, error_msg, code

                    code = new_code

                except subprocess.TimeoutExpired:
                    return False, "编译超时", code
                except Exception as e:
                    return False, f"编译过程出错: {str(e)}", code
    
    def _fix_compilation_errors_simple(self, code: str, error_msg: str) -> str:
        """简单的编译错误修复（作为fallback）"""
        fixed_code = code
        
        # 如果错误是找不到包或类，尝试移除相关的import和使用
        if "程序包" in error_msg or "找不到符号" in error_msg or "package" in error_msg.lower() or "cannot find symbol" in error_msg.lower():
            # 提取所有import语句
            import_pattern = r'^import\s+[^;]+;'
            imports = re.findall(import_pattern, code, re.MULTILINE)
            
            # 从错误信息中提取找不到的包/类
            missing_classes = set()
            missing_patterns = [
                r'程序包([^\s]+)不存在',
                r'找不到符号[：:]\s*类\s+([^\s]+)',
                r'cannot find symbol[:\s]+class\s+([^\s]+)',
                r'package\s+([^\s]+)\s+does not exist',
            ]
            
            for pattern in missing_patterns:
                matches = re.findall(pattern, error_msg, re.IGNORECASE)
                missing_classes.update(matches)
            
            # 移除找不到的import语句
            for imp in imports:
                for missing in missing_classes:
                    if missing.replace('.', '\\').replace('\\', '.') in imp or imp.replace('import ', '').replace(';', '').startswith(missing.split('.')[0]):
                        fixed_code = fixed_code.replace(imp + '\n', '')
                        fixed_code = fixed_code.replace(imp, '')
                        logger.debug(f"  移除找不到的import: {imp}")
            
            # 尝试注释掉使用这些类的地方
            lines = fixed_code.split('\n')
            fixed_lines = []
            for line in lines:
                should_skip = False
                for missing in missing_classes:
                    class_name = missing.split('.')[-1]
                    if class_name in line and ('import' not in line):
                        if line.strip() and not line.strip().startswith('//'):
                            fixed_lines.append('// ' + line + ' // 已注释：找不到类 ' + missing)
                            should_skip = True
                            break
                
                if not should_skip:
                    fixed_lines.append(line)
            
            fixed_code = '\n'.join(fixed_lines)
        
        return fixed_code


class TestCaseGenerator:
    """测试用例生成器主类"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.issues_data = []
        self.methods_data = []
        self.prompt_builder = PromptBuilder(config['prompt_template_path'])
        self.api_client = DeepSeekAPIClient(
            config['deepseek_api_key'],
            config.get('deepseek_base_url', 'https://api.deepseek.com'),
            config.get('deepseek_model', 'deepseek-chat')
        )
        self.compiler = JavaCompiler(config.get('java_home'))
    
    def load_data(self):
        """加载数据文件"""
        # 加载issues
        with open(self.config['issues_json_path'], 'r', encoding='utf-8') as f:
            self.issues_data = json.load(f)
        
        # 加载方法签名
        with open(self.config['methods_json_path'], 'r', encoding='utf-8') as f:
            self.methods_data = json.load(f)
        
        logger.info(f"已加载 {len(self.issues_data)} 个issues")
        logger.info(f"已加载 {len(self.methods_data)} 个方法签名记录")
    
    def find_method_signatures(self, issue_number: int, commit_sha: Optional[str] = None, 
                               repo_name: Optional[str] = None) -> List[MethodSignature]:
        """根据issue_number和commit_sha查找方法签名
        
        Args:
            issue_number: issue编号
            commit_sha: commit SHA哈希值（用于唯一标识，如果提供则必须匹配）
            repo_name: repo名称（可选，用于进一步过滤）
        """
        signatures = []
        found_count = 0
        matched_repos = set()
        
        for item in self.methods_data:
            # 首先匹配issue_number
            if item.get('issue_number') != issue_number:
                continue
            
            found_count += 1
            item_repo = item.get('repo', '')
            item_commit_sha = item.get('commit_sha')
            
            # 如果提供了commit_sha，必须匹配（用于唯一标识）
            if commit_sha:
                if item_commit_sha != commit_sha:
                    continue
            
            # 如果指定了repo_name，需要匹配；否则不限制
            if repo_name:
                # 支持多种匹配方式：
                # 1. 完全匹配
                # 2. 只匹配repo名称部分（忽略owner）
                # 3. 忽略大小写
                repo_match = False
                if item_repo == repo_name:
                    repo_match = True
                elif '/' in item_repo and '/' in repo_name:
                    # 提取repo名称部分（最后一个/之后的部分）
                    item_repo_name = item_repo.split('/')[-1].lower()
                    target_repo_name = repo_name.split('/')[-1].lower()
                    if item_repo_name == target_repo_name:
                        repo_match = True
                
                if not repo_match:
                    continue
            
            matched_repos.add(item_repo)
            extracted = item.get('extracted', {})
            for file_path, methods in extracted.items():
                if methods and isinstance(methods, list):
                    # 过滤掉UNKNOWN和空字符串
                    valid_methods = [m for m in methods if m and m != "UNKNOWN"]
                    if valid_methods:
                        signatures.append(MethodSignature(
                            file_path=file_path,
                            signatures=valid_methods
                        ))
        
        if found_count == 0:
            logger.debug(f"在extracted_methods_javaparser.json中未找到issue_number={issue_number}" + 
                        (f"且commit_sha={commit_sha}" if commit_sha else "") + "的记录")
        elif len(signatures) == 0:
            if repo_name:
                logger.debug(f"找到issue_number={issue_number}的记录（repo: {matched_repos}），但与目标repo '{repo_name}'不匹配或没有有效的方法签名")
            else:
                logger.debug(f"找到issue_number={issue_number}的记录，但没有有效的方法签名（可能都是UNKNOWN）")
        
        return signatures
    
    def generate_for_issue(self, issue: Dict, issue_index: Optional[int] = None) -> TestCaseResult:
        """为单个issue生成测试用例
        
        Args:
            issue: issue数据字典
            issue_index: issue在数组中的索引（用于唯一标识）
        """
        issue_number = issue.get('issue_number')
        commit_sha = issue.get('commit_sha')
        issue_id_str = f"#{issue_number}"
        if commit_sha:
            issue_id_str += f" (commit: {commit_sha[:8]}...)"
        if issue_index is not None:
            issue_id_str += f" [索引: {issue_index}]"
        
        logger.info(f"处理 Issue {issue_id_str}: {issue.get('issue_title', '')[:50]}...")
        
        # 创建BugReport对象
        bug_report = BugReport(
            issue_id=issue.get('issue_id'),
            issue_number=issue_number,
            issue_title=issue.get('issue_title', ''),
            issue_body=issue.get('issue_body', ''),
            issue_url=issue.get('issue_url', ''),
            repo_fullname=issue.get('repo_fullname', '')
        )
        
        # 查找方法签名（使用commit_sha进行精确匹配）
        method_signatures = self.find_method_signatures(
            issue_number,
            commit_sha=commit_sha,  # 使用commit_sha进行唯一匹配
            repo_name=bug_report.repo_fullname
        )
        
        if method_signatures:
            logger.info(f"  找到 {len(method_signatures)} 个相关方法签名")
        else:
            logger.warning(f"  未找到相关方法签名")
        
        # 构建prompt
        try:
            prompt = self.prompt_builder.build(bug_report, method_signatures)
            logger.debug(f"  Prompt长度: {len(prompt)} 字符")
        except Exception as e:
            logger.error(f"  构建prompt失败: {str(e)}")
            return TestCaseResult(
                issue_number=issue_number,
                test_case_code="",
                compilation_success=False,
                compilation_error=f"构建prompt失败: {str(e)}",
                api_response_time=None
            )
        
        # 调用API生成测试用例
        try:
            test_case_code, api_time = self.api_client.generate_test_case(
                prompt,
                self.config.get('deepseek_model', 'deepseek-chat')
            )
            logger.info(f"  API调用成功，耗时: {api_time:.2f}秒，生成代码长度: {len(test_case_code)} 字符")
        except Exception as e:
            logger.error(f"  API调用失败: {str(e)}")
            return TestCaseResult(
                issue_number=issue_number,
                test_case_code="",
                compilation_success=False,
                compilation_error=f"API调用失败: {str(e)}",
                api_response_time=None
            )
        
        # 检查编译（启用自动修复，使用AI修复，支持多次迭代）
        use_ai_fix = self.config.get("use_ai_fix_compilation", True)
        max_fix_iterations = int(self.config.get("max_fix_iterations", 5))

        compilation_success, compilation_error, final_code = self.compiler.check_compilation(
            test_case_code,
            self.config.get("java_classpath"),
            try_fix=self.config.get("auto_fix_compilation", True),
            api_client=self.api_client if use_ai_fix else None,
            max_fix_iterations=max_fix_iterations,
        )

        # 使用修复后的代码
        if final_code is not None and final_code != test_case_code:
            test_case_code = final_code

        if compilation_success:
            logger.info("  ✓ 编译检查通过")
        else:
            error_msg = compilation_error[:200] if compilation_error else "未知错误"
            logger.warning(f"  ✗ 编译检查失败: {error_msg}")
        
        return TestCaseResult(
            issue_number=issue_number,
            test_case_code=test_case_code,  # 使用可能修复后的代码
            compilation_success=compilation_success,
            compilation_error=compilation_error,
            api_response_time=api_time
        )
    
    def generate_batch(self, issue_numbers: Optional[List[int]] = None, limit: Optional[int] = None, 
                       start_index: Optional[int] = None, end_index: Optional[int] = None) -> List[TestCaseResult]:
        """批量生成测试用例
        
        Args:
            issue_numbers: 指定issue_number列表（按issue_number筛选）
            limit: 限制处理数量（从筛选结果中取前N个）
            start_index: 起始索引（按数组索引顺序处理）
            end_index: 结束索引（按数组索引顺序处理，不包含）
        """
        results = []
        
        # 筛选issues
        issues_to_process = self.issues_data
        
        # 如果指定了issue_numbers，按issue_number筛选
        if issue_numbers:
            found_issues = []
            not_found = []
            for issue_num in issue_numbers:
                found = False
                for issue in self.issues_data:
                    if issue.get('issue_number') == issue_num:
                        found_issues.append(issue)
                        found = True
                        break
                if not found:
                    not_found.append(issue_num)
            
            if not_found:
                logger.warning(f"以下issue_number未找到: {not_found}")
            
            issues_to_process = found_issues
        
        # 如果指定了索引范围，按索引筛选
        elif start_index is not None or end_index is not None:
            start = start_index if start_index is not None else 0
            end = end_index if end_index is not None else len(issues_to_process)
            issues_to_process = issues_to_process[start:end]
            logger.info(f"按索引范围处理: [{start}:{end}]")
        
        # 如果指定了limit，限制数量
        if limit:
            issues_to_process = issues_to_process[:limit]
        
        logger.info(f"开始处理 {len(issues_to_process)} 个issues...")
        
        if len(issues_to_process) == 0:
            logger.warning("没有找到需要处理的issues，请检查筛选条件")
        
        for idx, issue in enumerate(issues_to_process, 1):
            logger.info(f"\n[{idx}/{len(issues_to_process)}]")
            try:
                # 计算原始索引（用于唯一标识）
                if start_index is not None:
                    original_index = start_index + idx - 1
                elif issue_numbers:
                    # 如果按issue_number筛选，尝试找到原始索引
                    original_index = next((i for i, item in enumerate(self.issues_data) 
                                         if item.get('issue_number') == issue.get('issue_number') 
                                         and item.get('commit_sha') == issue.get('commit_sha')), None)
                else:
                    original_index = None
                
                result = self.generate_for_issue(issue, issue_index=original_index)
                results.append(result)
                
                # 保存中间结果（总是保存，除非明确禁用）
                if self.config.get('save_intermediate', True):
                    self._save_result(result)
                else:
                    logger.debug(f"  save_intermediate已禁用，跳过保存Issue {result.issue_number}")
                
                # API限流控制
                if self.config.get('api_delay', 0) > 0:
                    time.sleep(self.config.get('api_delay', 0))
            except Exception as e:
                logger.error(f"  处理失败: {str(e)}", exc_info=True)
                results.append(TestCaseResult(
                    issue_number=issue.get('issue_number', 0),
                    test_case_code="",
                    compilation_success=False,
                    compilation_error=f"处理异常: {str(e)}"
                ))
        
        return results
    
    def _save_result(self, result: TestCaseResult):
        """保存单个结果"""
        output_dir = Path(self.config.get('output_dir', 'output'))
        output_dir.mkdir(exist_ok=True)
        
        # 保存测试用例代码（即使为空也保存）
        if result.test_case_code:
            code_file = output_dir / f"test_case_{result.issue_number}.java"
            code_file.write_text(result.test_case_code, encoding='utf-8')
            logger.debug(f"  已保存测试用例代码: {code_file}")
        else:
            logger.warning(f"  Issue {result.issue_number} 没有测试用例代码，跳过保存")
        
        # 保存结果元数据（总是保存）
        meta_file = output_dir / f"result_{result.issue_number}.json"
        meta_file.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        logger.debug(f"  已保存结果元数据: {meta_file}")
    
    def save_summary(self, results: List[TestCaseResult], append: bool = True):
        """保存汇总报告
        
        Args:
            results: 本次处理的结果列表
            append: 是否累加到现有summary.json（默认True）
        """
        output_dir = Path(self.config.get('output_dir', 'output'))
        output_dir.mkdir(exist_ok=True)
        
        summary_file = output_dir / "summary.json"
        
        # 如果启用累加模式且文件存在，加载现有数据
        existing_results = []
        if append and summary_file.exists():
            try:
                with open(summary_file, 'r', encoding='utf-8') as f:
                    existing_data = json.load(f)
                    existing_results = existing_data.get('results', [])
                    logger.info(f"加载现有summary.json，已有 {len(existing_results)} 条记录")
            except Exception as e:
                logger.warning(f"加载现有summary.json失败: {str(e)}，将创建新文件")
                existing_results = []
        
        # 合并结果（避免重复）
        existing_issue_numbers = {r.get('issue_number') for r in existing_results if isinstance(r, dict)}
        new_results = [asdict(r) for r in results]
        
        # 移除已存在的记录，然后添加新记录
        existing_results = [r for r in existing_results if r.get('issue_number') not in {r['issue_number'] for r in new_results}]
        all_results = existing_results + new_results
        
        # 计算统计信息
        summary = {
            "total": len(all_results),
            "compilation_success": sum(1 for r in all_results if r.get('compilation_success', False)),
            "compilation_failed": sum(1 for r in all_results if not r.get('compilation_success', False)),
            "api_success": sum(1 for r in all_results if r.get('api_response_time') is not None),
            "average_api_time": sum(r.get('api_response_time', 0) for r in all_results if r.get('api_response_time')) / max(1, sum(1 for r in all_results if r.get('api_response_time'))),
            "results": all_results
        }
        
        summary_file.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        
        logger.info(f"\n汇总报告已保存到: {summary_file}")
        logger.info(f"总计: {summary['total']} (本次新增: {len(new_results)})")
        logger.info(f"编译成功: {summary['compilation_success']}")
        logger.info(f"编译失败: {summary['compilation_failed']}")
        logger.info(f"平均API响应时间: {summary['average_api_time']:.2f}秒")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Bug Report to Test Case Generator')
    parser.add_argument('--config', type=str, default='config.json', help='配置文件路径')
    parser.add_argument('--issue', type=int, nargs='+', help='指定issue number(s)，按issue_number筛选')
    parser.add_argument('--limit', type=int, help='限制处理数量（从筛选结果中取前N个）')
    parser.add_argument('--start', type=int, help='起始索引（按数组索引顺序处理，从0开始）')
    parser.add_argument('--end', type=int, help='结束索引（按数组索引顺序处理，不包含此索引）')
    
    args = parser.parse_args()
    
    # 加载配置
    if not os.path.exists(args.config):
        logger.error(f"配置文件不存在: {args.config}")
        logger.error("请先创建config.json文件，参考config.example.json")
        return
    
    with open(args.config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 创建生成器
    generator = TestCaseGenerator(config)
    generator.load_data()
    
    # 生成测试用例
    results = generator.generate_batch(
        issue_numbers=args.issue,
        limit=args.limit,
        start_index=args.start,
        end_index=args.end
    )
    
    # 保存汇总（默认累加模式）
    append_mode = not args.get('--no-append', False) if hasattr(args, 'get') else True
    generator.save_summary(results, append=True)


if __name__ == '__main__':
    main()

