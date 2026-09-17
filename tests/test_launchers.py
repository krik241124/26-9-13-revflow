"""Regression guard for Windows CMD launcher encoding and line endings."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherFormatTests(unittest.TestCase):
    def test_cmd_scripts_are_ascii_crlf_without_bom(self):
        for name in ('start.bat', 'setup.bat', 'run.bat'):
            with self.subTest(name=name):
                raw = (ROOT / name).read_bytes()
                raw.decode('ascii')
                self.assertTrue(raw.startswith(b'@echo off\r\n'))
                self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
                self.assertNotIn(b'\r', raw.replace(b'\r\n', b''))

    def test_start_invokes_app_with_quoted_project_paths(self):
        text = (ROOT / 'start.bat').read_text(encoding='ascii')
        self.assertIn('"%~dp0.venv\\Scripts\\python.exe" "%~dp0webui\\app.py"', text)
        self.assertIn('exit /b 0', text)


if __name__ == '__main__':
    unittest.main()
