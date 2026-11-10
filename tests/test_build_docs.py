"""The documentation generator (packaging/build_docs.py), run on a tiny tree."""

import importlib.util
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).parents[1]

try:
    import markdown  # noqa: F401
except ImportError:
    build_docs = None
else:
    _spec = importlib.util.spec_from_file_location(
        "build_docs", ROOT / "packaging" / "build_docs.py"
    )
    assert _spec is not None and _spec.loader is not None
    build_docs = importlib.util.module_from_spec(_spec)
    sys.modules["build_docs"] = build_docs
    _spec.loader.exec_module(build_docs)


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = zlib.compress(b"\x00" + b"\x00" * width)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", raw * 1)
        + chunk(b"IEND", b"")
    )


NAV = """
home = { file = "docs/index.md", slug = "" }

[[section]]
title = "Guide"
pages = [
  { file = "docs/one.md", slug = "one" },
  { file = "docs/sub/two.md", slug = "two", label = "Second" },
]
"""


@unittest.skipIf(build_docs is None, "markdown is not installed")
class BuildDocsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "docs" / "sub").mkdir(parents=True)
        (self.root / "docs" / "previews").mkdir()
        (self.root / "site").mkdir()
        (self.root / "catalogs").mkdir()
        (self.root / "catalogs" / "community.toml").write_text("format = 1\n")
        (self.root / "LICENSE").write_text("x\n")
        (self.root / "docs" / "previews" / "shot.png").write_bytes(_png(40, 30))
        self.write("docs/nav.toml", NAV)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def pages(self, home: str, one: str, two: str = "# Two\n\nText.\n") -> None:
        self.write("docs/index.md", home)
        self.write("docs/one.md", one)
        self.write("docs/sub/two.md", two)

    def out(self, name: str) -> str:
        return (self.root / "site" / "docs" / name).read_text(encoding="utf-8")

    def render(self, text: str) -> str:
        self.pages("# Home\n\nWelcome.\n", text)
        build_docs.build(self.root)
        return self.out("one/index.html")

    # --- slugs ---

    def test_github_slug(self) -> None:
        slug = build_docs.github_slug
        self.assertEqual(slug("What's new? (v1.0)"), "whats-new-v10")
        self.assertEqual(slug("Snake_case and-dash"), "snake_case-and-dash")
        self.assertEqual(slug("  Mixed CASE  "), "mixed-case")

    def test_slugger_deduplicates(self) -> None:
        s = build_docs.Slugger()
        self.assertEqual([s.make("Setup") for _ in range(3)], ["setup", "setup-1", "setup-2"])

    def test_heading_ids_and_permalinks(self) -> None:
        page = self.render("# One\n\n## Setup\n\n## Setup\n\n### Deep dive\n")
        self.assertIn('<h2 id="setup">Setup<a ', page)
        self.assertIn('class="anchor" href="#setup">#</a>', page)
        self.assertIn('id="setup-1"', page)
        self.assertIn('<li class="sub"><a href="#deep-dive">Deep dive</a></li>', page)

    # --- links and images ---

    def test_link_rewriting(self) -> None:
        page = self.render(
            "# One\n\nSee [two](sub/two.md#part), [home](index.md), "
            "[community](../catalogs/community.toml), [licence](../LICENSE), "
            "[web](https://example.org/a), [mail](mailto:a@b.c), [here](#top).\n"
        )
        self.assertIn('href="/docs/two/#part"', page)
        self.assertIn('href="/docs/"', page)
        self.assertIn(
            'href="https://github.com/Vyrnexis/AppGlider/blob/main/catalogs/community.toml"', page
        )
        self.assertIn('href="https://github.com/Vyrnexis/AppGlider/blob/main/LICENSE"', page)
        self.assertIn('href="https://example.org/a"', page)
        self.assertIn('href="mailto:a@b.c"', page)
        self.assertIn('href="#top"', page)

    def test_image_is_copied_and_sized(self) -> None:
        page = self.render('# One\n\n![A shot](previews/shot.png "The caption")\n')
        self.assertIn('src="/docs/img/shot.png"', page)
        self.assertIn('width="40"', page)
        self.assertIn('height="30"', page)
        self.assertIn('loading="lazy"', page)
        self.assertIn("<figcaption>The caption</figcaption>", page)
        self.assertTrue((self.root / "site" / "docs" / "img" / "shot.png").is_file())

    def test_missing_link_fails(self) -> None:
        self.pages("# Home\n\nWelcome.\n", "# One\n\nSee [gone](missing.md).\n")
        with self.assertRaises(build_docs.BuildError) as caught:
            build_docs.build(self.root)
        self.assertIn("docs/one.md", str(caught.exception))
        self.assertIn("missing.md", str(caught.exception))

    def test_missing_page_file_fails(self) -> None:
        self.write("docs/index.md", "# Home\n")
        with self.assertRaises(build_docs.BuildError):
            build_docs.build(self.root)

    # --- content ---

    def test_alerts(self) -> None:
        page = self.render(
            "# One\n\n> [!WARNING]\n> Careful **now**.\n\nBreak.\n\n> Plain quote.\n"
        )
        self.assertIn('<div class="callout callout-warning">', page)
        self.assertIn('<p class="callout-title">Warning</p>', page)
        self.assertIn("Careful <strong>now</strong>.", page)
        self.assertNotIn("[!WARNING]", page)
        self.assertIn("<blockquote>", page)

    def test_tables_and_code(self) -> None:
        page = self.render(
            "# One\n\n| A | B |\n|---|---|\n| `x` | y |\n\n```toml\nformat = 1\n```\n"
        )
        self.assertIn('<div class="table-wrap"><table>', page)
        self.assertIn('<div class="code"><pre><code class="language-toml">', page)
        self.assertIn("<td><code>x</code></td>", page)

    # --- full build ---

    def test_full_build(self) -> None:
        self.pages(
            "# Home\n\nWelcome to the docs.\n",
            "# One\n\nFirst paragraph here.\n\n## Part\n\nDetails about widgets.\n",
        )
        pages = build_docs.build(self.root)
        self.assertEqual([p.slug for p in pages], ["", "one", "two"])

        home = self.out("index.html")
        self.assertIn("<title>Home · AppGlider docs</title>", home)
        self.assertIn('<link rel="canonical" href="https://appglider.org/docs/">', home)
        self.assertIn('rel="next"', home)
        self.assertNotIn('rel="prev"', home)

        one = self.out("one/index.html")
        self.assertIn('content="First paragraph here."', one)
        self.assertIn('<p class="lead">First paragraph here.</p>', one)
        self.assertIn('<a href="/docs/one/" aria-current="page">One</a>', one)
        self.assertIn('<a href="/docs/two/">Second</a>', one)
        self.assertIn('<span aria-current="page">Guide</span>', one)
        self.assertIn('href="/docs/" rel="prev"', one)
        self.assertIn('href="/docs/two/" rel="next"', one)
        self.assertIn("AppGlider/edit/main/docs/one.md", one)
        self.assertNotIn("<script>", one)
        self.assertNotIn("style=", one)

        entries = json.loads(self.out("search.json"))
        part = [e for e in entries if e["heading"] == "Part"]
        self.assertEqual(len(part), 1)
        self.assertEqual(part[0]["url"], "/docs/one/#part")
        self.assertEqual(part[0]["page"], "One")
        self.assertEqual(part[0]["text"], "Details about widgets.")

        sitemap = self.out("sitemap.xml")
        for url in ("/docs/", "/docs/one/", "/docs/two/"):
            self.assertIn(f"<loc>https://appglider.org{url}</loc>", sitemap)

    def test_rebuild_is_clean_and_deterministic(self) -> None:
        self.pages("# Home\n", "# One\n")
        build_docs.build(self.root)
        stale = self.root / "site" / "docs" / "stale.html"
        stale.write_text("x")
        first = self.out("one/index.html")
        build_docs.build(self.root)
        self.assertFalse(stale.exists())
        self.assertEqual(first, self.out("one/index.html"))

    def test_check_writes_nothing(self) -> None:
        self.pages("# Home\n", "# One\n")
        build_docs.build(self.root, write=False)
        self.assertFalse((self.root / "site" / "docs").exists())


if __name__ == "__main__":
    unittest.main()
