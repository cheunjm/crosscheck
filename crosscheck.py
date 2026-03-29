#!/usr/bin/env python3
"""crosscheck — adversarial code review for AI-generated code.

A Claude Code PreToolUse hook that sends diffs to a second model for
independent review before edits are applied.

Usage as hook:
    Register as a PreToolUse hook in ~/.claude/settings.json for
    Edit, Write, and NotebookEdit tool calls.

CLI modes:
    python3 crosscheck.py --test          Verify model connectivity
    python3 crosscheck.py --dry-run       Show what would be reviewed (pipe hook input via stdin)
    python3 crosscheck.py --version       Show version

Configuration:
    Config file: ~/.claude/crosscheck.json
    Environment variables override config values (prefix: CROSSCHECK_).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

__version__ = "0.1.0"

# --- Constants ---

TOOL_NAMES: set[str] = {"Edit", "Write", "NotebookEdit"}

SEVERITY_LEVELS: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "qwen3:8b",
    "endpoint": "http://localhost:11434/api/chat",
    "threshold": "medium",
    "include": ["*.py", "*.ts", "*.js", "*.tsx", "*.jsx", "*.go", "*.rs", "*.java"],
    "exclude": ["*.test.*", "*.spec.*", "*.min.*", "node_modules/**", "dist/**", "build/**"],
    "max_diff_lines": 200,
    "timeout": 30,
}

REVIEW_SYSTEM_PROMPT = """\
You are an adversarial code reviewer. Your job is to find real bugs, security \
vulnerabilities, and logic errors that the AI code generator likely missed.

Respond with ONLY a JSON array of issues found. No other text.

Format:
[{"severity": "low|medium|high", "line": <number or null>, "message": "<issue>"}]

If the code is fine, respond with: []

Focus on these categories:
- SECURITY: injection, XSS, path traversal, hardcoded secrets, unsafe deserialization
- BUGS: off-by-one, null/undefined access, race conditions, resource leaks
- LOGIC: wrong comparisons, inverted conditions, missing edge cases, infinite loops
- AI-SPECIFIC: hallucinated imports/APIs, wrong function signatures, deprecated methods, \
non-existent modules, incorrect async/await patterns

Do NOT flag:
- Style preferences or formatting
- Missing comments or documentation
- Naming conventions
- Type annotation completeness
- Things that are clearly intentional from context

Keep each message under 120 characters. Be specific.\
"""


# --- Logging ---


def log(msg: str) -> None:
    """Log to stderr so it doesn't interfere with hook JSON output."""
    print(f"[crosscheck] {msg}", file=sys.stderr)


# --- Config ---


def load_config() -> dict[str, Any]:
    """Load configuration from file and environment variables."""
    config = dict(DEFAULT_CONFIG)

    config_path = Path.home() / ".claude" / "crosscheck.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_config = json.load(f)
            config.update(file_config)
        except (json.JSONDecodeError, OSError):
            pass

    env_map: dict[str, str] = {
        "CROSSCHECK_MODEL": "model",
        "CROSSCHECK_ENDPOINT": "endpoint",
        "CROSSCHECK_THRESHOLD": "threshold",
        "CROSSCHECK_MAX_DIFF_LINES": "max_diff_lines",
        "CROSSCHECK_TIMEOUT": "timeout",
    }

    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if config_key in ("max_diff_lines", "timeout"):
                config[config_key] = int(val)
            else:
                config[config_key] = val

    include_env = os.environ.get("CROSSCHECK_INCLUDE")
    if include_env:
        config["include"] = [p.strip() for p in include_env.split(",")]

    exclude_env = os.environ.get("CROSSCHECK_EXCLUDE")
    if exclude_env:
        config["exclude"] = [p.strip() for p in exclude_env.split(",")]

    return config


# --- Pattern matching ---


