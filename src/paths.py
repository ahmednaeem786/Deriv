"""Stable project paths and input-file resolution."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_input_path(filename: str, fallback_directory: str) -> Path:
    """Prefer required root-level inputs, with compatibility for local subfolders."""
    root_path = PROJECT_ROOT / filename
    if root_path.exists():
        return root_path
    return PROJECT_ROOT / fallback_directory / filename


def artifact_path(filename: str) -> Path:
    """Return an output artifact path anchored to the project root."""
    return PROJECT_ROOT / filename
