"""Entry point for PDF Translate.

Run:  python main.py

First launch: open "设置" (Settings), choose DeepSeek, pick a model
(deepseek-v4-flash / deepseek-v4-pro) and paste your API key.
Then open a PDF, scroll to move through pages, and click "翻译本页".

If anything goes wrong, the full traceback is written to error.log next to
this file AND shown in a dialog (so the app no longer dies silently).
"""

import sys
import os
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


LOG_PATH = os.path.join(_app_dir(), "error.log")


def _log_and_show(text):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(text + "\n" + "-" * 60 + "\n")
    except Exception:
        pass
    try:
        QMessageBox.critical(None, "程序错误 (已写入 error.log)", text[-1800:])
    except Exception:
        pass


def excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _log_and_show(msg)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    sys.excepthook = excepthook  # catch any unhandled main-thread exception
    app = QApplication(sys.argv)
    from main_window import MainWindow  # imported after excepthook is installed
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
