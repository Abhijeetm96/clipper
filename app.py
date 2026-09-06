import os
import re
import io
import sys
import time
import uuid
import shutil
import zipfile
import subprocess
import threading
import traceback
import urllib.request
from pathlib import Path

# Ensure Windows PATH includes newly installed tools (FFmpeg, yt-dlp, aria2c, Python Scripts)
if sys.platform == "win32":
    try:
        import winreg

        def _get_reg_path(key, subkey):
            try:
                with winreg.OpenKey(key, subkey) as k:
                    val, _ = winreg.QueryValueEx(k, "Path")
                    return val
            except Exception:
                return ""

        extra_paths = [
            _get_reg_path(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
            _get_reg_path(winreg.HKEY_CURRENT_USER, r"Environment"),
            str(Path(sys.prefix) / "Scripts"),
            str(Path(sys.executable).parent / "Scripts"),
        ]
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
            if winget_pkgs.exists():
                for p in winget_pkgs.rglob("*.exe"):
                    if p.name.lower() in ["ffmpeg.exe", "aria2c.exe"]:
                        extra_paths.append(str(p.parent))

        os.environ["PATH"] = os.pathsep.join(filter(None, extra_paths)) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass

import yt_dlp

# Monkey-patch yt_dlp FFmpegMergerPP.run to handle Windows file locking [WinError 32]
def _patch_ffmpeg_merger():
    try:
        from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP, prepend_extension
        from yt_dlp.postprocessor.common import PostProcessor

        @PostProcessor._restrict_to(images=False)
        def robust_run(self, info):
            filename = info['filepath']
            temp_filename = prepend_extension(filename, 'temp')
            args = ['-c', 'copy']
            audio_streams = 0
            for (i, fmt) in enumerate(info.get('requested_formats', [])):
                if fmt.get('acodec') != 'none':
                    args.extend(['-map', f'{i}:a:0'])
                    aac_fixup = fmt.get('protocol', '').startswith('m3u8') and self.get_audio_codec(fmt.get('filepath')) == 'aac'
                    if aac_fixup:
                        args.extend([f'-bsf:a:{audio_streams}', 'aac_adtstoasc'])
                    audio_streams += 1
                if fmt.get('vcodec') != 'none':
                    args.extend(['-map', f'{i}:v:0'])
            self.to_screen(f'Merging formats into "{filename}"')
            self.run_ffmpeg_multiple_files(info['__files_to_merge'], temp_filename, args)

            # Windows lock mitigation: FFmpeg exit or Defender scan may lock the file briefly.
            renamed = False
            last_err = None
            for _ in range(25):
                try:
                    if os.path.exists(filename):
                        try:
                            os.remove(filename)
                        except Exception:
                            pass
                    os.replace(temp_filename, filename)
                    renamed = True
                    break
                except (PermissionError, OSError) as e:
                    last_err = e
                    time.sleep(0.3)

            if not renamed:
                try:
                    shutil.copy2(temp_filename, filename)
                    try:
                        os.remove(temp_filename)
                    except Exception:
                        pass
                    renamed = True
                except Exception:
                    pass

            if not renamed:
                if os.path.exists(temp_filename) and os.path.getsize(temp_filename) > 0:
                    info['filepath'] = temp_filename
                    return info['__files_to_merge'], info
                if last_err:
                    raise last_err

            return info['__files_to_merge'], info

        FFmpegMergerPP.run = robust_run

        from yt_dlp.postprocessor.ffmpeg import FFmpegCopyStreamPP

        def robust_fixup(self, msg, filename, options):
            temp_filename = prepend_extension(filename, 'temp')
            self.to_screen(f'{msg} of "{filename}"')
            self.run_ffmpeg(filename, temp_filename, options)

            renamed = False
            last_err = None
            for _ in range(25):
                try:
                    if os.path.exists(filename):
                        try:
                            os.remove(filename)
                        except Exception:
                            pass
                    os.replace(temp_filename, filename)
                    renamed = True
                    break
                except (PermissionError, OSError) as e:
                    last_err = e
                    time.sleep(0.3)

            if not renamed:
                try:
                    shutil.copy2(temp_filename, filename)
                    try:
                        os.remove(temp_filename)
                    except Exception:
                        pass
                    renamed = True
                except Exception:
                    pass

            if not renamed and last_err:
                raise last_err

        FFmpegCopyStreamPP._fixup = robust_fixup
    except Exception as e:
        print(f"Warning: Could not patch FFmpegMergerPP/FFmpegCopyStreamPP: {e}")

_patch_ffmpeg_merger()
from flask import Flask, request, jsonify, send_from_directory, render_template, send_file

BASE_DIR = Path(__file__).parent.resolve()
DOWNLOADS_DIR = BASE_DIR / "downloads"  # Internal staging directory
CLIPS_DIR = BASE_DIR / "clips"
SYSTEM_DOWNLOADS_DIR = Path.home() / "Downloads"

DOWNLOADS_DIR.mkdir(exist_ok=True)
CLIPS_DIR.mkdir(exist_ok=True)
SYSTEM_DOWNLOADS_DIR.mkdir(exist_ok=True)


def get_aria2_path():
    """Detect aria2c executable for high-speed multi-threaded downloads."""
    if shutil.which("aria2c"):
        return shutil.which("aria2c")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.exists():
            for p in winget_pkgs.rglob("aria2c.exe"):
                if p.is_file():
                    os.environ["PATH"] = str(p.parent) + os.pathsep + os.environ.get("PATH", "")
                    return str(p)
    return None


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "universal-video-clipper-production-secret-key")
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 4 * 1024 * 1024 * 1024))  # 4 GB max upload

