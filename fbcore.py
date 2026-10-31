"""
Core logic: scraping, video downloading and offline page archiving.
Every long-running function takes a `log(msg)` callback, an optional
`progress(done, total)` callback and a `stop` threading.Event so that
both the CLI and the GUI can drive it.
"""

import hashlib
import itertools
import html as htmllib
import mimetypes
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIDEO_HINTS = ("/videos/", "watch?v=", "watch/?v=", "/reel/")


PATIENCE = 6  # consecutive scrolls without anything new before we call it the end


def _scrolls(max_scrolls):
    """max_scrolls <= 0 means: keep going until the page stops yielding new items."""
    return range(max_scrolls) if max_scrolls > 0 else itertools.count()


def _noop(*_a, **_k):
    pass


# ── dependency helpers ─────────────────────────────────────────────────────────

FROZEN = getattr(sys, "frozen", False)  # running from the PyInstaller build


def ensure_yt_dlp(log=print):
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        if FROZEN:
            raise
        log("Installing yt-dlp...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)


def ensure_playwright(log=print):
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        if FROZEN:
            raise
        log("Installing playwright...")
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
    if not FROZEN:  # the installed app uses the Edge/Chrome already on the machine
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)


def _find_ffmpeg() -> str | None:
    import shutil
    local = Path(sys.executable if FROZEN else __file__).resolve().parent / "ffmpeg.exe"
    return str(local) if local.exists() else shutil.which("ffmpeg")


def _launch(pw, headless: bool):
    """Bundled Chromium when present, otherwise the system Edge/Chrome."""
    tries = ([{}] if not FROZEN else []) + [{"channel": "msedge"}, {"channel": "chrome"}]
    err = None
    for kw in tries:
        try:
            return pw.chromium.launch(headless=headless, **kw)
        except Exception as e:
            err = e
    raise RuntimeError(f"No browser available — install Microsoft Edge or Google Chrome. ({err})")


# ── cookies ────────────────────────────────────────────────────────────────────

def load_netscape_cookies(cookies_file: str) -> list[dict]:
    cookies = []
    with open(cookies_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#HttpOnly_"):
                line = line[len("#HttpOnly_"):]
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path, secure, expires, name, value = parts[:7]
            cookie: dict = {
                "domain": domain, "path": path, "name": name, "value": value,
                "secure": secure.upper() == "TRUE",
            }
            if expires.lstrip("-").isdigit() and int(expires) > 0:
                cookie["expires"] = int(expires)
            cookies.append(cookie)
    return cookies


# ── browser session ────────────────────────────────────────────────────────────

class BrowserSession:
    """Context manager yielding a Playwright page with the user's cookies loaded."""

    def __init__(self, cookies_file, headless=True, log=print):
        self.cookies_file, self.headless, self.log = cookies_file, headless, log

    def __enter__(self):
        ensure_playwright(self.log)
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = _launch(self._pw, self.headless)
        self.context = self.browser.new_context(user_agent=USER_AGENT, locale="en-US",
                                                viewport={"width": 1280, "height": 900})
        if self.cookies_file:
            try:
                cookies = load_netscape_cookies(self.cookies_file)
                self.context.add_cookies(cookies)
                self.log(f"Loaded {len(cookies)} cookies")
            except Exception as e:
                self.log(f"Warning: could not load cookies — {e}")
        self.page = self.context.new_page()
        return self

    def __exit__(self, *exc):
        try:
            self.browser.close()
        finally:
            self._pw.stop()


# ── URL helpers ────────────────────────────────────────────────────────────────

def build_videos_url(profile_url: str) -> str:
    url = profile_url.rstrip("/")
    if "profile.php" in url:
        if "sk=videos" not in url:
            url += ("&" if "?" in url else "?") + "sk=videos"
    elif "/videos" not in url:
        url += "/videos"
    return url


def _slug(url: str) -> str:
    p = urlparse(url)
    m = re.search(r"id=(\d+)", p.query)
    name = m.group(1) if m else (p.path.strip("/").split("/")[0] or p.netloc)
    return re.sub(r"[^\w.-]+", "_", name) or "page"


# ── video scraping + downloading ───────────────────────────────────────────────

def scrape_video_urls(profile_url, cookies_file=None, max_scrolls=30, headless=True,
                      log=print, progress=_noop, stop=None) -> list[str]:
    stop = stop or threading.Event()
    target = build_videos_url(profile_url)
    log(f"Opening: {target}")
    urls: set[str] = set()
    with BrowserSession(cookies_file, headless, log) as s:
        page = s.page
        page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3_000)
        no_change = prev = 0
        for i in _scrolls(max_scrolls):
            if stop.is_set():
                break
            try:
                links = page.eval_on_selector_all(
                    "a[href]",
                    "(els, hints) => els.map(e => e.href).filter(h => hints.some(k => h.includes(k)))",
                    list(VIDEO_HINTS))
                urls.update(links)
            except Exception:
                pass
            progress(i + 1, max_scrolls)  # total 0 = unlimited
            log(f"Scroll {i + 1}/{max_scrolls or '∞'} — {len(urls)} video URLs")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)
            no_change = no_change + 1 if len(urls) == prev else 0
            prev = len(urls)
            if no_change >= PATIENCE:
                log("No new videos after several scrolls — stopping scroll.")
                break
    return sorted(urls)


