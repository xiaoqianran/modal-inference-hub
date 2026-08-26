from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

from agent.windows_env import normalize_windows_environment


class WindowsEnvironmentTests(unittest.TestCase):
    def test_missing_system_paths_are_derived_from_system_root(self) -> None:
        with (
            patch.object(sys, 'platform', 'win32'),
            patch.dict(os.environ, {'SystemRoot': r'C:\WINDOWS'}, clear=True),
        ):
            normalize_windows_environment()

            self.assertEqual(os.environ['SystemDrive'], 'C:')
            self.assertEqual(os.environ['ProgramData'], r'C:\ProgramData')
            self.assertEqual(os.environ['ALLUSERSPROFILE'], r'C:\ProgramData')

    def test_existing_values_are_preserved(self) -> None:
        existing = {
            'SystemDrive': 'D:',
            'ProgramData': r'D:\SharedData',
            'ALLUSERSPROFILE': r'D:\AllUsers',
        }
        with patch.object(sys, 'platform', 'win32'), patch.dict(os.environ, existing, clear=True):
            normalize_windows_environment()
            for key, value in existing.items():
                self.assertEqual(os.environ[key], value)


if __name__ == '__main__':
    unittest.main()
