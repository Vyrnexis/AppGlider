# GitHub Actions and releases

GitHub Actions runs the automation defined in
[`appimage.yml`](../.github/workflows/appimage.yml). The build script itself is
`packaging/build_appimage.py`; it works locally as well as on GitHub.

**CI (continuous integration)** checks changes automatically.
**CD (continuous delivery)** prepares releases for users. For AppGlider that means
producing downloadable AppImages and checksums.

## Current workflow

On pushes, pull requests and manual dispatch, the workflow:

1. Starts an Ubuntu 22.04 x86_64 runner with Python 3.13.
2. Installs application and development dependencies, using the pinned runtime versions.
3. Runs Ruff, formatting checks, mypy and the application tests.

Version-tag pushes and manual dispatch also install the packaging dependencies, build
the trimmed AppImage with bundled Python and Qt, and open it with a disposable first-run
home to check startup, certificates and its shortcut. They upload the AppImage and
`SHA256SUMS.txt` as a workflow artifact. Ordinary branch pushes and pull requests skip
packaging, so pushing a commit and its tag builds the AppImage only once.

Open **Actions → Linux AppImage**, select a successful tag or manual run and download
**AppGlider-x86_64** under **Artifacts**. GitHub requires a signed-in
account with repository access to download workflow artifacts. Restore the AppImage's
executable permission with `chmod +x` after downloading.

The build job has read-only repository permissions. Only the tag-triggered release job
can write releases. The YAML file must be committed and pushed to a GitHub repository
before GitHub can execute it.

## Release process

| Trigger | Result |
| --- | --- |
| Pull request or ordinary push | Checks and tests |
| Manual dispatch | Checks, tests and a test-build artifact |
| Version tag such as `v0.2.3` | Checks, tests, build and a draft GitHub Release |
| Maintainer publishes the draft | AppImage and checksum become release downloads |

To prepare a release:

1. Set the version in `pyproject.toml`, for example `0.2.3`, add it to the `<releases>` list
   in `packaging/io.github.vyrnexis.AppGlider.appdata.xml`, and update any version numbers
   written in the docs. The interface and HTTP User-Agent read the installed package version.
   Reinstall an editable development checkout after a version change. Commit the changes.
2. Push that commit to the GitHub repository.
3. Create and push the matching tag:

   ```sh
   git tag v0.2.3
   git push origin v0.2.3
   ```

The tag must exactly match `v` plus the project version; a mismatch stops the build.
After all checks pass, the release job downloads the tested artifact, verifies its checksum
and creates a draft on the repository's **Releases** page. The draft contains the AppImage,
`SHA256SUMS.txt`, the example catalog `artcraft.toml` (copied from `docs/catalogs/`) and
generated release notes. No additional personal access token is required;
the job uses GitHub's workflow token.

Test the AppImage on a real desktop, review the draft's notes, then choose **Publish release**.
Ordinary branch pushes, pull requests and manual builds do not create releases.
An existing release for the same tag is not overwritten; creating it again fails.
Automatic AppGlider self-updates are separate work.

## References

- [GitHub Actions and continuous integration](https://docs.github.com/en/actions/about-github-actions/about-continuous-integration)
- [Workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)
- [Downloading workflow artifacts](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/downloading-workflow-artifacts)
- [Managing releases](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)
