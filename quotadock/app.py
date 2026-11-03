"""Qt desktop shell. Run with --demo for clearly marked example data."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Qt's whole-window opacity is supported through X11/XWayland. Do not silently
# pretend the compositor implements it when using native Wayland.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import Qt, QSettings, QTimer, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QMenu, QProgressBar, QPushButton, QScrollArea, QSizeGrip, QSlider,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .models import Quota, Snapshot
from .controller import RefreshController


STYLE = """
QWidget { color: #e6edf6; font-size: 13px; }
QMainWindow, QWidget#surface { background: #101722; }
QFrame#card { background: #192332; border: 1px solid #2b394b; border-radius: 12px; }
QLabel { background: transparent; }
QLabel#title { font-size: 25px; font-weight: 700; }
QLabel#muted { color: #9cacc2; }
QLabel#brand { font-size: 17px; font-weight: 600; }
QLabel#amount { font-size: 18px; font-weight: 600; }
QLabel#demo { color: #f1ca80; background: #352c20; padding: 9px; border-radius: 6px; }
QPushButton { background: #243247; border: 1px solid #35465f; padding: 7px 11px; border-radius: 7px; }
QPushButton:hover { background: #344762; }
QPushButton:checked { background: #254c4a; border: 1px solid #6bceba; color: #9ae5d2; }
QProgressBar { background: #2b384b; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #79d6be; border-radius: 3px; }
QSlider::groove:horizontal { background: #33445a; height: 4px; border-radius: 2px; }
QSlider::handle:horizontal { background: #91dfc9; width: 13px; margin: -5px 0; border-radius: 6px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #101722; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #3b4d64; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: #192332; border: 1px solid #35465f; padding: 5px; }
QMenu::item { padding: 7px 22px; }
QMenu::item:selected { background: #344762; }
"""


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#172536"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    for x, height in [(15, 18), (28, 30), (41, 40)]:
        painter.setBrush(QColor("#91dfc9"))
        painter.drawRoundedRect(x, 49-height, 8, height, 3, 3)
    painter.end()
    return QIcon(pixmap)


class ProviderCard(QFrame):
    def __init__(self, snapshot):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(label(snapshot.provider, "brand"), 1)
        status = label("Stale · " + snapshot.status if snapshot.stale else snapshot.status, "muted")
        status.setAlignment(Qt.AlignmentFlag.AlignRight)
        heading.addWidget(status)
        layout.addLayout(heading)
        layout.addWidget(label(snapshot.product, "muted"))
        for quota in snapshot.quotas:
            row = QHBoxLayout()
            row.addWidget(label(quota.label), 1)
            row.addWidget(label(f"{quota.remaining_percent:g}% left", "amount"))
            layout.addLayout(row)
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(round(quota.remaining_percent * 10))
            bar.setTextVisible(False)
            bar.setAccessibleName(f"{quota.label}: {quota.remaining_percent:g} percent remaining")
            if quota.remaining_percent <= 10:
                bar.setStyleSheet("QProgressBar::chunk { background: #f09393; }")
            elif quota.remaining_percent <= 30:
                bar.setStyleSheet("QProgressBar::chunk { background: #edc178; }")
            layout.addWidget(bar)
            layout.addWidget(label(f"{quota.used_percent:g}% used  ·  {quota.reset_text()}", "muted"))
        if snapshot.detail:
            layout.addWidget(label(snapshot.detail, "muted"))
        if snapshot.fetched_at:
            prefix = "Last success" if snapshot.stale else "Updated"
            layout.addWidget(label(f"{prefix} {snapshot.fetched_at.astimezone():%H:%M:%S}", "muted"))


class Overview(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.settings = QSettings("QuotaDock", "QuotaDock-demo" if demo else "QuotaDock")
        self.setWindowTitle("QuotaDock")
        self.setWindowIcon(make_icon())
        self.setMinimumSize(340, 330)
        self.resize(420, 710)
        self.setStyleSheet(STYLE)
        self.tray = None
        self.quitting = False
        self.snapshots = self.initial_snapshots()
        self.controller = None if demo else RefreshController(self)
        surface = QWidget()
        surface.setObjectName("surface")
        self.setCentralWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(20, 20, 20, 10)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label("QuotaDock", "title"), 1)
        self.pin = QPushButton("Pin")
        self.pin.setCheckable(True)
        self.pin.setToolTip("Keep the overview open and above other windows")
        self.pin.toggled.connect(self.set_pinned)
        heading.addWidget(self.pin)
        layout.addLayout(heading)
        layout.addWidget(label("Your AI subscriptions, at a glance.", "muted"))
        if demo:
            layout.addWidget(label("DEMO · Example values, not your account usage", "demo"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.cards = QWidget()
        self.cards.setObjectName("surface")
        self.card_layout = QVBoxLayout(self.cards)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(12)
        scroll.setWidget(self.cards)
        layout.addWidget(scroll, 1)
        controls = QHBoxLayout()
        controls.addWidget(label("Opacity", "muted"))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setAccessibleName("Window opacity")
        self.opacity_value = label("100%", "muted")
        self.opacity.valueChanged.connect(self.change_opacity)
        self.opacity.setValue(int(self.settings.value("opacity", 100)))
        controls.addWidget(self.opacity, 1)
        controls.addWidget(self.opacity_value)
        layout.addLayout(controls)
        footer = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        footer.addWidget(self.refresh_button)
        setup = QPushButton("Connections")
        setup.clicked.connect(self.show_connections)
        footer.addWidget(setup)
        footer.addStretch()
        footer.addWidget(QSizeGrip(self))
        layout.addLayout(footer)
        self.pin.setChecked(self.settings.value("pinned", False, type=bool))
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        if QApplication.platformName() == "wayland":
            self.opacity.setEnabled(False)
            self.opacity.setToolTip("Launch with QT_QPA_PLATFORM=xcb for whole-window opacity")
        self.render_cards()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.render_cards)
        self.timer.start(60_000)
        if self.controller:
            self.controller.changed.connect(self.update_readings)
            self.poll_timer = QTimer(self)
            self.poll_timer.timeout.connect(self.controller.refresh)
            self.poll_timer.start(30_000)
            QTimer.singleShot(0, self.controller.refresh)

    def initial_snapshots(self):
        if not self.demo:
            return [
                Snapshot("OpenAI", "Shared OpenAI plan", detail="Connect your Codex subscription to read its quota."),
                Snapshot("Anthropic", "Claude", detail="Connect your Claude subscription to read its quota."),
                Snapshot("Google", "Antigravity CLI", detail="Connect your Antigravity subscription to read its quota."),
            ]
        now = datetime.now(timezone.utc)
        return [
            Snapshot("OpenAI", "Shared plan · Codex, Work, Agents & Excel", (Quota("Weekly", 22, now+timedelta(days=1)),), "Example", "Chat conversations are not included."),
            Snapshot("Anthropic", "Claude", (Quota("5-hour", 73, now+timedelta(minutes=48)), Quota("Weekly", 39, now+timedelta(days=2, hours=14))), "Example"),
            Snapshot("Google", "Antigravity CLI", (Quota("Gemini · weekly", 18, now+timedelta(days=5)), Quota("Claude & GPT · weekly", 35, now+timedelta(days=5))), "Example"),
        ]

    def render_cards(self):
        while self.card_layout.count():
            item = self.card_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for snapshot in self.snapshots:
            self.card_layout.addWidget(ProviderCard(snapshot))
        self.card_layout.addStretch()

    def refresh(self):
        if self.controller:
            self.controller.refresh(manual=True)
        else:
            self.render_cards()

    def update_readings(self):
        self.snapshots = list(self.controller.readings.values())
        busy = bool(self.controller.jobs)
        self.refresh_button.setText("Refreshing…" if busy else "Refresh")
        self.refresh_button.setEnabled(not busy)
        self.render_cards()
        if self.tray:
            lines = ["QuotaDock"]
            for snapshot in self.snapshots:
                values = ", ".join(f"{q.label}: {q.remaining_percent:g}% left" for q in snapshot.quotas)
                lines.append(f"{snapshot.provider}: {values or snapshot.status}" + (" (stale)" if snapshot.stale else ""))
            self.tray.setToolTip("\n".join(lines))

    def show_connections(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("QuotaDock connections")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText("QuotaDock uses your existing CLI sign-ins. It reads quota only; it never sends prompts.")
        dialog.setInformativeText(
            "OpenAI: install Codex CLI and run `codex login`. Your shared plan quota is read through Codex app-server. Ordinary ChatGPT chats are excluded.\n\n"
            "Claude Code: run `claude auth login` if your session has expired. QuotaDock reads ~/.claude/.credentials.json (or CLAUDE_CONFIG_DIR).\n\n"
            "Antigravity CLI: open agy and sign in with Google. QuotaDock reads its existing desktop keyring login using secret-tool. Open agy to renew an expired sign-in. Legacy Gemini CLI credentials remain a fallback when no Antigravity login is present. API-key and enterprise authentication are not supported.\n\n"
            "Refreshes run every 5 minutes. Manual refreshes have a 30-second minimum interval. Credentials are never copied to QuotaDock settings."
        )
        dialog.exec()

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "pin"):
            QTimer.singleShot(200, self.dismiss_popup)
        return super().event(event)

    def dismiss_popup(self):
        if self.tray and not self.pin.isChecked() and not self.isActiveWindow() and not QApplication.activeModalWidget():
            self.hide()

    def change_opacity(self, value):
        self.opacity_value.setText(f"{value}%")
        self.setWindowOpacity(value / 100)
        self.settings.setValue("opacity", value)

    def set_pinned(self, pinned):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.pin.setText("Pinned" if pinned else "Pin")
        self.settings.setValue("pinned", pinned)
        if visible:
            self.show()

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        if self.tray and self.tray.isVisible() and not self.quitting:
            event.ignore()
            self.hide()
        else:
            event.accept()

    def quit(self):
        self.quitting = True
        self.close()
        QApplication.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Show explicitly labeled example data")
    parser.add_argument("--hidden", action="store_true", help="Start in the tray")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("QuotaDock")
    app.setFont(QFont("Sans Serif", 10))
    window = Overview(args.demo)
    if QSystemTrayIcon.isSystemTrayAvailable():
        app.setQuitOnLastWindowClosed(False)
        tray = QSystemTrayIcon(make_icon(), app)
        tray.setToolTip("QuotaDock · AI subscription usage")
        menu = QMenu()
        menu.addAction("Show / hide overview", window.toggle)
        menu.addAction("Refresh", window.refresh)
        menu.addSeparator()
        menu.addAction("Quit QuotaDock", window.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: window.toggle() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        window.tray = tray
    if not args.hidden or window.tray is None:
        window.show()
    return app.exec()
