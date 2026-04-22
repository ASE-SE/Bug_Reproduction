#!/usr/bin/env python3
"""Export Defects4J bugs to a SWE-bench-like JSONL file.

Output schema:
    repo, pull_number, instance_id, issue_numbers, base_commit, patch,
    test_patch, problem_statement, hints_text, created_at, FAIL_TO_PASS,
    PASS_TO_PASS, version

Notes:
    - Defects4J has no pull requests, so pull_number is set to the bug id by
        default. If you prefer a stricter mapping, change it to null.
    - patch is emitted in the fix direction: buggy revision -> fixed revision.
    - problem_statement is fetched from the issue URL when possible.
    - hints_text is usually empty for Defects4J data.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def run(cmd: List[str], cwd: Optional[Path] = None) -> str:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def load_repo_map(repos_csv: Path) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    with repos_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) != 2:
                continue
            mapping[row[0].strip()] = row[1].strip()
    return mapping


def load_active_bugs(active_bugs_csv: Path) -> List[Dict[str, str]]:
    with active_bugs_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def parse_repo_slug(repo_url: str) -> str:
    parsed = urllib.parse.urlparse(repo_url)
    slug = parsed.path.strip("/")
    return slug.lower()


def repo_local_path(workspace_root: Path, repo_url: str) -> Path:
    repo_name = Path(urllib.parse.urlparse(repo_url).path).name
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]
    return workspace_root / "project_repos" / f"{repo_name}.git"


def git_diff(repo_dir: Path, buggy_rev: str, fixed_rev: str) -> str:
    return run(
        ["git", "-C", str(repo_dir), "diff", "--no-ext-diff", "--binary", buggy_rev, fixed_rev]
    )


def read_patch_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_trigger_tests(trigger_file: Path) -> List[str]:
    if not trigger_file.exists():
        return []
    tests: List[str] = []
    with trigger_file.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("--- "):
                tests.append(line[4:].strip())
    return tests


def fetch_json(url: str, headers: Optional[Dict[str, str]] = None) -> object:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _iso_to_epoch_ms(value: str) -> int:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_issue_metadata(report_url: str, github_token: Optional[str] = None) -> Tuple[str, int]:
    """Best-effort fetch of problem statement and creation timestamp."""
    try:
        if "github.com" in report_url:
            match = re.search(r"github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", report_url)
            if not match:
                return "", 0
            slug = match.group(1)
            number = match.group(3)
            headers = {"User-Agent": "defects4j-jsonl-exporter"}
            if github_token:
                headers["Authorization"] = f"Bearer {github_token}"
            payload = fetch_json(
                f"https://api.github.com/repos/{slug}/issues/{number}",
                headers=headers,
            )
            if isinstance(payload, dict):
                title = payload.get("title") or ""
                body = payload.get("body") or ""
                created_at = payload.get("created_at") or payload.get("updated_at") or ""
                created_ms = _iso_to_epoch_ms(created_at) if created_at else 0
                return (title + "\n\n" + body).strip(), created_ms
            return "", 0

        if "issues.apache.org/jira" in report_url:
            match = re.search(r"/browse/([A-Z]+-\d+)", report_url)
            if not match:
                return "", 0
            issue_key = match.group(1)
            payload = fetch_json(f"https://issues.apache.org/jira/rest/api/2/issue/{issue_key}")
            if isinstance(payload, dict):
                fields = payload.get("fields", {})
                summary = fields.get("summary") or ""
                description = fields.get("description") or ""
                if isinstance(description, dict):
                    # Some JIRA instances return a structured description.
                    description = json.dumps(description, ensure_ascii=False)
                created_at = fields.get("created") or ""
                created_ms = _iso_to_epoch_ms(created_at) if created_at else 0
                return (summary + "\n\n" + str(description)).strip(), created_ms
            return "", 0

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return "", 0

    return "", 0


def export_one_bug(
    workspace_root: Path,
    project_id: str,
    repo_url: str,
    bug_row: Dict[str, str],
    github_token: Optional[str],
) -> OrderedDict:
    bug_id = int(bug_row["bug.id"])
    buggy_rev = bug_row["revision.id.buggy"]
    fixed_rev = bug_row["revision.id.fixed"]
    report_id = bug_row.get("report.id", str(bug_id))
    report_url = bug_row.get("report.url", "")

    repo_slug = parse_repo_slug(repo_url)
    local_repo = repo_local_path(workspace_root, repo_url)
    if not local_repo.exists():
        raise FileNotFoundError(f"Missing git mirror: {local_repo}")

    patch = git_diff(local_repo, buggy_rev, fixed_rev)
    test_patch_file = workspace_root / "framework" / "projects" / project_id / "patches" / f"{bug_id}.test.patch"
    test_patch = read_patch_file(test_patch_file)
    problem_statement, created_at = fetch_issue_metadata(report_url, github_token)

    trigger_file = workspace_root / "framework" / "projects" / project_id / "trigger_tests" / str(bug_id)
    fail_to_pass = parse_trigger_tests(trigger_file)

    instance_id = f"{repo_slug.replace('/', '__')}-{bug_id}"

    record = OrderedDict()
    record["repo"] = repo_slug
    record["pull_number"] = bug_id
    record["instance_id"] = instance_id
    record["issue_numbers"] = [str(report_id)] if report_id else []
    record["base_commit"] = buggy_rev
    record["patch"] = patch
    record["test_patch"] = test_patch
    record["problem_statement"] = problem_statement
    record["hints_text"] = ""
    record["created_at"] = created_at
    record["FAIL_TO_PASS"] = fail_to_pass
    record["PASS_TO_PASS"] = []
    record["version"] = "0.1"
    return record


def iter_projects(
    workspace_root: Path,
    selected_projects: Optional[Iterable[str]],
) -> Iterable[Tuple[str, Path, str]]:
    repo_map = load_repo_map(workspace_root / "project_repos" / "repos.csv")
    projects_dir = workspace_root / "framework" / "projects"
    project_ids = list(selected_projects) if selected_projects else sorted(repo_map.keys())

    for project_id in project_ids:
        if project_id not in repo_map:
            raise KeyError(f"Unknown project id in repos.csv: {project_id}")
        active_bugs = projects_dir / project_id / "active-bugs.csv"
        if not active_bugs.exists():
            raise FileNotFoundError(f"Missing active-bugs.csv for {project_id}: {active_bugs}")
        yield project_id, active_bugs, repo_map[project_id]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", required=True, help="Path to the output JSONL file")
    parser.add_argument(
        "--project",
        action="append",
        help="Defects4J project id to export; repeatable. Default: all projects in repos.csv",
    )
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    github_token = os.environ.get("GITHUB_TOKEN")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    exported = 0
    with output_path.open("w", encoding="utf-8") as out_handle:
        for project_id, active_bugs_csv, repo_url in iter_projects(workspace_root, args.project):
            for bug_row in load_active_bugs(active_bugs_csv):
                record = export_one_bug(workspace_root, project_id, repo_url, bug_row, github_token)
                out_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                exported += 1

    print(f"Exported {exported} records to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())