# In-memory job tracker: job_id -> {status, message, progress, clips: [filenames], clip_details: [...], mode: "video"|"audio", error}
JOBS = {}


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    name = re.sub(r"_+", "_", name)
    name = name.strip("_")
    return name[:60] or "clip"


def format_seconds(seconds: float) -> str:
    secs = int(seconds)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def parse_timestamp(val) -> float:
    """Convert MM:SS, HH:MM:SS, or seconds string to float seconds."""
    if val is None or str(val).strip() == "":
        return 0.0
    val = str(val).strip()
    parts = val.split(":")
    try:
        if len(parts) == 1:
            return max(0.0, float(parts[0]))
        elif len(parts) == 2:
            return max(0.0, float(parts[0]) * 60 + float(parts[1]))
        elif len(parts) == 3:
            return max(0.0, float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2]))
    except ValueError:
        return 0.0
    return 0.0


def get_video_duration(path: str) -> float:
    """Return duration in seconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def detect_platform(url: str, info: dict = None) -> dict:
    extractor = ((info.get("extractor_key") or info.get("extractor") or "") if info else "").lower()
    url_lower = url.lower()

    if "youtube" in extractor or "youtu" in url_lower:
        return {"name": "YouTube", "icon": "▶️", "color": "#ff334b", "is_yt": True}
    elif "tiktok" in extractor or "tiktok.com" in url_lower:
        return {"name": "TikTok", "icon": "🎵", "color": "#00f2fe", "is_yt": False}
    elif "twitter" in extractor or "x.com" in url_lower or "twitter.com" in url_lower:
        return {"name": "X / Twitter", "icon": "🐦", "color": "#1da1f2", "is_yt": False}
    elif "instagram" in extractor or "instagram.com" in url_lower:
        return {"name": "Instagram", "icon": "📸", "color": "#e1306c", "is_yt": False}
    elif "reddit" in extractor or "reddit.com" in url_lower or "redd.it" in url_lower:
        return {"name": "Reddit", "icon": "🤖", "color": "#ff4500", "is_yt": False}
    elif "vimeo" in extractor or "vimeo.com" in url_lower:
        return {"name": "Vimeo", "icon": "🎬", "color": "#1ab7ea", "is_yt": False}
    elif "facebook" in extractor or "fb.watch" in url_lower or "facebook.com" in url_lower:
        return {"name": "Facebook", "icon": "📘", "color": "#1877f2", "is_yt": False}
    elif "twitch" in extractor or "twitch.tv" in url_lower:
        return {"name": "Twitch", "icon": "👾", "color": "#9146ff", "is_yt": False}
    elif "dailymotion" in extractor or "dailymotion.com" in url_lower:
        return {"name": "Dailymotion", "icon": "📺", "color": "#0066dc", "is_yt": False}
    elif "pinterest" in extractor or "pinterest.com" in url_lower:
        return {"name": "Pinterest", "icon": "📌", "color": "#e60023", "is_yt": False}
    elif "linkedin" in extractor or "linkedin.com" in url_lower:
        return {"name": "LinkedIn", "icon": "💼", "color": "#0077b5", "is_yt": False}
    elif any(url_lower.endswith(ext) or ext in url_lower for ext in [".mp4", ".webm", ".m3u8", ".mov", ".mkv", ".ts"]):
        return {"name": "Direct Stream", "icon": "🌐", "color": "#10b981", "is_yt": False}
    else:
        site_name = extractor.capitalize() if extractor and extractor != "generic" else "Web Video"
        return {"name": site_name, "icon": "🌐", "color": "#6366f1", "is_yt": False}


def estimate_sizes(info: dict, duration: float, url: str = None) -> dict:
    """Estimate video download sizes across resolutions (1080p, 720p, Best) for any platform."""
    dur = duration or float(info.get("duration") or 0)
    formats = info.get("formats") or []

    def _calc_size(max_h=None):
        best_v = None
        best_a = None
        for f in formats:
            h = f.get("height") or 0
            vcodec = f.get("vcodec") or "none"
            acodec = f.get("acodec") or "none"
            if vcodec != "none" and (max_h is None or h <= max_h):
                if not best_v or (f.get("tbr") or 0) > (best_v.get("tbr") or 0):
                    best_v = f
            if acodec != "none" and vcodec == "none":
                if not best_a or (f.get("tbr") or 0) > (best_a.get("tbr") or 0):
                    best_a = f

        s = 0
        if best_v:
            s += best_v.get("filesize") or best_v.get("filesize_approx") or (best_v.get("tbr", 0) * 1024 / 8 * dur if best_v.get("tbr") and dur > 0 else 0)
        if best_a:
            s += best_a.get("filesize") or best_a.get("filesize_approx") or (best_a.get("tbr", 0) * 1024 / 8 * dur if best_a.get("tbr") and dur > 0 else 0)

        # Fallback for single-stream platforms (Twitter, TikTok, direct MP4, etc.)
        if s == 0:
            s = info.get("filesize") or info.get("filesize_approx") or 0

        # Fallback for direct links via HTTP Content-Length
        if s == 0 and url:
            try:
                probe_url = (best_v and best_v.get("url")) or info.get("url") or url
                req = urllib.request.Request(
                    probe_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                    method="HEAD"
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    cl = resp.headers.get("Content-Length")
                    if cl:
                        s = int(cl)
            except Exception:
                pass

        if s > 0:
            mb = round(s / (1024 * 1024), 1)
            if mb >= 1000:
                return f"~{round(mb / 1024, 2)} GB"
            elif mb < 1.0:
                kb = round(s / 1024, 1)
                return f"~{kb} KB"
            return f"~{mb} MB"
        return "Auto (Source)"

    return {
        "best": _calc_size(None),
        "1080": _calc_size(1080),
        "720": _calc_size(720),
    }


def find_heatmap_peaks(heatmap: list, total_duration: float, max_clips: int = 5, clip_len: int = 60) -> list:
    """Analyze YouTube heatmap engagement graph to identify the top viral peaks."""
    if not heatmap or total_duration <= 0:
        return []

    sorted_points = sorted(heatmap, key=lambda x: x.get("value", 0), reverse=True)
    peaks = []
    min_separation = max(20.0, float(clip_len) * 0.7)

    for pt in sorted_points:
        mid = (pt.get("start_time", 0) + pt.get("end_time", 0)) / 2
        if any(abs(mid - p["mid"]) < min_separation for p in peaks):
            continue

        c_start = max(0.0, mid - (clip_len / 2))
        c_end = min(total_duration, c_start + clip_len)
        score_pct = int(round(pt.get("value", 0) * 100))

        peaks.append({
            "rank": len(peaks) + 1,
            "mid": mid,
            "start": round(c_start, 1),
            "end": round(c_end, 1),
            "duration": round(c_end - c_start, 1),
            "score_pct": score_pct,
            "label": f"Peak #{len(peaks) + 1} ({format_seconds(c_start)} - {format_seconds(c_end)}) [Retention: {score_pct}%]"
        })
        if len(peaks) >= max_clips:
            break

    return sorted(peaks, key=lambda p: p["start"])


def detect_silence_pauses(video_path: str, search_start: float = 0.0, search_duration: float = 600.0) -> list:
    """Run FFmpeg silencedetect to locate pause timestamps."""
    cmd = [
        "ffmpeg", "-ss", str(search_start),
        "-i", video_path,
        "-t", str(search_duration),
        "-af", "silencedetect=noise=-28dB:d=0.2",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    silence_points = []
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                val = float(line.split("silence_start:")[1].split()[0].strip())
                silence_points.append(round(search_start + val, 2))
            except Exception:
                pass
    return sorted(silence_points)


def snap_cuts_to_silence(target_cuts: list, video_path: str, max_duration: float, window: float = 3.5) -> list:
    """Adjust cut points to nearest silence/breath gaps."""
    if not target_cuts or len(target_cuts) <= 1:
        return target_cuts

    pauses = detect_silence_pauses(video_path, search_start=target_cuts[0], search_duration=target_cuts[-1] - target_cuts[0] + 10)
    if not pauses:
        return target_cuts

    snapped = [target_cuts[0]]
    for cut in target_cuts[1:-1]:
        candidates = [p for p in pauses if abs(p - cut) <= window and p > snapped[-1] + 3.0]
        if candidates:
            best_pause = min(candidates, key=lambda p: abs(p - cut))
            snapped.append(best_pause)
        else:
            snapped.append(cut)
    snapped.append(target_cuts[-1])
    return snapped


def get_format_for_quality(quality: str) -> str:
    # Prefer matching MP4/M4A streams so merging is instant stream-copy without re-encoding
    if quality == "720":
        return "bv*[height<=720][ext=mp4]+ba[ext=m4a]/bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best"
    elif quality in ["best", "4k"]:
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b/best"
    else:  # default 1080p
        return "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/best[height<=1080]/best"


def run_job(job_id: str, url: str, clip_length: int, quality: str = "1080",
            aspect_ratio: str = "16:9", mode: str = "video",
            start_time_raw: str = "", end_time_raw: str = "",
            strategy: str = "sequential", snap_silence: bool = False,
            selected_chapters: list = None, prefix: str = "",
            add_progress_bar: bool = False, upload_id: str = None):
    job_dir = CLIPS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    clean_prefix = sanitize_filename(prefix) if prefix else ""
    meta = {}

    try:
        # Case A: Local uploaded file
        if upload_id:
            JOBS[job_id]["status"] = "splitting"
            JOBS[job_id]["progress"] = 30.0
            JOBS[job_id]["message"] = "Preparing uploaded file..."
            uploaded_files = list(DOWNLOADS_DIR.glob(f"{upload_id}.*"))
            if not uploaded_files:
                raise RuntimeError("Uploaded video file not found on server.")
            video_path = str(uploaded_files[0])

        # Case B: Download from Web Video Link
        else:
            JOBS[job_id]["status"] = "downloading"
            JOBS[job_id]["progress"] = 5
            JOBS[job_id]["message"] = "Connecting to video server..."

            output_template = str(DOWNLOADS_DIR / f"{job_id}.%(ext)s")

            def progress_hook(d):
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    downloaded = d.get("downloaded_bytes", 0)
                    pct = (downloaded / total * 100) if total > 0 else 0
                    speed = d.get("_speed_str", "").strip()
                    eta = d.get("_eta_str", "").strip()
                    parts = [f"Downloading: {pct:.1f}%"]
                    if speed:
                        parts.append(f"at {speed}")
                    if eta:
                        parts.append(f"(ETA {eta})")
                    JOBS[job_id]["message"] = " ".join(parts)
                    JOBS[job_id]["progress"] = round(5 + (pct * 0.45), 1)
                elif d.get("status") == "finished":
                    JOBS[job_id]["message"] = "Download complete. Merging streams..."
                    JOBS[job_id]["progress"] = 50.0

            aria2_cmd = get_aria2_path()

            ydl_opts = {
                "format": get_format_for_quality(quality),
                "merge_output_format": "mp4",
                "outtmpl": output_template,
                "noplaylist": True,
                "overwrites": True,
                "windowsfilenames": True,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
                "remote_components": ["ejs:github"],
                "js_runtimes": {"node": {"path": "node"}},
                "concurrent_fragment_downloads": 8,
                "http_chunk_size": 10485760,  # 10 MB chunks to defeat throttling
                "buffersize": 1048576,        # 1 MB buffer
                "retries": 10,
                "fragment_retries": 10,
            }

            if aria2_cmd:
                ydl_opts["external_downloader"] = "aria2c"
                ydl_opts["external_downloader_args"] = ["-x", "8", "-s", "8", "-k", "1M", "-j", "8"]

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    meta = ydl.extract_info(url, download=True)
            except Exception as e:
                # If external downloader encountered an issue, fallback immediately to internal multi-threaded downloader
                if "external_downloader" in ydl_opts:
                    ydl_opts.pop("external_downloader", None)
                    ydl_opts.pop("external_downloader_args", None)
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        meta = ydl.extract_info(url, download=True)
                else:
                    raise e

            target_mp4 = DOWNLOADS_DIR / f"{job_id}.mp4"
            temp_mp4 = DOWNLOADS_DIR / f"{job_id}.temp.mp4"

            if target_mp4.exists() and target_mp4.stat().st_size > 1000:
                video_path = str(target_mp4)
            elif temp_mp4.exists() and temp_mp4.stat().st_size > 1000:
                renamed = False
                for _ in range(15):
                    try:
                        if target_mp4.exists():
                            os.remove(target_mp4)
                        os.replace(temp_mp4, target_mp4)
                        video_path = str(target_mp4)
                        renamed = True
                        break
                    except (PermissionError, OSError):
                        time.sleep(0.3)
                if not renamed:
                    video_path = str(temp_mp4)
            else:
                candidates = [
                    f for f in DOWNLOADS_DIR.glob(f"{job_id}*")
                    if f.suffix.lower() in [".mp4", ".mkv", ".webm"] and not f.name.endswith(".part")
                ]
                if not candidates:
                    raise RuntimeError("Download completed but video file could not be found.")
                candidates.sort(key=lambda f: f.stat().st_size, reverse=True)
                video_path = str(candidates[0])

        JOBS[job_id]["status"] = "splitting"
        JOBS[job_id]["progress"] = 50.0
        JOBS[job_id]["message"] = "Processing full video..." if strategy == "full" else "Analyzing video duration & smart segments..."

        total_video_duration = get_video_duration(video_path)
        if total_video_duration <= 0:
            raise RuntimeError("Could not read video duration.")

        clip_specs = []
        ext = "mp3" if mode == "audio" else "mp4"

        # Strategy 0: FULL LENGTH VIDEO (Entire video in original quality / 4K)
        if strategy == "full":
            video_title = sanitize_filename(meta.get("title") or "full_video")
            if clean_prefix:
                out_name = f"{clean_prefix}_{video_title}.{ext}"
            else:
                out_name = f"{video_title}.{ext}"

            range_start = parse_timestamp(start_time_raw)
            range_end = parse_timestamp(end_time_raw)
            if range_end <= 0 or range_end > total_video_duration:
                range_end = total_video_duration
            if range_start >= range_end:
                range_start = 0.0

            c_dur = range_end - range_start
            clip_specs.append((range_start, c_dur, out_name))

        # Strategy 1: CHAPTERS
        elif strategy == "chapters":
            chapters = meta.get("chapters") or []
            if not chapters:
                raise RuntimeError("No chapters found in this video.")

            for idx, ch in enumerate(chapters):
                if selected_chapters and idx not in selected_chapters:
                    continue
                c_start = float(ch.get("start_time", 0))
                c_end = float(ch.get("end_time", total_video_duration))
                c_dur = c_end - c_start
                if c_dur > 0.5:
                    ch_name = sanitize_filename(ch.get("title") or f"Chapter_{idx+1}")
                    if clean_prefix:
                        out_name = f"{clean_prefix}_{idx+1:02d}_{ch_name}.{ext}"
                    else:
                        out_name = f"{idx+1:02d}_{ch_name}.{ext}"
                    clip_specs.append((c_start, c_dur, out_name))

        # Strategy 2: VIRAL (Most Replayed Heatmap)
        elif strategy == "viral":
            heatmap = meta.get("heatmap") or []
            peaks = find_heatmap_peaks(heatmap, total_video_duration, max_clips=5, clip_len=clip_length)
            if not peaks:
                raise RuntimeError("No viewer replay heatmap data available for this video.")

            for idx, p in enumerate(peaks):
                if clean_prefix:
                    out_name = f"{clean_prefix}_viral_rank{p['rank']}_{int(p['start'])}s_to_{int(p['end'])}s_{p['score_pct']}pct.{ext}"
                else:
                    out_name = f"viral_rank{p['rank']}_{int(p['start'])}s_to_{int(p['end'])}s_{p['score_pct']}pct.{ext}"
                clip_specs.append((p["start"], p["duration"], out_name))

        # Strategy 3: SEQUENTIAL
        else:
            range_start = parse_timestamp(start_time_raw)
            range_end = parse_timestamp(end_time_raw)
            if range_end <= 0 or range_end > total_video_duration:
                range_end = total_video_duration
            if range_start >= range_end:
                range_start = 0.0

            effective_duration = range_end - range_start
            n_cuts = int(effective_duration // clip_length) + (1 if effective_duration % clip_length > 1 else 0)
            if n_cuts == 0:
                n_cuts = 1

            cut_points = [range_start + (i * clip_length) for i in range(n_cuts)]
            cut_points.append(range_end)

            if snap_silence and len(cut_points) > 2:
                JOBS[job_id]["message"] = "Detecting speech pauses & snapping cut boundaries..."
                cut_points = snap_cuts_to_silence(cut_points, video_path, total_video_duration, window=3.5)

            for i in range(len(cut_points) - 1):
                start = cut_points[i]
                dur = cut_points[i + 1] - start
                if dur > 0.5:
                    if clean_prefix:
                        out_name = f"{clean_prefix}_{i+1:03d}.{ext}"
                    else:
                        out_name = f"clip_{i+1:03d}.{ext}"
                    clip_specs.append((start, dur, out_name))

        if not clip_specs:
            raise RuntimeError("No valid clips could be generated from the selected strategy.")

        num_clips = len(clip_specs)
        clip_files = []

        for i, (start, dur, out_name) in enumerate(clip_specs):
            split_pct = 50.0 + round((i / num_clips) * 50.0, 1)
            JOBS[job_id]["progress"] = split_pct
            action_label = "Extracting audio" if mode == "audio" else "Rendering clip"
            JOBS[job_id]["message"] = f"{action_label} {i+1} of {num_clips} ({split_pct:.0f}%)..."

            out_path = job_dir / out_name

            # Fast-path for full video: preserve 100% original 4K stream without re-encoding
            if strategy == "full" and mode == "video" and aspect_ratio == "16:9" and not add_progress_bar and start == 0.0 and dur >= (total_video_duration - 1.0):
                try:
                    shutil.copy2(video_path, str(out_path))
                    if out_path.exists() and out_path.stat().st_size > 500:
                        clip_files.append(out_name)
                        continue
                except Exception:
                    pass

            if mode == "audio":
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", video_path,
                    "-t", str(dur),
                    "-vn", "-c:a", "libmp3lame", "-q:a", "2",
                    str(out_path),
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(start),
                    "-i", video_path,
                    "-t", str(dur),
                ]

                # Progress bar filter component
                progress_bar_filter = f"drawbox=x=0:y=ih-12:w=iw*t/{dur}:h=12:color=#ff334b@1.0:t=fill" if add_progress_bar else None

                if aspect_ratio == "9:16-blur":
                    complex_f = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
                    if progress_bar_filter:
                        complex_f += f",{progress_bar_filter}"
                    cmd.extend([
                        "-filter_complex", complex_f,
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"
                    ])
                elif aspect_ratio == "9:16-crop":
                    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
                    if progress_bar_filter:
                        vf += f",{progress_bar_filter}"
                    cmd.extend([
                        "-vf", vf,
                        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"
                    ])
                else:
                    if progress_bar_filter:
                        cmd.extend(["-vf", progress_bar_filter])
                    cmd.extend([
                        "-c:v", "libx264", "-c:a", "aac",
                        "-preset", "ultrafast"
                    ])
                cmd.append(str(out_path))

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 500:
                clip_files.append(out_name)

        if not clip_files:
            raise RuntimeError("No clips were produced.")

        # Save directly to user's real Downloads folder (permanent, never deleted)
        saved_dest_path = None
        if strategy == "full" and clip_files:
            full_file = clip_files[0]
            src_fp = job_dir / full_file
            if src_fp.exists():
                base_stem = Path(full_file).stem
                file_ext = Path(full_file).suffix
                dest_fp = SYSTEM_DOWNLOADS_DIR / full_file
                counter = 1
                while dest_fp.exists() and dest_fp.stat().st_size > 0:
                    dest_fp = SYSTEM_DOWNLOADS_DIR / f"{base_stem}_{counter}{file_ext}"
                    counter += 1
                try:
                    shutil.copy2(src_fp, dest_fp)
                    saved_dest_path = str(dest_fp)
                except Exception as e:
                    print(f"Could not copy to Downloads folder: {e}")
                    saved_dest_path = str(src_fp)
        elif clip_files:
            video_title = sanitize_filename(meta.get("title") or (clean_prefix or f"batch_{job_id}"))
            clips_dest_dir = SYSTEM_DOWNLOADS_DIR / f"{video_title}_clips"
            counter = 1
            while clips_dest_dir.exists() and any(clips_dest_dir.iterdir()):
                clips_dest_dir = SYSTEM_DOWNLOADS_DIR / f"{video_title}_clips_{counter}"
                counter += 1
            try:
                clips_dest_dir.mkdir(parents=True, exist_ok=True)
                for f in clip_files:
                    if (job_dir / f).exists():
                        shutil.copy2(job_dir / f, clips_dest_dir / f)
                saved_dest_path = str(clips_dest_dir)
            except Exception as e:
                print(f"Could not copy clips to Downloads folder: {e}")
                saved_dest_path = str(job_dir)

        # Clean up ONLY temporary staging downloads fragments in internal scratch folder
        for leftover in DOWNLOADS_DIR.glob(f"{job_id}*"):
            try:
                os.remove(leftover)
            except OSError:
                pass

        # Calculate exact sizes for every generated clip
        clip_details = []
        total_clips_size = 0
        for f in clip_files:
            fp = job_dir / f
            size_bytes = fp.stat().st_size if fp.exists() else 0
            total_clips_size += size_bytes
            mb = round(size_bytes / (1024 * 1024), 2)
            clip_details.append({
                "name": f,
                "size_mb": mb,
                "size_str": f"{round(mb, 1)} MB" if mb >= 1.0 else f"{round(size_bytes / 1024, 1)} KB"
            })

        JOBS[job_id]["progress"] = 100.0
        JOBS[job_id]["status"] = "done"
        JOBS[job_id]["message"] = f"Done! {len(clip_files)} file{'s' if len(clip_files) != 1 else ''} ready."
        JOBS[job_id]["clips"] = clip_files
        JOBS[job_id]["clip_details"] = clip_details
        JOBS[job_id]["total_size_mb"] = round(total_clips_size / (1024 * 1024), 1)
        JOBS[job_id]["saved_to"] = saved_dest_path
        JOBS[job_id]["saved_folder"] = str(SYSTEM_DOWNLOADS_DIR)

    except Exception as e:
        traceback.print_exc()
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["message"] = "Something went wrong."


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload_video():
    """Receive and stage local video file for clipping."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded."}), 400
    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "Empty filename."}), 400

    orig_name = file.filename
    ext = Path(orig_name).suffix.lower()
    if ext not in [".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"]:
        return jsonify({"error": "Unsupported video format. Use MP4, MOV, MKV, or WEBM."}), 400

    upload_id = uuid.uuid4().hex[:12]
    out_path = DOWNLOADS_DIR / f"{upload_id}{ext}"
    file.save(str(out_path))

    duration = get_video_duration(str(out_path))
    if duration <= 0:
        try:
            os.remove(str(out_path))
        except OSError:
            pass
        return jsonify({"error": "Could not read video stream from uploaded file."}), 400

    size_bytes = out_path.stat().st_size
    size_mb = round(size_bytes / (1024 * 1024), 2)
    size_str = f"{size_mb} MB" if size_mb >= 1.0 else f"{round(size_bytes / 1024, 1)} KB"

    return jsonify({
        "upload_id": upload_id,
        "filename": orig_name,
        "duration": duration,
        "duration_str": format_seconds(duration),
        "size_str": size_str,
    })


