"""
constants.py
提供全局常量配置、路径解析以及标准化的日志记录器（Logger）。
"""

import os
import logging
from pathlib import Path
from typing import Final

# ==========================================
# 1. 路径与目录配置 (Path Management)
# ==========================================
ROOT_DIR: Final[Path] = Path(__file__).resolve().parent

REPOS_DIR: Final[Path] = ROOT_DIR / "repos"
RESULTS_DIR: Final[Path] = ROOT_DIR / "results"
ISSUES_FILE: Final[Path] = ROOT_DIR / "issues_commits.json"

# 确保运行时所需目录存在
REPOS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 2. 数据集与 API 配置 (Dataset & API Settings)
# ==========================================
# 明确指定字节跳动的多语言 SWE-bench 数据集
HF_DATASET_NAME: Final[str] = "ByteDance-Seed/Multi-SWE-bench"
HF_DATASET_SPLIT: Final[str] = "test"

# LLM API 默认配置 (阿里云百炼 / DeepSeek 等兼容 OpenAI 格式的接口)
DEFAULT_API_URL: Final[str] = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_API_MODEL: Final[str] = "deepseek-r1"

# 获取 API 密钥
EMBEDDED_API_KEY: Final[str] = os.getenv("API_KEY", "sk-1750d453ca0640339e9b24ffc4f49cf2")

# ==========================================
# 3. 标准化日志配置 (Logging Configuration)
# ==========================================
def _setup_logger() -> logging.Logger:
    _logger = logging.getLogger("JavaSweBench")
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(levelname)s - [%(module)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        _logger.addHandler(console_handler)
        
        file_handler = logging.FileHandler(ROOT_DIR / "experiment_run.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        _logger.addHandler(file_handler)
        
    return _logger

logger: Final[logging.Logger] = _setup_logger()