def download_videos(video_urls, output_dir, cookies_file=None, log=print,
                    progress=_noop, stop=None) -> list[str]:
    import yt_dlp
    stop = stop or threading.Event()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {len(video_urls)} videos to {out.resolve()}")
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        log("ffmpeg not found — using single-file formats (lower quality on some videos).")

    class _Log:
        def debug(self, m): pass
        def info(self, m): pass
        def warning(self, m): pass
        def error(self, m): log(str(m))

    failed = []
    for idx, url in enumerate(video_urls, 1):
        if stop.is_set():
            log("Stopped.")
            break
        progress(idx - 1, len(video_urls))
        log(f"[{idx}/{len(video_urls)}] {url}")
        step = {"last": -10}

        def hook(d):
            if stop.is_set():
                raise yt_dlp.utils.DownloadCancelled()
            if d.get("status") == "downloading":
                try:
                    pct = float(str(d.get("_percent_str", "0")).strip().rstrip("%"))
                except ValueError:
                    return
                if pct - step["last"] >= 10:
                    step["last"] = pct
                    log(f"   {pct:.0f}%  {str(d.get('_speed_str', '')).strip()}")

        opts = {
            "outtmpl": str(out / "%(uploader)s - %(title)s [%(id)s].%(ext)s"),
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" if ffmpeg else "best[ext=mp4]/best",
            "merge_output_format": "mp4", "concurrent_fragment_downloads": 4,
            "noprogress": True, "quiet": True, "no_warnings": True,
            "logger": _Log(), "progress_hooks": [hook],
        }
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
        if cookies_file:
            opts["cookiefile"] = cookies_file
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                if ydl.download([url]) != 0:
                    failed.append(url)
        except yt_dlp.utils.DownloadCancelled:
            log("Stopped.")
            break
        except Exception as e:
            log(f"   failed: {e}")
            failed.append(url)
    progress(len(video_urls), len(video_urls))
    log(f"Done: {len(video_urls) - len(failed)}/{len(video_urls)} downloaded.")
    for u in failed:
        log(f"  FAILED: {u}")
    return failed


# ── post dates ─────────────────────────────────────────────────────────────────

