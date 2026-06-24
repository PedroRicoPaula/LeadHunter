"""
Nexus OS — Launcher
Starts FastAPI (port 8000) + Vite dev server (port 5173).

Usage:
  python start.py
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
WEB_DIR = ROOT / "web"


def main():
    print("Starting Nexus OS...")
    print("  API  → http://localhost:8000")
    print("  UI   → http://localhost:5173")
    print("  Press Ctrl+C to stop\n")

    api_proc = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "uvicorn", "api.main:app",
         "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=str(ROOT),
    )

    time.sleep(1.5)

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    web_proc = subprocess.Popen(
        [npm, "run", "dev"],
        cwd=str(WEB_DIR),
    )

    try:
        api_proc.wait()
    except KeyboardInterrupt:
        print("\nStopping...")
        api_proc.terminate()
        web_proc.terminate()


if __name__ == "__main__":
    main()
