# 项目结构说明

## 文件结构

```
bug-report2-test-cases/
├── test_case_generator.py      # 主框架文件（核心功能）
├── example_usage.py             # 使用示例脚本
├── config.example.json          # 配置文件示例
├── requirements.txt             # Python依赖
├── README_CN.md                 # 中文文档
├── QUICKSTART.md                # 快速开始指南
├── PROJECT_STRUCTURE.md         # 本文件
│
├── issues_all.json              # Bug report数据（输入）
├── extracted_methods_javaparser.json  # 方法签名数据（输入）
├── prompt.txt                   # Prompt模板（输入）
│
└── output/                      # 输出目录（自动创建）
    ├── test_case_*.java         # 生成的测试用例
    ├── result_*.json            # 结果元数据
    └── summary.json             # 汇总报告
```

## 核心模块说明

### 1. test_case_generator.py

主框架文件，包含以下核心类：

#### BugReport (dataclass)
- 存储bug report的基本信息

#### MethodSignature (dataclass)
- 存储方法签名信息

#### TestCaseResult (dataclass)
- 存储测试用例生成结果

#### IssueParser
- **extract_version()**: 提取版本信息
- **extract_steps()**: 提取重现步骤
- **extract_error_info()**: 提取错误信息和日志
- **extract_expected_actual()**: 提取预期和实际行为
- **extract_user_operations()**: 提取用户操作

#### PromptBuilder
- **build()**: 构建符合prompt.txt格式的完整prompt

#### DeepSeekAPIClient
- **generate_test_case()**: 调用DeepSeek API生成测试用例

#### JavaCompiler
- **check_compilation()**: 检查Java代码能否编译

#### TestCaseGenerator
- **load_data()**: 加载数据文件
- **find_method_signatures()**: 查找方法签名
- **generate_for_issue()**: 为单个issue生成测试用例
- **generate_batch()**: 批量生成测试用例
- **save_summary()**: 保存汇总报告

## 工作流程

```
1. 加载数据
   ├── issues_all.json → issues_data
   └── extracted_methods_javaparser.json → methods_data

2. 处理每个issue
   ├── 解析issue_body → 提取关键信息
   ├── 查找方法签名 → 根据issue_number匹配
   ├── 构建prompt → 按照prompt.txt格式
   ├── 调用API → 生成测试用例代码
   └── 编译检查 → 验证代码可编译性

3. 保存结果
   ├── test_case_{issue_number}.java
   ├── result_{issue_number}.json
   └── summary.json
```

## 数据流

```
issues_all.json
    ↓
IssueParser (提取信息)
    ↓
extracted_methods_javaparser.json
    ↓
find_method_signatures (匹配方法)
    ↓
PromptBuilder (构建prompt)
    ↓
DeepSeekAPIClient (生成代码)
    ↓
JavaCompiler (编译检查)
    ↓
保存结果
```

## 配置说明

### config.json 关键配置项

- **deepseek_api_key**: DeepSeek API密钥（必需）
- **issues_json_path**: issues数据文件路径
- **methods_json_path**: 方法签名数据文件路径
- **prompt_template_path**: prompt模板文件路径
- **output_dir**: 输出目录
- **java_home**: Java安装路径（可选）
- **api_delay**: API调用间隔（秒，避免限流）

## 扩展点

### 1. 自定义信息提取

继承`IssueParser`类，重写提取方法：
```python
class CustomIssueParser(IssueParser):
    def extract_version(self, text: str) -> str:
        # 自定义版本提取逻辑
        pass
```

### 2. 自定义编译检查

继承`JavaCompiler`类，重写检查方法：
```python
class CustomJavaCompiler(JavaCompiler):
    def check_compilation(self, code: str, classpath: Optional[str] = None):
        # 自定义编译检查逻辑
        pass
```

### 3. 自定义API客户端

继承`DeepSeekAPIClient`类，支持其他API：
```python
class CustomAPIClient(DeepSeekAPIClient):
    def generate_test_case(self, prompt: str, model: str = "deepseek-chat"):
        # 自定义API调用逻辑
        pass
```

## 日志和调试

- 日志文件：`test_case_generator.log`
- 日志级别：INFO（可在代码中调整）
- 调试信息：查看日志文件了解详细执行过程

## 性能考虑

1. **API限流**：设置适当的`api_delay`避免触发限流
2. **批量处理**：使用`--limit`参数控制处理数量
3. **中间保存**：`save_intermediate=true`确保进度不丢失
4. **编译检查**：编译检查可能需要较长时间，可考虑异步处理

## 错误处理

框架包含完善的错误处理：
- API调用失败：记录错误，继续处理下一个
- 编译失败：记录错误信息，保存到结果中
- 数据缺失：使用默认值，记录警告
- 异常捕获：所有异常都会被捕获并记录

