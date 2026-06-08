"""Project metadata model for AI_CONTEXT bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProjectMetadata:
    project_name: str
    root_path: str
    primary_languages: list[str] = field(default_factory=list)
    dependency_files: list[str] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    docs_dirs: list[str] = field(default_factory=list)
    readme_path: str | None = None
    readme_summary: str | None = None
    directory_overview: list[str] = field(default_factory=list)

    @property
    def has_tests(self) -> bool:
        return bool(self.test_dirs)