@app.route("/api/info", methods=["POST"])
def get_video_info():
    """Fetch video metadata, chapters, viral retention heatmap, and estimated file sizes for any video site."""
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url or not re.match(r"^https?://[^\s]+", url):
        return jsonify({"error": "Please provide a valid video URL (starting with http:// or https://)."}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "remote_components": ["ejs:github"],
        "js_runtimes": {"node": {"path": "node"}},
        "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or "Web Video"
        thumbnail = info.get("thumbnail") or ""
        duration = float(info.get("duration") or 0)

        # If duration was not returned by extractor (e.g. direct mp4 stream), probe with ffprobe
        if duration <= 0:
            try:
                duration = get_video_duration(url)
            except Exception:
                duration = 0.0

        chapters = info.get("chapters") or []
        heatmap = info.get("heatmap") or []
        platform_info = detect_platform(url, info)

        clean_chapters = []
        for idx, ch in enumerate(chapters):
            s = float(ch.get("start_time", 0))
            e = float(ch.get("end_time", duration))
            clean_chapters.append({
                "index": idx,
                "title": ch.get("title") or f"Chapter {idx+1}",
                "start": s,
                "end": e,
                "duration_str": format_seconds(e - s),
                "timestamp_str": f"{format_seconds(s)} - {format_seconds(e)}"
            })

        peaks = find_heatmap_peaks(heatmap, duration, max_clips=5, clip_len=60) if duration > 0 else []
        sizes = estimate_sizes(info, duration, url=url)
        primary_size = sizes.get("1080") or sizes.get("best") or "Auto"

        return jsonify({
            "title": title,
            "thumbnail": thumbnail,
            "duration": duration,
            "duration_str": format_seconds(duration) if duration > 0 else "Full Video",
            "chapters": clean_chapters,
            "has_chapters": len(clean_chapters) > 0,
            "peaks": peaks,
            "has_peaks": len(peaks) > 0,
            "sizes": sizes,
            "primary_size": primary_size,
            "platform": platform_info,
        })
    except Exception as e:
        return jsonify({"error": f"Could not fetch video info: {str(e)}"}), 400


@app.route("/api/start", methods=["POST"])
def start_job():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    upload_id = (data.get("upload_id") or "").strip() or None
    clip_length = int(data.get("clip_length") or 60)
    quality = (data.get("quality") or "1080").strip()
    aspect_ratio = (data.get("aspect_ratio") or "16:9").strip()
    mode = (data.get("mode") or "video").strip()
    start_time = (data.get("start_time") or "").strip()
    end_time = (data.get("end_time") or "").strip()
    strategy = (data.get("strategy") or "sequential").strip()
    snap_silence = bool(data.get("snap_silence", False))
    selected_chapters = data.get("selected_chapters")
    prefix = (data.get("prefix") or "").strip()
    add_progress_bar = bool(data.get("add_progress_bar", False))

    if not upload_id and not url:
        return jsonify({"error": "Please provide a video URL or upload a video file."}), 400
    if not upload_id and not re.match(r"^https?://[^\s]+", url):
        return jsonify({"error": "That doesn't look like a valid video URL (must start with http:// or https://)."}), 400
    if clip_length < 3 or clip_length > 600:
        return jsonify({"error": "Clip length must be between 3 and 600 seconds."}), 400

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "status": "queued",
        "message": "Queued...",
        "progress": 0,
        "clips": [],
        "clip_details": [],
        "total_size_mb": 0,
        "mode": mode,
        "strategy": strategy,
        "error": None
    }

    thread = threading.Thread(
        target=run_job,
        args=(
            job_id, url, clip_length, quality, aspect_ratio, mode,
            start_time, end_time, strategy, snap_silence, selected_chapters,
            prefix, add_progress_bar, upload_id
        ),
        daemon=True
    )
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job id"}), 404
    return jsonify(job)


