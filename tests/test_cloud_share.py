# -*- coding: utf-8 -*-
import base64
import os
import tempfile
import unittest
from unittest import mock

from botocore.exceptions import ClientError

from core import cloud_share


class FakeS3Client:
    def __init__(self):
        self.upload_args = None
        self.presign_args = None

    def upload_file(self, *args, **kwargs):
        self.upload_args = (args, kwargs)

    def generate_presigned_url(self, *args, **kwargs):
        self.presign_args = (args, kwargs)
        return "https://example.invalid/sheet.png?X-Amz-Signature=test"


class CloudShareTests(unittest.TestCase):
    def test_disabled_configuration_is_rejected(self):
        with mock.patch.object(cloud_share, "is_enabled", return_value=False):
            with self.assertRaisesRegex(cloud_share.CloudShareError, "設定されていません"):
                cloud_share.share_sheet("0123456789ab", "/not/used.png")

    def test_invalid_endpoint_is_reported_as_configuration_error(self):
        """設定ミスで boto3 が ValueError を投げても 500 にせず利用者向けエラーにする。"""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"png")
            path = f.name
        try:
            with (
                mock.patch.object(cloud_share, "is_enabled", return_value=True),
                mock.patch.object(cloud_share.config, "R2_ENDPOINT",
                                  "https://ACCOUNT_ID.r2.cloudflarestorage.com"),
                mock.patch.object(cloud_share.config, "R2_ACCESS_KEY_ID", "dummy"),
                mock.patch.object(cloud_share.config, "R2_SECRET_ACCESS_KEY", "dummy"),
            ):
                with self.assertRaisesRegex(cloud_share.CloudShareError, "接続設定が不正"):
                    cloud_share.share_sheet("0123456789ab", path)
        finally:
            os.unlink(path)

    def test_upload_failure_reports_s3_error_code(self):
        """upload_file が包む S3UploadFailedError も 500 にせず理由を返す。"""
        from boto3.exceptions import S3UploadFailedError

        class FailingClient(FakeS3Client):
            def upload_file(self, *args, **kwargs):
                raise S3UploadFailedError(
                    "Failed to upload a to b: An error occurred (AccessDenied) "
                    "when calling the PutObject operation: Access Denied")

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"png")
            path = f.name
        try:
            with (
                mock.patch.object(cloud_share, "is_enabled", return_value=True),
                mock.patch.object(cloud_share, "_client", return_value=FailingClient()),
                mock.patch.object(cloud_share.config, "R2_BUCKET", "test-bucket"),
            ):
                with self.assertRaisesRegex(cloud_share.CloudShareError,
                                            "AccessDenied.*Object Read & Write"):
                    cloud_share.share_sheet("0123456789ab", path)
        finally:
            os.unlink(path)

    def test_error_code_from_client_error(self):
        exc = ClientError({"Error": {"Code": "NoSuchBucket"}}, "PutObject")
        self.assertEqual(cloud_share._error_code(exc), "NoSuchBucket")

    def test_upload_presign_and_qr(self):
        fake = FakeS3Client()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"png")
            path = f.name
        try:
            with (
                mock.patch.object(cloud_share, "is_enabled", return_value=True),
                mock.patch.object(cloud_share, "_client", return_value=fake),
                mock.patch.object(cloud_share.config, "R2_BUCKET", "test-bucket"),
                mock.patch.object(cloud_share.config, "R2_URL_TTL_S", 3600),
            ):
                result = cloud_share.share_sheet("0123456789ab", path)
        finally:
            os.unlink(path)

        upload_args, upload_kwargs = fake.upload_args
        self.assertEqual(upload_args[:3], (
            path, "test-bucket", "design-sheets/0123456789ab/sheet.png",
        ))
        self.assertEqual(upload_kwargs["ExtraArgs"]["ContentType"], "image/png")
        self.assertEqual(fake.presign_args[1]["ExpiresIn"], 3600)
        self.assertEqual(result["expires_in"], 3600)
        prefix = "data:image/png;base64,"
        self.assertTrue(result["qr_data_url"].startswith(prefix))
        self.assertEqual(base64.b64decode(result["qr_data_url"][len(prefix):])[:8],
                         b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
