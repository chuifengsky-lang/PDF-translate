"""Main application window.

Left panel  = original PDF (rendered, selectable).
Right panel = Chinese translation laid out in the same block positions.

Interactions:
  * Drag-select text in the original -> the matching translated block(s) highlight
    in the right panel (and vice-highlight in the left).
  * Double-click a single word -> popup with Chinese definition + concept note.
  * Settings -> enter API key / choose provider (DeepSeek by default).
"""

from PyQt6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsTextItem, QToolBar, QFileDialog, QLabel,
    QMessageBox, QSplitter, QWidget, QVBoxLayout, QDialog, QTextBrowser,
)
from PyQt6.QtGui import QPixmap, QImage, QColor, QBrush, QPen, QAction, QFont
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QPointF

from pdf_parser import PDFDocument
from db import Cache
from llm import LLMClient
from settings_dialog import SettingsDialog, load_settings

ZOOM = 2.0  # render zoom: scene units = pdf_points * ZOOM
HL_COLOR = QColor(255, 235, 59, 120)   # selection highlight (yellow, semi-transp)


# --------------------------------------------------------------------------- #
# Background workers
# --------------------------------------------------------------------------- #
class TranslateWorker(QThread):
    translated = pyqtSignal(str, str)   # (block_id, translation)
    failed = pyqtSignal(str)            # error message

    def __init__(self, client, cache, doc_name, jobs):
        super().__init__()
        self.client = client
        self.cache = cache
        self.doc_name = doc_name
        self.jobs = jobs  # list of (block_id, text)

    def run(self):
        for block_id, text in self.jobs:
            cached = self.cache.get_translation(self.doc_name, block_id, self.client.model)
            if cached:
                self.translated.emit(block_id, cached)
                continue
            try:
                result = self.client.translate(text)
            except Exception as e:
                self.failed.emit(str(e))
                return
            self.cache.set_translation(self.doc_name, block_id, self.client.model, text, result)
            self.translated.emit(block_id, result)


class WordWorker(QThread):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, client, cache, word, context):
        super().__init__()
        self.client = client
        self.cache = cache
        self.word = word
        self.context = context

    def run(self):
        cached = self.cache.get_word(self.word, self.client.model)
        if cached:
            self.done.emit(cached)
            return
        try:
            result = self.client.define_word(self.word, self.context)
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.cache.set_word(self.word, self.client.model, result)
        self.done.emit(result)


# --------------------------------------------------------------------------- #
# Word definition popup
# --------------------------------------------------------------------------- #
class WordPopup(QDialog):
    def __init__(self, parent, word):
        super().__init__(parent)
        self.setWindowTitle(word)
        self.setWindowFlag(Qt.WindowType.Popup, True)
        self.resize(360, 220)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setText("查询中…")
        layout.addWidget(self.browser)

    def set_text(self, text):
        self.browser.setText(text)


