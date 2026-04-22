from pathlib import Path

from constants import logger


def write_python_test(
    repo_dir: Path,
    test_code: str,
    file_name: str | None = None,
    path_hint: str | None = None,
    instance_id: str | None = None,
) -> Path | None:
    """
    将 LLM 返回的 pytest 代码写入仓库：
    - 若有 path_hint 则按提示路径落盘；
    - 否则写到 tests/test_generated_trigger_{instance_id}.py。
    """
    if not test_code.strip():
        logger.error("Empty test_code, skip writing.")
        return None

    if path_hint:
        target_file = repo_dir / path_hint
    else:
        final_name = file_name or f"test_generated_trigger_{instance_id or 'unknown'}.py"
        target_file = repo_dir / "tests" / final_name

    if target_file.suffix != ".py":
        target_file = target_file.with_suffix(".py")

    target_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        target_file.write_text(test_code, encoding="utf-8")
        logger.info("Wrote test to %s", target_file)
        return target_file
    except Exception as e:
        logger.error("Write failed: %s", e)
        return None