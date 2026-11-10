"""Build the documentation site (appglider.org/docs/) from Markdown.

Reads docs/nav.toml, renders each listed Markdown page to site/docs/<slug>/index.html
and writes search.json and sitemap.xml next to them. Needs Python-Markdown
(packaging/docs-requirements.txt). The output folder is deleted and recreated on each run.

    python packaging/build_docs.py [--check]
"""

# ruff: noqa: E501  (HTML templates below)
from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import shutil
import struct
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

ROOT = Path(__file__).resolve().parents[1]
SITE_URL = "https://appglider.org"
REPO_URL = "https://github.com/Vyrnexis/AppGlider"
ALERTS = {
    "NOTE": "Note",
    "TIP": "Tip",
    "IMPORTANT": "Important",
    "WARNING": "Warning",
    "CAUTION": "Caution",
}
_ALERT_RE = re.compile(r"^\s*\[!(" + "|".join(ALERTS) + r")\]\s*", re.IGNORECASE)
_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


class BuildError(Exception):
    """One or more problems that must fail the build."""


# --- Slugs -------------------------------------------------------------------


def github_slug(text: str) -> str:
    """The id GitHub gives a heading: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


class Slugger:
    """Hands out unique heading ids per page (foo, foo-1, foo-2, ...)."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def make(self, text: str) -> str:
        base = github_slug(text) or "section"
        slug, n = base, 0
        while slug in self._used:
            n += 1
            slug = f"{base}-{n}"
        self._used.add(slug)
        return slug


# --- Navigation --------------------------------------------------------------


@dataclass
class Page:
    file: str
    slug: str
    section: str | None
    label: str | None = None
    title: str = ""
    description: str = ""
    body: str = ""
    toc: list[tuple[int, str, str]] = field(default_factory=list)
    sections: list[dict[str, str]] = field(default_factory=list)

    @property
    def url(self) -> str:
        return "/docs/" if not self.slug else f"/docs/{self.slug}/"

    @property
    def nav_label(self) -> str:
        return self.label or self.title


@dataclass
class Nav:
    home: Page
    groups: list[tuple[str, list[Page]]]

    @property
    def pages(self) -> list[Page]:
        return [self.home] + [p for _, pages in self.groups for p in pages]


def load_nav(root: Path) -> Nav:
    path = root / "docs" / "nav.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise BuildError(f"cannot read {path}: {err}") from err

    def entry(item: dict[str, str], section: str | None) -> Page:
        if "file" not in item or "slug" not in item:
            raise BuildError(f"nav entry needs file and slug: {item}")
        return Page(item["file"], item["slug"], section, item.get("label"))

    home = entry(data.get("home", {}), None)
    groups = [
        (sec["title"], [entry(p, sec["title"]) for p in sec.get("pages", [])])
        for sec in data.get("section", [])
    ]
    nav = Nav(home, groups)
    seen_slugs: set[str] = set()
    seen_files: set[str] = set()
    errors = []
    for page in nav.pages:
        if page.slug in seen_slugs:
            errors.append(f"duplicate slug in nav: {page.slug!r}")
        if page.file in seen_files:
            errors.append(f"duplicate file in nav: {page.file}")
        if not (root / page.file).is_file():
            errors.append(f"nav lists a missing file: {page.file}")
        seen_slugs.add(page.slug)
        seen_files.add(page.file)
    if errors:
        raise BuildError("\n".join(errors))
    return nav


# --- Markdown processing -----------------------------------------------------


@dataclass
class Context:
    root: Path
    pages_by_file: dict[str, Page]
    images: dict[str, Path] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    page: Page | None = None

    def error(self, message: str) -> None:
        where = self.page.file if self.page else "?"
        self.errors.append(f"{where}: {message}")


def _resolve(href_path: str, ctx: Context) -> str | None:
    """Repo-relative path a link in the current page points at, or None if it escapes."""
    assert ctx.page is not None
    if href_path.startswith("/"):
        joined = href_path.lstrip("/")
    else:
        joined = posixpath.join(posixpath.dirname(ctx.page.file), href_path)
    target = posixpath.normpath(unquote(joined))
    if target == ".." or target.startswith("../"):
        return None
    return target


