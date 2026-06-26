"""Main application window  (v4 — shared selection model + draggable popup).

Layout:
  Left  = original PDF, all pages stacked vertically.
  Right = translation: the original page is shown underneath; each text
          paragraph is covered with white and replaced by Chinese as it streams
          in, so figures/charts/equations remain visible.

Selection model (identical in BOTH panels):
  * Double-click a word        -> definition popup for that word.
  * Drag                       -> select the words under the rectangle.
  * Ctrl + drag                -> add that rectangle's words to the selection.
  * Ctrl + click a word        -> toggle that single word in the selection.
  * Left-click (with an active selection) -> translate the selected text (popup).
  * Right-click                -> clear all highlights.

The popup (definitions and selection translation) is draggable by its header
and resizable via the grip in the bottom-right corner.
"""

from PyQt6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsTextItem, QToolBar, QFileDialog, QLabel,
    QMessageBox, QSplitter, QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser,
    QPushButton, QSizeGrip, QApplication,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QColor, QBrush, QPen, QAction, QFont, QShortcut,
    QKeySequence,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF, QPointF, QTimer

from pdf_parser import PDFDocument
from db import Cache
from llm import LLMClient
from settings_dialog import SettingsDialog, load_settings

ZOOM = 2.0
PAGE_GAP = 14
HL_COLOR = QColor(255, 235, 59, 110)


def page_layout(doc):
    offsets, sizes = [], []
    y = 0.0
    total_w = 0.0
    for p in range(doc.page_count):
        w, h = doc.page_size(p)
        sizes.append((w, h))
        offsets.append(y)
        y += h * ZOOM + PAGE_GAP
        total_w = max(total_w, w * ZOOM)
    return offsets, sizes, total_w, y


# --------------------------------------------------------------------------- #
# Workers
# --------------------------------------------------------------------------- #
class TranslateWorker(QThread):
    """Translate a page/document, streaming each paragraph block."""
    started_block = pyqtSignal(str)
    chunk = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(self, client, cache, doc_name, jobs):
        super().__init__()
        self.client = client
        self.cache = cache
        self.doc_name = doc_name
        self.jobs = jobs

    def run(self):
        for block_id, text in self.jobs:
            try:
                self.started_block.emit(block_id)
                cached = self.cache.get_translation(
                    self.doc_name, block_id, self.client.model)
                if cached:
                    self.chunk.emit(block_id, cached)
                    continue
                full = ""
                for delta in self.client.translate_stream(text):
                    full += delta
                    self.chunk.emit(block_id, delta)
                if full:
                    self.cache.set_translation(
                        self.doc_name, block_id, self.client.model, text, full)
            except Exception as e:
                self.failed.emit(str(e))
                return


class StreamWorker(QThread):
    """Generic streaming worker: runs a producer() that yields text deltas."""
    chunk = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, producer):
        super().__init__()
        self.producer = producer

    def run(self):
        try:
            for delta in self.producer():
                self.chunk.emit(delta)
        except Exception as e:
            self.failed.emit(str(e))


def make_word_producer(client, cache, word, context):
    def produce():
        cached = cache.get_word(word, client.model)
        if cached:
            yield cached
            return
        full = ""
        for d in client.define_word_stream(word, context):
            full += d
            yield d
        if full:
            cache.set_word(word, client.model, full)
    return produce


def make_translate_producer(client, text):
    def produce():
        for d in client.translate_stream(text):
            yield d
    return produce


# --------------------------------------------------------------------------- #
# Floating popup (draggable + resizable)
# --------------------------------------------------------------------------- #
class WordPopup(QWidget):
    def __init__(self, parent):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumSize(220, 150)
        self.resize(400, 260)
        self.setStyleSheet(
            "WordPopup { background:#ffffff; border:1px solid #9e9e9e;"
            " border-radius:8px; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        # --- header (drag handle) ---
        self.header = QWidget()
        header_l = QHBoxLayout(self.header)
        header_l.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("")
        self.title.setStyleSheet("font-size:15px; color:#1565c0;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { border:none; font-size:15px; color:#666;"
            " border-radius:13px; }"
            "QPushButton:hover { background:#f0f0f0; color:#d32f2f; }")
        close_btn.clicked.connect(self.hide)
        header_l.addWidget(self.title)
        header_l.addStretch(1)
        header_l.addWidget(close_btn)
        self.header.setCursor(Qt.CursorShape.SizeAllCursor)
        outer.addWidget(self.header)

        self.browser = QTextBrowser()
        outer.addWidget(self.browser)

        # --- resize grip ---
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self))
        outer.addLayout(grip_row)

        self.gen = 0
        self._buf = ""
        self._drag_off = None

    def begin(self, title, gen):
        self.gen = gen
        self._buf = ""
        self.title.setText("<b>%s</b>" % title)
        self.browser.setText("…")

    def append(self, gen, delta):
        if gen != self.gen:
            return
        self._buf += delta
        self.browser.setText(self._buf)

    def set_error(self, gen, msg):
        if gen != self.gen:
            return
        self.browser.setText("失败：" + msg)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # --- drag to move (from the header area) ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            # drag only when grabbing the header / empty chrome, not the text body
            if child is None or child is self.header or child is self.title:
                self._drag_off = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_off)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_off = None
        super().mouseReleaseEvent(event)


