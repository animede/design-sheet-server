# -*- coding: utf-8 -*-
import unittest

from core.prompts import build_mode_prompt


class ModePromptTests(unittest.TestCase):
    def test_chibi_prompt_requests_exactly_one_character(self):
        prompt = build_mode_prompt("chibi")

        self.assertIn("exactly one complete full-body character", prompt)
        self.assertIn("centered alone", prompt)

    def test_chibi_prompt_avoids_ambiguous_numeric_head_ratio(self):
        prompt = build_mode_prompt("chibi")

        self.assertNotIn("two-and-a-half", prompt)


if __name__ == "__main__":
    unittest.main()
