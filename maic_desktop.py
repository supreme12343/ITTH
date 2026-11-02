"""Native Chromium window for the MAIC classroom application."""
from __future__ import annotations

import atexit
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox, QProgressBar, QVBoxLayout, QWidget
from PySide6.QtWebEngineWidgets import QWebEngineView

from maic_bridge import start_maic_server
from unified_memory import record_activity

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "logs" / "maic-desktop.log"


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("MAIC Classroom")

    startup = QMainWindow()
    startup.setWindowTitle("MAIC Classroom · Campus Suite")
    startup.resize(560, 190)
    startup.setMinimumSize(460, 170)
    panel = QWidget()
    layout = QVBoxLayout(panel)
    status = QLabel("Starting MAIC Classroom…")
    status.setStyleSheet("font-size: 16px; color: #26342c; padding: 12px;")
    progress = QProgressBar()
    progress.setRange(0, 0)
    layout.addWidget(status)
    layout.addWidget(progress)
    startup.setCentralWidget(panel)
    startup.show()
    app.processEvents()

    process: subprocess.Popen | None = None
    try:
        status.setText("Connecting to the classroom workspace…")
        app.processEvents()
        process, url = start_maic_server()
        if process is not None:
            atexit.register(lambda: process.poll() is None and process.terminate())

        record_activity("campus-suite", "opened-maic-desktop")
        startup.setWindowTitle("MAIC Classroom")
        status.setText("Loading the classroom…")
        app.processEvents()

        browser = QWebEngineView(startup)
        # QWebEngine's default profile is off-the-record. That made IndexedDB
        # look empty every time the classroom window was reopened. Keep its
        # browser database beside MAIC's local classroom files so courses,
        # drafts, preferences, and media survive app restarts.
        profile_dir = ROOT / "MAIC" / "data" / "campus-suite-profile"
        (profile_dir / "storage").mkdir(parents=True, exist_ok=True)
        (profile_dir / "cache").mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("campus-suite-maic", app)
        profile.setPersistentStoragePath(str(profile_dir / "storage"))
        profile.setCachePath(str(profile_dir / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies,
        )
        browser.setPage(QWebEnginePage(profile, browser))
        browser.setUrl(QUrl(url))
        startup.setCentralWidget(browser)
        startup.resize(1440, 960)
        startup.setMinimumSize(1024, 700)
        startup.show()
        app.exec()
    except Exception as exc:
        if process and process.poll() is None:
            process.terminate()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
        QMessageBox.critical(
            None,
            "MAIC could not start",
            f"{exc}\n\nDetails were saved to logs/maic-desktop.log.",
        )
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    main()