_MONTHS = {}
for _i, _names in enumerate([
    ("january", "jan", "يناير", "كانون الثاني"), ("february", "feb", "فبراير", "شباط"),
    ("march", "mar", "مارس", "آذار"), ("april", "apr", "أبريل", "نيسان"),
    ("may", "مايو", "أيار"), ("june", "jun", "يونيو", "حزيران"),
    ("july", "jul", "يوليو", "تموز"), ("august", "aug", "أغسطس", "آب"),
    ("september", "sep", "sept", "سبتمبر", "أيلول"), ("october", "oct", "أكتوبر", "تشرين الأول"),
    ("november", "nov", "نوفمبر", "تشرين الثاني"), ("december", "dec", "ديسمبر", "كانون الأول"),
], 1):
    for _n in _names:
        _MONTHS[_n] = _i
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_REL_UNITS = {"m": 60, "min": 60, "mins": 60, "h": 3600, "hr": 3600, "hrs": 3600,
              "d": 86400, "w": 604800, "y": 31536000, "yr": 31536000, "yrs": 31536000}


def parse_fb_date(text: str, now: datetime | None = None) -> datetime | None:
    """Parse Facebook's hover tooltip ('Tuesday, October 21, 2025 at 4:32 PM', Arabic too)
    or its short relative label ('3h', 'Yesterday', 'Oct 5'). Returns None if unrecognised."""
    now = now or datetime.now()
    t = (text or "").translate(_AR_DIGITS).strip().lower()
    if not t:
        return None
    day = month = year = None
    m = re.search(r"(\d{1,2})\s+([^\W\d_]+(?: [^\W\d_]+)?)\s*,?\s+(\d{4})", t)
    if m and m.group(2) in _MONTHS:
        day, month, year = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    if not month:
        m = re.search(r"([^\W\d_]+)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?", t)
        if m and m.group(1) in _MONTHS:
            day, month = int(m.group(2)), _MONTHS[m.group(1)]
            year = int(m.group(3)) if m.group(3) else None
    hh = mm = 0
    tm = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm|ص|م)?", t)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
        if tm.group(3) in ("pm", "م") and hh < 12:
            hh += 12
        elif tm.group(3) in ("am", "ص") and hh == 12:
            hh = 0
    try:
        if month:
            if year is None:  # "Oct 5" means the most recent such day
                year = now.year
                if datetime(year, month, day) > now:
                    year -= 1
            return datetime(year, month, day, hh, mm)
        if "yesterday" in t or "أمس" in t:
            return (now - timedelta(days=1)).replace(hour=hh, minute=mm)
        r = re.fullmatch(r"(\d+)\s*([a-z]+)\.?", t)
        if r and r.group(2) in _REL_UNITS:
            return now - timedelta(seconds=int(r.group(1)) * _REL_UNITS[r.group(2)])
    except (ValueError, OverflowError):
        pass
    return None


def _hover_date(page, tag: str) -> str:
    """Facebook only shows the exact date in a tooltip when the time link is hovered."""
    try:
        page.locator(f'[data-fbdl="{tag}"]').first.hover(timeout=2_500)
        page.wait_for_timeout(350)
        return page.locator("[role=tooltip]").first.inner_text(timeout=1_500).strip()
    except Exception:
        return ""
    finally:
        try:
            page.mouse.move(0, 0)
        except Exception:
            pass


# ── offline page archive ───────────────────────────────────────────────────────

