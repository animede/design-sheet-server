# -*- coding: utf-8 -*-
import unittest

from core import config


class EndpointProblemTests(unittest.TestCase):
    def test_valid_r2_endpoint_has_no_problem(self):
        self.assertEqual(
            config._endpoint_problem("https://0123456789abcdef.r2.cloudflarestorage.com"), "")

    def test_placeholder_is_rejected(self):
        # .env.example をコピーしただけの状態。botocore は ValueError を投げる。
        problem = config._endpoint_problem("https://ACCOUNT_ID.r2.cloudflarestorage.com")
        self.assertIn("ACCOUNT_ID", problem)

    def test_empty_is_unset(self):
        self.assertEqual(config._endpoint_problem(""), "未設定")

    def test_scheme_is_required(self):
        self.assertIn("スキーム", config._endpoint_problem("0123456789ab.r2.cloudflarestorage.com"))

    def test_underscore_in_host_is_rejected(self):
        self.assertIn("ホスト名", config._endpoint_problem("https://my_bucket.example.com"))


if __name__ == "__main__":
    unittest.main()
