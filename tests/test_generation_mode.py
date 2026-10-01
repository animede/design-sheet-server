# -*- coding: utf-8 -*-
import io
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from PIL import Image
from fastapi import HTTPException

import app
from core import client, jobs


def _png(width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    return buf.getvalue()


class _AsyncUpload:
    """UploadFile のスレッドプールに依存しない generate() 単体テスト用入力。"""

    def __init__(self, contents):
        self._contents = contents

    async def read(self):
        return self._contents


class GenerationModeTests(unittest.TestCase):
    def test_ui_defaults_favor_chibi_demo_and_large_only(self):
        metadata = asyncio.run(app.meta())

        self.assertEqual(metadata["defaults"]["mode"], "chibi")
        self.assertEqual(metadata["defaults"]["generation_mode"], "demo")
        self.assertEqual(metadata["defaults"]["sizes"], ["large"])

    def test_demo_mode_uses_624_views_and_512_finishing(self):
        mode = app.GENERATION_MODE_BY_KEY["demo"]

        self.assertEqual(mode["size"], 624)
        self.assertEqual(mode["finish_size"], 512)

    def test_fast_dimensions_keep_aspect_ratio_and_target_area(self):
        self.assertEqual(jobs._edit_dims_for(_png(1024, 512), 512), (704, 352))

    def test_fast_charsheet_reference_gets_physical_safety_margin(self):
        padded = jobs._pad_charsheet_reference(_png(100, 200))
        with Image.open(io.BytesIO(padded)) as image:
            self.assertEqual(image.size, (130, 260))
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))

    def test_demo_mode_uses_larger_safety_margin_than_768_mode(self):
        self.assertEqual(jobs._charsheet_margin_ratio(624), 0.30)
        self.assertEqual(jobs._charsheet_margin_ratio(768), 0.15)

    def test_no_explicit_size_keeps_backend_auto_mode(self):
        with mock.patch.object(jobs.config, "EDIT_SIZE", 0):
            self.assertEqual(jobs._edit_dims_for(_png(1024, 512)), (None, None))

    def test_charsheet_size_is_forwarded_to_backend(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"job_id": "test-job"}
        with mock.patch.object(client, "_post_with_busy_retry", return_value=response) as post:
            result = client.charsheet_generate(_png(64, 64), 123, size=512)

        self.assertEqual(result, "test-job")
        self.assertEqual(post.call_args.kwargs["data"], {"seed": "123", "size": "512"})

    def test_selected_views_are_forwarded_to_backend(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"job_id": "test-job"}
        with mock.patch.object(client, "_post_with_busy_retry", return_value=response) as post:
            client.charsheet_generate(
                _png(64, 64), 123, size=768, views=["front", "left"]
            )

        self.assertEqual(post.call_args.kwargs["data"], {
            "seed": "123", "size": "768", "views": "front,left",
        })

    def test_quality_mode_omits_charsheet_size(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"job_id": "test-job"}
        with mock.patch.object(client, "_post_with_busy_retry", return_value=response) as post:
            client.charsheet_generate(_png(64, 64), 123)

        self.assertEqual(post.call_args.kwargs["data"], {"seed": "123"})

    def test_fast_api_mode_saves_tested_view_and_finishing_sizes(self):
        upload = _AsyncUpload(_png(320, 640))
        with mock.patch.object(app.jobs, "start_job", return_value="abc123def456") as start:
            result = asyncio.run(app.generate(
                image=upload,
                seed=42,
                views="front",
                variants="color",
                stylize=True,
                partial_target="the overalls",
                partial_color="red",
                quant="",
                lightning=True,
                layout="a4",
                hero_variant="lineart",
                hero_view="front",
                mode="illustration",
                sizes="small",
                generation_mode="fast",
            ))

        self.assertEqual(result, {"job_id": "abc123def456", "seed": 42})
        params = start.call_args.args[1]
        self.assertEqual(params["generation_mode"], "fast")
        self.assertEqual(params["generation_size"], 768)
        self.assertEqual(params["finishing_size"], 512)

    def test_demo_api_mode_saves_optimized_view_and_finishing_sizes(self):
        upload = _AsyncUpload(_png(320, 640))
        with mock.patch.object(app.jobs, "start_job", return_value="abc123def456") as start:
            result = asyncio.run(app.generate(
                image=upload,
                seed=42,
                views="front",
                variants="color",
                stylize=True,
                partial_target="the overalls",
                partial_color="red",
                quant="",
                lightning=True,
                layout="a4",
                hero_variant="lineart",
                hero_view="front",
                mode="chibi",
                sizes="large",
                generation_mode="demo",
            ))

        self.assertEqual(result, {"job_id": "abc123def456", "seed": 42})
        params = start.call_args.args[1]
        self.assertEqual(params["generation_mode"], "demo")
        self.assertEqual(params["generation_size"], 624)
        self.assertEqual(params["finishing_size"], 512)

    def test_unknown_generation_mode_is_rejected(self):
        upload = _AsyncUpload(_png(32, 32))
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(app.generate(
                image=upload,
                layout="a4",
                mode="illustration",
                generation_mode="tiny",
            ))
        self.assertEqual(caught.exception.status_code, 400)

    def test_completed_view_is_published_before_charsheet_job_finishes(self):
        job_id = "abc123def456"
        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
            jobs.config, "OUTPUTS_DIR", output_dir
        ), mock.patch.object(
            jobs.client, "charsheet_view_image", return_value=_png(512, 512)
        ):
            job_dir = os.path.join(output_dir, job_id)
            os.makedirs(job_dir)
            jobs._jobs[job_id] = {
                "job_id": job_id,
                "views": {"front": {"illustration": "pending"}},
                "cell_errors": {},
            }
            try:
                images = {}
                jobs._sync_completed_charsheet_views(
                    job_id,
                    job_dir,
                    "backend-job",
                    {"status": "running", "views": [
                        {"key": "front", "status": "done"},
                    ]},
                    ["front"],
                    images,
                    "illustration",
                    "source",
                )

                self.assertIn("front", images)
                self.assertEqual(
                    jobs._jobs[job_id]["views"]["front"]["illustration"], "done"
                )
                self.assertTrue(os.path.exists(os.path.join(
                    job_dir, "front_illustration.png"
                )))
            finally:
                jobs._jobs.pop(job_id, None)

    def test_fast_mode_keeps_preparation_quality_but_uses_768_for_views(self):
        job_id = "abc123def456"
        source = _png(984, 1056)
        params = {
            "views": ["front"],
            "seed": 123,
            "quant": "",
            "lightning": True,
            "generation_size": 768,
            "finishing_size": 512,
            "partial_target": "clothes",
            "partial_color": "red",
        }
        with tempfile.TemporaryDirectory() as output_dir, mock.patch.object(
            jobs.config, "OUTPUTS_DIR", output_dir
        ), mock.patch.object(
            jobs.config, "EDIT_SIZE", 0
        ), mock.patch.object(
            jobs.client, "edit", return_value=source
        ) as edit, mock.patch.object(
            jobs.client, "charsheet_generate", return_value="backend-job"
        ) as charsheet_generate, mock.patch.object(
            jobs.client, "charsheet_wait", return_value={"status": "done"}
        ), mock.patch.object(
            jobs.client, "charsheet_view_image", return_value=_png(512, 512)
        ):
            job_dir = os.path.join(output_dir, job_id)
            os.makedirs(job_dir)
            with open(os.path.join(job_dir, "input.png"), "wb") as f:
                f.write(source)
            jobs._jobs[job_id] = {
                "job_id": job_id,
                "views": {"front": {"illustration": "pending"}},
                "cell_errors": {},
            }
            try:
                jobs._generate_mode_views(job_id, job_dir, params, "illustration")
            finally:
                jobs._jobs.pop(job_id, None)

        self.assertIsNone(edit.call_args.kwargs["width"])
        self.assertIsNone(edit.call_args.kwargs["height"])
        self.assertEqual(charsheet_generate.call_args.kwargs["size"], 768)
        self.assertEqual(charsheet_generate.call_args.kwargs["views"], ["front"])
        sent_image = charsheet_generate.call_args.args[0]
        with Image.open(io.BytesIO(sent_image)) as image:
            self.assertEqual(image.size, (1280, 1372))


if __name__ == "__main__":
    unittest.main()
