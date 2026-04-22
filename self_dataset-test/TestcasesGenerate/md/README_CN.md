# Bug Report to Test Case Generator

自动化框架：从bug report生成测试用例，通过DeepSeek API自动生成Java测试用例并检查编译。

## 功能特性

1. **自动解析Bug Report**：从`issues_all.json`中提取bug信息
2. **方法签名查找**：根据issue number从`extracted_methods_javaparser.json`中查找相关方法签名
3. **Prompt构建**：严格按照`prompt.txt`格式构建完整的prompt
4. **API调用**：调用DeepSeek API生成测试用例代码
5. **编译检查**：自动检查生成的测试用例能否通过Java编译

## 安装

1. 安装Python依赖：
```bash
pip install -r requirements.txt
```

2. 配置DeepSeek API密钥：
```bash
cp config.example.json config.json
# 编辑config.json，填入你的DeepSeek API密钥
```

## 使用方法

### 基本使用

处理所有issues（限制前10个）：
```bash
python test_case_generator.py --limit 10
```

处理指定的issue（按issue_number）：
```bash
python test_case_generator.py --issue 654 721 777
```

按数组索引顺序处理（从第0个开始，处理10个）：
```bash
python test_case_generator.py --start 0 --end 10
```

按数组索引顺序处理前5个：
```bash
python test_case_generator.py --start 0 --limit 5
```

### 配置文件说明

`config.json`包含以下配置项：

- `deepseek_api_key`: DeepSeek API密钥（必需）
- `deepseek_base_url`: API基础URL（默认：https://api.deepseek.com）
- `deepseek_model`: 使用的模型（默认：deepseek-chat）
- `issues_json_path`: issues JSON文件路径
- `methods_json_path`: 方法签名JSON文件路径
- `prompt_template_path`: prompt模板文件路径
- `output_dir`: 输出目录
- `java_home`: Java安装路径（可选，会自动查找）
- `java_classpath`: Java类路径（可选）
- `save_intermediate`: 是否保存中间结果（默认：true）
- `api_delay`: API调用间隔（秒，默认：1.0）

## 输出结果

生成的测试用例和结果保存在`output`目录下：

- `test_case_{issue_number}.java`: 生成的测试用例代码
- `result_{issue_number}.json`: 结果元数据（包含编译状态等）
- `summary.json`: 汇总报告

## 工作流程

1. **数据加载**：加载issues和方法签名数据
2. **信息提取**：从issue_body中提取关键信息（版本、步骤、错误信息等）
3. **方法签名匹配**：根据issue_number查找相关方法签名
4. **Prompt构建**：按照prompt.txt格式构建完整prompt
5. **API调用**：调用DeepSeek API生成测试用例
6. **编译检查**：使用javac检查生成的代码能否编译
7. **结果保存**：保存测试用例代码和元数据

## 注意事项

1. **API限流**：建议设置适当的`api_delay`避免API限流
2. **Java环境**：确保系统已安装Java并配置了JAVA_HOME或PATH
3. **编译检查**：编译检查可能需要额外的依赖库，可通过`java_classpath`配置
4. **错误处理**：如果API调用失败或编译失败，会在结果中记录错误信息

## 示例

```bash
# 处理issue 654
python test_case_generator.py --issue 654

# 处理前5个issues
python test_case_generator.py --limit 5

# 处理多个指定issues
python test_case_generator.py --issue 654 721 777 4720
```

## 详细功能说明

### 1. Issue信息提取

框架会自动从`issue_body`中提取以下信息：
- **版本信息**：自动识别JDK版本、库版本等
- **重现步骤**：从markdown列表或文本中提取步骤
- **错误信息**：提取异常类型和错误消息
- **错误日志**：提取完整的堆栈跟踪
- **预期/实际行为**：从结构化文本中提取

### 2. 方法签名匹配

- 根据`issue_number`在`extracted_methods_javaparser.json`中查找
- 支持按repo名称过滤
- 自动过滤无效的方法签名（如"UNKNOWN"）

### 3. Prompt构建

严格按照`prompt.txt`格式构建prompt：
- 保留原有的Instruction和Approach部分
- 在Input部分填充提取的bug信息
- 添加相关方法签名信息
- 支持中英文双语格式

### 4. 编译检查

- 自动查找系统Java编译器（javac）
- 支持自定义JAVA_HOME
- 支持指定classpath
- 捕获编译错误并记录

## 故障排除

### API调用失败
- 检查API密钥是否正确
- 检查网络连接
- 查看`test_case_generator.log`日志文件
- 检查API限流设置（增加`api_delay`）

### 编译检查失败
- 确保已安装Java（运行`javac -version`检查）
- 检查生成的代码是否需要额外的依赖
- 查看`result_{issue_number}.json`中的编译错误信息
- 如果代码需要外部库，配置`java_classpath`

### 方法签名未找到
- 检查`extracted_methods_javaparser.json`中是否包含对应的issue_number
- 检查repo名称是否匹配
- 某些issue可能确实没有相关的方法签名

### Prompt构建问题
- 检查`prompt.txt`文件格式是否正确
- 查看日志文件了解详细信息
- 确保issue_body包含足够的信息

## 高级用法

### 自定义Java编译环境

```json
{
  "java_home": "C:/Program Files/Java/jdk-17",
  "java_classpath": "lib/*;dependencies/*"
}
```

### 调整API参数

```json
{
  "deepseek_model": "deepseek-chat",
  "api_delay": 2.0,
  "save_intermediate": true
}
```

### 批量处理特定issues

```bash
# 处理多个指定issues（按issue_number）
python test_case_generator.py --issue 654 721 777 4720

# 按数组索引顺序处理（推荐用于顺序处理）
python test_case_generator.py --start 0 --end 10  # 处理索引0-9的issues
python test_case_generator.py --start 0 --limit 5  # 处理前5个issues

# 限制处理数量（用于测试）
python test_case_generator.py --limit 5
```

### 处理方式说明

- `--issue`: 按issue_number筛选，可以指定多个issue_number
- `--start` / `--end`: 按数组索引顺序处理，适合批量顺序处理
- `--limit`: 限制处理数量，从筛选结果中取前N个

