"""AI_CONTEXT initializer tests."""

from __future__ import annotations

from pathlib import Path

from project_context.context_initializer import init_all_projects, init_context
from project_context.context_scanner import scan_project
from project_context.context_templates import CONTEXT_FILES


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_project(root: Path) -> None:
    _write(root / "README.md", "# Demo\n\nDemo project for metadata inference.")
    _write(root / "requirements.txt", "pytest\n")
    _write(root / "src" / "app.py", "print('demo')\n")
    _write(root / "tests" / "test_app.py", "def test_demo():\n    assert True\n")
    _write(root / "docs" / "guide.md", "# Guide\n")


def test_scan_project_infers_metadata(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    _seed_project(root)

    metadata = scan_project(root)

    assert metadata.project_name == "demo"
    assert "Python" in metadata.primary_languages
    assert "requirements.txt" in metadata.dependency_files
    assert "src" in metadata.source_dirs
    assert "tests" in metadata.test_dirs
    assert "docs" in metadata.docs_dirs
    assert metadata.readme_summary and "Demo project" in metadata.readme_summary


def test_init_context_creates_ai_context_files(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    _seed_project(root)

    result = init_context(root)

    context_dir = root / "AI_CONTEXT"
    assert context_dir.is_dir()
    assert {p.name for p in context_dir.iterdir() if p.is_file()} == set(CONTEXT_FILES)
    assert len(result.write_result.created_files) == len(CONTEXT_FILES)
    assert "Demo project for metadata inference" in (context_dir / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")


def test_init_context_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    _seed_project(root)
    existing = root / "AI_CONTEXT" / "PROJECT_CONTEXT.md"
    _write(existing, "KEEP ME\n")

    result = init_context(root)

    assert existing.read_text(encoding="utf-8") == "KEEP ME\n"
    assert existing.as_posix() in result.write_result.skipped_files
    assert (root / "AI_CONTEXT" / "CURRENT_STATUS.md").is_file()


def test_init_all_projects_initializes_immediate_project_dirs(tmp_path: Path) -> None:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    ignored = tmp_path / "notes"
    _seed_project(project_a)
    _write(project_b / "package.json", '{"name": "project-b"}\n')
    _write(project_b / "src" / "index.ts", "export const x = 1;\n")
    _write(ignored / "note.txt", "not a project\n")

    results = init_all_projects(tmp_path)

    roots = {Path(result.project_root).name for result in results}
    assert roots == {"project-a", "project-b"}
    assert (project_a / "AI_CONTEXT" / "TODO.md").is_file()
    assert (project_b / "AI_CONTEXT" / "PROJECT_CONTEXT.md").is_file()
    assert not (ignored / "AI_CONTEXT").exists()
