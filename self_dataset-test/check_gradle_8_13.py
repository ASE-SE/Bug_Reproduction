#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查 /root/gradle/gradle-8.13 是否存在，以及 bin/gradle 是否可执行。
输出为 JSON，便于脚本或其它程序解析。
"""

import os
import json
import subprocess
import sys
from pathlib import Path

TARGET = Path("/root/gradle/gradle-8.13")

def run_gradle_version(gradle_path: Path, timeout: int = 8):
    try:
        cp = subprocess.run([str(gradle_path), "-v"],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=timeout)
        return {"rc": cp.returncode, "stdout": cp.stdout.strip(), "stderr": cp.stderr.strip()}
    except FileNotFoundError as e:
        return {"rc": -127, "stdout": "", "stderr": f"not found: {e}"}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {"rc": -2, "stdout": "", "stderr": f"error: {e}"}

def main():
    out = {}
    out["target_path"] = str(TARGET)
    out["exists"] = TARGET.exists()
    out["is_dir"] = TARGET.is_dir()
    gradle_bin = TARGET / "bin" / "gradle"
    out["gradle_bin_path"] = str(gradle_bin)
    out["gradle_bin_exists"] = gradle_bin.exists()
    out["gradle_bin_is_file"] = gradle_bin.is_file()
    out["gradle_bin_executable"] = os.access(str(gradle_bin), os.X_OK) if gradle_bin.exists() else False

    # 如果可执行，则尝试读取版本信息（超时保护）
    if out["gradle_bin_executable"]:
        ver = run_gradle_version(gradle_bin, timeout=8)
        # 只保留合理长度
        out["gradle_version_rc"] = ver.get("rc")
        out["gradle_version_stdout"] = (ver.get("stdout") or "")[:2000]
        out["gradle_version_stderr"] = (ver.get("stderr") or "")[:2000]
    else:
        out["gradle_version_rc"] = None
        out["gradle_version_stdout"] = ""
        out["gradle_version_stderr"] = ""

    # Print pretty JSON
    print(json.dumps(out, indent=2, ensure_ascii=False))

    # Exit code: 0 = path exists & gradle executable ok; 1 = path exists but no executable; 2 = path missing
    if out["exists"] and out["gradle_bin_executable"]:
        sys.exit(0)
    if out["exists"]:
        sys.exit(1)
    sys.exit(2)

if __name__ == "__main__":
    main()