def rewrite_link(href: str, ctx: Context) -> str:
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path:
        return href
    target = _resolve(parts.path, ctx)
    if target is None:
        ctx.error(f"link leaves the repository: {href}")
        return href
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    page = ctx.pages_by_file.get(target)
    if page is not None:
        return page.url + fragment
    full = ctx.root / target
    if not full.exists():
        ctx.error(f"link to a file that does not exist: {href}")
        return href
    kind = "tree" if full.is_dir() else "blob"
    return f"{REPO_URL}/{kind}/main/{quote(target)}{fragment}"


def png_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def rewrite_image(img: ET.Element, ctx: Context) -> None:
    src = img.get("src", "")
    parts = urlsplit(src)
    if parts.scheme or parts.netloc or not parts.path:
        return
    target = _resolve(parts.path, ctx)
    full = ctx.root / target if target else None
    if target is None or full is None or not full.is_file():
        ctx.error(f"image does not exist: {src}")
        return
    name = posixpath.basename(target)
    known = ctx.images.get(name)
    if known is not None and known != full:
        ctx.error(f"two different images are named {name}: {known} and {full}")
        return
    ctx.images[name] = full
    img.set("src", f"/docs/img/{name}")
    img.set("loading", "lazy")
    size = png_size(full)
    if size:
        img.set("width", str(size[0]))
        img.set("height", str(size[1]))


_STASH_RE = re.compile("\x02wzxhzdk:\\d+\x03")


def _text(el: ET.Element) -> str:
    """Plain text of an element (raw HTML such as fenced code is left out)."""
    return _STASH_RE.sub(" ", "".join(el.itertext()))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:- ")
    return cut + "…"


class DocsProcessor(Treeprocessor):
    def __init__(self, md: markdown.Markdown, ctx: Context) -> None:
        super().__init__(md)
        self.ctx = ctx

    def run(self, root: ET.Element) -> None:
        page = self.ctx.page
        assert page is not None
        page.title = ""
        page.description = ""
        page.toc = []
        page.sections = []

        self._alerts(root)
        for el in root.iter():
            if el.tag == "a" and el.get("href") is not None:
                el.set("href", rewrite_link(el.get("href", ""), self.ctx))
            elif el.tag == "img":
                rewrite_image(el, self.ctx)

        slugger = Slugger()
        for el in list(root.iter()):
            if el.tag not in _HEADINGS:
                continue
            text = _squash(_text(el))
            el.set("id", slugger.make(text))
            if el.tag == "h1" and not page.title:
                page.title = text
            elif el.tag in ("h2", "h3"):
                page.toc.append((int(el.tag[1]), text, el.get("id", "")))
        for p in root.iter("p"):
            if p.get("class") == "callout-title":
                continue
            plain = _squash(_text(p))
            if plain:
                page.description = _truncate(plain, 160)
                break

        self._collect_sections(root, page)
        if len(root) > 1 and root[0].tag == "h1" and root[1].tag == "p":
            root[1].set("class", "lead")
        self._wrap(root)

    def _alerts(self, root: ET.Element) -> None:
        for el in root.iter("blockquote"):
            first = el[0] if len(el) else None
            if first is None or first.tag != "p" or first.text is None:
                continue
            found = _ALERT_RE.match(first.text)
            if not found:
                continue
            kind = found.group(1).upper()
            first.text = first.text[found.end() :]
            if not first.text and not len(first):
                el.remove(first)
            el.tag = "div"
            el.set("class", f"callout callout-{kind.lower()}")
            title = ET.Element("p", {"class": "callout-title"})
            title.text = ALERTS[kind]
            el.insert(0, title)

    def _collect_sections(self, root: ET.Element, page: Page) -> None:
        heading, anchor = "", ""
        chunks: list[str] = []

        def flush() -> None:
            text = _squash(" ".join(chunks))
            if heading or text:
                page.sections.append(
                    {
                        "page": page.title,
                        "heading": heading,
                        "url": page.url + (f"#{anchor}" if anchor else ""),
                        "text": _truncate(text, 300),
                    }
                )

        for el in root:
            if el.tag in ("h2", "h3"):
                flush()
                heading, anchor, chunks = _squash(_text(el)), el.get("id", ""), []
            elif el.tag != "h1":
                chunks.append(_text(el))
        flush()

    def _wrap(self, parent: ET.Element) -> None:
        for index, el in enumerate(list(parent)):
            if el.tag in ("h2", "h3"):
                link = ET.SubElement(
                    el,
                    "a",
                    {
                        "class": "anchor",
                        "href": f"#{el.get('id', '')}",
                        "aria-label": "Link to this section",
                    },
                )
                link.text = "#"
                continue
            self._wrap(el)
            if el.tag == "p" and self._lone_image(el):
                img = el[0]
                caption = img.attrib.pop("title", "")
                figure = ET.Element("figure")
                figure.append(img)
                if caption:
                    ET.SubElement(figure, "figcaption").text = caption
                parent[index] = figure
                continue
            wrapper: ET.Element | None = None
            if el.tag == "table":
                wrapper = ET.Element("div", {"class": "table-wrap"})
            if wrapper is not None:
                parent[index] = wrapper
                wrapper.append(el)

    @staticmethod
    def _lone_image(p: ET.Element) -> bool:
        return (
            len(p) == 1
            and p[0].tag == "img"
            and not (p.text or "").strip()
            and not (p[0].tail or "").strip()
        )


