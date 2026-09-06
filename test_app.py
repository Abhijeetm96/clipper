"""Comprehensive Test Suite for Universal Video Clipper & Downloader.

Tests core backend logic, API endpoints, format parsing, file uploads,
clipping strategies, audio extraction, Windows file locking resilience,
and File Explorer integration.
"""

import os
import io
import sys
import time
import shutil
import unittest
from pathlib import Path

# Import application components
import app as clipper_app
from app import (
    app,
    sanitize_filename,
    format_seconds,
    parse_timestamp,
    get_format_for_quality,
    detect_platform,
    estimate_sizes,
    get_video_duration,
    SYSTEM_DOWNLOADS_DIR,
    DOWNLOADS_DIR,
    CLIPS_DIR,
    JOBS,
)


class TestHelpers(unittest.TestCase):
    """Test utility and formatting helper functions."""

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("test video"), "test_video")
        self.assertEqual(sanitize_filename("cool:video?*name!"), "cool_video_name")
        self.assertEqual(sanitize_filename("   "), "clip")
        self.assertEqual(sanitize_filename(""), "clip")
        long_name = "a" * 100
        self.assertLessEqual(len(sanitize_filename(long_name)), 60)

    def test_format_seconds(self):
        self.assertEqual(format_seconds(0), "0:00")
        self.assertEqual(format_seconds(45), "0:45")
        self.assertEqual(format_seconds(65), "1:05")
        self.assertEqual(format_seconds(3665), "1:01:05")

    def test_parse_timestamp(self):
        self.assertEqual(parse_timestamp(""), 0.0)
        self.assertEqual(parse_timestamp(None), 0.0)
        self.assertEqual(parse_timestamp("45"), 45.0)
        self.assertEqual(parse_timestamp("01:30"), 90.0)
        self.assertEqual(parse_timestamp("01:00:00"), 3600.0)
        self.assertEqual(parse_timestamp("invalid"), 0.0)
        self.assertEqual(parse_timestamp("-10"), 0.0)

    def test_get_format_for_quality(self):
        f720 = get_format_for_quality("720")
        f1080 = get_format_for_quality("1080")
        f4k = get_format_for_quality("4k")
        fbest = get_format_for_quality("best")

        self.assertIn("720", f720)
        self.assertIn("1080", f1080)
        self.assertTrue("bv*" in f4k or "best" in f4k)
        self.assertTrue("bv*" in fbest or "best" in fbest)

    def test_detect_platform(self):
        p_yt = detect_platform("https://www.youtube.com/watch?v=dQw4w9WgXcQ", {})
        self.assertEqual(p_yt["name"], "YouTube")
        self.assertTrue(p_yt["is_yt"])

        p_tiktok = detect_platform("https://www.tiktok.com/@user/video/123", {})
        self.assertEqual(p_tiktok["name"], "TikTok")

        p_twitter = detect_platform("https://twitter.com/user/status/123", {})
        self.assertEqual(p_twitter["name"], "X / Twitter")

        p_direct = detect_platform("https://example.com/video.mp4", {})
        self.assertEqual(p_direct["name"], "Direct Stream")