# Facebook virtualises its feed (posts far above the viewport are emptied), so we
# snapshot every top-level post on each scroll step. Its CSS does not survive being
# copied out of the site, so we extract text/images/links and render our own layout.
_COLLECT_JS = """
() => {
  const out = [];
  const JUNK = /^(like|comment|share|send|reply|write a comment|أعجبني|تعليق|مشاركة|إرسال|اكتب تعليقا|اكتب تعليقاً|see more|عرض المزيد|…|\\d+)$/i;
  const PERM = /\\/posts\\/|story_fbid|\\/videos\\/|\\/photos\\/|permalink|\\/reel\\//;
  const clean = h => h.split('&__cft__')[0].split('?__cft__')[0];
  window.__fbn = window.__fbn || 0;
  document.querySelectorAll('[role=article]').forEach(el => {
    if (el.parentElement && el.parentElement.closest('[role=article]')) return; // skip comments
    const links = [...el.querySelectorAll('a[href]')].filter(a => PERM.test(a.href));
    const link = links[0];
    // the timestamp link is the permalink with short visible text ("3h", "Oct 5")
    const tl = links.find(a => { const t = a.innerText.trim(); return t.length > 0 && t.length <= 24; });
    let tag = '';
    if (tl) { if (!tl.dataset.fbdl) tl.dataset.fbdl = String(++window.__fbn); tag = tl.dataset.fbdl; }
    const msg = el.querySelector('[data-ad-comet-preview="message"], [data-ad-preview="message"]');
    let text = msg ? msg.innerText : (el.innerText || '').split('\\n').filter(l => l.trim().length > 1 && !JUNK.test(l.trim())).join('\\n');
    text = text.trim();
    const images = [...el.querySelectorAll('img')]
      .filter(i => (i.naturalWidth >= 150 || i.width >= 150) && i.src.startsWith('http'))
      .map(i => i.src);
    if (!text && !images.length) return;
    const url = link ? clean(link.href) : '';
    const author = el.querySelector('h2, h3, h4');
    out.push({key: url || text.slice(0, 200), link: url, tag,
              author: author ? author.innerText.trim() : '',
              when: tl ? tl.innerText.trim() : '',
              text, images,
              videos: [...el.querySelectorAll('a[href]')].map(a => a.href)});
  });
  return out;
}
"""

_EXPAND_JS = """
() => {
  const re = /^(…\\s*)?(see more|عرض المزيد|شاهد المزيد|اقرأ المزيد)$/i;
  let n = 0;
  document.querySelectorAll('[role=article] [role=button], [role=article] div[tabindex="0"]').forEach(b => {
    if (re.test((b.innerText || '').trim())) { b.click(); n++; }
  });
  return n;
}
"""

_SEE_MORE = re.compile(r"[\s…\.]*(see more|عرض المزيد|شاهد المزيد)\s*$", re.I)

_CSS = """
body{font-family:Segoe UI,Tahoma,Arial,sans-serif;background:#f0f2f5;margin:0;color:#1c1e21}
.wrap{width:min(97vw,1500px);margin:0 auto;padding:16px 0}
h1{font-size:22px}.meta{color:#65676b;margin-bottom:12px}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.bar button{border:1px solid #ccd0d5;background:#fff;border-radius:6px;padding:6px 12px;cursor:pointer;font:inherit}
.bar button.on{background:#1877f2;border-color:#1877f2;color:#fff}
.post{background:#fff;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.2);padding:16px 20px;margin-bottom:16px}
.author{font-weight:600}.when{color:#65676b;font-size:13px}
.text{white-space:pre-wrap;line-height:1.7;margin:10px 0;font-size:16px}
.post img{max-width:100%;border-radius:6px;margin-top:8px;display:block}
a{color:#1877f2;text-decoration:none}
@media print{body{background:#fff}.bar{display:none}.wrap{width:100%}
.post{box-shadow:none;border:1px solid #ddd;break-inside:avoid-page}}
"""

# Runs inside the saved page (works offline): reorders the post cards by date.
_SORT_JS = """
function fbSort(o){
  const c=document.getElementById('posts'), a=[...c.children];
  a.sort((x,y)=>{
    const i=x.dataset.i-y.dataset.i; if(o==='orig') return i;
    const tx=+x.dataset.ts, ty=+y.dataset.ts;
    if(!tx&&!ty) return i; if(!tx) return 1; if(!ty) return -1;  // undated posts go last
    return o==='asc'? tx-ty : ty-tx;
  });
  a.forEach(n=>c.appendChild(n));
  document.querySelectorAll('.bar button').forEach(b=>b.classList.toggle('on',b.dataset.o===o));
}
"""


def _ext_for(url: str, ctype: str) -> str:
    ext = mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ""
    if ext in (".jpe", ""):
        ext = Path(urlparse(url).path).suffix[:6] or ".jpg"
    return ".jpg" if ext == ".jpe" else ext


