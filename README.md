# crosscheck

[![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Adversarial code review for AI-generated code.**

A Claude Code hook that sends AI-generated diffs to a second, independent model for review before edits are applied. Catches blind spots that arise when the same AI reviews its own code — breaking the monoculture of thinking.

## Why

When an AI writes code and then reviews it, it shares the same biases, blind spots, and reasoning patterns. crosscheck introduces a second opinion from a different model (local Ollama, OpenRouter, or any OpenAI-compatible API), catching issues the primary model is structurally unlikely to notice.

Zero cost when using local models via Ollama.

## How it works

```
Claude Code
    |
    v
Edit/Write tool call
    |
    v
crosscheck (PreToolUse hook)
    |
    v
Extracts file path + new content
    |
    v
Sends diff to second model (Ollama / OpenRouter / any OpenAI-compatible API)
    |
    v
Parses review response
    |
    v
Returns warnings/suggestions inline
    |
    v
Claude Code applies (or reconsiders) the edit
```

## Quick start

### 1. Install

```bash
# Clone and run the installer
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

### 3. Configure your model

By default, crosscheck uses Ollama at `localhost:11434` with `qwen2.5:14b`. To change this, edit `~/.claude/crosscheck.json` or set environment variables:

```bash
export CROSSCHECK_MODEL="qwen2.5:14b"
export CROSSCHECK_ENDPOINT="http://localhost:11434/v1/chat/completions"
```

## Configuration

crosscheck reads configuration from `~/.claude/crosscheck.json`, with environment variable overrides.

| Option | Env var | Default | Description |
|--------|---------|---------|-------------|
| `model` | `CROSSCHECK_MODEL` | `qwen2.5:14b` | Model identifier |
| `endpoint` | `CROSSCHECK_ENDPOINT` | `http://localhost:11434/v1/chat/completions` | OpenAI-compatible chat completions endpoint |
| `threshold` | `CROSSCHECK_THRESHOLD` | `medium` | Minimum severity to report: `low`, `medium`, `high` |
| `include` | `CROSSCHECK_INCLUDE` | `["*.py", "*.ts", "*.js", "*.tsx", "*.jsx"]` | File patterns to review (comma-separated in env) |
| `exclude` | `CROSSCHECK_EXCLUDE` | `["*.test.*", "*.spec.*", "node_modules/**"]` | File patterns to skip (comma-separated in env) |
| `max_diff_lines` | `CROSSCHECK_MAX_DIFF_LINES` | `200` | Skip review if diff exceeds this many lines |

## Supported providers

crosscheck works with any OpenAI-compatible chat completions API:

| Provider | Endpoint example | Notes |
|----------|-----------------|-------|
| **Ollama** | `http://localhost:11434/v1/chat/completions` | Free, local, default |
| **vLLM** | `http://localhost:8000/v1/chat/completions` | Local, GPU-accelerated |
| **OpenRouter** | `https://openrouter.ai/api/v1/chat/completions` | Multi-model, set `OPENROUTER_API_KEY` |
| **LiteLLM** | `http://localhost:4000/v1/chat/completions` | Proxy/router |
| **Any OpenAI-compatible** | varies | Just point `endpoint` at it |

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

```bash
# Development setup
git clone https://github.com/cheunjm/crosscheck.git
cd crosscheck
pip install ruff mypy  # for linting
ruff check crosscheck.py
mypy crosscheck.py
```

## License

[MIT](LICENSE) - Jace Cheun
