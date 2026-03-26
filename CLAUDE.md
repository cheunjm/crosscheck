# crosscheck

Adversarial code review hook for Claude Code. Sends AI-generated diffs to a second model for independent review.

## Structure

```
crosscheck.py       Main hook script (PreToolUse for Claude Code)
crosscheck.json     Example configuration
install.sh          Installer script
```

## Development

```bash
ruff check crosscheck.py    # Lint
mypy crosscheck.py          # Type check
```

## How it works

1. Claude Code triggers an Edit/Write/NotebookEdit tool call
2. crosscheck.py receives the hook input via stdin (JSON)
3. Extracts file path and new content from the tool input
4. Checks file against include/exclude patterns
5. Sends content to a second model via OpenAI-compatible API
6. Parses the review and returns warnings/suggestions
7. Claude Code sees the warnings before applying the edit

## Config

Config file: `~/.claude/crosscheck.json` (see `crosscheck.json` for schema).
Environment variables override config file values (prefix: `CROSSCHECK_`).
