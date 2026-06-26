"""PDF parsing and rendering with PyMuPDF (fitz).

Extracts text blocks (paragraphs) with bounding boxes and per-word boxes so the
UI can do hit-testing for sentence/word selection. Also renders pages to pixmaps.
"""

import fitz  # PyMuPDF


class PDFDocument:
    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        # blocks[page] = [ {id, page, bbox, text}, ... ]
        self.blocks = {}
        # words[page] = [ (x0, y0, x1, y1, word, block_no, line_no, word_no), ... ]
        self.words = {}
        self._parse()

    @property
    def page_count(self):
        return self.doc.page_count

    def _parse(self):
        for pno in range(self.doc.page_count):
            page = self.doc.load_page(pno)
            page_blocks = []
            data = page.get_text("dict")
            for bidx, block in enumerate(data.get("blocks", [])):
                if block.get("type", 0) != 0:
                    continue  # skip images
                # join all spans into one paragraph string
                lines = []
                for line in block.get("lines", []):
                    spans = [s["text"] for s in line.get("spans", [])]
                    lines.append("".join(spans))
                text = " ".join(l.strip() for l in lines if l.strip()).strip()
                if not text:
                    continue
                page_blocks.append({
                    "id": "p%d_b%d" % (pno, bidx),
                    "page": pno,
                    "bbox": tuple(block["bbox"]),  # (x0, y0, x1, y1) in PDF points
                    "text": text,
                })
            self.blocks[pno] = page_blocks
            self.words[pno] = page.get_text("words")

    def page_size(self, pno):
        """Return (width, height) in PDF points."""
        r = self.doc.load_page(pno).rect
        return r.width, r.height

    def render_pixmap(self, pno, zoom=2.0):
        """Render a page to a fitz.Pixmap at the given zoom factor."""
        page = self.doc.load_page(pno)
        matrix = fitz.Matrix(zoom, zoom)
        return page.get_pixmap(matrix=matrix, alpha=False)

    def block_at(self, pno, x, y):
        """Return the block whose bbox contains the PDF-space point (x, y)."""
        for block in self.blocks.get(pno, []):
            x0, y0, x1, y1 = block["bbox"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return block
        return None

    def blocks_in_rect(self, pno, rect):
        """Return all blocks intersecting a PDF-space rect (x0, y0, x1, y1)."""
        rx0, ry0, rx1, ry1 = rect
        out = []
        for block in self.blocks.get(pno, []):
            x0, y0, x1, y1 = block["bbox"]
            if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
                out.append(block)
        return out

    def word_at(self, pno, x, y):
        """Return (word_str, bbox) for the word under PDF-space point, else None."""
        for w in self.words.get(pno, []):
            x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return word, (x0, y0, x1, y1)
        return None

    def close(self):
        self.doc.close()
