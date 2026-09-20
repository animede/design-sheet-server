# -*- coding: utf-8 -*-
import asyncio
import unittest
from unittest import mock

from fastapi import HTTPException

import app


class ShareApiTests(unittest.TestCase):
    def test_unconfigured_r2_returns_503(self):
        with mock.patch.object(app.cloud_share, "is_enabled", return_value=False):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(app.share_job_sheet("0123456789ab"))
        self.assertEqual(caught.exception.status_code, 503)

    def test_completed_job_is_shared(self):
        expected = {
            "url": "https://example.invalid/signed",
            "qr_data_url": "data:image/png;base64,test",
            "expires_in": 3600,
        }
        with (
            mock.patch.object(app.cloud_share, "is_enabled", return_value=True),
            mock.patch.object(app.jobs, "get_job", return_value={"sheet_ready": True}),
            mock.patch.object(app, "_job_file", return_value="/tmp/sheet.png"),
            mock.patch.object(app.cloud_share, "share_sheet", return_value=expected) as share,
        ):
            result = asyncio.run(app.share_job_sheet("0123456789ab"))
        self.assertEqual(result, {"ok": True, **expected})
        share.assert_called_once_with("0123456789ab", "/tmp/sheet.png")


if __name__ == "__main__":
    unittest.main()
