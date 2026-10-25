# Bundled runtime notices

AppGlider's AppImage contains unmodified upstream runtime libraries. Trimming removes
unused modules and tools; it does not change the retained libraries. Application artwork
and its notices are documented separately in `ATTRIBUTION.md`.

## Qt, PySide6 and Shiboken6

The current package uses Qt, PySide6 Essentials and Shiboken6 **6.12.0**, by The Qt Company
Ltd. and their contributors, under the **GNU Lesser General Public License version 3**.
The licence texts are included in `src/appglider/assets/licenses/LGPL-3.0-only.txt` and
`GPL-3.0-only.txt` in the source repository, and in the installed `appglider/assets/licenses/`
directory inside the AppImage.

Corresponding upstream source and build instructions are available from:

- [Qt 6.12.0 source archives](https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/):
  the retained Qt modules come from `qtbase`, `qtsvg` and `qtwayland`.
- [PySide6/Shiboken6 6.12.0 source archive](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz).
- [Qt build instructions](https://doc.qt.io/qt-6/build-sources.html) and
  [Qt for Python build instructions](https://doc.qt.io/qtforpython-6/building_from_source/index.html).
- [Qt's third-party notices](https://doc.qt.io/qt-6/licenses-used-in-qt.html).

The Qt and PySide libraries are loaded dynamically. You may modify and replace them,
including reverse engineering to debug those modifications, under the applicable licences.
To use replacement libraries, extract the AppImage into a writable directory:

```sh
./AppGlider-0.8.1-linux-x86_64.AppImage --appimage-extract
./squashfs-root/AppRun
```

Replace compatible libraries in
`squashfs-root/opt/python3.13/lib/python3.13/site-packages/PySide6/` and, when needed,
`shiboken6/`, then run `AppRun` again. The `Qt/lib/` and `Qt/plugins/` subdirectories hold
Qt's shared libraries and desktop plugins. Rebuild AppGlider with the packaging scripts
to create a new AppImage containing your replacements. No signature or licence check
prevents running the extracted application with modified libraries.

## Other runtime components

- **Python 3.13.16:** Python Software Foundation and contributors; the Python licence is
  preserved at `opt/python3.13/lib/python3.13/LICENSE.txt` inside the AppImage.
  [Source](https://www.python.org/ftp/python/3.13.16/Python-3.13.16.tar.xz).
- **ICU 73.2:** Unicode, Inc. and contributors; Unicode/ICU and third-party notices are
  included in `appglider/assets/licenses/ICU-73.2.txt`.
  [Source](https://github.com/unicode-org/icu/releases/tag/release-73-2).
- **certifi 2026.10.18:** certifi contributors; Mozilla Public License 2.0. Its licence
  is preserved in `certifi-2026.10.18.dist-info/licenses/` beside the installed package.
  [Source](https://github.com/certifi/python-certifi/tree/2026.10.18).
- **Ubuntu desktop libraries:** the packages pinned in `packaging/ubuntu-libs.json` retain
  their copyright and licence notices in
  `usr/share/doc/appglider/bundled-libraries/` inside the AppImage.
- **AppImage runtime:** AppImage contributors; the runtime pinned in
  `packaging/downloads.json` comes from the
  [type2-runtime project](https://github.com/AppImage/type2-runtime), which provides its
  source, runtime licence and third-party notices. This build pins the runtime from
  [commit fedfad51acc2860b2916a1eef32ba4a500ff31ef](https://github.com/AppImage/type2-runtime/tree/fedfad51acc2860b2916a1eef32ba4a500ff31ef).

Python package metadata and upstream licence files are retained with the bundled packages.
After extraction, this document and `ATTRIBUTION.md` are at
`squashfs-root/opt/python3.13/share/doc/appglider/`.
