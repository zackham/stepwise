"""Tests for stepwise.project — directory resolution, init, find."""

import pytest
from pathlib import Path

from stepwise.project import (
    DOT_DIR_NAME,
    SKILL_NAME,
    ProjectNotFoundError,
    StepwiseProject,
    detect_agent_skill_locations,
    find_project,
    get_bundled_skill_dir,
    get_bundled_templates_dir,
    get_bundled_web_dir,
    init_project,
    install_agent_skill,
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

    def test_bundled_skill_dir_exists(self):
        path = get_bundled_skill_dir()
        assert path.is_dir()
        assert (path / "SKILL.md").exists()
        assert (path / "FLOW_REFERENCE.md").exists()


class TestDetectAgentSkillLocations:
    """detect_agent_skill_locations() finds framework dirs and existing skills."""

    def _local(self, result):
        """Filter to local-scope locations only (ignores real ~/.claude etc.)."""
        return [loc for loc in result.locations if loc.scope == "local"]

    def test_no_frameworks(self, tmp_path):
        result = detect_agent_skill_locations(tmp_path)
        assert len(self._local(result)) == 0

    def test_claude_dir_no_skill(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert len(local) == 1
        assert local[0].framework_dir == ".claude"
        assert not local[0].has_skill

    def test_agents_dir_no_skill(self, tmp_path):
        (tmp_path / ".agents").mkdir()
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert len(local) == 1
        assert local[0].framework_dir == ".agents"
        assert not local[0].has_skill

    def test_both_dirs_exist(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".agents").mkdir()
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert len(local) == 2

    def test_detects_installed_skill(self, tmp_path):
        install_agent_skill(tmp_path / ".claude")
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert len(local) == 1
        assert local[0].has_skill
        assert local[0].skill_current

    def test_detects_outdated_skill(self, tmp_path):
        install_agent_skill(tmp_path / ".claude")
        # Modify installed file to make it outdated
        skill_file = tmp_path / ".claude" / "skills" / SKILL_NAME / "SKILL.md"
        skill_file.write_text("outdated content")
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert local[0].has_skill
        assert not local[0].skill_current

    def test_symlink_detection(self, tmp_path):
        (tmp_path / ".agents").mkdir()
        (tmp_path / ".claude").symlink_to(tmp_path / ".agents")
        result = detect_agent_skill_locations(tmp_path)
        local = self._local(result)
        assert len(local) == 2
        assert len(result.symlinked_groups) >= 1
        # Find the group containing our local dirs
        local_group = None
        for group in result.symlinked_groups:
            if any(loc.scope == "local" for loc in group):
                local_group = group
                break
        assert local_group is not None
        local_in_group = [loc for loc in local_group if loc.scope == "local"]
        assert len(local_in_group) == 2


class TestInstallAgentSkill:
    """install_agent_skill() copies bundled templates."""

    def test_installs_files(self, tmp_path):
        target = tmp_path / ".claude"
        installed = install_agent_skill(target)
        assert installed == target / "skills" / SKILL_NAME
        assert (installed / "SKILL.md").exists()
        assert (installed / "FLOW_REFERENCE.md").exists()

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / ".agents"
        assert not target.exists()
        installed = install_agent_skill(target)
        assert installed.is_dir()

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / ".claude"
        install_agent_skill(target)
        skill_file = target / "skills" / SKILL_NAME / "SKILL.md"
        skill_file.write_text("old content")
        install_agent_skill(target)
        assert skill_file.read_text() != "old content"

    def test_installed_matches_bundled(self, tmp_path):
        target = tmp_path / ".claude"
        installed = install_agent_skill(target)
        bundled = get_bundled_skill_dir()
        for bundled_file in bundled.iterdir():
            if bundled_file.is_file():
                installed_file = installed / bundled_file.name
                assert installed_file.read_text() == bundled_file.read_text()