def archive_page(page_url, output_dir, cookies_file=None, max_scrolls=40, headless=True,
                 with_videos=False, with_dates=True, order="orig", make_pdf=False,
                 log=print, progress=_noop, stop=None) -> Path:
    """Save a page's timeline as a self-contained offline site. Returns the index.html path.
    order: 'orig' (page order), 'desc' (newest first) or 'asc' (oldest first) — the saved page
    also has buttons to switch at any time."""
    stop = stop or threading.Event()
    root = Path(output_dir) / _slug(page_url)
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    url_map: dict[str, str] = {}      # remote image url -> file name in assets/
    posts: dict[str, dict] = {}       # key -> post data, insertion-ordered
    video_links: set[str] = set()

    def on_response(resp):
        try:
            url = resp.url
            if resp.request.resource_type != "image" or resp.status != 200 or url in url_map:
                return
            body = resp.body()
            if len(body) < 3_000:  # icons / emoji / tracking pixels
                return
            name = hashlib.sha1(url.encode()).hexdigest()[:20] + _ext_for(url, resp.headers.get("content-type"))
            (assets / name).write_bytes(body)
            url_map[url] = name
        except Exception:
            pass

    with BrowserSession(cookies_file, headless, log) as s:
        page = s.page
        page.on("response", on_response)
        log(f"Opening: {page_url}")
        page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_000)
        try:
            page.keyboard.press("Escape")  # dismiss a login nag if shown
            page.locator('[aria-label="Close"], [aria-label="إغلاق"]').first.click(timeout=2_000)
        except Exception:
            pass

        no_change = prev = 0
        for i in _scrolls(max_scrolls):
            if stop.is_set():
                break
            try:
                if page.evaluate(_EXPAND_JS):  # open every truncated post ("See more")
                    page.wait_for_timeout(700)
                for item in page.evaluate(_COLLECT_JS):
                    old = posts.get(item["key"])
                    if old:
                        item["images"] = list(dict.fromkeys(old["images"] + item["images"]))
                        if len(old["text"]) > len(item["text"]):
                            item["text"] = old["text"]
                        item["date_text"], item["ts"], item["tried"] = old["date_text"], old["ts"], old["tried"]
                    else:
                        item.update(date_text="", ts=None, tried=False)
                    if with_dates and not item["ts"] and not item["tried"] and item["tag"]:
                        item["tried"] = True
                        item["date_text"] = _hover_date(page, item["tag"])
                        dt = parse_fb_date(item["date_text"]) or parse_fb_date(item["when"])
                        item["ts"] = int(dt.timestamp()) if dt else None
                    posts[item["key"]] = item
                    video_links.update(l for l in item["videos"] if any(h in l for h in VIDEO_HINTS))
            except Exception as e:
                log(f"collect warning: {e}")
            progress(i + 1, max_scrolls)  # total 0 = unlimited
            log(f"Scroll {i + 1}/{max_scrolls or '∞'} — {len(posts)} posts, {len(url_map)} images")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_500)
            no_change = no_change + 1 if len(posts) == prev else 0
            prev = len(posts)
            if no_change >= PATIENCE:
                log("No new posts after several scrolls — done scrolling.")
                break
        page.wait_for_timeout(1_500)  # let the last images finish
        title = page.title() or _slug(page_url)
        if not posts:
            try:
                page.screenshot(path=str(root / "debug.png"))
                (root / "debug.html").write_text(page.content(), encoding="utf-8")
                log(f"Page title: {page.title()!r} | url: {page.url}")
                log(f"role=article count: {page.locator('[role=article]').count()}, "
                    f"login form: {page.locator('input[name=pass]').count() > 0}")
                log(f"Debug files saved in {root.resolve()} (debug.png, debug.html)")
            except Exception as e:
                log(f"debug dump failed: {e}")

    if not posts:
        raise RuntimeError("No posts found. Check that the page is public or that your cookies are valid.")
    if with_dates:
        log(f"Dates found for {sum(1 for p in posts.values() if p['ts'])}/{len(posts)} posts")

    videos_note = ""
    if with_videos and video_links and not stop.is_set():
        log(f"Downloading {len(video_links)} videos into the archive...")
        download_videos(sorted(video_links), root / "videos", cookies_file, log, progress, stop)
        vids = sorted((root / "videos").glob("*.mp4"))
        if vids:
            videos_note = ' · <a href="videos.html">▶ Videos (%d)</a>' % len(vids)
            items = "".join(f'<li class="post"><a href="videos/{v.name}">{htmllib.escape(v.name)}</a>'
                            f'<video controls preload="none" src="videos/{v.name}" style="width:100%"></video></li>'
                            for v in vids)
            (root / "videos.html").write_text(
                f'<!doctype html><meta charset="utf-8"><title>Videos</title><style>{_CSS}</style>'
                f'<div class="wrap"><h1>Videos</h1><p><a href="index.html">← Back</a></p>'
                f'<ul style="list-style:none;padding:0">{items}</ul></div>', encoding="utf-8")

    esc = htmllib.escape
    cards = []
    for n, p in enumerate(posts.values()):
        imgs = "".join(f'<img loading="lazy" src="assets/{url_map[u]}">' for u in p["images"] if u in url_map)
        orig = f' · <a href="{esc(p["link"])}">original</a>' if p["link"] else ""
        shown = p["date_text"] or p["when"]
        cards.append(
            f'<div class="post" dir="auto" data-i="{n}" data-ts="{p["ts"] or ""}">'
            f'<div class="author">{esc(p["author"])}</div>'
            f'<div class="when">{esc(shown)}{orig}</div>'
            f'<div class="text">{esc(_SEE_MORE.sub("", p["text"]).strip())}</div>{imgs}</div>')
    bar = ('<div class="bar"><b>Sort | الترتيب:</b>'
           '<button data-o="desc" onclick="fbSort(\'desc\')">Newest first | الأحدث أولاً</button>'
           '<button data-o="asc" onclick="fbSort(\'asc\')">Oldest first | الأقدم أولاً</button>'
           '<button data-o="orig" onclick="fbSort(\'orig\')">Page order | ترتيب الصفحة</button></div>')
    index = root / "index.html"
    index.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title><style>{_CSS}</style></head><body><div class="wrap">'
        f'<h1 dir="auto">{esc(title)}</h1><div class="meta">{len(posts)} posts · saved offline'
        f' · <a href="{esc(page_url)}">original page</a>{videos_note}</div>{bar}'
        f'<div id="posts">{"".join(cards)}</div></div>'
        f'<script>{_SORT_JS}fbSort({order!r});</script></body></html>',
        encoding="utf-8")
    log(f"Saved {len(posts)} posts → {index.resolve()}")
    if make_pdf and not stop.is_set():
        export_pdf(index, order=order if order != "orig" else None, log=log)
    return index


def export_pdf(index_html, pdf_path=None, order=None, log=print) -> Path:
    """Print a saved offline copy (index.html) to PDF. order: 'asc' | 'desc' | None (as saved)."""
    index = Path(index_html).resolve()
    pdf = Path(pdf_path) if pdf_path else index.with_suffix(".pdf")
    log(f"Exporting PDF… {pdf}")
    with BrowserSession(None, True, log) as s:  # PDF printing only works headless
        page = s.page
        page.set_default_timeout(180_000)
        page.goto(index.as_uri(), wait_until="load")
        page.evaluate("document.querySelectorAll('img').forEach(i => i.loading = 'eager')")
        try:
            page.wait_for_function("[...document.images].every(i => i.complete)", timeout=90_000)
        except Exception:
            log("Some images did not finish loading; exporting anyway.")
        if order:
            page.evaluate("o => fbSort(o)", order)
        page.emulate_media(media="print")
        page.pdf(path=str(pdf), format="A4", print_background=True,
                 margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"})
    log(f"PDF saved → {pdf}")
    return pdf
