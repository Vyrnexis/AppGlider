"""Check community catalog files before they are merged.

Usage: python packaging/check_catalog.py docs/catalogs/community.toml [more files]

For each file this confirms that it parses with no skipped entry, that no app is also in the
built-in example catalog, and, for every app, that the repository exists and has a release
with an x86_64 AppImage that AppGlider would install. It asks GitHub only for release
information, using AppGlider's own release discovery; no AppImage is downloaded, and
nothing is written outside a temporary folder.

Exit status: 0 when everything passes, 1 when an entry has a problem, 2 when GitHub's
request limit stopped the check (run it again later).
"""

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any
from urllib.request import Request

from appglider.core.catalog import custom_app
from appglider.core.catalogfile import CatalogEntry, CatalogError, parse
from appglider.github import assets, http
from appglider.github.release_info import discover_releases

EXAMPLE = Path(__file__).parents[1] / "src" / "appglider" / "assets" / "catalogs" / "artcraft.toml"
EXIT_OK, EXIT_PROBLEMS, EXIT_LIMITED = 0, 1, 2
CHOOSE_BUILD = 'several x86_64 AppImages are published; add build = "…" to choose one'

Say = Callable[[str], None]


def use_token(token: str) -> None:
    """Send a token with requests to GitHub's API, for the larger request limit in CI.

    Only this check does this; AppGlider itself never sends a token.
    """
    plain_open = http._open

    def open_with_token(req: Request, host: str) -> Any:
        if host == "api.github.com":
            req.add_header("Authorization", f"Bearer {token}")
        return plain_open(req, host)

    http._open = open_with_token


def example_repos() -> set[str]:
    return {entry.repo.casefold() for entry in parse(EXAMPLE.read_bytes()).entries}


def entry_problem(entry: CatalogEntry, cache_dir: Path) -> str:
    """Why this app cannot be installed, or an empty string when it can.

    Raises http.RateLimited when GitHub's request limit is reached.
    """
    app = custom_app(entry.repo, entry.name, not entry.stable_only, entry.build, entry.description)
    try:
        info = discover_releases(app, Event(), cache_dir, force=True)
    except http.RateLimited:
        raise
    except http.RepositoryMoved as moved:
        return f"the repository moved to {moved.new}; update repo to the new address"
    except assets.BuildChoiceRequired:
        return CHOOSE_BUILD
    except ValueError as error:  # includes http.Unreachable
        return str(error)[:300]
    choice = info.choice(not entry.stable_only)
    if choice is None:
        return info.error(not entry.stable_only)
    if choice.build_required:
        return CHOOSE_BUILD
    if choice.release is None:
        return choice.error or "no x86_64 AppImage that AppGlider can install was found"
    return ""


def check_file(path: Path, builtin: set[str], cache_dir: Path, say: Say) -> tuple[int, int]:
    """Check one catalog file. Returns (apps that passed, problems found).

    Raises http.RateLimited when GitHub's request limit is reached.
    """
    say(f"{path}")
    try:
        catalog = parse(path.read_bytes())
    except (OSError, CatalogError) as error:
        say(f"  PROBLEM  the file cannot be used: {error}")
        return 0, 1
    problems = 0
    for skipped in catalog.skipped:
        say(f"  PROBLEM  {skipped}")
        problems += 1
    passed = 0
    for entry in catalog.entries:
        if entry.repo.casefold() in builtin:
            problem = "already in the built-in example catalog"
        else:
            problem = entry_problem(entry, cache_dir)
        if problem:
            say(f"  PROBLEM  {entry.repo}: {problem}")
            problems += 1
        else:
            say(f"  OK       {entry.repo}")
            passed += 1
    return passed, problems


def main(argv: list[str], say: Say = print) -> int:
    if not argv:
        say("Usage: python packaging/check_catalog.py CATALOG.toml [CATALOG.toml ...]")
        return EXIT_PROBLEMS
    if token := os.environ.get("GITHUB_TOKEN", "").strip():
        use_token(token)
    builtin = example_repos()
    passed = problems = 0
    with tempfile.TemporaryDirectory(prefix="appglider-catalog-check-") as folder:
        for name in argv:
            try:
                ok, bad = check_file(Path(name), builtin, Path(folder), say)
            except http.RateLimited as error:
                say(f"WARNING  GitHub's request limit was reached: {error}")
                say("The check is incomplete. Run it again later; this is not a pass.")
                return EXIT_LIMITED
            passed += ok
            problems += bad
    say(f"{passed} passed, {problems} with problems.")
    return EXIT_PROBLEMS if problems else EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
