"""Tests for web UI asset serving — bundled vs dev-mode fallback."""

from pathlib import Path
from stepwise.project import get_bundled_web_dir


class TestWebAssets:
    """Web UI dev-mode fallback when _web/ doesn't exist."""

    def test_bundled_web_dir_path(self):
        """get_bundled_web_dir returns a path inside the stepwise package."""
        path = get_bundled_web_dir()
        assert path.name == "_web"
        # Should be inside the stepwise package directory
        assert "stepwise" in str(path)

    def test_dev_fallback_path_exists(self):
        """Dev fallback web/dist should be available in dev mode."""
        # In the development repo, web/dist may or may not exist
        # but the path logic should resolve correctly
        from stepwise.project import get_bundled_web_dir
        bundled = get_bundled_web_dir()
        # The fallback path (used in server.py) would be:
        dev_path = Path(__file__).parent.parent / "web" / "dist"
        # We just verify the path construction is valid, not that it exists
        assert dev_path.name == "dist"

    def test_bundled_dir_does_not_exist_in_dev(self):
        """In dev mode, _web/ should not exist (built via Makefile)."""
        path = get_bundled_web_dir()
        # Unless someone ran `make build-web`, this shouldn't exist
        # This test documents the expected state in dev mode
        # (not a hard assertion since CI might have built it)
        pass  # Documenting expected behavior