@app.route("/clips/<job_id>/<filename>")
def get_clip(job_id, filename):
    job_dir = CLIPS_DIR / job_id
    as_attachment = request.args.get("download", "0") == "1"
    return send_from_directory(job_dir, filename, as_attachment=as_attachment)


@app.route("/api/zip/<job_id>")
def download_zip(job_id):
    job_dir = CLIPS_DIR / job_id
    if not job_dir.exists():
        return jsonify({"error": "Job not found"}), 404

    files = sorted([f for f in job_dir.iterdir() if f.is_file()])
    if not files:
        return jsonify({"error": "No clips found"}), 404

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    mem_zip.seek(0)

    return send_file(
        mem_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"clips_{job_id}.zip"
    )


@app.route("/api/history")
def get_history():
    jobs = []
    if CLIPS_DIR.exists():
        for job_folder in sorted(CLIPS_DIR.iterdir(), key=lambda d: d.stat().st_mtime if d.is_dir() else 0, reverse=True):
            if job_folder.is_dir():
                job_id = job_folder.name
                files = sorted([f.name for f in job_folder.iterdir() if f.is_file()])
                if files:
                    total_size = sum(f.stat().st_size for f in job_folder.iterdir() if f.is_file())
                    size_mb = round(total_size / (1024 * 1024), 1)
                    jobs.append({
                        "job_id": job_id,
                        "clip_count": len(files),
                        "size_mb": size_mb,
                        "created_at": job_folder.stat().st_mtime,
                        "clips": files,
                    })
    return jsonify({"history": jobs})


