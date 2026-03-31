# crosscheck

Adversarial code review hook for Claude Code. Sends AI-generated diffs to a second model for independent review.

## Structure

```
crosscheck.py            Main hook script (single file, zero dependencies)
test_crosscheck.py       Unit tests (unittest, zero dependencies)
crosscheck.json          Example configuration
install.sh               Installer script
```

## Development

```bash
ruff check crosscheck.py test_crosscheck.py    # Lint
mypy crosscheck.py test_crosscheck.py           # Type check
python3 -m unittest discover -s . -p "test_*.py" -v  # Unit tests
python3 crosscheck.py --test                    # Verify model connectivity
python3 crosscheck.py --version
```

## Testing

```bash
# Dry run (no model call):
echo '{"tool_name":"Write","tool_input":{"file_path":"app.py","content":"x=1"}}' | python3 crosscheck.py --dry-run

# Live test (needs Ollama or other model endpoint):
echo '{"tool_name":"Write","tool_input":{"file_path":"app.py","content":"import os\nresult = os.popen(user_input)"}}' | python3 crosscheck.py
```

## How it works

1. Claude Code triggers an Edit/Write/NotebookEdit tool call
2. crosscheck.py receives the hook input via stdin (JSON)
3. Extracts file path, new content, and old content (for Edit diffs)
4. Checks file against include/exclude patterns
5. Sends content to a second model via OpenAI-compatible API
6. Parses structured JSON review response
7. Filters by severity threshold
8. Returns warnings inline — Claude sees them before proceeding
9. Fails open on any error (model down, timeout, parse failure)

## Key design decisions

- **Single file** — no package manager, no dependencies, just `crosscheck.py`
- **Fail open** — never blocks edits, only warns
- **Diff context** — Edit operations send both old and new content
- **AI-specific prompt** — tuned to catch hallucinated imports, wrong signatures, deprecated methods
- **Configurable** — model, endpoint, threshold, patterns, timeout, context_lines
- **Smart diffs** — Edit operations include surrounding file context; Write operations send unified diffs for existing files
