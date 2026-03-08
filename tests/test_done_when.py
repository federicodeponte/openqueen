"""Tests for Done When verification logic."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "/root/openqueen")


def verify_done_when(done_when: list, project_path: str) -> str:
    """Simulate tool_notify's Done When verification logic."""
    failed = []
    for condition in done_when:
        condition = condition.strip()
        if not condition:
            continue
        try:
            r = subprocess.run(
                condition, shell=True, capture_output=True, timeout=30,
                cwd=project_path or "/"
            )
            if r.returncode != 0:
                failed.append(condition)
        except Exception as e:
            failed.append(f"error: {condition}")
    if failed:
        joined = "\n".join(f"  - {c}" for c in failed)
        return f"NOT DONE — these Done When checks failed:\n{joined}"
    return "DONE"


def test_file_exists_passes(tmp_path):
    f = tmp_path / "output.txt"
    f.write_text("result")
    result = verify_done_when([f"test -f {f}"], str(tmp_path))
    assert result == "DONE"


def test_file_missing_fails(tmp_path):
    missing = tmp_path / "not-created.txt"
    result = verify_done_when([f"test -f {missing}"], str(tmp_path))
    assert "NOT DONE" in result
    assert str(missing) in result


def test_grep_content_passes(tmp_path):
    f = tmp_path / "result.py"
    f.write_text("# fix applied\nprint('hello')")
    result = verify_done_when([f"grep -q 'fix applied' {f}"], str(tmp_path))
    assert result == "DONE"


def test_grep_content_mismatch_fails(tmp_path):
    f = tmp_path / "result.py"
    f.write_text("print('nothing here')")
    result = verify_done_when([f"grep -q 'fix applied' {f}"], str(tmp_path))
    assert "NOT DONE" in result


def test_multiple_conditions_all_must_pass(tmp_path):
    f1 = tmp_path / "file1.txt"
    f1.write_text("exists")
    f2 = tmp_path / "file2.txt"  # not created
    result = verify_done_when([f"test -f {f1}", f"test -f {f2}"], str(tmp_path))
    assert "NOT DONE" in result
    assert str(f2) in result


def test_all_pass_returns_done(tmp_path):
    f1 = tmp_path / "f1.txt"
    f1.write_text("a")
    f2 = tmp_path / "f2.txt"
    f2.write_text("b")
    result = verify_done_when([f"test -f {f1}", f"test -f {f2}"], str(tmp_path))
    assert result == "DONE"


def test_empty_list_passes():
    result = verify_done_when([], "/tmp")
    assert result == "DONE"


def test_wc_l_check(tmp_path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}\n')
    result = verify_done_when([f"test $(wc -l < {f}) -gt 0"], str(tmp_path))
    assert result == "DONE"


def test_failure_message_names_failing_condition(tmp_path):
    missing = tmp_path / "missing.txt"
    result = verify_done_when([f"test -f {missing}"], str(tmp_path))
    assert "NOT DONE" in result
    assert "test -f" in result
    assert str(missing) in result


def test_python_json_check(tmp_path):
    f = tmp_path / "output.json"
    f.write_text('{"status": "ok", "count": 5}')
    cmd = f'python3 -c "import json; d=json.load(open(\'{f}\')); assert d[\'count\'] > 0"'
    result = verify_done_when([cmd], str(tmp_path))
    assert result == "DONE"


def test_python_json_check_fails_invalid(tmp_path):
    f = tmp_path / "output.json"
    f.write_text("not json")
    cmd = f'python3 -c "import json; json.load(open(\'{f}\'))"'
    result = verify_done_when([cmd], str(tmp_path))
    assert "NOT DONE" in result