# --------------------------------------------------------------------------- #
# Zoomable view base
# --------------------------------------------------------------------------- #
class ZoomableView(QGraphicsView):
    zoomRequested = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)


# --------------------------------------------------------------------------- #
# Shared selection logic (used by both panels)
# --------------------------------------------------------------------------- #
class SelectableView(ZoomableView):
    wordPicked = pyqtSignal(str, str, QPointF)     # word, context, global_pos
    translateSelection = pyqtSignal(str, QPointF)  # selected_text, global_pos

    SEL_Z = 6  # selection highlight z-value (above page/cover/text)

    def __init__(self):
        super().__init__()
        self.doc = None
        self.offsets = []
        self.sizes = []
        self.sel_words = []      # [{page, bbox, text}]
        self._sel_items = []
        self._rubber = None
        self._press_vp = None
        self._press_scene = None
        self._moved = False
        self._pending_pos = None
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._do_pending_click)

    # ---- page mapping ----
    def page_at_y(self, sy):
        for p in range(len(self.offsets)):
            top = self.offsets[p]
            if top <= sy < top + self.sizes[p][1] * ZOOM:
                return p
        return 0 if self.offsets else 0

    def _scene_to_pdf(self, sx, sy):
        for p in range(len(self.offsets)):
            top = self.offsets[p]
            if top <= sy < top + self.sizes[p][1] * ZOOM:
                return p, sx / ZOOM, (sy - top) / ZOOM
        return None

    # ---- selection highlight management ----
    def clear_selection(self):
        for it in self._sel_items:
            self.scene().removeItem(it)
        self._sel_items = []
        self.sel_words = []

    def _draw_word(self, page, bbox):
        x0, y0, x1, y1 = bbox
        top = self.offsets[page]
        rect = QRectF(x0 * ZOOM, top + y0 * ZOOM,
                     (x1 - x0) * ZOOM, (y1 - y0) * ZOOM)
        it = QGraphicsRectItem(rect)
        it.setBrush(QBrush(HL_COLOR))
        it.setPen(QPen(Qt.PenStyle.NoPen))
        it.setZValue(self.SEL_Z)
        self.scene().addItem(it)
        self._sel_items.append(it)

    def _has_word(self, page, bbox):
        for w in self.sel_words:
            if w["page"] == page and w["bbox"] == bbox:
                return True
        return False

    def _add_word(self, page, bbox, text):
        if self._has_word(page, bbox):
            return
        self.sel_words.append({"page": page, "bbox": bbox, "text": text})
        self._draw_word(page, bbox)

    def _toggle_word(self, page, bbox, text):
        if self._has_word(page, bbox):
            self.sel_words = [w for w in self.sel_words
                              if not (w["page"] == page and w["bbox"] == bbox)]
            self._redraw()
        else:
            self._add_word(page, bbox, text)

    def _redraw(self):
        for it in self._sel_items:
            self.scene().removeItem(it)
        self._sel_items = []
        for w in self.sel_words:
            self._draw_word(w["page"], w["bbox"])

    def _selected_text(self):
        ws = sorted(self.sel_words,
                    key=lambda w: (w["page"], round(w["bbox"][1] / 3.0), w["bbox"][0]))
        return " ".join(w["text"] for w in ws).strip()

    # ---- mouse ----
    def mousePressEvent(self, event):
        if not self.doc:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.RightButton:
            self.clear_selection()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_vp = event.pos()
            self._press_scene = self.mapToScene(event.pos())
            self._moved = False
            self._rubber = QGraphicsRectItem()
            self._rubber.setPen(QPen(QColor(33, 150, 243), 1))
            self._rubber.setBrush(QBrush(QColor(33, 150, 243, 40)))
            self._rubber.setZValue(self.SEL_Z + 1)
            self.scene().addItem(self._rubber)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber is not None and self._press_vp is not None:
            if (event.pos() - self._press_vp).manhattanLength() > 4:
                self._moved = True
            cur = self.mapToScene(event.pos())
            self._rubber.setRect(QRectF(self._press_scene, cur).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._rubber is not None:
            r = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber = None
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

            if self._moved:
                # drag selection (ctrl preserves existing)
                page = self.page_at_y(r.y() + r.height() / 2)
                top = self.offsets[page]
                pdf_rect = (r.x() / ZOOM, (r.y() - top) / ZOOM,
                            (r.x() + r.width()) / ZOOM,
                            (r.y() + r.height() - top) / ZOOM)
                if not ctrl:
                    self.clear_selection()
                for (x0, y0, x1, y1, word) in self.doc.words_in_rect(page, pdf_rect):
                    self._add_word(page, (x0, y0, x1, y1), word)
                event.accept()
                return

            # a click (no drag)
            sp = self.mapToScene(event.pos())
            if ctrl:
                mapped = self._scene_to_pdf(sp.x(), sp.y())
                if mapped:
                    page, x, y = mapped
                    hit = self.doc.word_near(page, x, y, tol=5.0)
                    if hit:
                        word, bbox = hit
                        self._toggle_word(page, bbox, word)
                event.accept()
                return

            # plain click -> defer (so a double-click can cancel it)
            gp = self.viewport().mapToGlobal(event.pos())
            self._pending_pos = QPointF(gp.x(), gp.y())
            self._click_timer.start(QApplication.doubleClickInterval() + 10)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _do_pending_click(self):
        pos = self._pending_pos
        self._pending_pos = None
        if pos is not None and self.sel_words:
            self.translateSelection.emit(self._selected_text(), pos)

    def mouseDoubleClickEvent(self, event):
        self._click_timer.stop()
        self._pending_pos = None
        if self._rubber is not None:
            self.scene().removeItem(self._rubber)
            self._rubber = None
        if self.doc and event.button() == Qt.MouseButton.LeftButton:
            sp = self.mapToScene(event.pos())
            mapped = self._scene_to_pdf(sp.x(), sp.y())
            if mapped:
                page, x, y = mapped
                hit = self.doc.word_near(page, x, y, tol=5.0)
                if hit:
                    word, _ = hit
                    block = self.doc.block_at(page, x, y)
                    context = block["text"] if block else word
                    gp = self.viewport().mapToGlobal(event.pos())
                    self.wordPicked.emit(word.strip(".,;:()[]\"'"), context,
                                         QPointF(gp.x(), gp.y()))
        event.accept()


# --------------------------------------------------------------------------- #
# Original PDF view
# --------------------------------------------------------------------------- #
class OriginalView(SelectableView):
    def load(self, doc):
        self.doc = doc
        self.offsets, self.sizes, W, H = page_layout(doc)
        self.scene().clear()
        self.clear_selection()
        self.scene().setSceneRect(QRectF(0, 0, W, H))
        for p in range(doc.page_count):
            pix = doc.render_pixmap(p, ZOOM)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888)
            item = QGraphicsPixmapItem(QPixmap.fromImage(img.copy()))
            item.setPos(0, self.offsets[p])
            self.scene().addItem(item)


