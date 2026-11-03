import os
import tempfile
import unittest

# Isolate Qt settings and avoid needing a display.
_config = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _config.name
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QLabel

from quotadock.app import Overview


def tearDownModule():
    _config.cleanup()


class OverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def countdown_texts(self, window):
        return [widget.text() for widget, _, _ in window.countdowns]

    def test_demo_renders_full_and_mini_with_live_countdowns(self):
        window = Overview(demo=True)
        try:
            window.set_mini(False)
            texts = self.countdown_texts(window)
            self.assertEqual(len(texts), 5)  # one per demo quota window
            self.assertTrue(all(text.startswith("↻ ") for text in texts))
            window.set_mini(True)
            self.assertEqual(len(window.countdowns), 3)  # one headline per provider
            self.assertTrue(window.strip.isVisibleTo(window))
            self.assertFalse(window.scroll.isVisibleTo(window))
            # The most constrained Anthropic window (5-hour, 87%) is the headline.
            labels = [w.text() for w in window.strip.findChildren(QLabel)]
            self.assertIn("87%", labels)
            self.assertEqual(window.mini_button.text(), "▢ full")
        finally:
            window.quitting = True
            window.close()

    def test_mode_and_pin_persist(self):
        window = Overview(demo=True)
        window.set_mini(True)
        window.pin.setChecked(True)
        window.quitting = True
        window.close()
        again = Overview(demo=True)
        try:
            self.assertTrue(again.mini)
            self.assertEqual(again.pin.text(), "◆ pinned")
        finally:
            again.set_mini(False)
            again.pin.setChecked(False)
            again.quitting = True
            again.close()


if __name__ == "__main__":
    unittest.main()