# --------------------------------------------------------------------------- #
# Original PDF view (selectable)
# --------------------------------------------------------------------------- #
class OriginalView(QGraphicsView):
    blocksSelected = pyqtSignal(list)        # list of block_ids
    wordPicked = pyqtSignal(str, str, QPointF)  # word, context_sentence, global_pos

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.doc = None
        self.page = 0
        self._origin = None
        self._rubber = None
        self._hl_items = []

    def load_page(self, doc, page):
        self.doc = doc
        self.page = page
        self.scene().clear()
        self._hl_items = []
        pix = doc.render_pixmap(page, ZOOM)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888)
        item = QGraphicsPixmapItem(QPixmap.fromImage(img.copy()))
        self.scene().addItem(item)
        self.scene().setSceneRect(QRectF(0, 0, pix.width, pix.height))

    def highlight_blocks(self, block_ids):
        for it in self._hl_items:
            self.scene().removeItem(it)
        self._hl_items = []
        if not self.doc:
            return
        for block in self.doc.blocks.get(self.page, []):
            if block["id"] in block_ids:
                x0, y0, x1, y1 = block["bbox"]
                rect = QRectF(x0 * ZOOM, y0 * ZOOM,
                             (x1 - x0) * ZOOM, (y1 - y0) * ZOOM)
                it = QGraphicsRectItem(rect)
                it.setBrush(QBrush(HL_COLOR))
                it.setPen(QPen(Qt.PenStyle.NoPen))
                self.scene().addItem(it)
                self._hl_items.append(it)

    # ----- mouse: drag select -----
    def mousePressEvent(self, event):
        if self.doc and event.button() == Qt.MouseButton.LeftButton:
            self._origin = self.mapToScene(event.pos())
            self._rubber = QGraphicsRectItem()
            self._rubber.setPen(QPen(QColor(33, 150, 243), 1))
            self._rubber.setBrush(QBrush(QColor(33, 150, 243, 40)))
            self.scene().addItem(self._rubber)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._origin is not None and self._rubber is not None:
            cur = self.mapToScene(event.pos())
            self._rubber.setRect(QRectF(self._origin, cur).normalized())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._origin is not None and self._rubber is not None:
            r = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber = None
            self._origin = None
            # map scene rect -> pdf points
            pdf_rect = (r.x() / ZOOM, r.y() / ZOOM,
                        (r.x() + r.width()) / ZOOM, (r.y() + r.height()) / ZOOM)
            blocks = self.doc.blocks_in_rect(self.page, pdf_rect)
            ids = [b["id"] for b in blocks]
            if ids:
                self.blocksSelected.emit(ids)
        super().mouseReleaseEvent(event)

    # ----- double click: single word -----
    def mouseDoubleClickEvent(self, event):
        if self.doc:
            sp = self.mapToScene(event.pos())
            x, y = sp.x() / ZOOM, sp.y() / ZOOM
            hit = self.doc.word_at(self.page, x, y)
            if hit:
                word, _ = hit
                block = self.doc.block_at(self.page, x, y)
                context = block["text"] if block else word
                gp = self.viewport().mapToGlobal(event.pos())
                self.wordPicked.emit(word.strip(".,;:()[]\"'"), context,
                                     QPointF(gp.x(), gp.y()))
        super().mouseDoubleClickEvent(event)


