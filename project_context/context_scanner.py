"""Local read-only project scanner for AI_CONTEXT bootstrap."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from project_context.project_metadata import ProjectMetadata


IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "outputs",
    "archive",
    ".pytest_cache",
    ".mypy_cache",
}

DEPENDENCY_FILES = (
    "requirements.txt",
    "requirements-macos.txt",
    "pyproject.toml",
    "setup.py",
    "Pipfile",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
)

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
}

SOURCE_DIR_NAMES = ("src", "app", "lib", "deepsignal", "project_context", "scripts")
TEST_DIR_NAMES = ("tests", "test", "__tests__")
DOCS_DIR_NAMES = ("docs", "doc")


def _is_ignored(path: Path) -> bool:
    return any(part in IGNORE_DIRS or part.startswith("._") for part in path.parts)


def _readme_summary(readme: Path | None) -> str | None:
    if readme is None or not readme.is_file():
        return None
    try:
        lines = readme.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    collected: list[str] = []
    for line in lines[:80]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("!") or stripped.startswith("["):
            continue
        collected.append(stripped)
        if len(" ".join(collected)) >= 240:
            break
    if not collected:
        return None
    summary = " ".join(collected)
    return summary[:500]


def _find_readme(root: Path) -> Path | None:
    for name in ("README.md", "README.rst", "README.txt", "Readme.md"):
        path = root / name
        if path.is_file():
            return path
    return None


def _top_level_overview(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    entries: list[str] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    for child in children:
        if child.name.startswith(".") and child.name not in {".cursor"}:
            continue
        if child.name in IGNORE_DIRS or child.name.startswith("._"):
            continue
        suffix = "/" if child.is_dir() else ""
        entries.append(f"{child.name}{suffix}")
        if len(entries) >= 40:
            break
    return entries


def _language_counts(root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not root.is_dir():
        return counts
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if _is_ignored(rel):
            continue
        if path.is_file():
            lang = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
            if lang:
                counts[lang] += 1
    return counts


def scan_project(root: str | Path) -> ProjectMetadata:
    """Infer basic project metadata using local filesystem reads only."""
    project_root = Path(root).expanduser().resolve()
    readme = _find_readme(project_root)
    lang_counts = _language_counts(project_root)
    dependency_files = [name for name in DEPENDENCY_FILES if (project_root / name).is_file()]
    source_dirs = [name for name in SOURCE_DIR_NAMES if (project_root / name).is_dir()]
    test_dirs = [name for name in TEST_DIR_NAMES if (project_root / name).is_dir()]
    docs_dirs = [name for name in DOCS_DIR_NAMES if (project_root / name).is_dir()]

    if "package.json" in dependency_files and "TypeScript" not in lang_counts and any((project_root / name).exists() for name in ("tsconfig.json", "src")):
        lang_counts["TypeScript"] += 1
    if any(name.startswith("requirements") or name in {"pyproject.toml", "setup.py"} for name in dependency_files):
        lang_counts["Python"] += 1

    return ProjectMetadata(
        project_name=project_root.name,
        root_path=project_root.as_posix(),
        primary_languages=[name for name, _count in lang_counts.most_common(5)],
        dependency_files=dependency_files,
        source_dirs=source_dirs,
        test_dirs=test_dirs,
        docs_dirs=docs_dirs,
        readme_path=readme.name if readme else None,
        readme_summary=_readme_summary(readme),
        directory_overview=_top_level_overview(project_root),
    )


def is_project_candidate(path: Path) -> bool:
    """Return true for directories that look like project roots."""
    if not path.is_dir() or path.name in IGNORE_DIRS or path.name.startswith("."):
        return False
    markers = ["README.md", *DEPENDENCY_FILES, ".git", "src", "tests"]
    return any((path / marker).exists() for marker in markers)
