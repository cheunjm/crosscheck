#!/usr/bin/env python3
"""crosscheck — adversarial code review for AI-generated code.

A Claude Code PreToolUse hook that sends diffs to a second model for
independent review before edits are applied.

Usage:
    Register as a PreToolUse hook in ~/.claude/settings.json for
    Edit, Write, and NotebookEdit tool calls.

Configuration:
    Config file: ~/.claude/crosscheck.json
    Environment variables override config values (prefix: CROSSCHECK_).
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# --- Constants ---

TOOL_NAMES = {"Edit", "Write", "NotebookEdit"}

SEVERITY_LEVELS = {"low": 0, "medium": 1, "high": 2}

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "qwen2.5:14b",
    "endpoint": "http://localhost:11434/v1/chat/completions",
    "threshold": "medium",
    "include": ["*.py", "*.ts", "*.js", "*.tsx", "*.jsx"],
    "exclude": ["*.test.*", "*.spec.*", "node_modules/**"],
    "max_diff_lines": 200,
}

REVIEW_SYSTEM_PROMPT = """\
You are an adversarial code reviewer. Your job is to find bugs, security issues, \
logic errors, and bad practices in code diffs. Be concise and direct.

For each issue found, respond with a JSON array of objects:
[
  {
    "severity": "low" | "medium" | "high",
    "line": <line number or null>,
    "message": "<concise description of the issue>"
  }
]

If the code looks fine, respond with an empty array: []

Rules:
- Focus on real bugs, security issues, and logic errors
- Do NOT flag style preferences or minor formatting
- Do NOT flag issues that are clearly intentional
- Be specific about what's wrong and why
- Keep messages under 100 characters each
"""


# --- Config ---


def load_config() -> dict[str, Any]:
    """Load configuration from file and environment variables."""
    config = dict(DEFAULT_CONFIG)

    # Load from config file
    config_path = Path.home() / ".claude" / "crosscheck.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_config = json.load(f)
            config.update(file_config)
        except (json.JSONDecodeError, OSError):
            pass  # Fall back to defaults

    # Environment variable overrides
    env_map: dict[str, str] = {
        "CROSSCHECK_MODEL": "model",
        "CROSSCHECK_ENDPOINT": "endpoint",
        "CROSSCHECK_THRESHOLD": "threshold",
        "CROSSCHECK_MAX_DIFF_LINES": "max_diff_lines",
    }

    for env_key, config_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if config_key == "max_diff_lines":
                config[config_key] = int(val)
            else:
                config[config_key] = val

    # List overrides (comma-separated)
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
    include = config.get("include", DEFAULT_CONFIG["include"])
    exclude = config.get("exclude", DEFAULT_CONFIG["exclude"])

    if not matches_patterns(file_path, include):
        return False

    if matches_patterns(file_path, exclude):
        return False

    return True


# --- Model interaction ---


def build_review_prompt(file_path: str, content: str) -> str:
    """Build the user prompt for the review model."""
    return f"Review this code being written to `{file_path}`:\n\n```\n{content}\n```"


def call_review_model(
    prompt: str, config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Send the diff to the review model and parse the response."""
    endpoint: str = config["endpoint"]
    model: str = config["model"]

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }

    headers = {
        "Content-Type": "application/json",
    }

    # Support API keys via environment
    api_key = os.environ.get("CROSSCHECK_API_KEY") or os.environ.get(
        "OPENROUTER_API_KEY"
    )
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        # Model unavailable — fail open (don't block edits)
        return []
    except json.JSONDecodeError:
        return []

    # Extract the response content
    try:
        response_text = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return []

    return parse_review_response(response_text)


def parse_review_response(response_text: str) -> list[dict[str, Any]]:
    """Parse the model's review response into structured issues."""
    # Try to extract JSON from the response
    text = response_text.strip()

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

    try:
        issues = json.loads(text)
        if isinstance(issues, list):
            return issues
    except json.JSONDecodeError:
        pass

    # If we can't parse JSON, try to find a JSON array in the text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            issues = json.loads(text[start : end + 1])
            if isinstance(issues, list):
                return issues
        except json.JSONDecodeError:
            pass

    return []


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


def extract_tool_input(hook_input: dict[str, Any]) -> tuple[str | None, str | None]:
    """Extract file path and content from the tool input."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name == "Write":
        return tool_input.get("file_path"), tool_input.get("content")
    elif tool_name == "Edit":
        return tool_input.get("file_path"), tool_input.get("new_string")
    elif tool_name == "NotebookEdit":
        return tool_input.get("file_path"), tool_input.get("new_source")

    return None, None


def format_hook_response(issues: list[dict[str, Any]], file_path: str) -> dict[str, Any]:
    """Format issues as a Claude Code hook response."""
    if not issues:
        return {"decision": "approve"}

    # Build warning message
    lines = [f"crosscheck found {len(issues)} issue(s) in {os.path.basename(file_path)}:"]
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


def main() -> None:
    """Main hook entry point. Reads hook input from stdin."""
    try:
        raw_input = sys.stdin.read()
        hook_input = json.loads(raw_input)
    except (json.JSONDecodeError, OSError):
        # Can't parse input — fail open
        print(json.dumps({"decision": "approve"}))
        return

    # Check if this is a tool we care about
    tool_name = hook_input.get("tool_name", "")
    if tool_name not in TOOL_NAMES:
        print(json.dumps({"decision": "approve"}))
        return

    # Load config
    config = load_config()

    # Extract file path and content
    file_path, content = extract_tool_input(hook_input)
    if not file_path or not content:
        print(json.dumps({"decision": "approve"}))
        return

    # Check if file matches patterns
    if not should_review(file_path, config):
        print(json.dumps({"decision": "approve"}))
        return

    # Check content length
    max_lines: int = config.get("max_diff_lines", 200)
    if content.count("\n") > max_lines:
        print(json.dumps({"decision": "approve"}))
        return

    # Send to review model
    prompt = build_review_prompt(file_path, content)
    issues = call_review_model(prompt, config)

    # Filter by threshold
    threshold: str = config.get("threshold", "medium")
    issues = filter_by_threshold(issues, threshold)

    # Format and output response
    response = format_hook_response(issues, file_path)
    print(json.dumps(response))


if __name__ == "__main__":
    main()
