import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cc2obsidian import config


class VaultPathTest(unittest.TestCase):
    def test_relative_env_var_resolves_to_absolute_path(self):
        # A relative CC2OBSIDIAN_VAULT must not silently depend on the
        # caller's working directory when compared/stored later — two
        # invocations from different cwds must not normalise to the same
        # string while pointing at different directories on disk.
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"CC2OBSIDIAN_VAULT": "relative/vault"}):
                    result = config.vault_path()
            finally:
                os.chdir(cwd)
            self.assertTrue(result.is_absolute())
            self.assertEqual(result, Path(tmp).resolve() / "relative" / "vault")


class TranscriptPathTest(unittest.TestCase):
    def test_claude_projects_dir_keeps_the_legacy_alias(self):
        with mock.patch.dict(
            os.environ,
            {"CC2OBSIDIAN_CLAUDE_PROJECTS": "/tmp/custom-claude-projects"},
        ):
            self.assertEqual(
                config.claude_projects_dir(), Path("/tmp/custom-claude-projects")
            )
            self.assertEqual(config.projects_dir(), config.claude_projects_dir())

    def test_codex_session_roots_can_be_overridden(self):
        with mock.patch.dict(
            os.environ,
            {
                "CC2OBSIDIAN_CODEX_SESSIONS": "/tmp/custom-codex-sessions",
                "CC2OBSIDIAN_CODEX_ARCHIVED_SESSIONS": "/tmp/custom-codex-archive",
            },
        ):
            self.assertEqual(
                config.codex_sessions_dir(), Path("/tmp/custom-codex-sessions")
            )
            self.assertEqual(
                config.codex_archived_sessions_dir(), Path("/tmp/custom-codex-archive")
            )


if __name__ == "__main__":
    unittest.main()