class DocsExtension(Extension):
    def __init__(self, ctx: Context) -> None:
        super().__init__()
        self.ctx = ctx

    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.treeprocessors.register(DocsProcessor(md, self.ctx), "appglider_docs", 5)


def render_page(page: Page, ctx: Context) -> None:
    ctx.page = page
    source = (ctx.root / page.file).read_text(encoding="utf-8")
    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", DocsExtension(ctx)],
        output_format="html",
    )
    page.body = re.sub(
        r"<pre>.*?</pre>",
        lambda m: f'<div class="code">{m.group(0)}</div>',
        md.convert(source),
        flags=re.DOTALL,
    )
    if not page.title:
        ctx.error('no "# " heading to use as the page title')
    if not page.description:
        page.description = "AppGlider documentation."
    ctx.page = None


# --- HTML --------------------------------------------------------------------

esc = html.escape


def _head(page: Page) -> str:
    title = esc(f"{page.title} · AppGlider docs")
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{esc(page.description)}">
<link rel="canonical" href="{SITE_URL}{page.url}">
<meta name="theme-color" content="#eef0f4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#15181e" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/assets/appglider-taskbar-icon.svg" type="image/svg+xml">
<link rel="icon" href="/assets/favicon-32.png" type="image/png" sizes="32x32">
<link rel="stylesheet" href="/styles.css">
<link rel="stylesheet" href="/docs-assets/docs.css">
<script src="/docs-assets/docs.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>

<header class="nav">
  <div class="wrap nav-inner">
    <a class="brand" href="/" aria-label="AppGlider home">
      <img src="/assets/appglider-taskbar-icon.svg" width="32" height="32" alt="">
      <span>AppGlider</span>
    </a>
    <nav class="nav-links" aria-label="Main">
      <a href="/#features">Features</a>
      <a href="/#install">Install</a>
      <a href="/#questions">Questions</a>
      <a href="/docs/" aria-current="page">Docs</a>
      <a href="{REPO_URL}">GitHub</a>
    </nav>
    <a class="btn btn-primary btn-small" href="{REPO_URL}/releases/latest">Download</a>
  </div>
</header>
"""


_FOOT = f"""
<footer class="footer">
  <div class="wrap footer-inner">
    <p>© 2026 Vyrnexis · <a href="{REPO_URL}/blob/main/LICENSE">GPL-3.0-or-later</a></p>
    <p class="footer-links">
      <a href="{REPO_URL}">Source</a>
      <a href="/docs/">Docs</a>
      <!--email_off--><a href="mailto:contact@appglider.org">contact@appglider.org</a><!--/email_off-->
    </p>
  </div>
