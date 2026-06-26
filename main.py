"""Entry point for PDF Translate.

Run:  python main.py
First launch: open "设置" (Settings), pick DeepSeek, paste your API key.
Then open a PDF, click "翻译本页" to translate the current page.
"""

import sys
from PyQt6.QtWidgets import QApplication
from main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
