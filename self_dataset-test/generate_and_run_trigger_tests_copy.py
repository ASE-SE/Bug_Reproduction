#!/usr/bin/env python3
"""
generate_and_run_trigger_tests.py

Modifications summary (important behavioral changes):
- Before generating tests, the script now attempts to ENSURE the repository can be built (without running tests).
  * For Gradle projects we run `gradle build -x test` (preferring a local /root/gradle/gradle-8.13/bin/gradle or system gradle).
  * For Maven we run `mvn -DskipTests -q package`.
  * For Python/Node we run their install/package steps appropriate for preparing a build environment.
- If the build fails, the script calls the auto-fix API (call_api_request_fixes) to suggest patch files, applies them and retries,
  up to a configurable number of attempts (default 2).
- The script will try to reach a successful build; if after attempts the build still fails it records that status, but **will still
  generate and write the trigger test files** into the repo (you asked to ignore whether the generated test compiles).
- The later test-run+fix loop has been removed: the script no longer tries to run tests or auto-fix test compilation problems.
- All other features (cloning, checkout_pre_patch_commit, API-based test generation, file writing, logging) are preserved.

Usage remains the same as your previous script. This file replaces the prior script with the new build-first/autofix-first flow.
"""

import os
import sys
import json
import re
import shutil
import subprocess
import time
import argparse
import threading
import logging
from pathlib import Path
from collections import Counter
from typing import Dict, Any, Optional, Tuple, List

try:
    import requests
