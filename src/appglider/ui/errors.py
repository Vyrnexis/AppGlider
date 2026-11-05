"""Plain sentences for failures, shared by every place that shows an error."""

import errno
from pathlib import Path

STORAGE_HINT = "change the install folder in Settings → Storage"


def _folder(error: OSError) -> str:
    """The folder an error is about: the file's folder, or the folder itself."""
    name = error.filename
    if not isinstance(name, (str, Path)) or not str(name):
        return ""
    path = Path(name)
    return str(path if path.is_dir() else path.parent)


def describe_error(error: BaseException) -> str:
    """What went wrong and what to do next; other errors keep their own text."""
    if not isinstance(error, OSError):
        return str(error)
    folder = _folder(error)
    where = f" in {folder}" if folder else ""
    if error.errno in (errno.ENOSPC, errno.EDQUOT):
        return (
            f"There is not enough free disk space{where}. Free some space there or {STORAGE_HINT}."
        )
    if error.errno in (errno.EACCES, errno.EPERM):
        return (
            f"AppGlider is not allowed to change files{where}. "
            f"Check the folder's permissions or {STORAGE_HINT}."
        )
    if error.errno == errno.EROFS:
        return f"The disk{where} is read-only. Choose a folder that can be written: {STORAGE_HINT}."
    if error.errno == errno.ENAMETOOLONG:
        return f"A file name{where} is too long. Use a shorter name, or {STORAGE_HINT}."
    return str(error)
