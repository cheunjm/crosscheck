# crosscheck

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Adversarial code review for AI-generated code.**

A Claude Code hook that sends AI-generated diffs to a second, independent model for review before edits are applied. Catches blind spots that arise when the same AI reviews its own code — breaking the monoculture of thinking.

## Why

When an AI writes code and then reviews it, it shares the same biases, blind spots, and reasoning patterns. crosscheck introduces a second opinion from a different model (local Ollama, OpenRouter, or any OpenAI-compatible API), catching issues the primary model is structurally unlikely to notice.

Zero cost when using local models via Ollama.

## How it works

```
Claude Code → Edit/Write tool call
                    ↓
          crosscheck (PreToolUse hook)
                    ↓
          Extracts file path + diff context
                    ↓
          Sends to second model (Ollama / OpenRouter / any OpenAI-compatible API)
                    ↓
          Parses structured review response
                    ↓
          Returns warnings inline → Claude sees them before proceeding
```

For **Edit** operations, crosscheck sends both the old and new content so the reviewer sees the actual change — not just the new code in isolation.

## Quick start

### 1. Install

```bash
git clone https://github.com/cheunjm/crosscheck.git
cd crosscheck
bash install.sh
```

### 2. Register the hook

Add to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|NotebookEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/crosscheck.py"
          }
        ]
      }
    ]
  }
}
```

### 3. Test connectivity

```bash
python3 ~/.claude/hooks/crosscheck.py --test
```

```
crosscheck v0.1.0
  Model:    qwen2.5:14b
  Endpoint: http://localhost:11434/v1/chat/completions
  Timeout:  30s

Sending test review to qwen2.5:14b...
  Response in 2.3s
  Found 1 issue(s) — model is working correctly:
    [HIGH] Hardcoded password — use environment variable or secrets manager

OK — crosscheck is ready.
```

### 4. Configure your model

By default, crosscheck uses Ollama at `localhost:11434` with `qwen2.5:14b`. Edit `~/.claude/crosscheck.json` or set environment variables:

```bash
export CROSSCHECK_MODEL="qwen2.5:14b"
export CROSSCHECK_ENDPOINT="http://localhost:11434/v1/chat/completions"
```

## Configuration

crosscheck reads from `~/.claude/crosscheck.json`, with environment variable overrides.

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `model` | `CROSSCHECK_MODEL` | `qwen2.5:14b` | Model identifier |
| `endpoint` | `CROSSCHECK_ENDPOINT` | `http://localhost:11434/v1/chat/completions` | OpenAI-compatible endpoint |
| `threshold` | `CROSSCHECK_THRESHOLD` | `medium` | Min severity to report: `low`, `medium`, `high` |
| `include` | `CROSSCHECK_INCLUDE` | `*.py,*.ts,*.js,*.tsx,*.jsx,*.go,*.rs,*.java` | File patterns to review |
| `exclude` | `CROSSCHECK_EXCLUDE` | `*.test.*,*.spec.*,*.min.*,node_modules/**,...` | File patterns to skip |
| `max_diff_lines` | `CROSSCHECK_MAX_DIFF_LINES` | `200` | Skip review if content exceeds this |
| `timeout` | `CROSSCHECK_TIMEOUT` | `30` | Request timeout in seconds |

## CLI modes

```bash
# Test model connectivity
python3 crosscheck.py --test

# Dry run — see what would be reviewed without calling the model
echo '{"tool_name":"Edit","tool_input":{"file_path":"app.py","old_string":"x=1","new_string":"x=2"}}' | python3 crosscheck.py --dry-run

# Show version
python3 crosscheck.py --version
```

## What it catches

crosscheck's review prompt is tuned to catch issues AI code generators commonly miss:

- **Security**: injection, XSS, path traversal, hardcoded secrets
- **Bugs**: off-by-one, null access, race conditions, resource leaks
- **Logic**: wrong comparisons, inverted conditions, missing edge cases
- **AI-specific**: hallucinated imports, wrong function signatures, deprecated methods, non-existent modules

It does NOT flag style, formatting, naming, or documentation — only real issues.

## Supported providers

| Provider | Endpoint | Notes |
|----------|----------|-------|
| **Ollama** | `http://localhost:11434/v1/chat/completions` | Free, local, default |
| **vLLM** | `http://localhost:8000/v1/chat/completions` | Local, GPU-accelerated |
| **OpenRouter** | `https://openrouter.ai/api/v1/chat/completions` | Multi-model, set `CROSSCHECK_API_KEY` |
| **LiteLLM** | `http://localhost:4000/v1/chat/completions` | Proxy/router |
| **Any OpenAI-compatible** | varies | Just point `endpoint` at it |

For cloud providers, set the API key:

```bash
export CROSSCHECK_API_KEY="your-api-key"
# or for OpenRouter specifically:
export OPENROUTER_API_KEY="your-key"
```

## Design principles

- **Fail open** — if the review model is unavailable, edits proceed normally
- **Zero dependencies** — stdlib only, no pip install needed
- **Fast** — small diffs, low temperature, 1024 max tokens
- **Non-blocking** — approves with warnings, never blocks edits
- **Configurable** — threshold, patterns, model, timeout all tunable

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

```bash
git clone https://github.com/cheunjm/crosscheck.git
cd crosscheck
pip install ruff mypy
ruff check crosscheck.py
mypy crosscheck.py
```

## License

[MIT](LICENSE) — Jace Cheun
