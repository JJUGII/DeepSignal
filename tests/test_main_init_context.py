"""main.py init-context CLI tests."""

from __future__ import annotations

from pathlib import Path

import main as main_mod


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_project(root: Path) -> None:
    _write(root / "README.md", "# CLI Demo\n\nCLI context init project.")
    _write(root / "requirements.txt", "pytest\n")
    _write(root / "tests" / "test_demo.py", "def test_demo():\n    assert True\n")


def test_init_context_cli_creates_missing_files(tmp_path: Path, capsys) -> None:
    project = tmp_path / "demo"
    _seed_project(project)

    rc = main_mod.main(["init-context", "--project", str(project)])

    assert rc == 0
    assert (project / "AI_CONTEXT" / "PROJECT_CONTEXT.md").is_file()
    assert (project / "AI_CONTEXT" / "PROMPT_HISTORY.md").is_file()
    console = capsys.readouterr().out
    assert "DeepSignal AI_CONTEXT initialization" in console
    assert "Created: 7" in console


def test_init_context_cli_preserves_existing_file(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    _seed_project(project)
    existing = project / "AI_CONTEXT" / "RULES.md"
    _write(existing, "CUSTOM RULES\n")

    rc = main_mod.main(["init-context", "--project", str(project)])

    assert rc == 0
    assert existing.read_text(encoding="utf-8") == "CUSTOM RULES\n"
    assert (project / "AI_CONTEXT" / "RESULT_HISTORY.md").is_file()


def test_init_context_cli_all_projects(tmp_path: Path) -> None:
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    _seed_project(project_a)
    _write(project_b / "package.json", '{"name": "b"}\n')
    _write(project_b / "src" / "index.js", "console.log('b')\n")

    rc = main_mod.main(["init-context", "--project", str(tmp_path), "--all-projects"])

    assert rc == 0
    assert (project_a / "AI_CONTEXT" / "CURRENT_STATUS.md").is_file()
    assert (project_b / "AI_CONTEXT" / "CURRENT_STATUS.md").is_file()
