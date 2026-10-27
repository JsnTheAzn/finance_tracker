"""Tests for backend/config.py (.env loading)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.config import PROJECT_ROOT, load_env_file, project_path


class EnvFileTests(unittest.TestCase):
    def test_load_env_file_does_not_override_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('# comment\nFT_TEST_A="from-file"\nFT_TEST_B=b=c\n\nFT_TEST_C=file\n')
            # patch.dict restores os.environ after the block.
            with mock.patch.dict(os.environ, {"FT_TEST_C": "already-set"}):
                load_env_file(env_path)
                self.assertEqual(os.environ["FT_TEST_A"], "from-file")  # quotes removed
                self.assertEqual(os.environ["FT_TEST_B"], "b=c")  # split on first '='
                self.assertEqual(os.environ["FT_TEST_C"], "already-set")

    def test_load_env_file_handles_export_comments_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "export FT_TEST_EXPORT=exported\n"
                "FT_TEST_COMMENT=abc123  # sandbox secret\n"
                "FT_TEST_HASH=abc#123\n"
                "FT_TEST_QUOTED='keep # this'\n"
                "FT_TEST_UNMATCHED=\"abc'\n"
            )
            names = ["FT_TEST_EXPORT", "FT_TEST_COMMENT", "FT_TEST_HASH", "FT_TEST_QUOTED", "FT_TEST_UNMATCHED"]
            with mock.patch.dict(os.environ, {}):
                for name in names:
                    os.environ.pop(name, None)
                load_env_file(env_path)
                self.assertEqual(os.environ["FT_TEST_EXPORT"], "exported")
                self.assertNotIn("export FT_TEST_EXPORT", os.environ)
                self.assertEqual(os.environ["FT_TEST_COMMENT"], "abc123")
                self.assertEqual(os.environ["FT_TEST_HASH"], "abc#123")
                self.assertEqual(os.environ["FT_TEST_QUOTED"], "keep # this")
                self.assertEqual(os.environ["FT_TEST_UNMATCHED"], "\"abc'")  # not a matching pair

    def test_load_env_file_quoted_value_with_comment_and_bad_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                'FT_TEST_QC="sandbox"  # only sandbox\n'
                "FT_TEST_SQ='a # b' # note\n"
                "=no-name\n"
                "export =also-no-name\n"
                "1BAD=starts-with-digit\n"
                "FT_TEST_AFTER=still-loaded\n"
            )
            with mock.patch.dict(os.environ, {}):
                for name in ("FT_TEST_QC", "FT_TEST_SQ", "FT_TEST_AFTER"):
                    os.environ.pop(name, None)
                load_env_file(env_path)  # must not raise OSError
                self.assertEqual(os.environ["FT_TEST_QC"], "sandbox")  # no quote marks
                self.assertEqual(os.environ["FT_TEST_SQ"], "a # b")
                self.assertNotIn("1BAD", os.environ)
                self.assertEqual(os.environ["FT_TEST_AFTER"], "still-loaded")


    def test_project_path_resolves_relative_to_project_root(self):
        self.assertEqual(project_path("certs/aiven-ca.pem"), PROJECT_ROOT / "certs/aiven-ca.pem")
        self.assertEqual(project_path("/etc/ca.pem"), Path("/etc/ca.pem"))


if __name__ == "__main__":
    unittest.main()