@app.route("/api/delete/<job_id>", methods=["DELETE", "POST"])
def delete_job(job_id):
    job_dir = CLIPS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    if job_id in JOBS:
        del JOBS[job_id]
    return jsonify({"success": True})


@app.route("/api/open_folder", methods=["POST"])
def open_folder():
    """Open Windows File Explorer or native file manager showing the downloaded file."""
    data = request.get_json(force=True) or {}
    file_path = (data.get("path") or "").strip()
    if not file_path or not os.path.exists(file_path):
        file_path = str(SYSTEM_DOWNLOADS_DIR)

    if os.path.exists(file_path):
        try:
            if sys.platform == "win32":
                abs_p = os.path.abspath(file_path)
                if os.path.isfile(abs_p):
                    subprocess.Popen(f'explorer /select,"{abs_p}"')
                else:
                    subprocess.Popen(f'explorer "{abs_p}"')
                return jsonify({"success": True})
            elif sys.platform == "darwin":
                if os.path.isfile(file_path):
                    subprocess.Popen(["open", "-R", file_path])
                else:
                    subprocess.Popen(["open", file_path])
                return jsonify({"success": True})
            else:
                target = os.path.dirname(file_path) if os.path.isfile(file_path) else file_path
                subprocess.Popen(["xdg-open", target])
                return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": f"Path '{file_path}' was not found on server."}), 404


