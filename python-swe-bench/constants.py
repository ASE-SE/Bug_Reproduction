"""
Global constants and logger for python-swe-bench.
"""

import logging
import os
from pathlib import Path
from typing import Final

ROOT_DIR: Final[Path] = Path(__file__).resolve().parent
REPOS_DIR: Final[Path] = ROOT_DIR / "repos"
RESULTS_DIR: Final[Path] = ROOT_DIR / "results"
ISSUES_FILE: Final[Path] = ROOT_DIR / "issues_commits.json"
PREDICTIONS_FILE: Final[Path] = ROOT_DIR / "predictions.jsonl"

# 运行时目录保障：避免第一次运行即因目录不存在报错
REPOS_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# 数据集配置：优先从 Hugging Face 拉 SWE-bench / SWT-Bench 标准数据集，
# 离线/失败时回退到本地 Parquet。HF 数据集的 instance_id 与 SWT-Bench harness
# 的 Docker 镜像一一对应，是接入 SWT-Bench 评测的前提。
HF_DATASET_NAME: Final[str] = os.getenv("HF_DATASET_NAME", "princeton-nlp/SWE-bench_Lite")
HF_DATASET_SPLIT: Final[str] = os.getenv("HF_DATASET_SPLIT", "test")
LOCAL_PARQUET_PATH: Final[str] = os.getenv(
    "LOCAL_PARQUET_PATH",
    "/root/bug-report2-test-cases2/data/test-00000-of-00001.parquet",
)

# SWT-Bench predictions 中标识本方法的名字，会用于 harness 输出目录命名
METHOD_NAME: Final[str] = os.getenv("METHOD_NAME", "llm-trigger-test")

DEFAULT_API_URL: Final[str] = os.getenv(
    "DEFAULT_API_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
)
DEFAULT_API_MODEL: Final[str] = os.getenv("DEFAULT_API_MODEL", "deepseek-r1")
EMBEDDED_API_KEY: Final[str] = os.getenv("API_KEY", "sk-1750d453ca0640339e9b24ffc4f49cf2")


def _setup_logger() -> logging.Logger:
    """统一日志格式：控制台 + experiment_run.log 文件。"""
    logger_obj = logging.getLogger("PythonSweBench")
    if not logger_obj.handlers:
        logger_obj.setLevel(logging.INFO)
        formatter = logging.Formatter(
            fmt="%(asctime)s - %(levelname)s - [%(module)s] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger_obj.addHandler(console_handler)

        file_handler = logging.FileHandler(ROOT_DIR / "experiment_run.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger_obj.addHandler(file_handler)
    return logger_obj


logger: Final[logging.Logger] = _setup_logger()