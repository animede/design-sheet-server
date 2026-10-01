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

    def test_fast_mode_prompt_keeps_complete_body_inside_frame(self):
        prompt = build_mode_prompt("illustration", full_body_margin=True)

        self.assertIn("top of the hair", prompt)
        self.assertIn("soles of both feet", prompt)
        self.assertIn("margin", prompt)

    def test_quality_mode_prompt_remains_unchanged(self):
        regular = build_mode_prompt("illustration")
        fast = build_mode_prompt("illustration", full_body_margin=True)

        self.assertNotIn("generous pure white margin", regular)
        self.assertIn("generous pure white margin", fast)


if __name__ == "__main__":
    unittest.main()
