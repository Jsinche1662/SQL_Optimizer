"""Tests del modulo sql_optimizer (no llaman a OpenAI ni GitHub)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sql_optimizer as so  # noqa: E402


def test_validate_input_detects_bad_sql():
    state = {
        "files": [so.FileResult(path="x.sql", original_sql="SELECT * FROM t WHERE")],
        "summary_markdown": "",
    }
    so.node_validate_input(state)
    assert state["files"][0].is_valid_input is False
    assert state["files"][0].input_error


def test_validate_input_accepts_good_sql():
    state = {
        "files": [so.FileResult(path="x.sql", original_sql="SELECT 1 AS a")],
        "summary_markdown": "",
    }
    so.node_validate_input(state)
    assert state["files"][0].is_valid_input is True


def test_build_diff_produces_unified_diff():
    f = so.FileResult(
        path="q.sql",
        original_sql="SELECT * FROM t",
        optimized_sql="SELECT id FROM t",
    )
    state = {"files": [f], "summary_markdown": ""}
    so.node_build_diff(state)
    assert "SELECT" in f.diff
    assert "@@" in f.diff or "---" in f.diff


def test_render_comment_handles_no_files():
    state = {"files": [], "summary_markdown": ""}
    so.node_render_comment(state)
    assert "No se detectaron" in state["summary_markdown"]


def test_render_comment_includes_changes():
    f = so.FileResult(
        path="q.sql",
        original_sql="SELECT * FROM t",
        optimized_sql="SELECT id FROM t",
        changes=["Reemplaza SELECT *"],
        explanation="Menos I/O",
    )
    state = {"files": [f], "summary_markdown": ""}
    so.node_validate_input(state)
    so.node_build_diff(state)
    so.node_render_comment(state)
    md = state["summary_markdown"]
    assert "Reemplaza SELECT *" in md
    assert "Menos I/O" in md
    assert "```sql" in md


if __name__ == "__main__":
    import traceback
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError:
                failed += 1
                print(f"FAIL {name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
