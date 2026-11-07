from __future__ import annotations

import unittest
from unittest.mock import patch

import dops.__main__ as dops_main


class BootstrapTests(unittest.TestCase):
    def test_run_converts_import_time_keyboard_interrupt_into_exit_code(self) -> None:
        with patch("dops.__main__._load_main", side_effect=KeyboardInterrupt):
            self.assertEqual(dops_main.run(), 130)