except Exception:
    print("Missing dependency 'requests'. Install with: pip install requests", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parent
ISSUES_FILE = ROOT / "issues_commits.json"
EXTRACTED_FILE = ROOT / "extracted_methods_javaparser.json"
REPOS_DIR = ROOT / "repos"
RESULTS_DIR = ROOT / "results"
LOG_FILE = RESULTS_DIR / "progress.log"

# NEW: local gradle installation detection (if present use it instead of relying on network downloads)
LOCAL_GRADLE_DIR = Path("/root/gradle/gradle-8.13")
LOCAL_GRADLE_BIN = LOCAL_GRADLE_DIR / "bin" / "gradle"

os.makedirs(REPOS_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# Defaults (can be overridden with CLI or env)
DEFAULT_API_URL = os.getenv("QW_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
DEFAULT_API_MODEL = os.getenv("QW_API_MODEL", "deepseek-r1")
EMBEDDED_API_KEY = "sk-1750d453ca0640339e9b24ffc4f49cf2"

# Configure structured logging to both console and file
logger = logging.getLogger("trigger_test_generator")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")

# Console handler
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(formatter)
logger.addHandler(ch)

# File handler
fh = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
fh.setLevel(logging.INFO)
fh.setFormatter(formatter)
logger.addHandler(fh)

# Small helper to emit a progress marker (both log & flush)
def progress(msg: str, level: str = "info") -> None:
    if level.lower() == "debug":
        logger.debug(msg)
    elif level.lower() == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)
    # try to flush handlers to ensure immediate file visibility
    for h in logger.handlers:
        try:
            h.flush()
        except Exception:
            pass

PROMPT_TEMPLATE = """<Instruction>
You are a senior software test engineer specializing in defect reproduction and trigger-test design. Your goal is to produce a minimal, precise, and directly executable Trigger Test Case for the target repository based on the provided Bug Report key information. The generated test must reliably reproduce the observed failure (exception, crash, incorrect behavior, or explicit error output) and must be complete and compilable within the target project's test folder without introducing unnecessary dependencies or external network calls.
</Instruction>

<Constraints>
1. Use the repository's existing build and test ecosystem (Gradle or Maven). Do not introduce external network dependencies or new runtime services.
2. Place test code under the project's standard test sources (e.g., src/test/java for Java). Ensure the package name matches the repository's common package prefix; if that cannot be inferred, the caller will adjust automatically.
3. Provide a single, complete source file for the test class containing package, imports, class declaration, and test method(s). Name the test class GeneratedTriggerTest_issue{ISSUE_NUMBER}.
4. Tests must be self-contained: use @TempDir, temp files, or repository fixtures. Do not require interactive input or external resources.
5. Include concrete assertions (assertThrows, assertEquals, assertTrue, etc.) that determine whether the bug is triggered. If the reproduction is an exception, use assertThrows and match the exception type and at least a distinctive substring of the message.
6. Avoid modifying production code in src/main/java unless absolutely necessary to run the test. If a build-file or source patch is unavoidable, return a JSON "files" array with the required patch files (see OutputFormat).
7. Provide an exact single-line run command for local execution (for example: ./gradlew test --tests "*GeneratedTriggerTest_issue1234*" or mvn -Dtest=GeneratedTriggerTest_issue1234 test).
8. Do not include any explanatory text outside the required JSON response.
</Constraints>
"""

# ----------------- Utility -----------------


def ensure_str(obj: Any) -> Any:
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    return obj


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None, timeout: int = 600) -> Dict[str, Any]:
    """
    Run a shell command and log start/finish/duration. Return dict with returncode, stdout, stderr.
    This function intentionally logs command start and finish so long-running commands appear in progress.log.
    """
    cmd_str = " ".join(cmd)
    start = time.time()
    progress(f"CMD START: {cmd_str} (cwd={str(cwd) if cwd else '.'})")
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        elapsed = time.time() - start
        progress(f"CMD FINISH: {cmd_str} rc={completed.returncode} elapsed={elapsed:.1f}s")
        if stdout:
            # Truncate large outputs in logs but keep them for return
            progress(f"CMD STDOUT (truncated 2000): {ensure_str(stdout)[:2000]}")
        if stderr:
            progress(f"CMD STDERR (truncated 2000): {ensure_str(stderr)[:2000]}", level="warning")
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {"returncode": completed.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        progress(f"CMD TIMEOUT: {cmd_str} elapsed={elapsed:.1f}s", level="warning")
        stdout = getattr(e, "stdout", "") or ""
        stderr = getattr(e, "stderr", "") or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return {"returncode": -1, "stdout": stdout, "stderr": f"TIMEOUT after {timeout}s; {stderr}"}
    except Exception as e:
        elapsed = time.time() - start
        progress(f"CMD ERROR: {cmd_str} elapsed={elapsed:.1f}s error={e}", level="warning")
        return {"returncode": -1, "stdout": "", "stderr": f"run_cmd failed: {e}"}


def sanitize_for_json(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8", errors="replace")
        except Exception:
            return str(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {(k if isinstance(k, str) else str(k)): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize_for_json(x) for x in obj]
    try:
        json.dumps(obj)
        return obj
    except Exception:
        try:
            return str(obj)
        except Exception:
            return repr(obj)


def safe_json_dump(path: Path, obj: Any, **kwargs) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(obj), f, **kwargs)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ----------------- Repo helpers -----------------


def clone_or_update_repo(repo_fullname: str, clone_dir: Path, commit_sha: Optional[str] = None, update_existing: bool = False) -> Tuple[bool, str]:
    git_url = f"https://github.com/{repo_fullname}.git"
    if clone_dir.exists():
        if update_existing:
            progress(f"Fetching updates for existing repo {repo_fullname} ...")
            r = run_cmd(["git", "fetch", "--all", "--prune"], cwd=clone_dir)
            msg = f"updated existing repo {repo_fullname}: rc={r['returncode']}"
            ok = r["returncode"] == 0
        else:
            msg = f"repo already exists at {clone_dir}, skipping clone (use --update-existing to fetch/update)"
            progress(msg)
            return True, msg
    else:
        progress(f"Cloning {repo_fullname} ...")
        r = run_cmd(["git", "clone", "--depth", "1", git_url, str(clone_dir)])
        ok = r["returncode"] == 0
        msg = f"cloned {repo_fullname}: rc={r['returncode']}"
    if not ok:
        return False, msg + f" stdout:{ensure_str(r.get('stdout',''))[:1000]} stderr:{ensure_str(r.get('stderr',''))[:1000]}"
    if commit_sha:
        progress(f"Checking out specified commit {commit_sha} in {repo_fullname} ...")
        r = run_cmd(["git", "checkout", commit_sha], cwd=clone_dir)
        if r["returncode"] != 0:
            run_cmd(["git", "fetch", "--unshallow"], cwd=clone_dir)
            run_cmd(["git", "fetch", "origin", commit_sha], cwd=clone_dir)
            r = run_cmd(["git", "checkout", commit_sha], cwd=clone_dir)
        if r["returncode"] != 0:
            return False, f"failed to checkout {commit_sha}: stdout:{ensure_str(r.get('stdout',''))[:1000]} stderr:{ensure_str(r.get('stderr',''))[:1000]}"
        msg += f" checked out {commit_sha}"
    return True, msg


def discard_local_changes(repo_dir: Path) -> Dict[str, Any]:
    """
    Destructively drop local changes in repo_dir.
    Runs git reset --hard and git clean -fdx and returns their outputs.
    """
    progress(f"Discarding local changes in {repo_dir} (git reset --hard; git clean -fdx) ...")
    out = {}
    r1 = run_cmd(["git", "reset", "--hard"], cwd=repo_dir)
    out["reset"] = {"rc": r1["returncode"], "stdout": ensure_str(r1["stdout"])[:2000], "stderr": ensure_str(r1["stderr"])[:2000]}
    r2 = run_cmd(["git", "clean", "-fdx"], cwd=repo_dir)
    out["clean"] = {"rc": r2["returncode"], "stdout": ensure_str(r2["stdout"])[:2000], "stderr": ensure_str(r2["stderr"])[:2000]}
    progress(f"Discard result reset.rc={out['reset']['rc']} clean.rc={out['clean']['rc']}")
    return out


def detect_build_system(repo_dir: Path) -> str:
    if (repo_dir / "pom.xml").exists():
        return "maven"
    if (repo_dir / "build.gradle").exists() or (repo_dir / "build.gradle.kts").exists() or (repo_dir / "gradlew").exists():
        return "gradle"
    if (repo_dir / "package.json").exists():
        return "node"
    if (repo_dir / "setup.py").exists() or (repo_dir / "pyproject.toml").exists() or (repo_dir / "requirements.txt").exists():
        return "python"
    return "unknown"


def _is_executable_file(p: Path) -> bool:
    try:
        return p.exists() and p.is_file() and os.access(str(p), os.X_OK)
    except Exception:
        return False


def get_preferred_gradle_bin() -> Optional[str]:
    """
    Prefer a local gradle installation at /root/gradle/gradle-8.13 if available and executable.
    Otherwise fall back to system 'gradle' if found in PATH, else return None.
    """
    try:
        if _is_executable_file(LOCAL_GRADLE_BIN):
            progress(f"Using local Gradle at {LOCAL_GRADLE_BIN}")
            return str(LOCAL_GRADLE_BIN)
    except Exception:
        pass
    gradle_bin = shutil.which("gradle")
    if gradle_bin:
        progress(f"Using system Gradle at {gradle_bin}")
        return gradle_bin
    return None


def install_and_prepare(repo_dir: Path, build_system: str) -> Dict[str, Any]:
    progress(f"Preparing build environment for {repo_dir} (detected {build_system}) ...")
    out = {"build_system": build_system, "actions": []}
    if build_system == "python":
        venv_dir = repo_dir / ".venv_trigger_test"
        python_bin = sys.executable
        if not venv_dir.exists():
            r = run_cmd([python_bin, "-m", "venv", str(venv_dir)], cwd=repo_dir)
            out["actions"].append(f"create venv rc={r['returncode']}")
        else:
            out["actions"].append("venv exists, skipping creation")
        pip = str(venv_dir / "bin" / "pip") if os.name != "nt" else str(venv_dir / "Scripts" / "pip.exe")
        if (repo_dir / "requirements.txt").exists():
            r = run_cmd([pip, "install", "-r", "requirements.txt"], cwd=repo_dir)
            out["actions"].append(f"pip install requirements rc={r['returncode']}")
            out["last"] = {"rc": r["returncode"], "stdout": ensure_str(r["stdout"])[:2000], "stderr": ensure_str(r["stderr"])[:2000]}
        else:
            out["actions"].append("no requirements.txt found")
    elif build_system == "maven":
        r = run_cmd(["mvn", "-q", "dependency:resolve"], cwd=repo_dir)
        out["actions"].append(f"mvn dependency:resolve rc={r['returncode']}")
        out["last"] = {"rc": r["returncode"], "stdout": ensure_str(r["stdout"])[:2000], "stderr": ensure_str(r["stderr"])[:2000]}
    elif build_system == "gradle":
        wrapper = repo_dir / "gradlew"
        # 优先使用首选本地/system gradle（例如 /root/gradle/gradle-8.13/bin/gradle 或 PATH 上的 gradle）
        preferred_gradle = get_preferred_gradle_bin()
        if preferred_gradle:
            cmd = [preferred_gradle, "dependencies"]
            used = "preferred gradle"
        elif wrapper.exists():
            cmd = [str(wrapper), "dependencies"]
            used = "gradlew"
        else:
            cmd = ["gradle", "dependencies"]
            used = "gradle"
        r = run_cmd(cmd, cwd=repo_dir)
        out["actions"].append(f"{used} dependencies rc={r['returncode']}")
        out["last"] = {"rc": r["returncode"], "stdout": ensure_str(r["stdout"])[:2000], "stderr": ensure_str(r["stderr"])[:2000]}
    elif build_system == "node":
        if (repo_dir / "package.json").exists():
            pkg_mgr = "npm"
            if (repo_dir / "yarn.lock").exists():
                pkg_mgr = "yarn"
            r = run_cmd([pkg_mgr, "install"], cwd=repo_dir)
            out["actions"].append(f"{pkg_mgr} install rc={r['returncode']}")
            out["last"] = {"rc": r["returncode"], "stdout": ensure_str(r["stdout"])[:2000], "stderr": ensure_str(r["stderr"])[:2000]}
        else:
            out["actions"].append("no package.json")
    else:
        out["actions"].append("unknown build system; no install attempted")
    progress(f"Preparation complete for {repo_dir}: {out['actions']}")
    return out


# ----------------- Java helpers -----------------


def find_common_java_package(repo_dir: Path, search_limit: int = 200) -> Optional[str]:
    candidates = []
    src_dirs = [repo_dir / "src" / "main" / "java", repo_dir / "src"]
    count = 0
    for d in src_dirs:
        if not d.exists():
            continue
        for path in d.rglob("*.java"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            m = re.search(r'package\s+([a-zA-Z0-9_.]+)\s*;', text)
            if m:
                candidates.append(m.group(1))
            count += 1
            if count >= search_limit:
                break
    if not candidates:
        return None
    most_common = Counter(candidates).most_common(1)[0][0]
    parts = most_common.split(".")
    if len(parts) > 4:
        parts = parts[:4]
    return ".".join(parts)


def extract_java_classname(java_code: str) -> Optional[str]:
    m = re.search(r'public\s+class\s+([A-Za-z_][A-Za-z0-9_]*)', java_code)
    if m:
        return m.group(1)
    m2 = re.search(r'\bclass\s+([A-Za-z_][A-Za-z0-9_]*)', java_code)
    if m2:
        return m2.group(1)
    return None


def ensure_java_package_and_class(repo_dir: Path, java_code: str, desired_basename: str) -> Tuple[str, str]:
    code = java_code
    pkg_match = re.search(r'^\s*package\s+([a-zA-Z0-9_.]+)\s*;\s*', code, flags=re.MULTILINE)
    if pkg_match:
        pkg = pkg_match.group(1)
    else:
        inferred = find_common_java_package(repo_dir)
        if inferred:
            pkg = inferred
            m_import = re.search(r'(^\s*import\s+)', code, flags=re.MULTILINE)
            insert_at = m_import.start() if m_import else 0
            code = f"package {pkg};\n\n{code}"
        else:
            pkg = None
    cls = extract_java_classname(code)
    if cls:
        if cls != desired_basename:
            code = re.sub(r'(public\s+class\s+)' + re.escape(cls), r'\1' + desired_basename, code, count=1)
            final_basename = desired_basename
        else:
            final_basename = cls
    else:
        final_basename = desired_basename
        wrapper = f"public class {final_basename} {{\n{code}\n}}\n"
        code = wrapper
    return code, final_basename


def write_test_file(repo_dir: Path, language: str, test_code: Any, path_hint: Optional[str], issue_number: int) -> Path:
    if isinstance(test_code, bytes):
        test_code = test_code.decode("utf-8", errors="replace")
    test_code = str(test_code)
    if path_hint:
        pth = Path(path_hint)
        if pth.is_absolute():
            try:
                path_hint = str(pth.relative_to(pth.anchor))
            except Exception:
                path_hint = str(pth.name)
    if language.lower() == "java":
        desired_basename = f"GeneratedTriggerTest_issue{issue_number}"
        final_code, final_basename = ensure_java_package_and_class(repo_dir, test_code, desired_basename)
        pkg_match = re.search(r'^\s*package\s+([a-zA-Z0-9_.]+)\s*;', final_code, flags=re.MULTILINE)
        if pkg_match:
            pkg = pkg_match.group(1)
            base = repo_dir / "src" / "test" / "java" / Path(pkg.replace(".", "/"))
        else:
            base = repo_dir / "src" / "test" / "java"
        if path_hint:
            base = repo_dir / "src" / "test" / "java" / Path(path_hint)
        base.mkdir(parents=True, exist_ok=True)
        fname = f"{final_basename}.java"
        out_path = base / fname
        out_path.write_text(final_code, encoding="utf-8")
        progress(f"Wrote test file: {out_path}")
        return out_path
    elif language.lower() == "python":
        base = repo_dir / "tests"
        if path_hint:
            base = repo_dir / path_hint
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / f"test_generated_issue_{issue_number}.py"
        out_path.write_text(test_code, encoding="utf-8")
        progress(f"Wrote test file: {out_path}")
        return out_path
    elif language.lower() in ("js", "javascript"):
        base = repo_dir / "test"
        if path_hint:
            base = repo_dir / path_hint
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / f"generated_test_issue_{issue_number}.test.js"
        out_path.write_text(test_code, encoding="utf-8")
        progress(f"Wrote test file: {out_path}")
        return out_path
    else:
        base = repo_dir / "generated_tests"
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / f"generated_issue_{issue_number}.txt"
        out_path.write_text(test_code, encoding="utf-8")
        progress(f"Wrote generic test output: {out_path}")
        return out_path


# ----------------- API wrapper (keeps behavior) -----------------


def strip_code_fence(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = s.strip()
    s = re.sub(r'^```(?:json)?\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*```$', '', s)
    return s.strip()


def call_api_generate_test(bug_entry: Dict[str, Any], extracted_methods: Dict[str, Any],
                           api_url: str, api_key: str, api_model: str) -> Dict[str, Any]:
    api_model = api_model or DEFAULT_API_MODEL
    system_msg = (
        "You are a senior software test engineer specializing in defect reproduction and test case design. "
        "Generate a minimal, executable trigger test and return JSON only. Valid JSON should contain either "
        "'test_code' and 'language' (single file), or 'files' (array of {path, content}). "
        "Do not include additional commentary outside the JSON."
    )
    try:
        bug_json = json.dumps(bug_entry, ensure_ascii=False, indent=2)
    except Exception:
        bug_json = str(bug_entry)
    extracted_for_repo = extracted_methods.get(bug_entry.get("repo", ""), {})
    if isinstance(extracted_for_repo, dict):
        trimmed = {}
        for k, v in extracted_for_repo.items():
            if isinstance(v, list):
                trimmed[k] = v[:5]
            else:
                trimmed[k] = v
        extracted_for_repo = trimmed
    else:
        if isinstance(extracted_for_repo, list):
            extracted_for_repo = extracted_for_repo[:10]
    try:
        extracted_json = json.dumps(extracted_for_repo, ensure_ascii=False, indent=2)
    except Exception:
        extracted_json = str(extracted_for_repo)
    user_msg = (
        "Bug report and extracted method signatures (trimmed):\n\n"
        f"BUG:\n{bug_json}\n\nEXTRACTED_METHODS:\n{extracted_json}\n\n"
        "Output must be valid JSON containing either 'test_code' or 'files'. No extra text."
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]
    payload = {"model": api_model, "messages": messages, "temperature": 0.0, "max_tokens": 1800}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    progress("Calling test generation API ...")
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=(30, 600))
    except Exception as e:
        progress(f"API request failed: {e}", level="warning")
        return {"ok": False, "error": f"API request failed: {e}"}
    status = resp.status_code
    text = ensure_str(resp.text)
    progress(f"API returned status {status} (response length {len(text)} chars)")
    if status != 200:
        return {"ok": False, "error": f"API returned {status}", "status_code": status, "resp_text": text}
    try:
        data = resp.json()
    except Exception as e:
        progress(f"Invalid JSON from API: {e}", level="warning")
        return {"ok": False, "error": f"Invalid JSON from API: {e}", "status_code": status, "resp_text": text}
    if isinstance(data, dict) and ("test_code" in data or "files" in data):
        return {"ok": True, "data": data}
    try:
        choices = data.get("choices") if isinstance(data, dict) else None
        if choices and isinstance(choices, list) and len(choices) > 0:
            first = choices[0]
            content = None
            if isinstance(first, dict):
                if "message" in first and isinstance(first["message"], dict):
                    content = first["message"].get("content") or first["message"].get("text")
                else:
                    content = first.get("text") or first.get("message")
            if content:
                content_str = strip_code_fence(ensure_str(content))
                try:
                    parsed = json.loads(content_str)
                    return {"ok": True, "data": parsed}
                except Exception:
                    return {"ok": True, "data": {"test_code": content_str, "language": "unknown"}}
    except Exception:
        pass
    return {"ok": False, "error": f"Unexpected API response shape: {data}"}


# ----------------- New: API call for suggested fixes -----------------


def extract_failed_java_files(compiler_stderr: str) -> List[str]:
    """
    Extract Java file paths from the compiler stderr (Gradle/Maven).
    Looks for patterns like '/path/to/File.java:line: error: ...'
    Returns unique file paths.
    """
    paths = []
    for m in re.finditer(r'([/\w\-\._~:\\]+\.java):\d+: error:', compiler_stderr):
        path = m.group(1)
        paths.append(path)
    seen = set()
    out = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def build_fix_prompt(repo_dir: Path, failing_files: List[str], stderr_snippet: str, max_file_bytes: int = 20000) -> str:
    file_contents = {}
    for f in failing_files:
        p = Path(f)
        if not p.exists():
            candidate = repo_dir / Path(f).name
            if candidate.exists():
                p = candidate
            else:
                matches = list(repo_dir.rglob(Path(f).name))
                p = matches[0] if matches else None
        if p and p.exists():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                file_contents[str(p.relative_to(repo_dir))] = txt[:max_file_bytes]
            except Exception:
                file_contents[str(p.relative_to(repo_dir))] = "<could not read file>"
        else:
            file_contents[f] = "<file not found in repo>"
    build_files = {}
    for fname in ("build.gradle", "build.gradle.kts", "settings.gradle", "pom.xml"):
        bf = repo_dir / fname
        if bf.exists():
            try:
                build_files[fname] = bf.read_text(encoding="utf-8", errors="ignore")[:max_file_bytes]
            except Exception:
                build_files[fname] = "<could not read build file>"
    prompt = (
        "You are an expert Java engineer and build-fix assistant. The developer runs tests and got the following\n"
        "compiler/test stderr snippet (below). There are failing Java sources (listed) and possibly build files that\n"
        "need to be adjusted. Produce a JSON object ONLY with the key 'files' whose value is an array of files to write\n"
        "or patch in the repository. Each file object should be {\"path\":\"relative/path/in/repo\",\"content\":\"file contents\"}.\n"
        "Do NOT include any explanation outside the JSON. The patch files should aim to fix compilation/runtime test infra\n"
        "errors so the test can run; minimal, conservative changes are preferred (e.g., add missing imports, adjust tests,\n"
        "add a Gradle dependency, or modify test signatures). If you propose changes to build.gradle or pom.xml, include the\n"
        "entire file content to replace it.\n\n"
        "STDERR_SNIPPET:\n"
        "```\n" + stderr_snippet[:8000] + "\n```\n\n"
        "FAILING_FILES_AND_CONTENTS:\n"
    )
    for path, content in file_contents.items():
        prompt += f"\n--- {path} ---\n{content}\n"
    if build_files:
        prompt += "\nBUILD_FILES:\n"
        for path, content in build_files.items():
            prompt += f"\n--- {path} ---\n{content}\n"
    prompt += (
        "\nReturn a JSON object: {\"files\":[{\"path\":\"...\",\"content\":\"...\"}, ...]}\n"
        "If you cannot produce a fix, return {\"files\":[]}.\n"
    )
    return prompt


def call_api_request_fixes(repo_dir: Path, failing_files: List[str], stderr_snippet: str,
                           api_url: str, api_key: str, api_model: str) -> Dict[str, Any]:
    system_msg = "You are an expert software engineer. Provide patch files as JSON only."
    user_prompt = build_fix_prompt(repo_dir, failing_files, stderr_snippet)
    messages = [{"role": "system", "content": system_msg}, {"role": "user", "content": user_prompt}]
    payload = {"model": api_model, "messages": messages, "temperature": 0.0, "max_tokens": 2500}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    progress("Calling auto-fix API ...")
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=(30, 900))
    except Exception as e:
        progress(f"Auto-fix API request failed: {e}", level="warning")
        return {"ok": False, "error": f"API request failed: {e}"}
    status = resp.status_code
    text = ensure_str(resp.text)
    progress(f"Auto-fix API returned status {status} (response length {len(text)} chars)")
    if status != 200:
        return {"ok": False, "error": f"API returned {status}", "status_code": status, "resp_text": text}
    try:
        data = resp.json()
    except Exception:
        try:
            raw = strip_code_fence(text)
            parsed = json.loads(raw)
            return {"ok": True, "data": parsed}
        except Exception as e:
            progress(f"Invalid JSON from auto-fix API: {e}", level="warning")
            return {"ok": False, "error": f"Invalid JSON from API: {e}", "resp_text": text}
    if isinstance(data, dict) and "choices" in data:
        first = data["choices"][0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            content = message.get("content") or message.get("text") or ""
            content = strip_code_fence(ensure_str(content))
            try:
                parsed = json.loads(content)
                return {"ok": True, "data": parsed}
            except Exception:
                return {"ok": False, "error": "API returned non-JSON content", "resp_text": content}
    if isinstance(data, dict) and ("files" in data):
        return {"ok": True, "data": data}
    return {"ok": False, "error": f"Unexpected API response shape: {data}"}


# ----------------- New: build attempt with auto-fix loop -----------------


def attempt_build_with_autofix(repo_dir: Path, build_system: str, api_url: str, api_key: str, api_model: str, max_fix_attempts: int = 2, timeout: int = 1200) -> Dict[str, Any]:
    """
    Attempt to build the project (without running tests) and, if build fails, call auto-fix API to apply patches and retry.
    Returns a dict with build steps, applied fixes info, and final success flag.
    """
    res = {"build_system": build_system, "steps": [], "fix_attempts": [], "final_success": False}
    progress(f"Attempting build for {repo_dir} (build_system={build_system}) ...")

    def run_build_once() -> Dict[str, Any]:
        # returns {rc, stdout, stderr, cmd}
        if build_system == "maven":
            cmd = ["mvn", "-DskipTests", "-q", "package"]
            r = run_cmd(cmd, cwd=repo_dir, timeout=timeout)
            return {"cmd": " ".join(cmd), "rc": r["returncode"], "stdout": ensure_str(r["stdout"]), "stderr": ensure_str(r["stderr"])}
        elif build_system == "gradle":
            wrapper = repo_dir / "gradlew"
            env = os.environ.copy()
            env["GRADLE_OPTS"] = env.get("GRADLE_OPTS", "") + " -Dorg.gradle.internal.http.socketTimeout=600000 -Dorg.gradle.internal.http.connectionTimeout=600000"
            # prefer local/system gradle to avoid wrapper downloads
            preferred = get_preferred_gradle_bin()
            if preferred:
                cmd = [preferred, "build", "-x", "test"]
            else:
                if wrapper.exists():
                    cmd = [str(wrapper), "build", "-x", "test"]
                else:
                    cmd = ["gradle", "build", "-x", "test"]
            r = run_cmd(cmd, cwd=repo_dir, env=env, timeout=timeout)
            return {"cmd": " ".join(cmd), "rc": r["returncode"], "stdout": ensure_str(r["stdout"]), "stderr": ensure_str(r["stderr"])}
        elif build_system == "python":
            # For python, try installing requirements and run a simple package build if possible
            venv_dir = repo_dir / ".venv_trigger_test"
            python_bin = sys.executable
            if not venv_dir.exists():
                r0 = run_cmd([python_bin, "-m", "venv", str(venv_dir)], cwd=repo_dir)
                res["steps"].append({"cmd": f"{python_bin} -m venv {venv_dir}", "rc": r0["returncode"], "stdout": ensure_str(r0["stdout"]), "stderr": ensure_str(r0["stderr"])})
            pip = str(venv_dir / "bin" / "pip")
            if (repo_dir / "requirements.txt").exists():
                r = run_cmd([pip, "install", "-r", "requirements.txt"], cwd=repo_dir, timeout=timeout)
                return {"cmd": f"{pip} install -r requirements.txt", "rc": r["returncode"], "stdout": ensure_str(r["stdout"]), "stderr": ensure_str(r["stderr"])}
            else:
                # Try packaging
                if (repo_dir / "setup.py").exists():
                    r = run_cmd([str(venv_dir / "bin" / "python"), "setup.py", "sdist", "bdist_wheel"], cwd=repo_dir, timeout=timeout)
                    return {"cmd": "python setup.py sdist bdist_wheel", "rc": r["returncode"], "stdout": ensure_str(r["stdout"]), "stderr": ensure_str(r["stderr"])}
                return {"cmd": "no python build step found", "rc": 0, "stdout": "", "stderr": ""}
        elif build_system == "node":
            if (repo_dir / "package.json").exists():
                r = run_cmd(["npm", "install"], cwd=repo_dir, timeout=timeout)
                return {"cmd": "npm install", "rc": r["returncode"], "stdout": ensure_str(r["stdout"]), "stderr": ensure_str(r["stderr"])}
            return {"cmd": "no node build step found", "rc": 0, "stdout": "", "stderr": ""}
        else:
            return {"cmd": "unknown build system - no build attempted", "rc": 1, "stdout": "", "stderr": "unknown build system"}

    attempt = 0
    while attempt <= max_fix_attempts:
        attempt += 1
        progress(f"  Build attempt {attempt}/{max_fix_attempts+1} ...")
        run = run_build_once()
        res["steps"].append({"cmd": run["cmd"], "rc": run["rc"], "stdout": run["stdout"][:20000], "stderr": run["stderr"][:20000]})
        if run["rc"] == 0:
            res["final_success"] = True
            progress(f"  Build succeeded on attempt {attempt}.")
            return res
        # If failed, try to extract failing files / errors and call auto-fix API
        stderr_combined = run["stderr"] or run["stdout"]
        # detect infra-like failures (network/download) -> break and report infra_failed
        if "Could not resolve" in stderr_combined or "Could not GET" in stderr_combined or "Connection timed out" in stderr_combined or "Failed to download" in stderr_combined:
            res["infra_failed"] = True
            res["infra_message"] = stderr_combined[:4000]
            progress("  Detected infra failure during build; aborting auto-fix attempts.", level="warning")
            return res
        # extract failing java files (best-effort)
        failing_files = extract_failed_java_files(stderr_combined)
        progress(f"  Detected failing files for auto-fix: {failing_files}")
        if not failing_files:
            # Even if no .java paths were found, still try to call auto-fix API with stderr (it may propose build file patches)
            progress("  No explicit failing .java files found; will still request auto-fix using stderr snippet.")
            fail_list_for_api = []
        else:
            fail_list_for_api = failing_files
        fix_api = call_api_request_fixes(repo_dir, fail_list_for_api, stderr_combined, api_url, api_key, api_model)
        if not fix_api.get("ok"):
            res["fix_attempts"].append({"ok": False, "error": fix_api.get("error")})
            progress(f"  Auto-fix API error: {fix_api.get('error')}", level="warning")
            break
        fixes = fix_api.get("data", {}).get("files", [])
        if not fixes:
            res["fix_attempts"].append({"ok": True, "files": [], "note": "no files suggested"})
            progress("  Auto-fix API returned no files to apply.")
            break
        applied = []
        for f in fixes:
            rel = f.get("path")
            content = f.get("content", "")
            if not rel:
                continue
            dest = repo_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                dest.write_text(content, encoding="utf-8")
                applied.append(str(rel))
                progress(f"  Applied patch file: {rel}")
            except Exception as e:
                res.setdefault("fix_errors", []).append({"path": rel, "error": str(e)})
                progress(f"  Failed to write patch file {rel}: {e}", level="warning")
        res["fix_attempts"].append({"ok": True, "applied": applied})
        # commit applied patches if any
        if applied:
            try:
                run_cmd(["git", "add"] + applied, cwd=repo_dir)
                run_cmd(["git", "commit", "-m", f"Auto-applied build fixes attempt {attempt}"], cwd=repo_dir)
                progress(f"  Committed applied patches: {applied}")
            except Exception:
                progress("  Committing applied patches failed or was skipped.", level="warning")
        # loop to next attempt - run_build_once will retry

    # after attempts
    res["final_success"] = False
    progress("Build attempts exhausted; final_success=False", level="warning")
    return res


# ----------------- Remaining helpers (unchanged) -----------------


# (write_test_file, call_api_generate_test, call_api_request_fixes, etc. are above and reused)


# ----------------- Main orchestration -----------------


def main():
    parser = argparse.ArgumentParser(description="Generate and run trigger tests from issues dataset.")
    parser.add_argument("--api-key", "-k", help="API key (overrides QW_API_KEY env var)")
    parser.add_argument("--api-url", "-u", help="API URL (overrides QW_API_URL env var)")
    parser.add_argument("--api-model", help="API model to request (overrides QW_API_MODEL env var)")
    parser.add_argument("--limit", "-n", type=int, help="Limit number of issues processed (for quick testing)")
    parser.add_argument("--update-existing", action="store_true", help="If set, git fetch/update existing cloned repos instead of skipping them")
    parser.add_argument("--issue-id", help="Process only issues whose id/number matches this (comma-separated list allowed). Matches fields: issue_number, issue_id, id, number (string/int).")
    parser.add_argument("--discard-local-changes", action="store_true", help="If set, discard any local uncommitted changes in existing cloned repos (git reset --hard; git clean -fdx). WARNING: destructive.")
    parser.add_argument("--accept-compile-failure", action="store_true", help="(Kept for compatibility) if set, treat compile/test compilation failures that look like bug reproductions as 'triggered'.")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("QW_API_KEY", EMBEDDED_API_KEY)
    api_url = args.api_url or os.getenv("QW_API_URL", DEFAULT_API_URL)
    api_model = args.api_model or os.getenv("QW_API_MODEL", DEFAULT_API_MODEL)
    update_existing = bool(args.update_existing)
    discard_local = bool(args.discard_local_changes)
    accept_compile_failure = bool(args.accept_compile_failure)

    issue_filter_set = None
    if args.issue_id:
        parts = [p.strip() for p in str(args.issue_id).split(",") if p.strip()]
        issue_filter_set = set(parts) if parts else None

    progress(f"Script start. Using API URL: {api_url}")
    progress(f"Using API model: {api_model}")
    if issue_filter_set:
        progress(f"Filtering to issue ids: {sorted(issue_filter_set)}")
    if discard_local:
        progress("WARNING: --discard-local-changes is set; the script will run git reset --hard and git clean -fdx on existing repos (destructive).", level="warning")
    if accept_compile_failure:
        progress("INFO: --accept-compile-failure flag is present (compatibility).")

    if not api_key:
        progress("ERROR: API key not provided (use --api-key or set QW_API_KEY).", level="warning")
        print("ERROR: API key not provided (use --api-key or set QW_API_KEY).", file=sys.stderr)
        sys.exit(1)

    if not ISSUES_FILE.exists() or not EXTRACTED_FILE.exists():
        progress(f"Missing {ISSUES_FILE.name} or {EXTRACTED_FILE.name} in {ROOT}. Aborting.", level="warning")
        print(f"Missing {ISSUES_FILE.name} or {EXTRACTED_FILE.name} in {ROOT}. Please provide them and re-run.", file=sys.stderr)
        sys.exit(1)

    issues = load_json(ISSUES_FILE)
    extracted_raw = load_json(EXTRACTED_FILE)
    extracted_map = extracted_raw if isinstance(extracted_raw, dict) else {}
    summary = {"total": 0, "processed": 0, "results": []}
    generated_tests = []
    limit = args.limit or None

    MATCH_KEYS = ["issue_number", "issue_id", "id", "number", "issue"]

    total_issues = len(issues)
    progress(f"Loaded {total_issues} issues from {ISSUES_FILE}")

    for idx, entry in enumerate(issues):
        if limit is not None and idx >= limit:
            progress(f"Limit reached ({limit}); stopping early.")
            break
        summary["total"] += 1

        issue_number = entry.get("issue_number") or entry.get("issue_id") or entry.get("id") or f"idx{idx}"
        repo_fullname = entry.get("repo") or entry.get("repo_fullname")
        if not repo_fullname:
            progress(f"[skip] issue {issue_number} missing repo field", level="warning")
            print(f"[skip] issue {issue_number} missing repo field", file=sys.stderr)
            continue

        if issue_filter_set:
            found = False
            for k in MATCH_KEYS:
                v = entry.get(k)
                if v is None:
                    continue
                if str(v) in issue_filter_set:
                    found = True
                    break
            if not found:
                composite = f"{repo_fullname}#{issue_number}"
                if composite in issue_filter_set:
                    found = True
            if not found:
                continue

        progress(f"[{idx+1}/{total_issues}] [{issue_number}] Processing repo {repo_fullname} ...")
        repo_dir = REPOS_DIR / repo_fullname.replace("/", "_")
        commit_sha = entry.get("commit_sha")
        ok, msg = clone_or_update_repo(repo_fullname, repo_dir, commit_sha=None, update_existing=update_existing)
        record = {"issue_number": issue_number, "repo": repo_fullname, "clone_ok": ok, "clone_msg": msg, "timestamp": time.time()}
        progress(f"  clone: {msg}")
        if not ok:
            record["final_status"] = "clone_failed"
            safe_json_dump(RESULTS_DIR / f"{issue_number}.json", record, indent=2, ensure_ascii=False)
            summary["results"].append(record)
            continue

        if discard_local and repo_dir.exists():
            try:
                discard_out = discard_local_changes(repo_dir)
                record["discard_local_changes"] = discard_out
                progress(f"  discard_local_changes: reset rc={discard_out['reset']['rc']} clean rc={discard_out['clean']['rc']}")
            except Exception as e:
                record["discard_local_changes_error"] = str(e)
                progress(f"  discard_local_changes failed: {e}", level="warning")

        try:
            co_ok, co_msg, checked_commit = checkout_pre_patch_commit(repo_dir, entry)
            record["requested_patch_commit"] = {k: entry.get(k) for k in ("pre_patch_commit", "pre_patch_sha", "pre_commit_sha", "patch_commit", "patch_commit_sha", "fix_commit", "fix_commit_sha", "commit_sha") if entry.get(k)}
            record["checkout_pre_patch_ok"] = co_ok
            record["checkout_pre_patch_msg"] = co_msg
            record["checked_out_commit"] = checked_commit
            progress(f"  checkout_pre_patch: ok={co_ok} msg={co_msg} checked={checked_commit}")
        except Exception as e:
            record["checkout_pre_patch_ok"] = False
            record["checkout_pre_patch_msg"] = f"exception during checkout_pre_patch_commit: {e}"
            record["checked_out_commit"] = None
            progress(f"  checkout_pre_patch failed: {e}", level="warning")

        build_system = detect_build_system(repo_dir)
        record["build_system_detected"] = build_system
        prep = install_and_prepare(repo_dir, build_system)
        record["prepare"] = prep

        # NEW: attempt to build and auto-fix build failures (this aims to ensure project can be built; tests are skipped)
        build_result = attempt_build_with_autofix(repo_dir, build_system, api_url, api_key, api_model, max_fix_attempts=2)
        record["build_attempts"] = build_result
        record["build_final_status"] = "success" if build_result.get("final_success") else "failed"
        if build_result.get("final_success"):
            progress(f"  Build stage: success for {repo_fullname}")
        else:
            progress(f"  Build stage: FAILED for {repo_fullname} (attempts made); proceeding to generate test regardless as requested.", level="warning")

        # Now generate test (we proceed regardless of build success per your instruction)
        api_result = call_api_generate_test(entry, extracted_map, api_url, api_key, api_model)
        if not api_result.get("ok"):
            record["api_ok"] = False
            record["api_error"] = api_result.get("error")
            record["final_status"] = "api_failed"
            progress(f"  API error: {api_result.get('error')}", level="warning")
            safe_json_dump(RESULTS_DIR / f"{issue_number}.json", record, indent=2, ensure_ascii=False)
            summary["results"].append(record)
            continue

        record["api_ok"] = True
        api_data = api_result["data"]
        language = api_data.get("language", "unknown")
        test_code = api_data.get("test_code", "")
        path_hint = api_data.get("path_hint")
        record["api_language"] = language
        record["path_hint_suggested"] = path_hint
        record["api_raw"] = api_data

        files_for_aggregation = []
        try:
            if "files" in api_data and isinstance(api_data["files"], list) and api_data["files"]:
                for fobj in api_data["files"]:
                    fpath = fobj.get("path") or path_hint or f"generated_issue_{issue_number}.txt"
                    fcontent = fobj.get("content", "")
                    if isinstance(fcontent, bytes):
                        fcontent = fcontent.decode("utf-8", errors="replace")
                    dest = repo_dir / fpath
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(fcontent, encoding="utf-8")
                    files_for_aggregation.append({"path": str(Path(fpath)), "content": fcontent})
                    progress(f"  wrote API-provided file: {fpath}")
            else:
                if isinstance(test_code, bytes):
                    test_code = test_code.decode("utf-8", errors="replace")
                if not test_code:
                    raise ValueError("API returned neither 'files' nor 'test_code'")
                written_path = write_test_file(repo_dir, language, test_code, path_hint, issue_number)
                try:
                    rel = str(written_path.relative_to(repo_dir))
                except Exception:
                    rel = str(written_path)
                files_for_aggregation.append({"path": rel, "content": test_code})
        except Exception as e:
            record["final_status"] = "write_failed"
            record["write_error"] = str(e)
            progress(f"  write_failed: {e}", level="warning")
            safe_json_dump(RESULTS_DIR / f"{issue_number}.json", record, indent=2, ensure_ascii=False)
            summary["results"].append(record)
            continue

        record["test_written_files"] = files_for_aggregation

        # Commit generated test files (no attempt to run tests / fix test compilation)
        try:
            run_cmd(["git", "add"] + [str(repo_dir / f["path"]) for f in files_for_aggregation], cwd=repo_dir)
            run_cmd(["git", "commit", "-m", f"Add generated trigger test for issue {issue_number}"], cwd=repo_dir)
            progress("Committed generated test files (if any changes).")
        except Exception:
            progress("Git commit skipped (possibly no changes to commit).")

        # Final status: record build status and that test was written
        record["final_status"] = "test_written"
        record["build_success"] = bool(build_result.get("final_success"))
        safe_json_dump(RESULTS_DIR / f"{issue_number}.json", record, indent=2, ensure_ascii=False)

        generated_tests.append({
            "issue_number": issue_number,
            "repo": repo_fullname,
            "language": language,
            "path_hint": path_hint,
            "files": files_for_aggregation,
            "api_raw": api_data,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "final_status": record["final_status"],
            "build_success": record["build_success"],
            "checked_out_commit": record.get("checked_out_commit"),
        })
        summary["processed"] += 1
        summary["results"].append(record)

    safe_json_dump(RESULTS_DIR / "summary.json", summary, indent=2, ensure_ascii=False)
    safe_json_dump(RESULTS_DIR / "generated_tests.json", {"generated_tests": generated_tests, "meta": {"count": len(generated_tests), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}, indent=2, ensure_ascii=False)
    progress("Done. Results saved to results/ directory.")
    progress(f"Aggregated generated tests: {RESULTS_DIR / 'generated_tests.json'}")


if __name__ == "__main__":
    main()
