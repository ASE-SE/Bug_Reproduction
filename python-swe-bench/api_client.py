import json
from typing import Any

import requests

from constants import DEFAULT_API_MODEL, logger


def _strip_fence(text: str) -> str:
    """去掉模型可能返回的 markdown code fence，保留纯 JSON 字符串。"""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def call_api_generate_python_test(
    bug_entry: dict[str, Any],
    api_url: str,
    api_key: str,
    api_model: str | None = None,
) -> dict[str, Any]:
    """
    调用兼容 OpenAI chat/completions 的 LLM 接口，生成 Python 触发测试。

    约定输出 JSON：
    - language
    - test_code
    - path_hint
    - file_name
    - run_command
    """
    model = api_model or DEFAULT_API_MODEL
    instance_id = str(bug_entry.get("instance_id", "unknown")).replace("-", "_").replace(".", "_")
    file_name = f"test_generated_trigger_{instance_id}.py"

    # 提示词采用“严格 JSON 输出”约束，避免解析失败
    system_msg = f"""
You are a senior Python test engineer.
Generate one pytest trigger test that reproduces the bug described in the input.
Constraints:
1) Test must be standalone and executable with pytest.
2) Do not modify production code.
3) Output STRICT JSON only.
JSON schema:
{{
  "language": "python",
  "test_code": "full python test file content",
  "path_hint": "tests/{file_name}",
  "file_name": "{file_name}",
  "run_command": "pytest -q tests/{file_name}"
}}
"""

    # 【重要修改】过滤掉 test_patch 和 fail_to_pass 等"答案"字段，防止数据泄露给大模型
    safe_entry = {
        "instance_id": bug_entry.get("instance_id"),
        "repo": bug_entry.get("repo"),
        "problem_description": bug_entry.get("problem_description"),
        "hints_text": bug_entry.get("hints_text")
    }

    user_msg = "BUG REPORT JSON:\n" + json.dumps(safe_entry, indent=2, ensure_ascii=False)
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg.strip()},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    logger.info("Calling API (%s) for %s...", model, instance_id)
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=120)
        if resp.status_code != 200:
            return {"ok": False, "error": f"Status {resp.status_code}: {resp.text[:400]}"}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        
        # 【重要修改】增加了对 JSON 解析失败的显式异常处理
        try:
            parsed = json.loads(_strip_fence(content))
        except json.JSONDecodeError as e:
            logger.error("JSON Decode failed for %s. Raw LLM output: %s", instance_id, content)
            return {"ok": False, "error": f"JSON Decode Error: {e}"}

        if "file_name" not in parsed:
            parsed["file_name"] = file_name
        parsed.setdefault("path_hint", f"tests/{parsed['file_name']}")
        parsed.setdefault("language", "python")
        
        return {"ok": True, "data": parsed}
        
    except requests.Timeout:
        logger.error("API request timed out for %s", instance_id)
        return {"ok": False, "error": "Request Timeout"}
    except Exception as e:
        logger.error("API Error for %s: %s", instance_id, e)
        return {"ok": False, "error": str(e)}