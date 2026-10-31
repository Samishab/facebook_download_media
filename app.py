"""Facebook Media Downloader — desktop GUI (Tkinter + sv-ttk theme)."""

import json
import os
import queue
import re
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fbcore

try:
    import sv_ttk
except ImportError:  # theme is cosmetic; fall back to the default ttk look
    sv_ttk = None

SETTINGS = Path.home() / ".fbdl_settings.json"
FONT = ("Segoe UI", 10)

_AR_RUN = re.compile(r"[؀-ۿ][؀-ۿ ]*[؀-ۿ]|[؀-ۿ]")


def rtl(text: str) -> str:
    """Tk does no bidi reordering, so Arabic words would show last-to-first; reverse each Arabic run."""
    return _AR_RUN.sub(lambda m: " ".join(reversed(m.group(0).split(" "))), text)


ORDERS = {
    "orig": "Page order | ترتيب الصفحة",
    "desc": "Newest first | الأحدث أولاً",
    "asc": "Oldest first | الأقدم أولاً",
}

MODES = [
    ("videos", "🎬  Download all videos", "تحميل كل الفيديوهات"),
    ("offline", "🗂  Save page offline", "حفظ الصفحة للتصفح بدون نت"),
    ("both", "⚡  Both", "الاثنان معاً"),
]


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Facebook Media Downloader | محمّل وسائط فيسبوك")
        self.geometry("920x860")
        self.minsize(820, 760)
        self.q: queue.Queue = queue.Queue()
        self.stop_ev = threading.Event()
        self.last_index: Path | None = None

        s = self._load()
        self.url = tk.StringVar()
        self.cookies = tk.StringVar(value=s.get("cookies", ""))
        self.outdir = tk.StringVar(value=s.get("outdir", str(Path.home() / "Downloads" / "FacebookMedia")))
        self.scrolls = tk.IntVar(value=s.get("scrolls", 0))
        self.show_browser = tk.BooleanVar(value=s.get("show_browser", False))
        self.dark = tk.BooleanVar(value=s.get("dark", False))
        self.mode = tk.StringVar(value="videos")
        self.with_videos = tk.BooleanVar(value=True)
        self.with_dates = tk.BooleanVar(value=s.get("with_dates", True))
        self.make_pdf = tk.BooleanVar(value=s.get("make_pdf", False))
        self.order = tk.StringVar(value=rtl(ORDERS[s.get("order", "orig")]))
        self.status = tk.StringVar(value=rtl("Ready | جاهز"))

        if sv_ttk:
            sv_ttk.set_theme("dark" if self.dark.get() else "light")
        style = ttk.Style()
        style.configure(".", font=FONT)
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 18))
        style.configure("Sub.TLabel", foreground="#6b7280")
        style.configure("Status.TLabel", font=("Segoe UI Semibold", 10))

        self._build()
        self._fix_arabic(self)
        self._enable_clipboard_keys()
        self.after(100, self._pump)

    # ── UI ──
    def _build(self):
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Facebook Media Downloader", style="Title.TLabel").pack(side="left")
        ttk.Checkbutton(head, text="Dark | داكن", style="Switch.TCheckbutton" if sv_ttk else "TCheckbutton",
                        variable=self.dark, command=self._toggle_theme).pack(side="right")
        ttk.Label(root, text="Videos or a whole page for offline browsing | فيديوهات أو صفحة كاملة بدون إنترنت",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 12))

        # target
        card = ttk.LabelFrame(root, text="  Target | الهدف  ", padding=12)
        card.pack(fill="x", pady=(0, 10))
        card.columnconfigure(1, weight=1)
        self._row(card, 0, "Page / profile URL | الرابط", self.url,
                  ("Paste | لصق", lambda: self.url.set(self._clip())))
        self._row(card, 1, "cookies.txt", self.cookies, ("Browse…", self._pick_cookies))
        self._row(card, 2, "Save to | مجلد الحفظ", self.outdir, ("Browse…", self._pick_out))

        # task
        card = ttk.LabelFrame(root, text="  What to do | المهمة  ", padding=12)
        card.pack(fill="x", pady=(0, 10))
        for val, en, ar in MODES:
            ttk.Radiobutton(card, text=f"{en}   —   {ar}", variable=self.mode, value=val).pack(anchor="w", pady=3)
        ttk.Separator(card).pack(fill="x", pady=8)
        row = ttk.Frame(card)
        row.pack(fill="x")
        ttk.Label(row, text="Max scrolls (0 = whole page) | السكرول (0 = كاملة)").pack(side="left")
        ttk.Spinbox(row, from_=0, to=100000, width=7, textvariable=self.scrolls).pack(side="left", padx=8)
        row2 = ttk.Frame(card)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row2, text="Show browser | إظهار المتصفح", variable=self.show_browser).pack(side="left")
        ttk.Checkbutton(row2, text="Include videos in offline copy | الفيديوهات ضمن النسخة",
                        variable=self.with_videos).pack(side="left", padx=20)
        row3 = ttk.Frame(card)
        row3.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row3, text="Include post dates | تضمين تاريخ المنشور",
                        variable=self.with_dates).pack(side="left")
        ttk.Checkbutton(row3, text="Export PDF after saving | تصدير PDF بعد الحفظ",
                        variable=self.make_pdf).pack(side="left", padx=20)
        row4 = ttk.Frame(card)
        row4.pack(fill="x", pady=(8, 0))
        ttk.Label(row4, text="Order | الترتيب").pack(side="left")
        ttk.Combobox(row4, state="readonly", width=26, textvariable=self.order,
                     values=[rtl(v) for v in ORDERS.values()]).pack(side="left", padx=8)

        # actions
        btns = ttk.Frame(root)
        btns.pack(fill="x", pady=(2, 8))
        self.start_btn = ttk.Button(btns, text="▶  Start | ابدأ", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="left", ipadx=14, ipady=3)
        self.stop_btn = ttk.Button(btns, text="■  Stop | إيقاف", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8, ipady=3)
        self.open_btn = ttk.Button(btns, text="Open offline copy | فتح النسخة",
                                   command=self._open_offline, state="disabled")
        self.open_btn.pack(side="right", ipady=3)
        ttk.Button(btns, text="Open folder | فتح المجلد", command=self._open_folder).pack(side="right", padx=8, ipady=3)
        ttk.Button(btns, text="Export PDF… | تصدير PDF", command=self._export_pdf).pack(side="right", ipady=3)

        ttk.Label(root, textvariable=self.status, style="Status.TLabel").pack(anchor="w")
        self.bar = ttk.Progressbar(root, mode="determinate")
        self.bar.pack(fill="x", pady=(4, 8))

        logf = ttk.Frame(root)
        logf.pack(fill="both", expand=True)
        self.log_box = tk.Text(logf, height=10, wrap="word", state="disabled", font=("Consolas", 9),
                               relief="flat", padx=8, pady=6)
        sb = ttk.Scrollbar(logf, command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=sb.set)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _fix_arabic(self, widget):
        for w in widget.winfo_children():
            if isinstance(w, (ttk.Label, ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.LabelFrame)):
                w.configure(text=rtl(str(w.cget("text"))))
            self._fix_arabic(w)

    def _row(self, parent, r, label, var, button):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=5)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text=button[0], command=button[1]).grid(row=r, column=2, padx=(8, 0), pady=5)

    def _toggle_theme(self):
        if sv_ttk:
            sv_ttk.set_theme("dark" if self.dark.get() else "light")
        self._save()

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
                "cookies": self.cookies.get(), "outdir": self.outdir.get(), "scrolls": self.scrolls.get(),
                "show_browser": self.show_browser.get(), "dark": self.dark.get(),
                "with_dates": self.with_dates.get(), "make_pdf": self.make_pdf.get(),
                "order": self._order_key()}), encoding="utf-8")
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
                    self.log_box.insert("end", rtl(val) + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                    self.status.set(rtl(val[:110]))
                elif kind == "progress":
                    done, total = val
                    if total:
                        self.bar.stop()
                        self.bar.configure(mode="determinate", maximum=total, value=done)
                    elif str(self.bar.cget("mode")) != "indeterminate":  # unlimited scroll: busy bar
                        self.bar.configure(mode="indeterminate")
                        self.bar.start(15)
                elif kind == "finished":
                    self.bar.stop()
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
                    scrolls=self.scrolls.get(), with_videos=self.with_videos.get(),
                    with_dates=self.with_dates.get(), make_pdf=self.make_pdf.get(), order=self._order_key())
        threading.Thread(target=self._run, args=(url, cookies, opts), daemon=True).start()

    def _order_key(self) -> str:
        shown = self.order.get()
        return next((k for k, v in ORDERS.items() if rtl(v) == shown), "orig")

    def _export_pdf(self):
        """Turn an already-saved offline copy into a PDF (uses the selected order)."""
        start = self.last_index.parent if self.last_index else Path(self.outdir.get()) / "offline"
        path = filedialog.askopenfilename(title="Choose the saved index.html", initialdir=str(start),
                                          filetypes=[("Saved page", "index.html"), ("HTML", "*.html")])
        if not path:
            return
        order = self._order_key()

        def work():
            try:
                fbcore.export_pdf(path, order=None if order == "orig" else order, log=self._log)
            except Exception as e:
                self._log(f"ERROR: {e}")
            finally:
                self.q.put(("finished", None))

        self.start_btn.configure(state="disabled")
        threading.Thread(target=work, daemon=True).start()

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
                    with_videos=o["with_videos"] and o["mode"] == "offline", with_dates=o["with_dates"],
                    order=o["order"], make_pdf=o["make_pdf"], **kw)
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
