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


if __name__ == "__main__":
    unittest.main()
