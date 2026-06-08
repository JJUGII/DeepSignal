"""Markdown templates for standard AI_CONTEXT files."""

from __future__ import annotations

from project_context.project_metadata import ProjectMetadata


CONTEXT_FILES = (
    "PROJECT_CONTEXT.md",
    "CURRENT_STATUS.md",
    "TODO.md",
    "KNOWN_ISSUES.md",
    "RULES.md",
    "PROMPT_HISTORY.md",
    "RESULT_HISTORY.md",
)


def _list_or_placeholder(items: list[str], placeholder: str = "(unknown)") -> str:
    if not items:
        return f"- {placeholder}"
    return "\n".join(f"- `{item}`" for item in items)


def _plain_list(items: list[str], placeholder: str = "(unknown)") -> str:
    if not items:
        return f"- {placeholder}"
    return "\n".join(f"- {item}" for item in items)


def render_project_context(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Project Context

## Project Purpose

{metadata.readme_summary or "Describe the purpose of this project."}

## Core Features

- TODO: Document the primary user-facing capabilities.
- TODO: Document important internal workflows.

## Current Architecture

- Project root: `{metadata.root_path}`
- Primary languages:
{_plain_list(metadata.primary_languages)}
- Source directories:
{_list_or_placeholder(metadata.source_dirs)}
- Test directories:
{_list_or_placeholder(metadata.test_dirs, "(none detected)")}
- Docs directories:
{_list_or_placeholder(metadata.docs_dirs, "(none detected)")}

## Technology Stack

- Dependency files:
{_list_or_placeholder(metadata.dependency_files, "(none detected)")}
- README: `{metadata.readme_path or "not detected"}`

## Major Directory Structure

{_list_or_placeholder(metadata.directory_overview, "(empty or unavailable)")}

## Long-Term Goals

- TODO: Capture the long-term product or platform goals.
- TODO: Capture integrations such as Overmind, Telegram, Slack, or CI work loops.

## Current Development Direction

- Keep this file concise and update it when architecture or project direction changes.
"""


def render_current_status(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Current Status

## Recently Completed

- Initial AI_CONTEXT bootstrap created.

## In Progress

- TODO: Add the current active task.

## Last Test Status

- Unknown. Record the latest test command and result here.

## Current Branch / Version

- Unknown. Fill manually if relevant.

## Next Priorities

- Review and refine generated context files.
- Add project-specific rules and known issues.
"""


def render_todo(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — TODO

## HIGH

- [ ] Review generated AI_CONTEXT templates.

## MEDIUM

- [ ] Add project-specific architecture notes.

## LOW

- [ ] Add optional workflow notes for Cursor/GPT/Claude handoff.

## BACKLOG

- [ ] Connect this context to Overmind or an AI Work Loop Controller.
"""


def render_known_issues(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Known Issues

## Current Known Problems

- None documented yet.

## Reproduction Conditions

- TODO: Add steps or environment details when a problem is discovered.

## Temporary Workarounds

- TODO: Add any manual workaround.

## Why Unresolved

- TODO: Capture the constraint, missing decision, or dependency.
"""


def render_rules(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Rules

## Absolutely Forbidden

- Do not overwrite existing `AI_CONTEXT` files without explicit approval.
- Do not commit secrets, API keys, tokens, credentials, or `.env` values.
- Do not run destructive operations without explicit approval.
- Do not call external networks or LLM APIs from context bootstrap.

## Code Style Rules

- Prefer existing project patterns over new abstractions.
- Keep changes small, reviewable, and tested.
- Document non-obvious behavior near the relevant code.

## Safety Rules

- Preserve user changes and avoid reverting unrelated work.
- Keep generated context concise and useful for future agents.

## Test Rules

- Add or update tests when behavior changes.
- Record the latest test command and result in `CURRENT_STATUS.md` when useful.

## Architecture Boundary

- Keep AI context management separate from application runtime logic unless explicitly needed.
"""


def render_prompt_history(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Prompt History

Append-only log. Add newest entries at the top or bottom consistently.

| Date | Work Goal | Cursor Prompt Summary | Result Status |
|------|-----------|-----------------------|---------------|
| TODO | Initial context bootstrap | Created standard AI_CONTEXT structure | Draft |
"""


def render_result_history(metadata: ProjectMetadata) -> str:
    return f"""# {metadata.project_name} — Result History

Append-only log. Record completed work at the end of each meaningful task.

| Date | Changed Files | Test Result | Remaining Issues | Next Candidates |
|------|---------------|-------------|------------------|-----------------|
| TODO | `AI_CONTEXT/*` | Not run | Templates need project-specific review | Fill context details |
"""


def render_template(filename: str, metadata: ProjectMetadata) -> str:
    renderers = {
        "PROJECT_CONTEXT.md": render_project_context,
        "CURRENT_STATUS.md": render_current_status,
        "TODO.md": render_todo,
        "KNOWN_ISSUES.md": render_known_issues,
        "RULES.md": render_rules,
        "PROMPT_HISTORY.md": render_prompt_history,
        "RESULT_HISTORY.md": render_result_history,
    }
    try:
        return renderers[filename](metadata)
    except KeyError as exc:
        raise ValueError(f"Unknown AI_CONTEXT template: {filename}") from exc
