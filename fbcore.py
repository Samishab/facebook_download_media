"""
Core logic: scraping, video downloading and offline page archiving.
Every long-running function takes a `log(msg)` callback, an optional
`progress(done, total)` callback and a `stop` threading.Event so that
both the CLI and the GUI can drive it.
"""

import hashlib
import html as htmllib
import mimetypes
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIDEO_HINTS = ("/videos/", "watch?v=", "watch/?v=", "/reel/")


def _noop(*_a, **_k):
    pass


# ── dependency helpers ─────────────────────────────────────────────────────────

def ensure_yt_dlp(log=print):
    try:
        subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("Installing yt-dlp...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"], check=True)


def ensure_playwright(log=print):
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        log("Installing playwright...")
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True)


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
        self.browser = self._pw.chromium.launch(headless=self.headless)
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
        for i in range(max_scrolls):
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
            progress(i + 1, max_scrolls)
            log(f"Scroll {i + 1}/{max_scrolls} — {len(urls)} video URLs")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_000)
            no_change = no_change + 1 if len(urls) == prev else 0
            prev = len(urls)
            if no_change >= 4:
                log("No new videos after 4 scrolls — stopping scroll.")
                break
    return sorted(urls)


def download_videos(video_urls, output_dir, cookies_file=None, log=print,
                    progress=_noop, stop=None) -> list[str]:
    stop = stop or threading.Event()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {len(video_urls)} videos to {out.resolve()}")
    failed = []
    for idx, url in enumerate(video_urls, 1):
        if stop.is_set():
            log("Stopped.")
            break
        progress(idx - 1, len(video_urls))
        log(f"[{idx}/{len(video_urls)}] {url}")
        cmd = [sys.executable, "-m", "yt_dlp", "--ignore-errors", "--no-warnings", "--newline",
               "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
               "--merge-output-format", "mp4", "--concurrent-fragments", "4",
               "--output", str(out / "%(uploader)s - %(title)s [%(id)s].%(ext)s")]
        if cookies_file:
            cmd += ["--cookies", cookies_file]
        cmd.append(url)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        last = ""
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("[download]") and "%" in line:
                last = line  # keep only the latest progress line
            elif line:
                log(line)
            if stop.is_set():
                proc.terminate()
                break
        proc.wait()
        if last:
            log(last)
        if proc.returncode != 0 and not stop.is_set():
            failed.append(url)
    progress(len(video_urls), len(video_urls))
    log(f"Done: {len(video_urls) - len(failed)}/{len(video_urls)} downloaded.")
    for u in failed:
        log(f"  FAILED: {u}")
    return failed


# ── offline page archive ───────────────────────────────────────────────────────

# Facebook virtualises its feed (posts far above the viewport are emptied), so we
# snapshot every top-level post on each scroll step. Its CSS does not survive being
# copied out of the site, so we extract text/images/links and render our own layout.
_COLLECT_JS = """
() => {
  const out = [];
  const JUNK = /^(like|comment|share|send|reply|write a comment|أعجبني|تعليق|مشاركة|إرسال|اكتب تعليقا|اكتب تعليقاً|see more|عرض المزيد|…|\\d+)$/i;
  document.querySelectorAll('[role=article]').forEach(el => {
    if (el.parentElement && el.parentElement.closest('[role=article]')) return; // skip comments
    const link = el.querySelector('a[href*="/posts/"], a[href*="story_fbid"], a[href*="/videos/"], a[href*="/photos/"], a[href*="permalink"], a[href*="/reel/"]');
    const msg = el.querySelector('[data-ad-comet-preview="message"], [data-ad-preview="message"]');
    let text = msg ? msg.innerText : (el.innerText || '').split('\\n').filter(l => l.trim().length > 1 && !JUNK.test(l.trim())).join('\\n');
    text = text.trim();
    const images = [...el.querySelectorAll('img')]
      .filter(i => (i.naturalWidth >= 150 || i.width >= 150) && i.src.startsWith('http'))
      .map(i => i.src);
    if (!text && !images.length) return;
    const url = link ? link.href.split('&__cft__')[0].split('?__cft__')[0] : '';
    const author = el.querySelector('h2, h3, h4');
    out.push({key: url || text.slice(0, 200), link: url,
              author: author ? author.innerText.trim() : '',
              when: link ? link.innerText.trim().slice(0, 40) : '',
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
.wrap{max-width:920px;margin:0 auto;padding:16px}
h1{font-size:22px}.meta{color:#65676b;margin-bottom:16px}
.post{background:#fff;border-radius:8px;box-shadow:0 1px 2px rgba(0,0,0,.2);padding:14px;margin-bottom:16px}
.author{font-weight:600}.when{color:#65676b;font-size:13px}
.text{white-space:pre-wrap;line-height:1.6;margin:10px 0;font-size:16px}
.post img{max-width:100%;border-radius:6px;margin-top:8px;display:block}
a{color:#1877f2;text-decoration:none}
"""


def _ext_for(url: str, ctype: str) -> str:
    ext = mimetypes.guess_extension((ctype or "").split(";")[0].strip()) or ""
    if ext in (".jpe", ""):
        ext = Path(urlparse(url).path).suffix[:6] or ".jpg"
    return ".jpg" if ext == ".jpe" else ext


def archive_page(page_url, output_dir, cookies_file=None, max_scrolls=40, headless=True,
                 with_videos=False, log=print, progress=_noop, stop=None) -> Path:
    """Save a page's timeline as a self-contained offline site. Returns the index.html path."""
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
        for i in range(max_scrolls):
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
                    posts[item["key"]] = item
                    video_links.update(l for l in item["videos"] if any(h in l for h in VIDEO_HINTS))
            except Exception as e:
                log(f"collect warning: {e}")
            progress(i + 1, max_scrolls)
            log(f"Scroll {i + 1}/{max_scrolls} — {len(posts)} posts, {len(url_map)} images")
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_500)
            no_change = no_change + 1 if len(posts) == prev else 0
            prev = len(posts)
            if no_change >= 4:
                log("No new posts after 4 scrolls — done scrolling.")
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
    for p in posts.values():
        imgs = "".join(f'<img loading="lazy" src="assets/{url_map[u]}">' for u in p["images"] if u in url_map)
        orig = f' · <a href="{esc(p["link"])}">original</a>' if p["link"] else ""
        cards.append(
            f'<div class="post" dir="auto"><div class="author">{esc(p["author"])}</div>'
            f'<div class="when">{esc(p["when"])}{orig}</div>'
            f'<div class="text">{esc(_SEE_MORE.sub("", p["text"]).strip())}</div>{imgs}</div>')
    index = root / "index.html"
    index.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(title)}</title><style>{_CSS}</style></head><body><div class="wrap">'
        f'<h1 dir="auto">{esc(title)}</h1><div class="meta">{len(posts)} posts · saved offline'
        f' · <a href="{esc(page_url)}">original page</a>{videos_note}</div>{"".join(cards)}</div></body></html>',
        encoding="utf-8")
    log(f"Saved {len(posts)} posts → {index.resolve()}")
    return index
