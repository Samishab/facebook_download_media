"""Facebook Media Downloader — desktop GUI (Tkinter, no extra dependencies)."""

import json
import os
import queue
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fbcore

SETTINGS = Path.home() / ".fbdl_settings.json"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Facebook Media Downloader | محمّل وسائط فيسبوك")
        self.geometry("820x640")
        self.minsize(700, 540)
        self.q: queue.Queue = queue.Queue()
        self.stop_ev = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_index: Path | None = None

        s = self._load()
        self.url = tk.StringVar()
        self.cookies = tk.StringVar(value=s.get("cookies", ""))
        self.outdir = tk.StringVar(value=s.get("outdir", str(Path.cwd() / "downloads")))
        self.scrolls = tk.IntVar(value=s.get("scrolls", 30))
        self.show_browser = tk.BooleanVar(value=s.get("show_browser", False))
        self.mode = tk.StringVar(value="videos")
        self.with_videos = tk.BooleanVar(value=True)

        self._build()
        self._enable_clipboard_keys()
        self.after(100, self._pump)

    # ── UI ──
    def _build(self):
        pad = {"padx": 10, "pady": 4}
        top = ttk.LabelFrame(self, text="Target | الهدف")
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Profile / Page URL | الرابط").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(top, textvariable=self.url).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Paste | لصق", command=lambda: self.url.set(self._clip())).grid(row=0, column=2, padx=6)

        ttk.Label(top, text="cookies.txt").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(top, textvariable=self.cookies).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse…", command=self._pick_cookies).grid(row=1, column=2, padx=6)

        ttk.Label(top, text="Output folder | المجلد").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(top, textvariable=self.outdir).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Browse…", command=self._pick_out).grid(row=2, column=2, padx=6)

        opt = ttk.LabelFrame(self, text="What to do | المهمة")
        opt.pack(fill="x", **pad)
        ttk.Radiobutton(opt, text="Download all videos | تحميل كل الفيديوهات",
                        variable=self.mode, value="videos").pack(anchor="w", padx=8, pady=2)
        ttk.Radiobutton(opt, text="Save page offline (browse without internet) | حفظ الصفحة للتصفح بدون نت",
                        variable=self.mode, value="offline").pack(anchor="w", padx=8, pady=2)
        ttk.Radiobutton(opt, text="Both | الاثنان معاً",
                        variable=self.mode, value="both").pack(anchor="w", padx=8, pady=2)

        row = ttk.Frame(opt)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Max scrolls | عدد السكرول").pack(side="left")
        ttk.Spinbox(row, from_=1, to=1000, width=6, textvariable=self.scrolls).pack(side="left", padx=6)
        ttk.Checkbutton(row, text="Show browser | إظهار المتصفح",
                        variable=self.show_browser).pack(side="left", padx=12)
        ttk.Checkbutton(row, text="Include videos in offline copy | الفيديوهات ضمن النسخة",
                        variable=self.with_videos).pack(side="left", padx=12)

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="▶ Start | ابدأ", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="■ Stop | إيقاف", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(btns, text="Open offline copy | فتح النسخة",
                                   command=self._open_offline, state="disabled")
        self.open_btn.pack(side="right")
        ttk.Button(btns, text="Open folder | فتح المجلد", command=self._open_folder).pack(side="right", padx=6)

        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill="x", **pad)

        logf = ttk.Frame(self)
        logf.pack(fill="both", expand=True, **pad)
        self.log_box = tk.Text(logf, height=14, wrap="word", state="disabled", font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ── clipboard (Ctrl+V/C/X fail in Tk when an Arabic layout is active, so match by keycode) ──
    def _clip(self) -> str:
        try:
            return self.clipboard_get().strip()
        except tk.TclError:
            return ""

    def _enable_clipboard_keys(self):
        def on_key(e):
            if not (e.state & 0x4):  # Ctrl not held
                return
            ev = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 65: "select"}.get(e.keycode)
            if ev == "select" and isinstance(e.widget, ttk.Entry):
                e.widget.select_range(0, "end")
                return "break"
            if ev and ev != "select":
                e.widget.event_generate(ev)
                return "break"

        def popup(e):
            m = tk.Menu(self, tearoff=0)
            for label, ev in (("Cut", "<<Cut>>"), ("Copy", "<<Copy>>"), ("Paste | لصق", "<<Paste>>")):
                m.add_command(label=label, command=lambda ev=ev: e.widget.event_generate(ev))
            m.tk_popup(e.x_root, e.y_root)

        self.bind_class("TEntry", "<Key>", on_key, add="+")
        self.bind_class("TEntry", "<Button-3>", popup, add="+")

    # ── helpers ──
    @staticmethod
    def _load() -> dict:
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        try:
            SETTINGS.write_text(json.dumps({
                "cookies": self.cookies.get(), "outdir": self.outdir.get(),
                "scrolls": self.scrolls.get(), "show_browser": self.show_browser.get()}),
                encoding="utf-8")
        except Exception:
            pass

    def _pick_cookies(self):
        p = filedialog.askopenfilename(filetypes=[("Cookies", "*.txt"), ("All", "*.*")])
        if p:
            self.cookies.set(p)

    def _pick_out(self):
        p = filedialog.askdirectory()
        if p:
            self.outdir.set(p)

    def _open_folder(self):
        p = Path(self.outdir.get())
        p.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(p)
        else:
            webbrowser.open(p.resolve().as_uri())

    def _open_offline(self):
        if self.last_index and self.last_index.exists():
            webbrowser.open(self.last_index.resolve().as_uri())

    def _log(self, msg):
        self.q.put(("log", str(msg)))

    def _progress(self, done, total):
        self.q.put(("progress", (done, total)))

    def _pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", val + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "progress":
                    done, total = val
                    self.bar.configure(maximum=max(total, 1), value=done)
                elif kind == "finished":
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    if self.last_index:
                        self.open_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._pump)

    # ── run ──
    def _start(self):
        url = self.url.get().strip()
        if not url.startswith("http"):
            messagebox.showwarning("URL", "Enter a valid Facebook URL.\nأدخل رابط فيسبوك صحيح.")
            return
        cookies = self.cookies.get().strip() or None
        if cookies and not Path(cookies).exists():
            messagebox.showwarning("cookies", "cookies file not found.")
            return
        self._save()
        self.stop_ev.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        # read Tk variables here: they must not be touched from the worker thread
        opts = dict(out=self.outdir.get(), mode=self.mode.get(), headless=not self.show_browser.get(),
                    scrolls=self.scrolls.get(), with_videos=self.with_videos.get())
        self.worker = threading.Thread(target=self._run, args=(url, cookies, opts), daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_ev.set()
        self._log("Stopping… | جاري الإيقاف")

    def _run(self, url, cookies, o):
        kw = dict(log=self._log, progress=self._progress, stop=self.stop_ev)
        try:
            fbcore.ensure_yt_dlp(self._log)
            if o["mode"] in ("offline", "both"):
                self._log("── Offline copy | نسخة أوفلاين ──")
                self.last_index = fbcore.archive_page(
                    url, Path(o["out"]) / "offline", cookies, o["scrolls"], o["headless"],
                    with_videos=o["with_videos"] and o["mode"] == "offline", **kw)
            if o["mode"] in ("videos", "both") and not self.stop_ev.is_set():
                self._log("── Videos | الفيديوهات ──")
                urls = fbcore.scrape_video_urls(url, cookies, o["scrolls"], o["headless"], **kw)
                if urls:
                    fbcore.download_videos(urls, Path(o["out"]) / "videos", cookies, **kw)
                else:
                    self._log("No videos found (private page or invalid cookies).")
            self._log("Finished ✔ | تم")
        except Exception as e:
            self._log(f"ERROR: {e}")
        finally:
            self.q.put(("finished", None))


if __name__ == "__main__":
    App().mainloop()
