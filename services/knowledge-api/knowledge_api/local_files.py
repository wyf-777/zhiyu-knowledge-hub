from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_local_document(path: Path) -> None:
    """Open a user-imported document with the operating system's default app."""
    resolved = path.resolve(strict=True)
    if sys.platform == "win32":
        os.startfile(resolved)  # type: ignore[attr-defined]
        return
    command = ["open", str(resolved)] if sys.platform == "darwin" else ["xdg-open", str(resolved)]
    subprocess.Popen(command, close_fds=True)  # noqa: S603
