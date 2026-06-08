"""Write AI_CONTEXT files without overwriting existing content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from project_context.context_templates import CONTEXT_FILES, render_template
from project_context.project_metadata import ProjectMetadata


@dataclass
class ContextWriteResult:
    project_root: str
    context_dir: str
    created_files: list[str]
    skipped_files: list[str]


def write_context_files(project_root: str | Path, metadata: ProjectMetadata) -> ContextWriteResult:
    """Create missing AI_CONTEXT files. Existing files are never overwritten."""
    root = Path(project_root).expanduser().resolve()
    context_dir = root / "AI_CONTEXT"
    context_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []
    for filename in CONTEXT_FILES:
        path = context_dir / filename
        if path.exists():
            skipped.append(path.as_posix())
            continue
        path.write_text(render_template(filename, metadata), encoding="utf-8")
        created.append(path.as_posix())

    return ContextWriteResult(
        project_root=root.as_posix(),
        context_dir=context_dir.as_posix(),
        created_files=created,
        skipped_files=skipped,
    )
