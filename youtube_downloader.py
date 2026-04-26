from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL


SUPPORTED_HOSTS = ("youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com")
DEFAULT_OUTPUT_DIR = Path.home() / "Downloads" / "youtube_downloads"
COOKIE_BROWSER_SOURCES = ("chrome", "edge", "firefox")


class SilentYDLLogger:
    def debug(self, msg: str) -> None:
        _ = msg

    def warning(self, msg: str) -> None:
        _ = msg

    def error(self, msg: str) -> None:
        _ = msg


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def build_video_format_selector(max_height: int) -> str:
    if has_ffmpeg():
        return f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]"
    # Without ffmpeg, pick a progressive stream (video+audio in one file).
    return f"best[height<={max_height}][vcodec!=none][acodec!=none]/best[height<={max_height}]/best"


def should_download_playlist(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    query = parse_qs(parsed.query)

    if path.startswith("/playlist"):
        return True

    has_list = bool(query.get("list"))
    has_watch_video = bool(query.get("v"))

    # For watch URLs (often with list/radio params), default to single video.
    if has_watch_video:
        return False

    # For short links like youtu.be/<id>, treat as single video.
    if "youtu.be" in parsed.netloc.lower() and parsed.path.strip("/"):
        return False

    return has_list


def run_ydl_with_cookie_fallback(
    base_opts: dict[str, Any],
    action: Callable[[YoutubeDL], Any],
) -> Any:
    last_error: Exception | None = None

    for browser in COOKIE_BROWSER_SOURCES:
        opts = dict(base_opts)
        opts["cookiesfrombrowser"] = (browser,)
        try:
            with YoutubeDL(opts) as ydl:
                return action(ydl)
        except Exception as error:
            last_error = error

    try:
        with YoutubeDL(dict(base_opts)) as ydl:
            return action(ydl)
    except Exception as error:
        last_error = error

    raise SystemExit(f"YouTube operation failed after cookie fallback attempts: {last_error}")


def format_bytes(num_bytes: float | int | None) -> str:
    if num_bytes is None:
        return "?B"

    value = float(num_bytes)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    return f"{value:.1f}{units[unit_index]}"


def format_eta(seconds: float | int | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


class DownloadStatusBar:
    def __init__(self, compact: bool = False) -> None:
        self._last_len = 0
        self.compact = compact
        self.use_color = self._supports_color()

    def _supports_color(self) -> bool:
        if os.environ.get("NO_COLOR"):
            return False
        return sys.stdout.isatty()

    def _color(self, text: str, code: str) -> str:
        if not self.use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def _print_line(self, line: str, *, end: str = "") -> None:
        padded = line
        if len(line) < self._last_len:
            padded = line + (" " * (self._last_len - len(line)))
        self._last_len = len(line)
        sys.stdout.write("\r" + padded + end)
        sys.stdout.flush()

    def hook(self, payload: dict[str, Any]) -> None:
        status = payload.get("status")
        if status == "downloading":
            downloaded = payload.get("downloaded_bytes")
            total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
            speed = payload.get("speed")
            eta = payload.get("eta")
            info = payload.get("info_dict") or {}

            playlist_index = info.get("playlist_index")
            playlist_count = (
                info.get("n_entries")
                or info.get("playlist_count")
                or info.get("playlist_size")
            )

            pct = 0.0
            if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total > 0:
                pct = min(100.0, (float(downloaded) / float(total)) * 100)

            bar_width = 14 if self.compact else 26
            filled = int((pct / 100) * bar_width)
            bar = "#" * filled + "-" * (bar_width - filled)
            bar = self._color(bar, "36")

            title = str(info.get("title") or "")
            max_title_len = 24 if self.compact else 42
            if len(title) > max_title_len:
                title = title[: max_title_len - 3] + "..."

            item_prefix = ""
            if isinstance(playlist_index, int) and isinstance(playlist_count, int) and playlist_count > 0:
                item_prefix = f"[{playlist_index}/{playlist_count}] "

            pct_text = self._color(f"{pct:5.1f}%", "32")

            if self.compact:
                line = f"{item_prefix}[{bar}] {pct_text} ETA {format_eta(eta)}"
            else:
                line = (
                    f"{item_prefix}[{bar}] {pct_text} "
                    f"{format_bytes(downloaded)}/{format_bytes(total)} "
                    f"{format_bytes(speed)}/s ETA {format_eta(eta)}"
                )
            if title:
                line += f" | {title}"
            self._print_line(line)

        elif status == "finished":
            filename = str(payload.get("filename") or "File")
            done_bar = self._color("##########################", "36")
            done_pct = self._color("100.0%", "32")
            self._print_line(f"[{done_bar}] {done_pct} Saved: {filename}", end="\n")
            self._last_len = 0



def validate_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise SystemExit("A YouTube URL is required.")

    lowered = raw.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        raise SystemExit("Please provide a full URL starting with http:// or https://")

    if not any(host in lowered for host in SUPPORTED_HOSTS):
        raise SystemExit("URL must be a YouTube video or playlist URL.")

    return raw


def build_ydl_base_opts(url: str) -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": not should_download_playlist(url),
        "logger": SilentYDLLogger(),
    }


def extract_info(url: str) -> dict[str, Any]:
    opts = build_ydl_base_opts(url)
    return run_ydl_with_cookie_fallback(opts, lambda ydl: ydl.extract_info(url, download=False))


def first_video_entry(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") != "playlist":
        return info

    entries = info.get("entries") or []
    for entry in entries:
        if entry:
            return entry

    raise SystemExit("Playlist has no downloadable videos.")


def list_available_heights(video_info: dict[str, Any]) -> list[int]:
    heights: set[int] = set()
    for fmt in video_info.get("formats") or []:
        if fmt.get("vcodec") == "none":
            continue

        height = fmt.get("height")
        if isinstance(height, int) and height > 0:
            heights.add(height)

    return sorted(heights)


def choose_download_mode(heights: list[int]) -> tuple[str, str, bool]:
    print("\nAvailable options:")

    options: list[tuple[str, str, bool]] = []
    index = 1

    for h in sorted(heights, reverse=True):
        label = f"Video up to {h}p"
        fmt = build_video_format_selector(h)
        options.append((label, fmt, False))
        print(f"  {index}. {label}")
        index += 1

    options.append(("Audio only (best quality)", "bestaudio/best", True))
    print(f"  {index}. Audio only (best quality)")

    while True:
        choice_raw = input("Select an option number: ").strip()
        try:
            choice = int(choice_raw)
        except ValueError:
            print("Please enter a valid number.")
            continue

        if 1 <= choice <= len(options):
            label, fmt, audio_only = options[choice - 1]
            return label, fmt, audio_only

        print("Choice out of range.")


def build_download_opts(
    url: str,
    output_dir: Path,
    format_selector: str,
    audio_only: bool,
    status_bar: DownloadStatusBar,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": format_selector,
        "outtmpl": str(output_dir / "%(playlist_title|NA_playlist)s" / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": not should_download_playlist(url),
        "ignoreerrors": True,
        "retries": 3,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [status_bar.hook],
        "logger": SilentYDLLogger(),
    }
    if audio_only:
        if has_ffmpeg():
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
    elif has_ffmpeg():
        opts["merge_output_format"] = "mp4"
    return opts


def download_url(
    url: str,
    output_dir: Path,
    format_selector: str,
    audio_only: bool,
    compact_progress: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    status_bar = DownloadStatusBar(compact=compact_progress)
    opts = build_download_opts(url, output_dir, format_selector, audio_only, status_bar)
    run_ydl_with_cookie_fallback(opts, lambda ydl: ydl.download([url]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download YouTube video(s) from a video or playlist URL with quality selection.",
    )
    parser.add_argument("url", nargs="?", default=None, help="YouTube video or playlist URL")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Base folder for downloads (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Exit immediately after completion without waiting for Enter",
    )
    parser.add_argument(
        "--compact-progress",
        action="store_true",
        help="Use a compact one-line progress view for smaller terminals",
    )
    args = parser.parse_args()

    url = validate_url(args.url or input("Paste YouTube video or playlist URL: "))
    output_dir = Path(args.output_dir).expanduser()

    print("Inspecting available formats...")
    info = extract_info(url)
    probe_video = first_video_entry(info)
    heights = list_available_heights(probe_video)
    if not has_ffmpeg():
        print("Note: ffmpeg not found. Using single-file progressive formats and skipping merge.")
    if not heights:
        print("No video qualities found. Falling back to audio-only mode.")
        selected_label, fmt, audio_only = ("Audio only (best quality)", "bestaudio/best", True)
    else:
        selected_label, fmt, audio_only = choose_download_mode(heights)

    if info.get("_type") == "playlist":
        title = info.get("title") or "Untitled Playlist"
        count = len([entry for entry in (info.get("entries") or []) if entry])
        print(f"\nPlaylist detected: {title} ({count} items)")
    else:
        title = info.get("title") or "Untitled Video"
        print(f"\nVideo detected: {title}")

    print(f"Selected mode: {selected_label}")
    print(f"Downloading to: {output_dir}")

    download_url(url, output_dir, fmt, audio_only, args.compact_progress)

    print("\nTask completed.")
    if not args.no_pause:
        try:
            input("Press Enter to exit...")
        except EOFError:
            pass


if __name__ == "__main__":
    main()
