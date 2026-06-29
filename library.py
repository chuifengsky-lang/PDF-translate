"""Study Library: create in-app folders (collections), add paper PDFs, open any
of them for translation, and let the LLM read the selected papers to produce a
summary + learning advice.

Folders/paper lists are persisted to library.json next to the app.
"""

import os
import json

import fitz  # PyMuPDF — lightweight full-text extraction for the summary

from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QFileDialog, QInputDialog, QTextBrowser, QMessageBox,
    QSplitter, QWidget,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from db import _app_dir

LIB_PATH = os.path.join(_app_dir(), "library.json")
TOTAL_CHAR_BUDGET = 40000


def load_library():
    try:
        with open(LIB_PATH, encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("folders", {})
            return data
    except Exception:
        return {"folders": {}}


def save_library(data):
    try:
        with open(LIB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def add_paper_to_library(parent, path):
    """Prompt for a folder (existing or new) and add `path` to it. Returns the
    folder name, or None if cancelled. Used by the main window's 加入学习库."""
    data = load_library()
    folders = list(data.get("folders", {}).keys())
    NEW = "＋ 新建文件夹…"
    items = folders + [NEW]
    choice, ok = QInputDialog.getItem(
        parent, "加入学习库", "选择文件夹：", items, 0, False)
    if not ok:
        return None
    if choice == NEW or not folders:
        name, ok2 = QInputDialog.getText(parent, "新建文件夹", "名称：")
        if not ok2 or not name.strip():
            return None
        choice = name.strip()
    lst = data.setdefault("folders", {}).setdefault(choice, [])
    if path not in lst:
        lst.append(path)
    save_library(data)
    return choice


# --------------------------------------------------------------------------- #
class StudyWorker(QThread):
    chunk = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, client, paths):
        super().__init__()
        self.client = client
        self.paths = paths

    def run(self):
        try:
            per_cap = max(2000, TOTAL_CHAR_BUDGET // max(1, len(self.paths)))
            papers = []
            for p in self.paths:
                try:
                    doc = fitz.open(p)
                    txt = "".join(page.get_text() for page in doc)
                    doc.close()
                except Exception:
                    txt = ""
                papers.append((os.path.basename(p), txt[:per_cap]))
            for delta in self.client.study_summary_stream(papers):
                self.chunk.emit(delta)
        except Exception as e:
            self.failed.emit(str(e))


class ResultDialog(QDialog):
    def __init__(self, parent, client, paths):
        super().__init__(parent)
        self.setWindowTitle("学习总结与建议")
        self.resize(720, 680)
        lay = QVBoxLayout(self)
        head = QLabel("正在阅读 %d 篇论文并生成总结…" % len(paths))
        head.setStyleSheet("color:#1565c0; font-size:13px;")
        lay.addWidget(head)
        self.browser = QTextBrowser()
        lay.addWidget(self.browser)
        row = QHBoxLayout()
        row.addStretch(1)
        save_btn = QPushButton("保存为 .md")
        save_btn.clicked.connect(self._save)
        row.addWidget(save_btn)
        lay.addLayout(row)

        self._buf = ""
        self._started = False
        self.worker = StudyWorker(client, paths)
        self.worker.chunk.connect(self._on_chunk)
        self.worker.failed.connect(lambda m: self.browser.setText("失败：" + m))
        self.worker.start()

    def _on_chunk(self, delta):
        if not self._started:
            self._buf = ""
            self._started = True
        self._buf += delta
        self.browser.setMarkdown(self._buf)

    def _save(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存", "学习总结.md", "Markdown (*.md)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(self._buf)
            except Exception as e:
                QMessageBox.warning(self, "保存失败", str(e))


# --------------------------------------------------------------------------- #
_BTN = ("QPushButton{padding:6px 10px;}")


class LibraryDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle("学习库")
        self.resize(860, 560)
        self.data = load_library()
        self._result = None

        outer = QVBoxLayout(self)
        tip = QLabel("左侧管理文件夹，右侧管理论文。双击论文可直接在主界面翻译；"
                     "勾选后可生成学习总结与建议。")
        tip.setStyleSheet("color:#666;")
        tip.setWordWrap(True)
        outer.addWidget(tip)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- left: folders ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("📁 文件夹"))
        self.folder_list = QListWidget()
        self.folder_list.currentItemChanged.connect(lambda *_: self._on_folder())
        lv.addWidget(self.folder_list)
        fb = QHBoxLayout()
        for text, fn in (("新建", self._new_folder),
                         ("重命名", self._rename_folder),
                         ("删除", self._del_folder)):
            b = QPushButton(text)
            b.setStyleSheet(_BTN)
            b.clicked.connect(fn)
            fb.addWidget(b)
        lv.addLayout(fb)
        splitter.addWidget(left)

        # ---- right: papers ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("📄 论文（勾选要学习的，双击打开翻译）"))
        self.paper_list = QListWidget()
        self.paper_list.itemDoubleClicked.connect(self._open_for_translation)
        rv.addWidget(self.paper_list)
        pb = QHBoxLayout()
        for text, fn in (("添加论文", self._add_papers),
                         ("移除", self._remove_paper),
                         ("打开翻译", self._open_for_translation),
                         ("全选/全不选", self._toggle_all)):
            b = QPushButton(text)
            b.setStyleSheet(_BTN)
            b.clicked.connect(fn)
            pb.addWidget(b)
        rv.addLayout(pb)
        go = QPushButton("生成学习总结与建议")
        go.setStyleSheet("QPushButton{padding:9px; font-size:14px;"
                         " background:#1565c0; color:white; border-radius:6px;}"
                         "QPushButton:hover{background:#1976d2;}")
        go.clicked.connect(self._generate)
        rv.addWidget(go)
        splitter.addWidget(right)

        splitter.setSizes([280, 560])
        outer.addWidget(splitter)
        self._reload_folders()

    # ---- folders ----
    def _reload_folders(self):
        self.folder_list.clear()
        for name, papers in self.data.get("folders", {}).items():
            it = QListWidgetItem("%s  (%d)" % (name, len(papers)))
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.folder_list.addItem(it)
        if self.folder_list.count():
            self.folder_list.setCurrentRow(0)
        self._on_folder()

    def _current_folder(self):
        it = self.folder_list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _on_folder(self):
        self.paper_list.clear()
        name = self._current_folder()
        if not name:
            return
        for p in self.data["folders"].get(name, []):
            label = os.path.basename(p)
            if not os.path.exists(p):
                label += "（文件丢失）"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.paper_list.addItem(it)

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "新建文件夹", "名称：")
        if ok and name.strip():
            self.data.setdefault("folders", {}).setdefault(name.strip(), [])
            save_library(self.data)
            self._reload_folders()

    def _rename_folder(self):
        old = self._current_folder()
        if not old:
            return
        name, ok = QInputDialog.getText(self, "重命名文件夹", "新名称：", text=old)
        if ok and name.strip() and name.strip() != old:
            folders = self.data["folders"]
            folders[name.strip()] = folders.pop(old)
            save_library(self.data)
            self._reload_folders()

    def _del_folder(self):
        name = self._current_folder()
        if name and name in self.data.get("folders", {}):
            if QMessageBox.question(self, "删除文件夹",
                                    "确定删除“%s”？（不会删除论文原文件）" % name) \
                    == QMessageBox.StandardButton.Yes:
                del self.data["folders"][name]
                save_library(self.data)
                self._reload_folders()

    # ---- papers ----
    def _add_papers(self):
        name = self._current_folder()
        if not name:
            QMessageBox.information(self, "提示", "请先新建或选择一个文件夹。")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择论文 PDF", "", "PDF (*.pdf)")
        if paths:
            lst = self.data["folders"].setdefault(name, [])
            for p in paths:
                if p not in lst:
                    lst.append(p)
            save_library(self.data)
            self._reload_folders()

    def _remove_paper(self):
        name = self._current_folder()
        it = self.paper_list.currentItem()
        if name and it:
            p = it.data(Qt.ItemDataRole.UserRole)
            lst = self.data["folders"].get(name, [])
            if p in lst:
                lst.remove(p)
                save_library(self.data)
                self._reload_folders()

    def _toggle_all(self):
        if self.paper_list.count() == 0:
            return
        # if any unchecked -> check all; else uncheck all
        any_unchecked = any(self.paper_list.item(i).checkState()
                            != Qt.CheckState.Checked
                            for i in range(self.paper_list.count()))
        new = Qt.CheckState.Checked if any_unchecked else Qt.CheckState.Unchecked
        for i in range(self.paper_list.count()):
            self.paper_list.item(i).setCheckState(new)

    def _open_for_translation(self, item=None):
        it = item if isinstance(item, QListWidgetItem) else self.paper_list.currentItem()
        if not it:
            QMessageBox.information(self, "提示", "请先选择一篇论文。")
            return
        p = it.data(Qt.ItemDataRole.UserRole)
        if not os.path.exists(p):
            QMessageBox.warning(self, "文件丢失", "找不到该 PDF：\n%s" % p)
            return
        self.parent_win.load_pdf(p)

    # ---- generate ----
    def _generate(self):
        client = getattr(self.parent_win, "client", None)
        if not client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key 与模型。")
            return
        paths = []
        for i in range(self.paper_list.count()):
            it = self.paper_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                p = it.data(Qt.ItemDataRole.UserRole)
                if os.path.exists(p):
                    paths.append(p)
        if not paths:
            QMessageBox.information(self, "提示", "请勾选至少一篇存在的论文。")
            return
        self._result = ResultDialog(self, client, paths)
        self._result.show()