</footer>
</body>
</html>
"""


def _sidebar(nav: Nav, current: Page) -> str:
    def link(p: Page) -> str:
        mark = ' aria-current="page"' if p is current else ""
        return f'          <li><a href="{p.url}"{mark}>{esc(p.nav_label)}</a></li>'

    out = [
        '    <nav class="sidebar" id="sidebar" aria-label="Documentation">',
        '      <form class="search" role="search" action="#" method="get">',
        '        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" '
        'focusable="false"><circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/></svg>',
        '        <input id="doc-search" type="search" placeholder="Search the docs" '
        'aria-label="Search the docs" autocomplete="off">',
        "      </form>",
        '        <section class="side-group" aria-label="Overview">',
        "          <ul>",
        link(nav.home),
        "          </ul>",
        "        </section>",
    ]
    for title, pages in nav.groups:
        gid = "g-" + github_slug(title)
        out.append(f'        <section class="side-group" aria-labelledby="{gid}">')
        out.append(f'          <h2 id="{gid}">{esc(title)}</h2>')
        out.append("          <ul>")
        out.extend(link(p) for p in pages)
        out.append("          </ul>")
        out.append("        </section>")
    out.append("    </nav>")
    return "\n".join(out)


def _crumbs(page: Page) -> str:
    if page.section is None:
        items = '<li><span aria-current="page">Docs</span></li>'
    else:
        items = (
            '<li><a href="/docs/">Docs</a></li>\n'
            f'        <li><span aria-current="page">{esc(page.section)}</span></li>'
        )
    return f"""    <nav class="crumbs" aria-label="Breadcrumb">
      <ol>
        {items}
      </ol>
    </nav>"""


def _pager(prev: Page | None, nxt: Page | None) -> str:
    cells = []
    if prev:
        cells.append(
            f'<a class="prev" href="{prev.url}" rel="prev"><span class="dir">Previous</span>'
            f'<span class="title">{esc(prev.nav_label)}</span></a>'
        )
    if nxt:
        cells.append(
            f'<a class="next" href="{nxt.url}" rel="next"><span class="dir">Next</span>'
            f'<span class="title">{esc(nxt.nav_label)}</span></a>'
        )
    inner = "\n        ".join(cells)
    return f"""      <nav class="pager" aria-label="Previous and next page">
        {inner}
      </nav>"""


def _toc(page: Page) -> str:
    if not page.toc:
        return ""
    items = "\n".join(
        '      <li{}><a href="#{}">{}</a></li>'.format(
            ' class="sub"' if level == 3 else "", esc(anchor), esc(text)
        )
        for level, text, anchor in page.toc
    )
    return f"""
  <aside class="toc" aria-label="On this page">
    <h2>On this page</h2>
    <ul>
{items}
    </ul>
  </aside>"""


def render_html(nav: Nav, page: Page) -> str:
    order = nav.pages
    i = order.index(page)
    prev = order[i - 1] if i > 0 else None
    nxt = order[i + 1] if i + 1 < len(order) else None
    return (
        _head(page)
        + f"""
<div class="wrap docs">
  <div class="docs-nav">
    <button class="menu-toggle" type="button" aria-expanded="false" aria-controls="sidebar">
      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      Menu
    </button>
{_sidebar(nav, page)}
  </div>

  <main class="article" id="main">
{_crumbs(page)}

{page.body}

    <footer class="article-foot">
      <p class="edit-link"><a href="{REPO_URL}/edit/main/{quote(page.file)}">Edit this page on GitHub</a></p>
{_pager(prev, nxt)}
    </footer>
  </main>
{_toc(page)}
</div>
"""
        + _FOOT
    )


# --- Build -------------------------------------------------------------------


def build(root: Path = ROOT, site_dir: Path | None = None, write: bool = True) -> list[Page]:
    """Render every page. With write=False nothing on disk is touched (used by --check)."""
    root = root.resolve()
    site_dir = (site_dir or root / "site").resolve()
    out = site_dir / "docs"
    nav = load_nav(root)
    ctx = Context(root, {p.file: p for p in nav.pages})
    for page in nav.pages:
        render_page(page, ctx)
    if ctx.errors:
        raise BuildError("\n".join(ctx.errors))
    if not write:
        return nav.pages

    if out.exists():
        shutil.rmtree(out)
    for page in nav.pages:
        dest = out / page.slug / "index.html" if page.slug else out / "index.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_html(nav, page), encoding="utf-8")
    if ctx.images:
        (out / "img").mkdir(parents=True, exist_ok=True)
        for name, src in sorted(ctx.images.items()):
            shutil.copyfile(src, out / "img" / name)

    entries = [s for page in nav.pages for s in page.sections]
    (out / "search.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    urls = "\n".join(f"  <url><loc>{SITE_URL}{p.url}</loc></url>" for p in nav.pages)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n",
        encoding="utf-8",
    )
    return nav.pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the AppGlider documentation site.")
    parser.add_argument("--check", action="store_true", help="check pages and links, write nothing")
    args = parser.parse_args(argv)
    try:
        pages = build(write=not args.check)
    except BuildError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    verb = "checked" if args.check else "built"
    print(f"docs: {verb} {len(pages)} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
