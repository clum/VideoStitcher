"""Resolve shared scenario and workflow paths from settings.json."""

import re
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mts", ".m2ts"}


def scenario_prefix(scenario_name: str) -> str:
    """Return a filename-safe scenario prefix shared by workflow steps."""
    prefix = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", scenario_name).strip(" ._")
    if not prefix:
        raise ValueError("ScenarioName does not contain any valid filename characters.")
    return prefix


def results_directory(settings: dict[str, Any], repository_root: Path) -> Path:
    """Resolve ResultsDirectory; relative settings paths are rooted at the repo."""
    value = settings.get("ResultsDirectory")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Set ResultsDirectory to a folder in settings.json.")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else repository_root / path).resolve()


def combined_video_path(settings: dict[str, Any], camera: str, repository_root: Path) -> Path:
    """Find the unique Step 01 combined output for a camera and scenario."""
    scenario = settings.get("ScenarioName")
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError("Set a non-empty ScenarioName in settings.json.")
    if camera not in {"CameraA", "CameraB"}:
        raise ValueError(f"Unsupported camera name: {camera}")
    folder = results_directory(settings, repository_root) / "Step01_CombinedVideos"
    if not folder.is_dir():
        raise FileNotFoundError(
            f"Step 01 combined-video folder does not exist: {folder}. Run Step01_CombineRawVideos.py first."
        )
    prefix = scenario_prefix(scenario)
    candidates = sorted(
        path for path in folder.glob(f"{prefix}_{camera}.*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not candidates:
        raise FileNotFoundError(
            f"No Step 01 combined video found for {camera} in {folder}. "
            f"Expected {prefix}_{camera} with a supported video extension; run Step01_CombineRawVideos.py first."
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"Multiple Step 01 combined videos found for {camera}: {names}")
    return candidates[0]
