"""High-level AI_CONTEXT initializer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project_context.context_scanner import is_project_candidate, scan_project
from project_context.context_writer import ContextWriteResult, write_context_files
from project_context.project_metadata import ProjectMetadata


@dataclass
class ContextInitResult:
    project_root: str
    metadata: ProjectMetadata
    write_result: ContextWriteResult


def init_context(project_root: str | Path = ".") -> ContextInitResult:
    """Scan one project and create missing AI_CONTEXT files."""
    root = Path(project_root).expanduser().resolve()
    metadata = scan_project(root)
    write_result = write_context_files(root, metadata)
    return ContextInitResult(project_root=root.as_posix(), metadata=metadata, write_result=write_result)


def discover_projects(base_dir: str | Path = ".") -> list[Path]:
    """Discover immediate child project roots under a base directory."""
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []
    projects = [child for child in sorted(base.iterdir(), key=lambda p: p.name.lower()) if is_project_candidate(child)]
    return projects


def init_all_projects(base_dir: str | Path = ".") -> list[ContextInitResult]:
    """Initialize all immediate child projects under base_dir."""
    return [init_context(project) for project in discover_projects(base_dir)]
