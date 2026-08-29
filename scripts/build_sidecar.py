from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "services" / "knowledge-api"
ENTRYPOINT = SERVICE_DIR / "knowledge_api" / "server.py"
OUTPUT_DIR = ROOT / "src-tauri" / "binaries"
WORK_DIR = ROOT / ".sidecar-build"


def rust_host_target() -> str:
    result = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    raise RuntimeError("rustc did not report a host target")


def main() -> None:
    target = rust_host_target()
    executable_suffix = ".exe" if sys.platform == "win32" else ""
    bundle_name = "knowledge-api-sidecar"
    destination = OUTPUT_DIR / f"knowledge-api-{target}{executable_suffix}"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            # Tauri's shell plugin starts sidecars with CREATE_NO_WINDOW on Windows.
            # Keeping the console bootloader lets Tauri own and terminate the full
            # PyInstaller process tree instead of leaving a detached worker behind.
            "--console",
            "--name",
            bundle_name,
            "--distpath",
            str(WORK_DIR / "dist"),
            "--workpath",
            str(WORK_DIR / "work"),
            "--specpath",
            str(WORK_DIR),
            "--paths",
            str(SERVICE_DIR),
            "--collect-data",
            "rapidocr_onnxruntime",
            str(ENTRYPOINT),
        ],
        cwd=ROOT,
        check=True,
    )

    source = WORK_DIR / "dist" / f"{bundle_name}{executable_suffix}"
    shutil.copy2(source, destination)
    print(f"Sidecar ready: {destination}")


if __name__ == "__main__":
    main()
