# 快速开始指南

## 5分钟快速上手

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置API密钥

```bash
# 复制示例配置文件
cp config.example.json config.json

# 编辑config.json，填入你的DeepSeek API密钥
# 注意：config.json已在.gitignore中，不会被提交
```

编辑`config.json`：
```json
{
  "deepseek_api_key": "sk-your-actual-api-key-here",
  ...
}
```

### 3. 验证Java环境

```bash
# 检查Java是否安装
javac -version
```

如果未安装，请先安装Java JDK。

### 4. 运行第一个测试

```bash
# 处理单个issue（例如issue 654）
python test_case_generator.py --issue 654
```

### 5. 查看结果

结果保存在`output`目录：
- `test_case_654.java` - 生成的测试用例代码
- `result_654.json` - 结果元数据
- `summary.json` - 汇总报告

## 常见问题

**Q: API调用失败怎么办？**
A: 检查API密钥是否正确，查看`test_case_generator.log`日志文件。

**Q: 编译检查失败？**
A: 确保已安装Java，检查生成的代码是否需要额外的依赖库。

**Q: 如何批量处理？**
A: 使用`--limit`参数限制数量，或使用`--issue`指定多个issue numbers。

## 下一步

- 阅读完整的[README_CN.md](README_CN.md)了解详细功能
- 查看[example_usage.py](example_usage.py)了解编程接口
- 根据需要调整`config.json`中的配置

