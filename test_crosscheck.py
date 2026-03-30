"""Unit tests for crosscheck.py."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from crosscheck import (
    CACHE_MAX_ENTRIES,
    CACHE_TTL_SECONDS,
    DEFAULT_CONFIG,
    _cache_key,
    _load_cache,
    _save_cache,
    build_review_prompt,
    cache_lookup,
    cache_store,
    compute_file_diff,
    extract_tool_input,
    filter_by_threshold,
    format_hook_response,
    matches_patterns,
    parse_review_response,
    read_surrounding_context,
    should_review,
)


# --- parse_review_response ---


class TestParseReviewResponse(unittest.TestCase):
    def test_valid_json_array(self) -> None:
        text = '[{"severity":"high","line":1,"message":"bug found"}]'
        issues = parse_review_response(text)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "high")
        self.assertEqual(issues[0]["message"], "bug found")

    def test_empty_array(self) -> None:
        self.assertEqual(parse_review_response("[]"), [])

    def test_empty_string(self) -> None:
        self.assertEqual(parse_review_response(""), [])

    def test_markdown_wrapped_json(self) -> None:
        text = '```json\n[{"severity":"medium","line":5,"message":"issue"}]\n```'
        issues = parse_review_response(text)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["severity"], "medium")

    def test_garbage_input(self) -> None:
        self.assertEqual(parse_review_response("not json at all"), [])

    def test_json_with_surrounding_text(self) -> None:
        text = 'Here are issues: [{"severity":"low","message":"minor"}] Done.'
        issues = parse_review_response(text)
        self.assertEqual(len(issues), 1)

    def test_missing_message_field(self) -> None:
        text = '[{"severity":"high","line":1}]'
        self.assertEqual(parse_review_response(text), [])

    def test_unknown_severity_defaults_to_medium(self) -> None:
        text = '[{"severity":"critical","message":"something"}]'
        issues = parse_review_response(text)
        self.assertEqual(issues[0]["severity"], "medium")

    def test_message_truncated_to_200(self) -> None:
        long_msg = "x" * 300
        text = json.dumps([{"severity": "high", "message": long_msg}])
        issues = parse_review_response(text)
        self.assertEqual(len(issues[0]["message"]), 200)

    def test_non_dict_items_skipped(self) -> None:
        text = '["not a dict", {"message": "valid"}]'
        issues = parse_review_response(text)
        self.assertEqual(len(issues), 1)


# --- should_review / matches_patterns ---


class TestMatchesPatterns(unittest.TestCase):
    def test_basename_match(self) -> None:
        self.assertTrue(matches_patterns("app.py", ["*.py"]))

    def test_fullpath_match(self) -> None:
        self.assertTrue(matches_patterns("node_modules/foo.js", ["node_modules/**"]))

    def test_no_match(self) -> None:
        self.assertFalse(matches_patterns("readme.md", ["*.py", "*.js"]))


class TestShouldReview(unittest.TestCase):
    def test_included_extension(self) -> None:
        self.assertTrue(should_review("app.py", DEFAULT_CONFIG))

    def test_excluded_extension(self) -> None:
        self.assertFalse(should_review("app.min.js", DEFAULT_CONFIG))

    def test_not_included(self) -> None:
        self.assertFalse(should_review("readme.md", DEFAULT_CONFIG))

    def test_test_file_excluded(self) -> None:
        self.assertFalse(should_review("app.test.ts", DEFAULT_CONFIG))

    def test_spec_file_excluded(self) -> None:
        self.assertFalse(should_review("app.spec.js", DEFAULT_CONFIG))


# --- filter_by_threshold ---


class TestFilterByThreshold(unittest.TestCase):
    def setUp(self) -> None:
        self.issues = [
            {"severity": "low", "message": "a"},
            {"severity": "medium", "message": "b"},
            {"severity": "high", "message": "c"},
        ]

    def test_threshold_low(self) -> None:
        result = filter_by_threshold(self.issues, "low")
        self.assertEqual(len(result), 3)

    def test_threshold_medium(self) -> None:
        result = filter_by_threshold(self.issues, "medium")
        self.assertEqual(len(result), 2)
        severities = {i["severity"] for i in result}
        self.assertNotIn("low", severities)

    def test_threshold_high(self) -> None:
        result = filter_by_threshold(self.issues, "high")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], "high")

    def test_unknown_threshold_defaults_to_medium(self) -> None:
        result = filter_by_threshold(self.issues, "unknown")
        self.assertEqual(len(result), 2)


# --- extract_tool_input ---


class TestExtractToolInput(unittest.TestCase):
    def test_write_tool(self) -> None:
        hook = {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x=1"}}
        name, path, content, old = extract_tool_input(hook)
        self.assertEqual(name, "Write")
        self.assertEqual(path, "a.py")
        self.assertEqual(content, "x=1")
        self.assertIsNone(old)

    def test_edit_tool(self) -> None:
        hook = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "a.py", "old_string": "old", "new_string": "new"},
        }
        name, path, content, old = extract_tool_input(hook)
        self.assertEqual(name, "Edit")
        self.assertEqual(path, "a.py")
        self.assertEqual(content, "new")
        self.assertEqual(old, "old")

    def test_notebook_edit_tool(self) -> None:
        hook = {
            "tool_name": "NotebookEdit",
            "tool_input": {"file_path": "nb.ipynb", "old_source": "old", "new_source": "new"},
        }
        name, path, content, old = extract_tool_input(hook)
        self.assertEqual(name, "NotebookEdit")
        self.assertEqual(content, "new")
        self.assertEqual(old, "old")

    def test_unknown_tool(self) -> None:
        hook = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
        name, path, content, old = extract_tool_input(hook)
        self.assertEqual(name, "Bash")
        self.assertIsNone(path)
        self.assertIsNone(content)
        self.assertIsNone(old)


# --- build_review_prompt ---


class TestBuildReviewPrompt(unittest.TestCase):
    def test_edit_with_old_content(self) -> None:
        prompt = build_review_prompt("Edit", "a.py", "new_code", "old_code")
        self.assertIn("BEFORE:", prompt)
        self.assertIn("AFTER:", prompt)
        self.assertIn("old_code", prompt)
        self.assertIn("new_code", prompt)

    def test_edit_with_surrounding_context(self) -> None:
        prompt = build_review_prompt(
            "Edit", "a.py", "new_code", "old_code", surrounding_context="ctx here"
        )
        self.assertIn("SURROUNDING CODE", prompt)
        self.assertIn("ctx here", prompt)
        self.assertIn("BEFORE:", prompt)

    def test_write_new_file(self) -> None:
        prompt = build_review_prompt("Write", "a.py", "content")
        self.assertIn("new file", prompt)
        self.assertIn("content", prompt)

    def test_write_with_diff(self) -> None:
        prompt = build_review_prompt("Write", "a.py", "content", diff_text="--- a\n+++ b\n@@ @@")
        self.assertIn("change to existing file", prompt)
        self.assertIn("```diff", prompt)
        self.assertNotIn("new file", prompt)

    def test_fallback_generic(self) -> None:
        prompt = build_review_prompt("NotebookEdit", "nb.ipynb", "code")
        self.assertIn("code change", prompt)


# --- cache ---


class TestCacheKey(unittest.TestCase):
    def test_deterministic(self) -> None:
        k1 = _cache_key("a.py", "content", None)
        k2 = _cache_key("a.py", "content", None)
        self.assertEqual(k1, k2)

    def test_different_content_different_key(self) -> None:
        k1 = _cache_key("a.py", "content1", None)
        k2 = _cache_key("a.py", "content2", None)
        self.assertNotEqual(k1, k2)

    def test_old_content_affects_key(self) -> None:
        k1 = _cache_key("a.py", "new", None)
        k2 = _cache_key("a.py", "new", "old")
        self.assertNotEqual(k1, k2)

    def test_key_length(self) -> None:
        key = _cache_key("a.py", "content", None)
        self.assertEqual(len(key), 16)


class TestAtomicCacheWrite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.cache_path = Path(self.tmp.name)
        self.patcher = patch("crosscheck.CACHE_PATH", self.cache_path)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_save_and_load_roundtrip(self) -> None:
        data = {"key1": {"ts": time.time(), "response": {"decision": "approve"}}}
        _save_cache(data)
        loaded = _load_cache()
        self.assertIn("key1", loaded)

    def test_eviction_by_ttl(self) -> None:
        old_ts = time.time() - CACHE_TTL_SECONDS - 100
        data = {"old": {"ts": old_ts, "response": {}}, "new": {"ts": time.time(), "response": {}}}
        _save_cache(data)
        loaded = _load_cache()
        self.assertNotIn("old", loaded)
        self.assertIn("new", loaded)

    def test_eviction_by_count(self) -> None:
        now = time.time()
        data = {f"k{i}": {"ts": now - i, "response": {}} for i in range(CACHE_MAX_ENTRIES + 20)}
        _save_cache(data)
        loaded = _load_cache()
        self.assertLessEqual(len(loaded), CACHE_MAX_ENTRIES)

    def test_cache_store_and_lookup(self) -> None:
        response = {"decision": "approve", "message": "test"}
        cache_store("a.py", "content", None, response)
        result = cache_lookup("a.py", "content", None)
        self.assertEqual(result, response)

    def test_cache_miss(self) -> None:
        result = cache_lookup("nonexistent.py", "content", None)
        self.assertIsNone(result)


# --- format_hook_response ---


class TestFormatHookResponse(unittest.TestCase):
    def test_no_issues(self) -> None:
        result = format_hook_response([], "a.py", 1.0)
        self.assertEqual(result, {"decision": "approve"})
        self.assertNotIn("message", result)

    def test_with_issues(self) -> None:
        issues = [{"severity": "high", "line": 5, "message": "security bug"}]
        result = format_hook_response(issues, "a.py", 1.5)
        self.assertEqual(result["decision"], "approve")
        self.assertIn("[HIGH] L5", result["message"])
        self.assertIn("security bug", result["message"])
        self.assertIn("1.5s", result["message"])

    def test_issue_without_line(self) -> None:
        issues = [{"severity": "medium", "message": "general issue"}]
        result = format_hook_response(issues, "a.py", 0.5)
        self.assertIn("[MEDIUM]:", result["message"])
        self.assertNotIn(" L", result["message"].split(":")[0])


# --- read_surrounding_context ---


class TestReadSurroundingContext(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        lines = [f"line {i}\n" for i in range(1, 31)]
        self.tmp.write("".join(lines))
        self.tmp.close()
        self.file_path = self.tmp.name

    def tearDown(self) -> None:
        os.unlink(self.file_path)

    def test_match_found_with_context(self) -> None:
        result = read_surrounding_context(self.file_path, "line 15\n", context_lines=3)
        assert result is not None
        self.assertIn("EDIT STARTS HERE", result)
        self.assertIn("line 12", result)
        self.assertIn("line 18", result)

    def test_file_not_found(self) -> None:
        result = read_surrounding_context("/nonexistent/file.py", "anything")
        self.assertIsNone(result)

    def test_old_string_not_in_file(self) -> None:
        result = read_surrounding_context(self.file_path, "not in file")
        self.assertIsNone(result)

    def test_match_at_start_of_file(self) -> None:
        result = read_surrounding_context(self.file_path, "line 1\n", context_lines=5)
        assert result is not None
        self.assertIn("EDIT STARTS HERE", result)

    def test_match_at_end_of_file(self) -> None:
        result = read_surrounding_context(self.file_path, "line 30\n", context_lines=5)
        assert result is not None
        self.assertIn("EDIT STARTS HERE", result)


# --- compute_file_diff ---


class TestComputeFileDiff(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        self.tmp.write("line 1\nline 2\nline 3\n")
        self.tmp.close()
        self.file_path = self.tmp.name

    def tearDown(self) -> None:
        os.unlink(self.file_path)

    def test_file_not_found(self) -> None:
        diff, is_new = compute_file_diff("/nonexistent/file.py", "content")
        self.assertIsNone(diff)
        self.assertTrue(is_new)

    def test_identical_content(self) -> None:
        diff, is_new = compute_file_diff(self.file_path, "line 1\nline 2\nline 3\n")
        self.assertIsNone(diff)
        self.assertFalse(is_new)

    def test_file_with_changes(self) -> None:
        diff, is_new = compute_file_diff(self.file_path, "line 1\nline CHANGED\nline 3\n")
        assert diff is not None
        self.assertFalse(is_new)
        self.assertIn("-line 2", diff)
        self.assertIn("+line CHANGED", diff)

    def test_diff_exceeds_max_lines(self) -> None:
        new_content = "\n".join(f"new line {i}" for i in range(500))
        diff, is_new = compute_file_diff(self.file_path, new_content, max_lines=5)
        self.assertIsNone(diff)
        self.assertFalse(is_new)


# --- load_config ---


class TestLoadConfig(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=False)
    @patch("crosscheck.Path.home")
    def test_defaults(self, mock_home: unittest.mock.MagicMock) -> None:
        mock_home.return_value = Path("/nonexistent")
        from crosscheck import load_config

        config = load_config()
        self.assertEqual(config["model"], "qwen3:8b")
        self.assertEqual(config["timeout"], 30)
        self.assertEqual(config["context_lines"], 10)

    @patch.dict(os.environ, {"CROSSCHECK_MODEL": "gpt-4", "CROSSCHECK_TIMEOUT": "60"})
    @patch("crosscheck.Path.home")
    def test_env_override(self, mock_home: unittest.mock.MagicMock) -> None:
        mock_home.return_value = Path("/nonexistent")
        from crosscheck import load_config

        config = load_config()
        self.assertEqual(config["model"], "gpt-4")
        self.assertEqual(config["timeout"], 60)

    @patch.dict(os.environ, {"CROSSCHECK_CONTEXT_LINES": "20"})
    @patch("crosscheck.Path.home")
    def test_context_lines_env(self, mock_home: unittest.mock.MagicMock) -> None:
        mock_home.return_value = Path("/nonexistent")
        from crosscheck import load_config

        config = load_config()
        self.assertEqual(config["context_lines"], 20)


if __name__ == "__main__":
    unittest.main()
