"""The example catalog as apps, for tests that need a library of real-looking applications."""

from pathlib import Path

from appglider.core.catalog import KNOWN_IDS, custom_app
from appglider.core.catalogfile import read
from appglider.install.library import Library

STARTER = Path(__file__).parents[1] / "docs" / "catalogs" / "artcraft.toml"
_BY_REPO = {entry.repo: entry for entry in read(STARTER).entries}
STARTER_ENTRIES = tuple(_BY_REPO[repo] for repo in KNOWN_IDS)
APPS = tuple(
    custom_app(entry.repo, entry.name, not entry.stable_only, entry.build, entry.description)
    for entry in STARTER_ENTRIES
)


def stock(library: Library) -> Library:
    """Fill a fresh library with the example apps, as importing the starter catalog does."""
    library.import_catalog(STARTER_ENTRIES)
    return library