@app.route("/api/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health_check():
    """Production health and dependency readiness check."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    aria2_ok = get_aria2_path() is not None
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "platform": sys.platform,
        "ffmpeg": "available" if ffmpeg_ok else "missing",
        "aria2c": "available" if aria2_ok else "not_found",
        "active_jobs": len([j for j in JOBS.values() if j.get("status") in ["downloading", "processing"]])
    }), 200


@app.errorhandler(404)
def handle_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Resource not found"}), 404
    return render_template("index.html"), 200


@app.errorhandler(413)
def handle_413(e):
    return jsonify({"error": "File exceeds maximum upload size (4 GB)"}), 413


@app.errorhandler(500)
def handle_500(e):
    return jsonify({"error": "Internal server error occurred"}), 500


def find_available_port(default_port: int = 5000) -> int:
    import socket
    for p in [default_port, 5001, 5002, 5003, 8080]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return default_port


if __name__ == "__main__":
    env_port = os.environ.get("PORT")
    env_host = os.environ.get("HOST", "127.0.0.1")
    port = int(env_port) if env_port else find_available_port(5000)
    use_prod = os.environ.get("PRODUCTION", "").lower() in ("1", "true", "yes")

    if use_prod:
        try:
            from waitress import serve
            print(f"\n  🚀 Production Server (Waitress WSGI) active at: http://{env_host}:{port}\n")
            serve(app, host=env_host, port=port, threads=8)
            sys.exit(0)
        except ImportError:
            pass

    print(f"\n  Universal Video Clipper running at: http://{env_host}:{port}\n")
    app.run(host=env_host, port=port, debug=False, threaded=True)
