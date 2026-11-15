from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


class BrowserBridgeError(RuntimeError):
    pass


class BrowserBridge:
    def __init__(self, project_root: Path, profile: Path, cdp_port: int = 9223):
        self.project_root = project_root
        self.profile = profile
        self.cdp_port = cdp_port
        self.data_dir = project_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.cdp_port}"

    def open(self, url: str) -> None:
        if not self._cdp_ready():
            self._start_chrome("about:blank")
            self._wait_for_cdp()
        self._ensure_watcher()
        request = urllib.request.Request(
            f"{self.cdp_url}/json/new?{urllib.parse.quote(url, safe='')}", method="PUT"
        )
        with urllib.request.urlopen(request, timeout=5):
            pass

    def status(self) -> dict[str, bool]:
        return {"chrome": self._cdp_ready(), "watcher": self._watcher_running()}

    def _cdp_ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=1) as response:
                return response.status == 200
        except OSError:
            return False

    def _start_chrome(self, url: str) -> None:
        chrome = next((path for path in (
            shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
            "/opt/google/chrome/google-chrome",
        ) if path and Path(path).exists()), None)
        if not chrome:
            raise BrowserBridgeError("Google Chrome was not found")
        self.profile.mkdir(parents=True, exist_ok=True)
        command = [chrome, f"--user-data-dir={self.profile}", "--no-first-run",
                   "--no-default-browser-check", "--remote-debugging-address=127.0.0.1",
                   f"--remote-debugging-port={self.cdp_port}", url]
        log = open(self.data_dir / "chrome.log", "ab")
        process = subprocess.Popen(command, cwd=self.project_root, stdout=log, stderr=log,
                                   start_new_session=True)
        (self.data_dir / "chrome.pid").write_text(str(process.pid), encoding="ascii")

    def _wait_for_cdp(self) -> None:
        for _ in range(40):
            if self._cdp_ready():
                return
            time.sleep(0.25)
        raise BrowserBridgeError("Chrome started but its local DevTools endpoint is unavailable")

    def _ensure_watcher(self) -> None:
        if self._watcher_running():
            return
        script = self.project_root / "scripts" / "watch_youpass.py"
        log = open(self.data_dir / "watch-youpass.log", "ab")
        env = {**os.environ, "PYTHONPATH": str(self.project_root)}
        process = subprocess.Popen(
            [sys.executable, str(script), "--cdp", self.cdp_url], cwd=self.project_root,
            env=env, stdout=log, stderr=log, start_new_session=True,
        )
        (self.data_dir / "watch-youpass.pid").write_text(str(process.pid), encoding="ascii")

    def _watcher_running(self) -> bool:
        pid_file = self.data_dir / "watch-youpass.pid"
        try:
            pid = int(pid_file.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False
