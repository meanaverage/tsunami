"""Tests for webdev tool helpers."""

import tempfile
from pathlib import Path

from tsunami.agent import Agent
from tsunami.config import TsunamiConfig
from tsunami.tools.webdev import (
    _default_screenshot_output_path,
    normalize_screenshot_output_path,
)


def test_normalize_screenshot_output_path_keeps_image_suffix():
    path, note = normalize_screenshot_output_path("shots/homepage.png")
    assert path == "shots/homepage.png"
    assert note is None


def test_normalize_screenshot_output_path_rewrites_markdown_suffix():
    path, note = normalize_screenshot_output_path("screenshot.md")
    assert path == "screenshot.png"
    assert note is not None
    assert "Adjusted screenshot output path" in note


def test_normalize_screenshot_output_path_adds_png_when_missing():
    path, note = normalize_screenshot_output_path("shots/homepage")
    assert path == "shots/homepage.png"
    assert note is not None


def test_default_screenshot_output_path_uses_active_project_qa_dir():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = TsunamiConfig(workspace_dir=str(tmp / "workspace"), watcher_enabled=False)
        agent = Agent(cfg)
        project_dir = cfg.deliverables_dir / "demo-project"
        project_dir.mkdir(parents=True)
        (project_dir / "tsunami.md").write_text("# demo-project\n")
        agent.set_project("demo-project")

        out = _default_screenshot_output_path(cfg.workspace_dir, "screenshot.png")

        assert out.parent == project_dir / ".qa"
        assert out.name.startswith("demo-project-screenshot-")
        assert out.suffix == ".png"


def test_default_screenshot_output_path_preserves_custom_relative_path():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        cfg = TsunamiConfig(workspace_dir=str(tmp / "workspace"), watcher_enabled=False)

        out = _default_screenshot_output_path(cfg.workspace_dir, "artifacts/preview.png")

        assert out == Path(cfg.workspace_dir) / "artifacts" / "preview.png"
