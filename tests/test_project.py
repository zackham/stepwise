"""Tests for stepwise.project — directory resolution, init, find."""

import pytest
from pathlib import Path

from stepwise.project import (
    DOT_DIR_NAME,
    ProjectNotFoundError,
    StepwiseProject,
    find_project,
    get_bundled_templates_dir,
    get_bundled_web_dir,
    init_project,
)


class TestFindProject:
    """find_project() walks up directories to find .stepwise/."""

    def test_finds_in_current_dir(self, tmp_path):
        (tmp_path / DOT_DIR_NAME).mkdir()
        project = find_project(tmp_path)
        assert project.root == tmp_path
        assert project.dot_dir == tmp_path / DOT_DIR_NAME

    def test_finds_in_parent_dir(self, tmp_path):
        (tmp_path / DOT_DIR_NAME).mkdir()
        child = tmp_path / "subdir" / "deep"
        child.mkdir(parents=True)
        project = find_project(child)
        assert project.root == tmp_path

    def test_raises_when_not_found(self, tmp_path):
        with pytest.raises(ProjectNotFoundError):
            find_project(tmp_path)

    def test_project_dir_override(self, tmp_path):
        """--project-dir equivalent: pass explicit start path."""
        project_root = tmp_path / "myproject"
        project_root.mkdir()
        (project_root / DOT_DIR_NAME).mkdir()

        # Even though we're "in" tmp_path, passing project_root finds it
        project = find_project(project_root)
        assert project.root == project_root


class TestInitProject:
    """init_project() creates .stepwise/ with correct structure."""

    def test_creates_dot_dir_with_subdirs(self, tmp_path):
        project = init_project(tmp_path)
        assert project.dot_dir.is_dir()
        assert (project.dot_dir / "jobs").is_dir()
        assert (project.dot_dir / "templates").is_dir()

    def test_creates_inner_gitignore(self, tmp_path):
        init_project(tmp_path)
        gitignore = tmp_path / DOT_DIR_NAME / ".gitignore"
        assert gitignore.exists()
        assert gitignore.read_text() == "*\n"

    def test_appends_to_existing_gitignore(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")
        init_project(tmp_path)
        content = gitignore.read_text()
        assert "node_modules/" in content
        assert f"{DOT_DIR_NAME}/" in content

    def test_creates_gitignore_if_missing(self, tmp_path):
        init_project(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert f"{DOT_DIR_NAME}/" in gitignore.read_text()

    def test_does_not_duplicate_gitignore_entry(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(f"{DOT_DIR_NAME}/\n")
        init_project(tmp_path)
        content = gitignore.read_text()
        assert content.count(f"{DOT_DIR_NAME}/") == 1

    def test_errors_on_existing_without_force(self, tmp_path):
        init_project(tmp_path)
        with pytest.raises(FileExistsError):
            init_project(tmp_path)

    def test_force_reinitializes(self, tmp_path):
        project1 = init_project(tmp_path)
        # Create a db file to verify it's preserved
        project1.db_path.write_text("dummy")

        project2 = init_project(tmp_path, force=True)
        assert project2.dot_dir.is_dir()
        # DB file should still be there
        assert project2.db_path.read_text() == "dummy"


class TestStepwiseProject:
    """StepwiseProject paths resolve correctly."""

    def test_paths(self, tmp_path):
        (tmp_path / DOT_DIR_NAME).mkdir()
        project = find_project(tmp_path)
        assert project.db_path == tmp_path / DOT_DIR_NAME / "stepwise.db"
        assert project.jobs_dir == tmp_path / DOT_DIR_NAME / "jobs"
        assert project.templates_dir == tmp_path / DOT_DIR_NAME / "templates"


class TestBundledPaths:
    """get_bundled_templates_dir() / get_bundled_web_dir() return valid paths."""

    def test_bundled_templates_path(self):
        path = get_bundled_templates_dir()
        assert path.name == "_templates"
        assert "stepwise" in str(path)

    def test_bundled_web_path(self):
        path = get_bundled_web_dir()
        assert path.name == "_web"
        assert "stepwise" in str(path)