# --------------------------------------------------------------------------- #
# Translation view (original page underneath, text covered as translated)
# --------------------------------------------------------------------------- #
class TranslationView(SelectableView):
    def __init__(self):
        super().__init__()
        self.text_items = {}
        self.covers = {}
        self.started = set()

    def load(self, doc):
        self.doc = doc
        self.offsets, self.sizes, W, H = page_layout(doc)
        self.scene().clear()
        self.clear_selection()
        self.text_items = {}
        self.covers = {}
        self.started = set()
        self.scene().setSceneRect(QRectF(0, 0, W, H))
        for p in range(doc.page_count):
            top = self.offsets[p]
            pix = doc.render_pixmap(p, ZOOM)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888)
            page_item = QGraphicsPixmapItem(QPixmap.fromImage(img.copy()))
            page_item.setPos(0, top)
            page_item.setZValue(0)
            self.scene().addItem(page_item)
            for block in doc.blocks.get(p, []):
                x0, y0, x1, y1 = block["bbox"]
                pad = 1.0
                cover = QGraphicsRectItem(
                    x0 * ZOOM - pad, top + y0 * ZOOM - pad,
                    (x1 - x0) * ZOOM + 2 * pad, (y1 - y0) * ZOOM + 2 * pad)
                cover.setBrush(QBrush(QColor("white")))
                cover.setPen(QPen(Qt.PenStyle.NoPen))
                cover.setZValue(1)
                cover.setVisible(False)
                self.scene().addItem(cover)
                self.covers[block["id"]] = cover

                px = max(9, int(round(block.get("size", 10.0) * ZOOM * 0.92)))
                item = QGraphicsTextItem("")
                item.setPos(x0 * ZOOM, top + y0 * ZOOM)
                item.setTextWidth(max(40.0, (x1 - x0) * ZOOM))
                item.document().setDocumentMargin(1)
                font = QFont()
                font.setPixelSize(px)
                item.setFont(font)
                item.setDefaultTextColor(QColor(20, 20, 20))
                item.setZValue(2)
                self.scene().addItem(item)
                self.text_items[block["id"]] = item

    def start_block(self, block_id):
        cover = self.covers.get(block_id)
        if cover:
            cover.setVisible(True)
        item = self.text_items.get(block_id)
        if item:
            item.setPlainText("")
        self.started.add(block_id)

    def append_chunk(self, block_id, delta):
        if block_id not in self.started:
            self.start_block(block_id)
        item = self.text_items.get(block_id)
        if item:
            item.setPlainText(item.toPlainText() + delta)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Translate v5 — 论文翻译（表格按格翻译）")
        self.resize(1300, 880)

        self.doc = None
        self.doc_name = ""
        self.cache = Cache()
        self.client = None
        self.workers = []
        self.zoom_level = 1.0
        self._syncing = False
        self.popup = None
        self._gen = 0

        self.original = OriginalView()
        self.translation = TranslationView()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.original)
        splitter.addWidget(self.translation)
        splitter.setSizes([650, 650])
        self.setCentralWidget(splitter)

        for view in (self.original, self.translation):
            view.wordPicked.connect(self.on_word_picked)
            view.translateSelection.connect(self.on_translate_selection)
            view.zoomRequested.connect(self.do_zoom)

        self.original.verticalScrollBar().valueChanged.connect(
            self._sync_from_original)
        self.translation.verticalScrollBar().valueChanged.connect(
            self._sync_from_translation)

        self._build_toolbar()
        self._build_shortcuts()
        self._init_client()

    def _build_toolbar(self):
        tb = QToolBar()
        self.addToolBar(tb)
        open_act = QAction("打开 PDF", self)
        open_act.triggered.connect(self.open_pdf)
        tb.addAction(open_act)
        tb.addSeparator()
        self.page_label = QLabel("  0 / 0  ")
        tb.addWidget(self.page_label)
        tb.addSeparator()
        for text, d in (("放大 +", 1), ("缩小 −", -1)):
            a = QAction(text, self)
            a.triggered.connect(lambda _, dd=d: self.do_zoom(dd))
            tb.addAction(a)
        fit = QAction("适应宽度", self)
        fit.triggered.connect(self.fit_width)
        tb.addAction(fit)
        tb.addSeparator()
        tr_page = QAction("翻译本页", self)
        tr_page.triggered.connect(self.translate_current_page)
        tb.addAction(tr_page)
        tr_all = QAction("翻译全部", self)
        tr_all.triggered.connect(self.translate_all)
        tb.addAction(tr_all)
        tb.addSeparator()
        set_act = QAction("设置", self)
        set_act.triggered.connect(self.open_settings)
        tb.addAction(set_act)

    def _build_shortcuts(self):
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self.do_zoom(1))
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self.do_zoom(1))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self.do_zoom(-1))
        QShortcut(QKeySequence("Ctrl+0"), self, self.fit_width)

    def _init_client(self):
        provider, model, api_key = load_settings()
        if api_key:
            try:
                self.client = LLMClient(provider, api_key, model)
            except Exception:
                self.client = None

    # ----- zoom -----
    def do_zoom(self, direction):
        factor = 1.15 if direction > 0 else 1 / 1.15
        new = max(0.2, min(5.0, self.zoom_level * factor))
        applied = new / self.zoom_level
        if abs(applied - 1.0) < 1e-6:
            return
        for v in (self.original, self.translation):
            v.scale(applied, applied)
        self.zoom_level = new

    def fit_width(self):
        if not self.doc:
            return
        w, _ = self.doc.page_size(0)
        scene_w = w * ZOOM
        avail = self.original.viewport().width() - 4
        if scene_w > 0 and avail > 0:
            target = max(0.2, min(5.0, avail / scene_w))
            applied = target / self.zoom_level
            for v in (self.original, self.translation):
                v.scale(applied, applied)
            self.zoom_level = target

    # ----- scroll sync -----
    def _sync_from_original(self, value):
        if self._syncing:
            return
        self._syncing = True
        self.translation.verticalScrollBar().setValue(value)
        self._syncing = False
        self._update_page_label()

    def _sync_from_translation(self, value):
        if self._syncing:
            return
        self._syncing = True
        self.original.verticalScrollBar().setValue(value)
        self._syncing = False
        self._update_page_label()

    def _update_page_label(self):
        if not self.doc:
            return
        center = self.original.mapToScene(
            self.original.viewport().rect().center()).y()
        page = self.original.page_at_y(center)
        self.page_label.setText("  %d / %d  " % (page + 1, self.doc.page_count))

    def current_page(self):
        if not self.doc:
            return 0
        center = self.original.mapToScene(
            self.original.viewport().rect().center()).y()
        return self.original.page_at_y(center)

    # ----- file -----
    def open_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", "", "PDF (*.pdf)")
        if not path:
            return
        if self.doc:
            self.doc.close()
        self.doc = PDFDocument(path)
        self.doc_name = path
        self.zoom_level = 1.0
        self.original.resetTransform()
        self.translation.resetTransform()
        self.original.load(self.doc)
        self.translation.load(self.doc)
        if self.client:
            self._fill_cached()
        QTimer.singleShot(0, self._after_open)

    def _after_open(self):
        self.fit_width()
        self._update_page_label()

    def _fill_cached(self):
        for p in range(self.doc.page_count):
            for block in self.doc.blocks.get(p, []):
                cached = self.cache.get_translation(
                    self.doc_name, block["id"], self.client.model)
                if cached:
                    self.translation.start_block(block["id"])
                    self.translation.append_chunk(block["id"], cached)

    # ----- translation -----
    def _start_translation(self, jobs):
        if not jobs:
            return
        if not self.client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key 与模型。")
            return
        worker = TranslateWorker(self.client, self.cache, self.doc_name, jobs)
        worker.started_block.connect(self.translation.start_block)
        worker.chunk.connect(self.translation.append_chunk)
        worker.failed.connect(self._on_worker_failed)
        self.workers.append(worker)
        worker.start()

    def translate_current_page(self):
        if not self.doc:
            return
        p = self.current_page()
        jobs = [(b["id"], b["text"]) for b in self.doc.blocks.get(p, [])
                if b.get("translatable", True)]
        self._start_translation(jobs)

    def translate_all(self):
        if not self.doc:
            return
        jobs = []
        for p in range(self.doc.page_count):
            for b in self.doc.blocks.get(p, []):
                if b.get("translatable", True):
                    jobs.append((b["id"], b["text"]))
        self._start_translation(jobs)

    def _on_worker_failed(self, msg):
        QMessageBox.critical(self, "翻译失败", msg)

    # ----- streaming popup (definitions + selection translation) -----
    def _show_stream(self, title, producer, global_pos):
        if self.popup is None:
            self.popup = WordPopup(self)
        self._gen += 1
        gen = self._gen
        popup = self.popup
        popup.begin(title, gen)
        popup.move(int(global_pos.x()) + 12, int(global_pos.y()) + 12)
        popup.show()
        popup.raise_()
        worker = StreamWorker(producer)
        worker.chunk.connect(lambda d, g=gen: popup.append(g, d))
        worker.failed.connect(lambda m, g=gen: popup.set_error(g, m))
        self.workers.append(worker)
        worker.start()

    def on_word_picked(self, word, context, global_pos):
        if not word:
            return
        if not self.client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key 与模型。")
            return
        producer = make_word_producer(self.client, self.cache, word, context)
        self._show_stream(word, producer, global_pos)

    def on_translate_selection(self, text, global_pos):
        if not text.strip():
            return
        if not self.client:
            QMessageBox.warning(self, "需要 API Key",
                                "请先在“设置”中填写 API Key 与模型。")
            return
        title = "翻译：" + (text[:18] + "…" if len(text) > 18 else text)
        producer = make_translate_producer(self.client, text)
        self._show_stream(title, producer, global_pos)

    # ----- settings -----
    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            self._init_client()
            if self.doc:
                self._fill_cached()

    def closeEvent(self, event):
        for w in self.workers:
            try:
                w.wait(2000)
            except Exception:
                pass
        if self.doc:
            self.doc.close()
        self.cache.close()
        super().closeEvent(event)
