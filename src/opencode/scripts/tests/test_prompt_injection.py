#!/usr/bin/env python3
"""Tests for build.injections resolution.

Verifies that resolve_build_rules reads from the new nested structure.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__),
    "../../../opencode/skills/pg-build/scripts"))

from pipeline.config import resolve_build_rules


class ResolveBuildRulesNestedTest(unittest.TestCase):

    def test_reads_from_nested_structure(self):
        config = {
            "build": {
                "injections": {
                    "dev": [
                        {"position": "prepend", "template": "[CHECKLIST]"},
                    ],
                },
            },
        }
        prepend, append = resolve_build_rules(config, "dev")
        self.assertEqual(prepend, "[CHECKLIST]")
        self.assertEqual(append, "")

    def test_empty_when_missing_phase(self):
        config = {"build": {"injections": {"dev": []}}}
        prepend, append = resolve_build_rules(config, "verify")
        self.assertEqual(prepend, "")
        self.assertEqual(append, "")

    def test_empty_when_no_build_key(self):
        prepend, append = resolve_build_rules({}, "dev")
        self.assertEqual(prepend, "")
        self.assertEqual(append, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)