class TestAPIEndpoints(unittest.TestCase):
    """Test Flask API endpoints with test client."""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

    def test_index_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Universal Video Clipper", res.data)
        self.assertIn(b"Download Full Video", res.data)
        self.assertIn(b"savedBanner", res.data)

    def test_api_info_validation(self):
        # Empty body
        res = self.client.post("/api/info", json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("error", data)

        # Invalid URL scheme
        res = self.client.post("/api/info", json={"url": "not_a_url"})
        self.assertEqual(res.status_code, 400)

    def test_api_start_validation(self):
        # Missing URL and upload_id
        res = self.client.post("/api/start", json={})
        self.assertEqual(res.status_code, 400)

        # Invalid clip length (<3s)
        res = self.client.post("/api/start", json={"url": "https://example.com/video.mp4", "clip_length": 1})
        self.assertEqual(res.status_code, 400)

        # Invalid clip length (>600s)
        res = self.client.post("/api/start", json={"url": "https://example.com/video.mp4", "clip_length": 700})
        self.assertEqual(res.status_code, 400)

    def test_api_upload_validation(self):
        # No file sent
        res = self.client.post("/api/upload")
        self.assertEqual(res.status_code, 400)

        # Disallowed file extension (.txt)
        data = {"file": (io.BytesIO(b"fake text content"), "test.txt")}
        res = self.client.post("/api/upload", data=data, content_type="multipart/form-data")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Unsupported video format", res.get_json()["error"])

    def test_api_open_folder(self):
        # Valid path (system downloads directory)
        res = self.client.post("/api/open_folder", json={"path": str(SYSTEM_DOWNLOADS_DIR)})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

        # Empty path should fall back to SYSTEM_DOWNLOADS_DIR and succeed
        res = self.client.post("/api/open_folder", json={"path": ""})
        self.assertEqual(res.status_code, 200)

        # Non-existent path should also gracefully fallback to SYSTEM_DOWNLOADS_DIR
        res = self.client.post("/api/open_folder", json={"path": "C:\\non_existent_folder_xyz_123"})
        # Should either fallback or return error gracefully
        self.assertIn(res.status_code, [200, 404])

    def test_api_history(self):
        res = self.client.get("/api/history")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("history", data)
        self.assertIsInstance(data["history"], list)

    def test_api_status_unknown(self):
        res = self.client.get("/api/status/non_existent_job_123")
        self.assertEqual(res.status_code, 404)

    def test_api_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("ffmpeg", data)

        res_z = self.client.get("/healthz")
        self.assertEqual(res_z.status_code, 200)


class TestFullPipeline(unittest.TestCase):
    """End-to-end execution testing with sample video."""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        cls.client = app.test_client()

        # Generate a minimal valid 3-second MP4 test video with ffmpeg
        cls.test_video_path = DOWNLOADS_DIR / "unit_test_sample.mp4"
        import subprocess
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(cls.test_video_path)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        if cls.test_video_path.exists():
            try:
                cls.test_video_path.unlink()
            except OSError:
                pass

    def test_upload_and_sequential_clipping(self):
        """Test uploading a file and clipping it sequentially with 9:16 vertical blur and progress bar."""
        with open(self.test_video_path, "rb") as f:
            upload_data = {"file": (f, "unit_test_sample.mp4")}
            res = self.client.post("/api/upload", data=upload_data, content_type="multipart/form-data")
        
        self.assertEqual(res.status_code, 200)
        up_info = res.get_json()
        upload_id = up_info["upload_id"]
        self.assertTrue(upload_id)
        self.assertGreater(up_info["duration"], 0)

        # Start job with uploaded file
        res = self.client.post("/api/start", json={
            "upload_id": upload_id,
            "clip_length": 3,
            "aspect_ratio": "9:16-blur",
            "add_progress_bar": True,
            "strategy": "sequential",
            "mode": "video",
            "prefix": "test_clip"
        })
        self.assertEqual(res.status_code, 200)
        job_id = res.get_json()["job_id"]

        # Poll status until done or error
        job = None
        for _ in range(30):
            status_res = self.client.get(f"/api/status/{job_id}")
            job = status_res.get_json()
            if job["status"] in ["done", "error"]:
                break
            time.sleep(0.5)

        self.assertIsNotNone(job)
        self.assertEqual(job["status"], "done", f"Job failed with message: {job.get('error')}")
        self.assertGreater(len(job["clips"]), 0)
        self.assertTrue(job["clips"][0].startswith("test_clip_"))
        self.assertIn("saved_to", job)

        # Verify clip can be retrieved
        clip_name = job["clips"][0]
        with self.client.get(f"/clips/{job_id}/{clip_name}") as clip_res:
            self.assertEqual(clip_res.status_code, 200)
            self.assertGreater(len(clip_res.data), 1000)

        # Verify ZIP download
        with self.client.get(f"/api/zip/{job_id}") as zip_res:
            self.assertEqual(zip_res.status_code, 200)
            self.assertEqual(zip_res.content_type, "application/zip")

        # Cleanup job
        del_res = self.client.post(f"/api/delete/{job_id}")
        self.assertEqual(del_res.status_code, 200)

    def test_audio_mode_extraction(self):
        """Test audio MP3 extraction mode."""
        with open(self.test_video_path, "rb") as f:
            upload_data = {"file": (f, "unit_test_sample.mp4")}
            res = self.client.post("/api/upload", data=upload_data, content_type="multipart/form-data")
        
        self.assertEqual(res.status_code, 200)
        upload_id = res.get_json()["upload_id"]

        res = self.client.post("/api/start", json={
            "upload_id": upload_id,
            "clip_length": 3,
            "mode": "audio",
            "strategy": "sequential",
            "prefix": "audio_test"
        })
        self.assertEqual(res.status_code, 200)
        job_id = res.get_json()["job_id"]

        job = None
        for _ in range(30):
            status_res = self.client.get(f"/api/status/{job_id}")
            job = status_res.get_json()
            if job["status"] in ["done", "error"]:
                break
            time.sleep(0.5)

        self.assertEqual(job["status"], "done")
        self.assertTrue(job["clips"][0].endswith(".mp3"))

        # Cleanup
        self.client.post(f"/api/delete/{job_id}")

    def test_full_video_download_and_permanent_folder_save(self):
        """Test full video strategy, fast stream copy, and permanent Downloads folder delivery."""
        with open(self.test_video_path, "rb") as f:
            upload_data = {"file": (f, "unit_test_sample.mp4")}
            res = self.client.post("/api/upload", data=upload_data, content_type="multipart/form-data")
        
        self.assertEqual(res.status_code, 200)
        upload_id = res.get_json()["upload_id"]

        res = self.client.post("/api/start", json={
            "upload_id": upload_id,
            "strategy": "full",
            "quality": "best",
            "prefix": "full_perm_test"
        })
        self.assertEqual(res.status_code, 200)
        job_id = res.get_json()["job_id"]

        job = None
        for _ in range(30):
            status_res = self.client.get(f"/api/status/{job_id}")
            job = status_res.get_json()
            if job["status"] in ["done", "error"]:
                break
            time.sleep(0.5)

        self.assertEqual(job["status"], "done")
        self.assertEqual(len(job["clips"]), 1)
        self.assertTrue("saved_to" in job and job["saved_to"])
        
        # Verify file exists in user's Downloads folder
        saved_file = Path(job["saved_to"])
        self.assertTrue(saved_file.exists())
        self.assertGreater(saved_file.stat().st_size, 500)

        # Cleanup job and test file
        self.client.post(f"/api/delete/{job_id}")
        try:
            saved_file.unlink()
        except OSError:
            pass

    def test_range_trimmer(self):
        """Test trimming specific range (00:00 to 00:01)."""
        with open(self.test_video_path, "rb") as f:
            upload_data = {"file": (f, "unit_test_sample.mp4")}
            res = self.client.post("/api/upload", data=upload_data, content_type="multipart/form-data")
        
        self.assertEqual(res.status_code, 200)
        upload_id = res.get_json()["upload_id"]

        res = self.client.post("/api/start", json={
            "upload_id": upload_id,
            "clip_length": 3,
            "strategy": "sequential",
            "start_time": "00:00",
            "end_time": "00:01",
            "prefix": "trim_test"
        })
        self.assertEqual(res.status_code, 200)
        job_id = res.get_json()["job_id"]

        job = None
        for _ in range(30):
            status_res = self.client.get(f"/api/status/{job_id}")
            job = status_res.get_json()
            if job["status"] in ["done", "error"]:
                break
            time.sleep(0.5)

        self.assertEqual(job["status"], "done")
        self.assertEqual(len(job["clips"]), 1)
        self.client.post(f"/api/delete/{job_id}")

    def test_error_handling_invalid_url(self):
        """Test error status handling for uncontactable URL."""
        res = self.client.post("/api/start", json={
            "url": "https://invalid-non-existent-domain-xyz-123.com/fake.mp4",
            "clip_length": 30,
            "strategy": "sequential"
        })
        self.assertEqual(res.status_code, 200)
        job_id = res.get_json()["job_id"]

        job = None
        for _ in range(30):
            status_res = self.client.get(f"/api/status/{job_id}")
            job = status_res.get_json()
            if job["status"] in ["done", "error"]:
                break
            time.sleep(0.5)

        self.assertEqual(job["status"], "error")
        self.assertTrue(bool(job.get("error")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
