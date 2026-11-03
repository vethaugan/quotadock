"""Qt desktop shell. Run with --demo for clearly marked example data."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

# Qt's whole-window opacity is supported through X11/XWayland. Do not silently
# pretend the compositor implements it when using native Wayland.
if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PySide6.QtCore import Qt, QSettings, QSize, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QProgressBar, QScrollArea, QSizeGrip, QSlider,
    QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from .models import Quota, Snapshot, countdown_text, percent_text, usage_tone
from .controller import RefreshController


# Dark HUD palette: mint accent on near-black teal.
BG = "#080f0f"
FG = "#daede8"
PRIMARY = "#2ff0b2"
PRIMARY_FG = "#030e0b"
MUTED = "#1f2626"
MUTED_FG = "#798a89"
ACCENT = "#1a2121"
WARN = "#f9ad26"
DANGER = "#ff4d57"
BORDER = "#0f382c"
TONES = {"normal": PRIMARY, "warning": WARN, "critical": DANGER}

FULL_MIN, FULL_SIZE = QSize(320, 280), QSize(380, 560)
MINI_MIN, MINI_WIDTH = QSize(320, 0), 560

STYLE = f"""
QWidget {{ color: {FG}; font-size: 11px; }}
QMainWindow, QWidget#surface {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel#brand, QLabel#muted, QLabel#small {{ color: {MUTED_FG}; font-size: 10px; }}
QLabel#name {{ font-size: 13px; font-weight: 700; }}
QLabel#countdown {{ color: {PRIMARY}; font-size: 10px; }}
QLabel#stale {{ color: {WARN}; font-size: 10px; }}
QLabel#demo {{ color: {WARN}; background: #2a2210; padding: 1px 6px; border-radius: 3px; font-size: 10px; }}
QFrame#rule {{ background: {BORDER}; border: none; }}
QToolButton {{ background: transparent; border: none; border-radius: 3px; padding: 2px 6px; color: {MUTED_FG}; font-size: 10px; }}
QToolButton:hover {{ background: {ACCENT}; color: {FG}; }}
QToolButton:checked {{ background: {PRIMARY}; color: {PRIMARY_FG}; }}
QToolButton:disabled {{ color: {MUTED}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QProgressBar {{ background: {MUTED}; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::groove:horizontal {{ background: {MUTED}; height: 4px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {PRIMARY}; width: 11px; margin: -4px 0; border-radius: 5px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: {BG}; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {MUTED}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {BG}; border: 1px solid {BORDER}; padding: 6px; }}
QMenu::item {{ padding: 6px 20px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QPushButton {{ background: {ACCENT}; border: 1px solid {BORDER}; padding: 5px 12px; border-radius: 4px; }}
QPushButton:hover {{ background: {MUTED}; }}
"""


def label(text, name=None, wrap=True):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def rule():
    line = QFrame()
    line.setObjectName("rule")
    line.setFixedHeight(1)
    return line


def meter(quota, height):
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setTextVisible(False)
    bar.setFixedHeight(height)
    if quota is not None:
        bar.setValue(round(quota.used_percent * 10))
        bar.setAccessibleName(f"{quota.label}: {percent_text(quota.used_percent)} percent used")
        color = TONES[usage_tone(quota.used_percent)]
        bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: {height // 2}px; }}")
    return bar


def big_percent(quota):
    """Headline 'used' number, colored by how close the window is to its limit."""
    widget = QLabel()
    widget.setTextFormat(Qt.TextFormat.RichText)
    if quota is None:
        widget.setText(f"<span style='font-size:20px;color:{MUTED_FG}'>—</span>")
    else:
        color = FG if usage_tone(quota.used_percent) == "normal" else TONES[usage_tone(quota.used_percent)]
        widget.setText(f"<span style='font-size:20px;color:{color}'>{percent_text(quota.used_percent)}</span>"
                       f"<span style='font-size:11px;color:{MUTED_FG}'>%</span>")
    widget.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    return widget


def status_line(snapshot):
    """Product, status and freshness; stale readings are called out in amber."""
    parts = [snapshot.product]
    if snapshot.stale:
        parts.append(f"stale · {snapshot.status}")
        if snapshot.fetched_at:
            parts.append(f"last ok {snapshot.fetched_at.astimezone():%H:%M}")
    else:
        parts.append(snapshot.status)
        if snapshot.fetched_at:
            parts.append(f"{snapshot.fetched_at.astimezone():%H:%M}")
    return label(" · ".join(parts), "stale" if snapshot.stale else "muted")


def quota_tooltip(snapshot):
    lines = [f"{snapshot.provider} · {snapshot.product}"]
    for quota in snapshot.quotas:
        lines.append(f"{quota.label}: {percent_text(quota.used_percent)}% used · {quota.reset_text()}")
    if not snapshot.quotas:
        lines.append(snapshot.status)
    if snapshot.stale:
        lines.append("Stale: showing the last successful reading")
    return "\n".join(lines)


def half_circle_icon():
    """Opacity glyph drawn directly; '◐' is missing from common monospace fonts."""
    pixmap = QPixmap(44, 44)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(MUTED_FG), 4))
    painter.drawEllipse(4, 4, 36, 36)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(MUTED_FG))
    painter.drawPie(4, 4, 36, 36, 90 * 16, 180 * 16)
    painter.end()
    return QIcon(pixmap)


def make_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#0d1a19"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14)
    for x, height in [(15, 18), (28, 30), (41, 40)]:
        painter.setBrush(QColor(PRIMARY))
        painter.drawRoundedRect(x, 49-height, 8, height, 3, 3)
    painter.end()
    return QIcon(pixmap)


class Overview(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.demo = demo
        self.settings = QSettings("QuotaDock", "QuotaDock-demo" if demo else "QuotaDock")
        self.setWindowTitle("QuotaDock")
        self.setWindowIcon(make_icon())
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.setFont(self.mono)
        # Style sheet font rules replace inherited fonts, so name the family here too.
        family = self.mono.family().replace('"', "")
        self.setStyleSheet(STYLE + f'QWidget {{ font-family: "{family}"; }}')
        self.tray = None
        self.quitting = False
        self.mini = None
        self.countdowns = []
        self.snapshots = self.initial_snapshots()
        self.controller = None if demo else RefreshController(self)

        surface = QWidget()
        surface.setObjectName("surface")
        self.setCentralWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.build_header())
        layout.addWidget(rule())

        if demo:
            self.demo_banner = label("Example values, not your account usage.", "stale")
            self.demo_banner.setContentsMargins(12, 6, 12, 0)
            layout.addWidget(self.demo_banner)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.cards = QWidget()
        self.cards.setObjectName("surface")
        self.card_layout = QVBoxLayout(self.cards)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)
        self.scroll.setWidget(self.cards)
        layout.addWidget(self.scroll, 1)

        self.strip = QWidget()
        self.strip_layout = QHBoxLayout(self.strip)
        self.strip_layout.setContentsMargins(12, 8, 12, 8)
        self.strip_layout.setSpacing(14)
        layout.addWidget(self.strip)
        self.strip_spacer = QWidget()
        layout.addWidget(self.strip_spacer, 1)

        self.grip_row = QWidget()
        grip_layout = QHBoxLayout(self.grip_row)
        grip_layout.setContentsMargins(0, 0, 2, 2)
        grip_layout.addStretch()
        grip_layout.addWidget(QSizeGrip(self))
        layout.addWidget(self.grip_row)

        self.opacity.setValue(int(self.settings.value("opacity", 100)))
        self.change_opacity(self.opacity.value())
        self.pin.setChecked(self.settings.value("pinned", False, type=bool))
        self.set_mini(self.settings.value("mini", False, type=bool), resize=False)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize_for_mode()
        if QApplication.platformName() == "wayland":
            self.opacity_button.setEnabled(False)
            self.opacity_button.setToolTip("Launch with QT_QPA_PLATFORM=xcb for whole-window opacity")

        # Countdowns tick every second; only the countdown labels are touched.
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start(1000)
        if self.controller:
            self.controller.changed.connect(self.update_readings)
            self.poll_timer = QTimer(self)
            self.poll_timer.timeout.connect(self.controller.refresh)
            self.poll_timer.start(30_000)
            QTimer.singleShot(0, self.controller.refresh)
        self.update_indicator()

    def build_header(self):
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 5, 6, 5)
        row.setSpacing(2)
        self.indicator = QLabel("●")
        self.indicator.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.indicator)
        row.addWidget(label("QUOTA.DOCK", "brand", wrap=False))
        if self.demo:
            demo = label("DEMO", "demo", wrap=False)
            demo.setToolTip("Example values, not your account usage")
            row.addSpacing(6)
            row.addWidget(demo)
        row.addStretch(1)

        self.refresh_button = self.tool_button("↻", "Refresh now (30-second minimum between attempts)", self.refresh)
        row.addWidget(self.refresh_button)
        row.addWidget(self.tool_button("?", "Connections and sign-in help", self.show_connections))

        self.opacity_button = self.tool_button("", "Window opacity")
        self.opacity_button.setIcon(half_circle_icon())
        self.opacity_button.setIconSize(QSize(11, 11))
        self.opacity_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.opacity_button)
        menu.setFont(self.mono)
        holder = QWidget()
        holder_row = QHBoxLayout(holder)
        holder_row.setContentsMargins(6, 4, 6, 4)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setMinimumWidth(130)
        self.opacity.setAccessibleName("Window opacity")
        self.opacity_value = label("100%", "muted", wrap=False)
        self.opacity_value.setMinimumWidth(32)
        self.opacity_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.opacity.valueChanged.connect(self.change_opacity)
        holder_row.addWidget(self.opacity, 1)
        holder_row.addWidget(self.opacity_value)
        action = QWidgetAction(menu)
        action.setDefaultWidget(holder)
        menu.addAction(action)
        self.opacity_button.setMenu(menu)
        row.addWidget(self.opacity_button)

        self.pin = self.tool_button("◇ pin", "Keep the overview open and above other windows")
        self.pin.setCheckable(True)
        self.pin.toggled.connect(self.set_pinned)
        row.addWidget(self.pin)
        self.mini_button = self.tool_button("▬ mini", "Switch to the compact strip", lambda: self.set_mini(not self.mini))
        row.addWidget(self.mini_button)
        return header

    def tool_button(self, text, tip, slot=None):
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if slot:
            button.clicked.connect(slot)
        return button

    def initial_snapshots(self):
        if not self.demo:
            return [
                Snapshot("OpenAI", "Shared OpenAI plan", detail="Connect your Codex subscription to read its quota."),
                Snapshot("Anthropic", "Claude", detail="Connect your Claude subscription to read its quota."),
                Snapshot("Google", "Antigravity CLI", detail="Connect your Antigravity subscription to read its quota."),
            ]
        now = datetime.now(timezone.utc)
        return [
            Snapshot("OpenAI", "Shared plan · Codex, Work, Agents & Excel", (Quota("Weekly", 22, now+timedelta(days=1, hours=3)),), "Example", "Chat conversations are not included.", now),
            Snapshot("Anthropic", "Claude Code", (Quota("5-hour", 87, now+timedelta(minutes=48, seconds=20)), Quota("Weekly", 39, now+timedelta(days=2, hours=14))), "Example", fetched_at=now),
            Snapshot("Google", "Antigravity CLI", (Quota("Gemini · weekly", 18, now+timedelta(days=5)), Quota("Claude & GPT · weekly", 68, now+timedelta(days=5))), "Example", fetched_at=now),
        ]

    # Rendering -----------------------------------------------------------

    def render_cards(self):
        self.countdowns = []
        for layout in (self.card_layout, self.strip_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        if self.mini:
            for snapshot in self.snapshots:
                self.strip_layout.addWidget(self.mini_column(snapshot), 1)
        else:
            for index, snapshot in enumerate(self.snapshots):
                if index:
                    self.card_layout.addWidget(rule())
                self.card_layout.addWidget(self.provider_section(snapshot))
            self.card_layout.addStretch()
        self.update_countdowns()

    def provider_section(self, snapshot):
        section = QWidget()
        section.setToolTip(quota_tooltip(snapshot))
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)
        heading = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(label(snapshot.provider, "name"))
        names.addWidget(status_line(snapshot))
        heading.addLayout(names, 1)
        heading.addWidget(big_percent(snapshot.headline))
        layout.addLayout(heading)
        for quota in snapshot.quotas:
            layout.addSpacing(3)
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(label(quota.label, "muted"), 1)
            row.addWidget(label(f"{percent_text(quota.used_percent)}%", "small", wrap=False))
            countdown = label("", "countdown", wrap=False)
            countdown.setMinimumWidth(72)
            countdown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            countdown.setToolTip(quota.reset_text())
            self.countdowns.append((countdown, quota, "↻ {}"))
            row.addWidget(countdown)
            layout.addLayout(row)
            layout.addWidget(meter(quota, 4))
        if snapshot.detail:
            layout.addSpacing(2)
            layout.addWidget(label(snapshot.detail, "muted"))
        return section

    def mini_column(self, snapshot):
        quota = snapshot.headline
        column = QWidget()
        column.setToolTip(quota_tooltip(snapshot))
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        top = QHBoxLayout()
        top.addWidget(label(snapshot.provider, "muted", wrap=False), 1)
        value = label("—" if quota is None else f"{percent_text(quota.used_percent)}%", wrap=False)
        if quota is not None and usage_tone(quota.used_percent) != "normal":
            value.setStyleSheet(f"color: {TONES[usage_tone(quota.used_percent)]};")
        top.addWidget(value)
        layout.addLayout(top)
        layout.addWidget(meter(quota, 3))
        if quota is None:
            layout.addWidget(label(snapshot.status, "stale" if snapshot.stale else "muted", wrap=False))
        else:
            countdown = label("", "stale" if snapshot.stale else "countdown", wrap=False)
            self.countdowns.append((countdown, quota, "↻ {} · stale" if snapshot.stale else "↻ {}"))
            layout.addWidget(countdown)
        return column

    def tick(self):
        if self.isVisible():
            self.update_countdowns()

    def update_countdowns(self):
        now = datetime.now(timezone.utc)
        for widget, quota, template in self.countdowns:
            widget.setText(template.format(countdown_text(quota.resets_at, now)))

    def update_indicator(self):
        busy = bool(self.controller and self.controller.jobs)
        degraded = any(s.stale or (s.fetched_at is None and not s.quotas) for s in self.snapshots)
        if busy:
            color, tip = MUTED_FG, "Refreshing…"
        elif degraded:
            color, tip = WARN, "Some providers are stale or not connected"
        else:
            color, tip = PRIMARY, "All providers up to date"
        self.indicator.setStyleSheet(f"color: {color}; font-size: 9px; padding-right: 4px;")
        self.indicator.setToolTip(tip)

    # Modes and window state ---------------------------------------------

    def set_mini(self, mini, resize=True):
        if self.mini is not None and resize:
            self.settings.setValue("mini_size" if self.mini else "full_size", self.size())
        self.mini = mini
        self.settings.setValue("mini", mini)
        self.mini_button.setText("▢ full" if mini else "▬ mini")
        self.mini_button.setToolTip("Switch to the full overview" if mini else "Switch to the compact strip")
        self.scroll.setVisible(not mini)
        self.grip_row.setVisible(not mini)
        self.strip.setVisible(mini)
        self.strip_spacer.setVisible(mini)
        if self.demo:
            self.demo_banner.setVisible(not mini)
        self.setMinimumSize(MINI_MIN if mini else FULL_MIN)
        self.render_cards()
        if resize:
            self.resize_for_mode()

    def resize_for_mode(self):
        saved = self.settings.value("mini_size" if self.mini else "full_size")
        if isinstance(saved, QSize) and saved.isValid():
            self.resize(saved)
        elif self.mini:
            self.centralWidget().layout().activate()
            self.resize(MINI_WIDTH, self.centralWidget().sizeHint().height())
        else:
            self.resize(FULL_SIZE)

    def refresh(self):
        if self.controller:
            self.controller.refresh(manual=True)
        else:
            self.render_cards()

    def update_readings(self):
        self.snapshots = list(self.controller.readings.values())
        self.refresh_button.setEnabled(not self.controller.jobs)
        self.render_cards()
        self.update_indicator()
        if self.tray:
            lines = ["QuotaDock"]
            for snapshot in self.snapshots:
                values = ", ".join(f"{q.label}: {percent_text(q.used_percent)}% used" for q in snapshot.quotas)
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
        if (self.tray and not self.pin.isChecked() and not self.isActiveWindow()
                and not QApplication.activeModalWidget() and not QApplication.activePopupWidget()):
            self.hide()

    def change_opacity(self, value):
        self.opacity_value.setText(f"{value}%")
        self.setWindowOpacity(value / 100)
        self.settings.setValue("opacity", value)

    def set_pinned(self, pinned):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.pin.setText("◆ pinned" if pinned else "◇ pin")
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
            self.update_countdowns()

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("mini_size" if self.mini else "full_size", self.size())
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
