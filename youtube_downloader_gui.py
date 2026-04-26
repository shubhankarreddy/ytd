from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from yt_dlp import YoutubeDL

from youtube_downloader import (
    DEFAULT_OUTPUT_DIR,
    build_video_format_selector,
    extract_info,
    first_video_entry,
    format_bytes,
    format_eta,
    has_ffmpeg,
    list_available_heights,
    run_ydl_with_cookie_fallback,
    should_download_playlist,
    validate_url,
)


class UILogger:
    def __init__(self, event_queue: queue.Queue[tuple[str, Any]]) -> None:
        self.event_queue = event_queue

    def debug(self, msg: str) -> None:
        if "Extracting cookies from" in msg:
            return
        self.event_queue.put(("log", msg))

    def warning(self, msg: str) -> None:
        if "Could not copy Chrome cookie database" in msg:
            return
        self.event_queue.put(("log", f"WARNING: {msg}"))

    def error(self, msg: str) -> None:
        if "Could not copy Chrome cookie database" in msg:
            return
        self.event_queue.put(("log", f"ERROR: {msg}"))


class DownloadCancelled(Exception):
    pass


def resolve_resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", str(Path(__file__).resolve().parent))
    return str(Path(base_path) / relative_path)


class YouTubeDownloaderGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("YouTube Media Downloader")
        self.geometry("1024x768")
        self.minsize(980, 700)

        try:
            self.iconbitmap(resolve_resource_path("assets/app.ico"))
        except Exception:
            pass

        self.url_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.quality_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.item_var = tk.StringVar(value="Item: -")
        self.ffmpeg_var = tk.StringVar(value="FFmpeg: checking...")

        self.event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.quality_map: dict[str, tuple[str, bool]] = {}
        self.cached_url = ""
        self.is_busy = False
        self.cancel_requested = threading.Event()
        self.active_mode = "idle"

        self._apply_styles()
        self._build_ui()
        self._refresh_ffmpeg_status(log=False)
        self.after(100, self._process_events)

    def _apply_styles(self) -> None:
        self.configure(bg="#ece9e4")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background="#ece9e4", font=("Segoe UI", 10))
        style.configure("TFrame", background="#ece9e4")
        style.configure("TLabelframe", background="#ece9e4")
        style.configure("TLabelframe.Label", background="#ece9e4", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background="#ece9e4")
        style.configure("TButton", padding=(10, 6))
        style.configure("TEntry", fieldbackground="#ffffff")
        style.configure("TCombobox", fieldbackground="#ffffff")
        style.configure("Horizontal.TProgressbar", thickness=18)
        style.configure("Header.TLabel", background="#ece9e4", font=("Segoe UI", 22, "bold"))
        style.configure("Subtle.TLabel", background="#ece9e4", foreground="#555555")
        style.configure("Ok.TLabel", background="#ece9e4", foreground="#116611")
        style.configure("Warn.TLabel", background="#ece9e4", foreground="#a15c00")

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        title = ttk.Label(container, text="YouTube Media Downloader", style="Header.TLabel")
        title.pack(anchor="w")
        subtitle = ttk.Label(
            container,
            text="Download videos or audio from a YouTube video or playlist URL.",
            style="Subtle.TLabel",
        )
        subtitle.pack(anchor="w", pady=(0, 12))

        settings = ttk.LabelFrame(container, text="Download Settings", padding=14)
        settings.pack(fill="x")

        ttk.Label(settings, text="Video or playlist URL").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=8)
        self.url_entry = ttk.Entry(settings, textvariable=self.url_var, width=84)
        self.url_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=8)

        ttk.Label(settings, text="Quality / mode").grid(row=1, column=0, sticky="w", padx=(0, 12), pady=8)
        self.quality_combo = ttk.Combobox(
            settings,
            textvariable=self.quality_var,
            state="readonly",
            values=[],
            width=52,
        )
        self.quality_combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=8)

        self.fetch_button = ttk.Button(settings, text="Fetch Qualities", command=self.on_fetch_qualities)
        self.fetch_button.grid(row=1, column=3, sticky="e", padx=(8, 0), pady=8)

        self.check_ffmpeg_button = ttk.Button(
            settings,
            text="Check FFmpeg",
            command=self.on_check_ffmpeg,
            width=14,
        )
        self.check_ffmpeg_button.grid(row=3, column=3, sticky="e", pady=(8, 2))

        self.ffmpeg_label = ttk.Label(settings, textvariable=self.ffmpeg_var, style="Subtle.TLabel")
        self.ffmpeg_label.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(settings, text="Output folder").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=8)
        self.output_entry = ttk.Entry(settings, textvariable=self.output_var, width=70)
        self.output_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)

        self.browse_button = ttk.Button(settings, text="Browse", command=self.on_browse)
        self.browse_button.grid(row=2, column=3, sticky="e", pady=8)

        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(2, weight=1)

        action_row = ttk.Frame(container)
        action_row.pack(fill="x", pady=(12, 8))

        self.start_button = ttk.Button(action_row, text="Start Download", command=self.on_start_download, width=16)
        self.start_button.pack(side="left")

        self.open_folder_button = ttk.Button(action_row, text="Open Folder", command=self.on_open_folder, width=12)
        self.open_folder_button.pack(side="left", padx=8)

        self.clear_log_button = ttk.Button(action_row, text="Clear Log", command=self.on_clear_log, width=10)
        self.clear_log_button.pack(side="left")

        self.cancel_button = ttk.Button(
            action_row,
            text="Cancel",
            command=self.on_cancel_download,
            state="disabled",
            width=9,
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(action_row, textvariable=self.status_var, foreground="#0b5ed7")
        self.status_label.pack(side="right")

        self.item_label = ttk.Label(action_row, textvariable=self.item_var, foreground="#333333")
        self.item_label.pack(side="right", padx=(0, 14))

        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill="x", pady=(0, 10))
        self.progress = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate", maximum=100)
        self.progress.pack(fill="x")

        log_frame = ttk.LabelFrame(container, text="Log", padding=10)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="word", height=18)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_text.configure(bg="#ffffff", fg="#111111", insertbackground="#111111", borderwidth=1)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _set_busy(self, busy: bool) -> None:
        self.is_busy = busy
        entry_state = "disabled" if busy else "normal"
        control_state = "disabled" if busy else "normal"

        self.url_entry.configure(state=entry_state)
        self.output_entry.configure(state=entry_state)
        self.fetch_button.configure(state=control_state)
        self.start_button.configure(state=control_state)
        self.browse_button.configure(state=control_state)
        self.check_ffmpeg_button.configure(state=control_state)

        if busy and self.active_mode == "download":
            self.cancel_button.configure(state="normal")
        else:
            self.cancel_button.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")

    def _process_events(self) -> None:
        try:
            while True:
                event, payload = self.event_queue.get_nowait()
                if event == "log":
                    self._log(str(payload))
                elif event == "status":
                    self.status_var.set(str(payload))
                elif event == "progress":
                    info = payload
                    self.progress["value"] = float(info.get("percent", 0.0))
                    self.status_var.set(str(info.get("status_text", "Downloading...")))
                    self.item_var.set(str(info.get("item_text", "Item: -")))
                elif event == "formats_ready":
                    data = payload
                    labels = data["labels"]
                    self.quality_map = data["quality_map"]
                    self.quality_combo.configure(values=labels)
                    if labels:
                        self.quality_combo.current(0)
                    self.cached_url = data["url"]
                    self._log(data["summary"])
                    self.status_var.set("Qualities loaded")
                    self.progress["value"] = 0
                    self.active_mode = "idle"
                    self._set_busy(False)
                elif event == "done":
                    self.progress["value"] = 100
                    self.status_var.set("Completed")
                    self.item_var.set("Item: done")
                    self._log("Task completed.")
                    self.active_mode = "idle"
                    self.cancel_requested.clear()
                    self._set_busy(False)
                elif event == "cancelled":
                    self.status_var.set("Cancelled")
                    self.item_var.set("Item: cancelled")
                    self._log("Download cancelled by user.")
                    self.active_mode = "idle"
                    self.cancel_requested.clear()
                    self._set_busy(False)
                elif event == "error":
                    self.status_var.set("Failed")
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Download Error", str(payload))
                    self.active_mode = "idle"
                    self.cancel_requested.clear()
                    self._set_busy(False)
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_events)

    def on_browse(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_OUTPUT_DIR))
        if folder:
            self.output_var.set(folder)

    def on_open_folder(self) -> None:
        output_path = Path(self.output_var.get()).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(output_path))

    def on_clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def _refresh_ffmpeg_status(self, *, log: bool) -> None:
        if has_ffmpeg():
            self.ffmpeg_var.set("FFmpeg: detected (high quality merge enabled)")
            self.ffmpeg_label.configure(style="Ok.TLabel")
            if log:
                self._log("FFmpeg detected. Merge and audio conversion features are available.")
        else:
            self.ffmpeg_var.set("FFmpeg: not found (single-file progressive mode)")
            self.ffmpeg_label.configure(style="Warn.TLabel")
            if log:
                self._log("FFmpeg not found. Using progressive single-file video and no merge conversion.")

    def on_check_ffmpeg(self) -> None:
        self._refresh_ffmpeg_status(log=True)

    def on_cancel_download(self) -> None:
        if not self.is_busy or self.active_mode != "download":
            return
        self.cancel_requested.set()
        self.status_var.set("Cancelling...")
        self.item_var.set("Item: stopping")
        self._log("Cancellation requested. Stopping after current transfer step...")
        self.cancel_button.configure(state="disabled")

    def on_fetch_qualities(self) -> None:
        if self.is_busy:
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Missing URL", "Please paste a YouTube video or playlist URL.")
            return

        self._set_busy(True)
        self.active_mode = "fetch"
        self.status_var.set("Fetching qualities...")
        self.item_var.set("Item: -")
        self.progress["value"] = 0
        self._log("Inspecting URL and loading available qualities...")

        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, raw_url: str) -> None:
        try:
            url = validate_url(raw_url)
            info = extract_info(url)
            probe_video = first_video_entry(info)
            heights = list_available_heights(probe_video)

            labels: list[str] = []
            quality_map: dict[str, tuple[str, bool]] = {}

            for h in sorted(heights, reverse=True):
                label = f"Video up to {h}p"
                fmt = build_video_format_selector(h)
                labels.append(label)
                quality_map[label] = (fmt, False)

            audio_label = "Audio only (best quality MP3)"
            labels.append(audio_label)
            quality_map[audio_label] = ("bestaudio/best", True)

            if info.get("_type") == "playlist":
                title = info.get("title") or "Untitled Playlist"
                count = len([entry for entry in (info.get("entries") or []) if entry])
                summary = f"Playlist detected: {title} ({count} items). {len(labels)} options loaded."
            else:
                title = info.get("title") or "Untitled Video"
                summary = f"Video detected: {title}. {len(labels)} options loaded."

            if not has_ffmpeg():
                summary += " ffmpeg not found, so progressive single-file formats will be used."

            self.event_queue.put(
                (
                    "formats_ready",
                    {
                        "labels": labels,
                        "quality_map": quality_map,
                        "summary": summary,
                        "url": url,
                    },
                )
            )
        except Exception as error:
            self.event_queue.put(("error", str(error)))

    def on_start_download(self) -> None:
        if self.is_busy:
            return

        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Missing URL", "Please paste a YouTube video or playlist URL.")
            return

        selected = self.quality_var.get().strip()
        if not selected or selected not in self.quality_map:
            messagebox.showerror("Missing quality", "Please click 'Fetch Qualities' and select an option.")
            return

        output_path = Path(self.output_var.get().strip() or str(DEFAULT_OUTPUT_DIR)).expanduser()
        fmt, audio_only = self.quality_map[selected]

        self._set_busy(True)
        self.active_mode = "download"
        self.cancel_requested.clear()
        self.status_var.set("Downloading...")
        self.item_var.set("Item: preparing")
        self.progress["value"] = 0
        self._log(f"Selected mode: {selected}")
        self._log(f"Downloading to: {output_path}")

        threading.Thread(
            target=self._download_worker,
            args=(validate_url(url), output_path, fmt, audio_only),
            daemon=True,
        ).start()

    def _download_worker(self, url: str, output_dir: Path, format_selector: str, audio_only: bool) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        logger = UILogger(self.event_queue)

        def progress_hook(payload: dict[str, Any]) -> None:
            if self.cancel_requested.is_set():
                raise DownloadCancelled("Cancelled by user")

            status = payload.get("status")
            info = payload.get("info_dict") or {}

            if status == "downloading":
                downloaded = payload.get("downloaded_bytes")
                total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
                speed = payload.get("speed")
                eta = payload.get("eta")

                percent = 0.0
                if isinstance(downloaded, (int, float)) and isinstance(total, (int, float)) and total > 0:
                    percent = min(100.0, (float(downloaded) / float(total)) * 100.0)

                playlist_index = info.get("playlist_index")
                playlist_count = (
                    info.get("n_entries")
                    or info.get("playlist_count")
                    or info.get("playlist_size")
                )

                if isinstance(playlist_index, int) and isinstance(playlist_count, int) and playlist_count > 0:
                    item_text = f"Item: {playlist_index}/{playlist_count}"
                else:
                    item_text = "Item: single video"

                status_text = (
                    f"{percent:5.1f}% | {format_bytes(downloaded)}/{format_bytes(total)} "
                    f"| {format_bytes(speed)}/s | ETA {format_eta(eta)}"
                )

                self.event_queue.put(
                    (
                        "progress",
                        {
                            "percent": percent,
                            "status_text": status_text,
                            "item_text": item_text,
                        },
                    )
                )

            elif status == "finished":
                filename = str(payload.get("filename") or "file")
                self.event_queue.put(("log", f"Saved: {filename}"))

        opts: dict[str, Any] = {
            "format": format_selector,
            "outtmpl": str(output_dir / "%(playlist_title|NA_playlist)s" / "%(title)s [%(id)s].%(ext)s"),
            "noplaylist": not should_download_playlist(url),
            "ignoreerrors": True,
            "retries": 3,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": logger,
            "progress_hooks": [progress_hook],
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

        try:
            run_ydl_with_cookie_fallback(opts, lambda ydl: ydl.download([url]))
        except DownloadCancelled:
            self.event_queue.put(("cancelled", None))
            return
        except Exception as error:
            if self.cancel_requested.is_set():
                self.event_queue.put(("cancelled", None))
                return
            self.event_queue.put(("error", str(error)))
            return

        if self.cancel_requested.is_set():
            self.event_queue.put(("cancelled", None))
            return

        self.event_queue.put(("done", None))


def main() -> None:
    app = YouTubeDownloaderGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