def matches_patterns(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the given glob patterns."""
    basename = os.path.basename(file_path)
    for pattern in patterns:
        if fnmatch.fnmatch(basename, pattern) or fnmatch.fnmatch(file_path, pattern):
            return True
    return False


def should_review(file_path: str, config: dict[str, Any]) -> bool:
    """Determine if a file should be reviewed based on include/exclude patterns."""
    include: list[str] = config.get("include", DEFAULT_CONFIG["include"])
    exclude: list[str] = config.get("exclude", DEFAULT_CONFIG["exclude"])

    if not matches_patterns(file_path, include):
        return False
    return not matches_patterns(file_path, exclude)


# --- Diff context ---


def build_review_prompt(
    tool_name: str,
    file_path: str,
    content: str,
    old_content: str | None = None,
) -> str:
    """Build the review prompt with full diff context."""
    parts: list[str] = []

    if tool_name == "Edit" and old_content:
        parts.append(f"Review this edit to `{file_path}`:")
        parts.append("")
        parts.append("BEFORE:")
        parts.append(f"```\n{old_content}\n```")
        parts.append("")
        parts.append("AFTER:")
        parts.append(f"```\n{content}\n```")
    elif tool_name == "Write":
        parts.append(f"Review this new file being written to `{file_path}`:")
        parts.append(f"```\n{content}\n```")
    else:
        parts.append(f"Review this code change in `{file_path}`:")
        parts.append(f"```\n{content}\n```")

    return "\n".join(parts)


# --- Model interaction ---


def call_review_model(
    prompt: str, config: dict[str, Any]
) -> tuple[list[dict[str, Any]], float, bool]:
    """Send the diff to the review model and parse the response.

    Returns (issues, elapsed_seconds, success).
    """
    endpoint: str = config["endpoint"]
    model: str = config["model"]
    timeout: int = config.get("timeout", 30)

    is_ollama_native = "/api/chat" in endpoint

    messages = [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    if is_ollama_native:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "options": {"temperature": 0.1, "num_predict": 1024},
            "think": False,
            "stream": False,
        }
    else:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 1024,
        }

    headers: dict[str, str] = {
        "Content-Type": "application/json",
    }

    api_key = os.environ.get("CROSSCHECK_API_KEY") or os.environ.get(
        "OPENROUTER_API_KEY"
    )
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - start
        log(f"HTTP {e.code} from {endpoint} ({elapsed:.1f}s)")
        return [], elapsed, False
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        elapsed = time.monotonic() - start
        log(f"Connection failed: {e} ({elapsed:.1f}s)")
        return [], elapsed, False
    except json.JSONDecodeError:
        elapsed = time.monotonic() - start
        log(f"Invalid JSON response ({elapsed:.1f}s)")
        return [], elapsed, False

    elapsed = time.monotonic() - start

    # Extract the response content — handle both API formats
    try:
        if is_ollama_native:
            response_text: str = result.get("message", {}).get("content", "")
        else:
            choice = result["choices"][0]["message"]
            response_text = choice.get("content") or ""
    except (KeyError, IndexError):
        return [], elapsed, False

    issues = parse_review_response(response_text)
    return issues, elapsed, True


def parse_review_response(response_text: str) -> list[dict[str, Any]]:
    """Parse the model's review response into structured issues."""
    text = response_text.strip()
    if not text:
        return []

    # Handle markdown code blocks
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("["):
                text = cleaned
                break

    # Try direct parse
    try:
        issues = json.loads(text)
        if isinstance(issues, list):
            return _validate_issues(issues)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON array from surrounding text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            issues = json.loads(text[start : end + 1])
            if isinstance(issues, list):
                return _validate_issues(issues)
        except json.JSONDecodeError:
            pass

    return []


def _validate_issues(issues: list[Any]) -> list[dict[str, Any]]:
    """Validate and normalize issue objects."""
    validated: list[dict[str, Any]] = []
    for item in issues:
        if not isinstance(item, dict):
            continue
        if "message" not in item:
            continue
        severity = str(item.get("severity", "medium")).lower()
        if severity not in SEVERITY_LEVELS:
            severity = "medium"
        validated.append({
            "severity": severity,
            "line": item.get("line"),
            "message": str(item["message"])[:200],
        })
    return validated


def filter_by_threshold(
    issues: list[dict[str, Any]], threshold: str
) -> list[dict[str, Any]]:
    """Filter issues by severity threshold."""
    min_level = SEVERITY_LEVELS.get(threshold, 1)
    return [
        issue
        for issue in issues
        if SEVERITY_LEVELS.get(issue.get("severity", "medium"), 1) >= min_level
    ]


# --- Hook interface ---


def extract_tool_input(
    hook_input: dict[str, Any],
) -> tuple[str, str | None, str | None, str | None]:
    """Extract tool name, file path, new content, and old content from hook input."""
    tool_name: str = hook_input.get("tool_name", "")
    tool_input: dict[str, Any] = hook_input.get("tool_input", {})

    if tool_name == "Write":
        return tool_name, tool_input.get("file_path"), tool_input.get("content"), None
    elif tool_name == "Edit":
        return (
            tool_name,
            tool_input.get("file_path"),
            tool_input.get("new_string"),
            tool_input.get("old_string"),
        )
    elif tool_name == "NotebookEdit":
        return (
            tool_name,
            tool_input.get("file_path"),
            tool_input.get("new_source"),
            tool_input.get("old_source"),
        )

    return tool_name, None, None, None


def format_hook_response(
    issues: list[dict[str, Any]], file_path: str, elapsed: float
) -> dict[str, Any]:
    """Format issues as a Claude Code hook response."""
    if not issues:
        return {"decision": "approve"}

    lines: list[str] = [
        f"crosscheck found {len(issues)} issue(s) in {os.path.basename(file_path)} ({elapsed:.1f}s):"
    ]
    for issue in issues:
        severity = issue.get("severity", "medium").upper()
        line_num = issue.get("line")
        message = issue.get("message", "Unknown issue")
        prefix = f"[{severity}]"
        if line_num:
            prefix += f" L{line_num}"
        lines.append(f"  {prefix}: {message}")

    return {
        "decision": "approve",
        "message": "\n".join(lines),
    }


def approve() -> None:
    """Output an approve decision and exit."""
    print(json.dumps({"decision": "approve"}))


# --- CLI modes ---


def cmd_test(config: dict[str, Any]) -> None:
    """Test connectivity to the review model."""
    endpoint = config["endpoint"]
    model = config["model"]

    print(f"crosscheck v{__version__}")
    print(f"  Model:    {model}")
    print(f"  Endpoint: {endpoint}")
    print(f"  Timeout:  {config.get('timeout', 30)}s")
    print()

    test_prompt = 'Review this code:\n\n```python\npassword = "admin123"\n```'
    print(f"Sending test review to {model}...")

    issues, elapsed, success = call_review_model(test_prompt, config)

    if not success:
        print(f"\n  FAIL: Could not get a valid response from {endpoint}")
        print(f"  Ensure the endpoint is reachable and the model '{model}' is loaded.")
        print(f"  Elapsed: {elapsed:.1f}s")
        sys.exit(1)

    print(f"  Response in {elapsed:.1f}s")

    if issues:
        print(f"  Found {len(issues)} issue(s) — model is working correctly:")
        for issue in issues:
            sev = issue.get("severity", "?").upper()
            msg = issue.get("message", "")
            print(f"    [{sev}] {msg}")
    else:
        print("  No issues found (model may not have flagged the test case, but connectivity works)")

    print()
    print("OK — crosscheck is ready.")


def cmd_dry_run(config: dict[str, Any]) -> None:
    """Show what would be reviewed without calling the model."""
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        print("Error: Could not parse hook input from stdin.", file=sys.stderr)
        sys.exit(1)

    tool_name = hook_input.get("tool_name", "")
    _, file_path, content, old_content = extract_tool_input(hook_input)

    print(f"Tool:     {tool_name}")
    print(f"File:     {file_path or '(none)'}")
    print(f"Matches:  {should_review(file_path, config) if file_path else False}")

    if content:
        line_count = content.count("\n") + 1
        max_lines: int = config.get("max_diff_lines", 200)
        print(f"Lines:    {line_count} (max: {max_lines})")
        if line_count > max_lines:
            print("Action:   SKIP (exceeds max_diff_lines)")
        elif file_path and not should_review(file_path, config):
            print("Action:   SKIP (excluded by patterns)")
        else:
            print("Action:   WOULD REVIEW")
            if old_content:
                print(f"\n--- OLD ({len(old_content)} chars) ---")
                print(old_content[:500])
            print(f"\n--- NEW ({len(content)} chars) ---")
            print(content[:500])
    else:
        print("Content:  (none)")
        print("Action:   SKIP (no content)")


# --- Main ---


def main() -> None:
    """Main entry point. Handles CLI args or hook input from stdin."""
    parser = argparse.ArgumentParser(
        prog="crosscheck",
        description="Adversarial code review for AI-generated code",
    )
    parser.add_argument("--test", action="store_true", help="Test model connectivity")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be reviewed (stdin)"
    )
    parser.add_argument("--version", action="version", version=f"crosscheck {__version__}")

    # Only parse known args — stdin may contain hook JSON
    args, _ = parser.parse_known_args()

    config = load_config()

    if args.test:
        cmd_test(config)
        return

    if args.dry_run:
        cmd_dry_run(config)
        return

    # --- Hook mode ---
    try:
        raw_input = sys.stdin.read()
        hook_input: dict[str, Any] = json.loads(raw_input)
    except (json.JSONDecodeError, OSError):
        approve()
        return

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in TOOL_NAMES:
        approve()
        return

    _, file_path, content, old_content = extract_tool_input(hook_input)
    if not file_path or not content:
        approve()
        return

    if not should_review(file_path, config):
        approve()
        return

    max_lines: int = config.get("max_diff_lines", 200)
    if content.count("\n") > max_lines:
        log(f"Skipping {file_path}: {content.count(chr(10))} lines > {max_lines} max")
        approve()
        return

    # Build prompt with diff context
    prompt = build_review_prompt(tool_name, file_path, content, old_content)

    log(f"Reviewing {file_path} ({content.count(chr(10)) + 1} lines)...")
    issues, elapsed, _success = call_review_model(prompt, config)
    log(f"Got {len(issues)} issue(s) in {elapsed:.1f}s")

    # Filter by threshold
    threshold: str = config.get("threshold", "medium")
    filtered = filter_by_threshold(issues, threshold)
    if len(filtered) < len(issues):
        log(f"Filtered to {len(filtered)} issue(s) at threshold={threshold}")

    response = format_hook_response(filtered, file_path, elapsed)
    print(json.dumps(response))


if __name__ == "__main__":
    main()