# --------------------------------------------------------------------------- #
# Translation view (mirrored layout)
# --------------------------------------------------------------------------- #
class TranslationView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.doc = None
        self.page = 0
        self.text_items = {}   # block_id -> QGraphicsTextItem
        self.hl_items = {}     # block_id -> QGraphicsRectItem (background)

    def load_page(self, doc, page):
        self.doc = doc
        self.page = page
        self.scene().clear()
        self.text_items = {}
        self.hl_items = {}
        w, h = doc.page_size(page)
        self.scene().setSceneRect(QRectF(0, 0, w * ZOOM, h * ZOOM))
        bg = QGraphicsRectItem(0, 0, w * ZOOM, h * ZOOM)
        bg.setBrush(QBrush(QColor("white")))
        bg.setPen(QPen(Qt.PenStyle.NoPen))
        self.scene().addItem(bg)

        for block in doc.blocks.get(page, []):
            x0, y0, x1, y1 = block["bbox"]
            item = QGraphicsTextItem("（待翻译…）")
            item.setPos(x0 * ZOOM, y0 * ZOOM)
            item.setTextWidth(max(40.0, (x1 - x0) * ZOOM))
            font = QFont()
            font.setPixelSize(15)
            item.setFont(font)
            item.setDefaultTextColor(QColor(120, 120, 120))
            self.scene().addItem(item)
            self.text_items[block["id"]] = item

    def set_translation(self, block_id, text):
        item = self.text_items.get(block_id)
        if item:
            item.setPlainText(text)
            item.setDefaultTextColor(QColor(20, 20, 20))

    def highlight_blocks(self, block_ids):
        for it in self.hl_items.values():
            self.scene().removeItem(it)
        self.hl_items = {}
        for bid in block_ids:
            item = self.text_items.get(bid)
            if not item:
                continue
            br = item.boundingRect()
            rect = QRectF(item.x(), item.y(), br.width(), br.height())
            hl = QGraphicsRectItem(rect)
            hl.setBrush(QBrush(HL_COLOR))
            hl.setPen(QPen(Qt.PenStyle.NoPen))
            hl.setZValue(-1)  # behind the text
            self.scene().addItem(hl)
            self.hl_items[bid] = hl
            self.ensureVisible(item)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Translate — 论文翻译")
        self.resize(1280, 860)

        self.doc = None
        self.doc_name = ""
        self.page = 0
        self.cache = Cache()
        self.client = None
        self.workers = []  # keep references so QThreads aren't GC'd

        self.original = OriginalView()
        self.translation = TranslationView()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.original)
        splitter.addWidget(self.translation)
        splitter.setSizes([640, 640])
        self.setCentralWidget(splitter)

        self.original.blocksSelected.connect(self.on_blocks_selected)
        self.original.wordPicked.connect(self.on_word_picked)

        self._build_toolbar()
        self._init_client()

    # ----- toolbar -----
    def _build_toolbar(self):
        tb = QToolBar()
        self.addToolBar(tb)

        open_act = QAction("打开 PDF", self)
        open_act.triggered.connect(self.open_pdf)
        tb.addAction(open_act)

        tb.addSeparator()
        prev_act = QAction("◀ 上一页", self)
        prev_act.triggered.connect(self.prev_page)
        tb.addAction(prev_act)

        self.page_label = QLabel("  0 / 0  ")
        tb.addWidget(self.page_label)

        next_act = QAction("下一页 ▶", self)
        next_act.triggered.connect(self.next_page)
        tb.addAction(next_act)

        tb.addSeparator()
        tr_act = QAction("翻译本页", self)
        tr_act.triggered.connect(self.translate_page)
        tb.addAction(tr_act)

        tb.addSeparator()
        set_act = QAction("设置", self)
        set_act.triggered.connect(self.open_settings)
        tb.addAction(set_act)

    def _init_client(self):
        provider, model, api_key = load_settings()
        if api_key:
            try:
                self.client = LLMClient(provider, api_key, model)
            except Exception:
                self.client = None

    # ----- file / pages -----
    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", "", "PDF (*.pdf)")
        if not path:
            return
        if self.doc:
            self.doc.close()
        self.doc = PDFDocument(path)
        self.doc_name = path
        self.page = 0
        self.show_page()

    def show_page(self):
        if not self.doc:
            return
        self.original.load_page(self.doc, self.page)
        self.translation.load_page(self.doc, self.page)
        self.page_label.setText("  %d / %d  " % (self.page + 1, self.doc.page_count))
        # pull any cached translations immediately
        if self.client:
            self._fill_cached()

    def _fill_cached(self):
        for block in self.doc.blocks.get(self.page, []):
            cached = self.cache.get_translation(self.doc_name, block["id"], self.client.model)
            if cached:
                self.translation.set_translation(block["id"], cached)

    def prev_page(self):
        if self.doc and self.page > 0:
            self.page -= 1
            self.show_page()

    def next_page(self):
        if self.doc and self.page < self.doc.page_count - 1:
            self.page += 1
            self.show_page()

    # ----- translation -----
    def translate_page(self):
        if not self.doc:
            return
        if not self.client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key。")
            return
        jobs = [(b["id"], b["text"]) for b in self.doc.blocks.get(self.page, [])]
        if not jobs:
            return
        worker = TranslateWorker(self.client, self.cache, self.doc_name, jobs)
        worker.translated.connect(self.translation.set_translation)
        worker.failed.connect(self._on_worker_failed)
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self.workers.append(worker)
        worker.start()

    def _on_worker_failed(self, msg):
        QMessageBox.critical(self, "翻译失败", msg)

    def _cleanup_worker(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)

    # ----- selection sync -----
    def on_blocks_selected(self, block_ids):
        self.original.highlight_blocks(block_ids)
        self.translation.highlight_blocks(block_ids)

    # ----- word popup -----
    def on_word_picked(self, word, context, global_pos):
        if not word:
            return
        if not self.client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key。")
            return
        popup = WordPopup(self, word)
        popup.move(int(global_pos.x()) + 8, int(global_pos.y()) + 8)
        popup.show()

        worker = WordWorker(self.client, self.cache, word, context)
        worker.done.connect(popup.set_text)
        worker.failed.connect(lambda m: popup.set_text("查询失败：" + m))
        worker.finished.connect(lambda: self._cleanup_worker(worker))
        self.workers.append(worker)
        worker.start()

    # ----- settings -----
    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._init_client()
            if self.doc:
                self._fill_cached()

    def closeEvent(self, event):
        for w in self.workers:
            w.wait(2000)
        if self.doc:
            self.doc.close()
        self.cache.close()
        super().closeEvent(event)
