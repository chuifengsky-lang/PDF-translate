"""PDF parsing and rendering with PyMuPDF (fitz).

Extracts text blocks (paragraphs) with bounding boxes and per-word boxes so the
UI can do hit-testing for sentence/word selection. Also renders pages to pixmaps.
"""

import fitz  # PyMuPDF


def _is_translatable(text):
    """True only if the text contains at least one Latin letter. Pure numbers,
    percentages, symbols (e.g. '12.3%', '±0.4', 'n/a') are left untranslated."""
    return any(c.isascii() and c.isalpha() for c in text)


def _rect_center_in(cx, cy, rects):
    for r in rects:
        if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
            return True
    return False


class PDFDocument:
    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        # blocks[page] = [ {id, page, bbox, text, size, translatable, kind}, ... ]
        self.blocks = {}
        # words[page] = [ (x0, y0, x1, y1, word, block_no, line_no, word_no), ... ]
        self.words = {}
        self._parse()

    @property
    def page_count(self):
        return self.doc.page_count

    def _avg_size_in_rect(self, page, rect):
        try:
            d = page.get_text("dict", clip=rect)
        except Exception:
            return 9.0
        sizes = []
        for b in d.get("blocks", []):
            for l in b.get("lines", []):
                for s in l.get("spans", []):
                    if s.get("size"):
                        sizes.append(s["size"])
        return sum(sizes) / len(sizes) if sizes else 9.0

    def _rule_regions(self, page):
        """Detect booktabs-style tables (horizontal rules, no vertical borders).
        Find horizontal rule segments from the page vector drawings, cluster
        those that share an x-range, and treat the band spanned by a cluster of
        >=2 rules as a table region. Two-column prose has no such rules, so it
        is not misdetected."""
        pw = page.rect.width
        try:
            drawings = page.get_drawings()
        except Exception:
            return []
        minlen = max(80.0, 0.22 * pw)
        rules = []
        for d in drawings:
            for it in d.get("items", []):
                try:
                    if it[0] == "l":                      # line segment
                        p1, p2 = it[1], it[2]
                        if abs(p1.y - p2.y) <= 1.0 and abs(p2.x - p1.x) >= minlen:
                            x0, x1 = sorted([p1.x, p2.x])
                            rules.append([x0, x1, (p1.y + p2.y) / 2])
                    elif it[0] == "re":                   # thin wide rectangle
                        r = it[1]
                        if r.height <= 2.0 and r.width >= minlen:
                            rules.append([r.x0, r.x1, (r.y0 + r.y1) / 2])
                except Exception:
                    continue
        n = len(rules)
        if n < 2:
            return []
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for i in range(n):
            for j in range(i + 1, n):
                ax0, ax1 = rules[i][0], rules[i][1]
                bx0, bx1 = rules[j][0], rules[j][1]
                ov = min(ax1, bx1) - max(ax0, bx0)
                if ov > 0 and ov >= 0.5 * min(ax1 - ax0, bx1 - bx0):
                    parent[find(i)] = find(j)

        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        regions = []
        for g in groups.values():
            if len(g) < 2:
                continue
            xs0 = min(rules[i][0] for i in g)
            xs1 = max(rules[i][1] for i in g)
            ys0 = min(rules[i][2] for i in g)
            ys1 = max(rules[i][2] for i in g)
            if ys1 - ys0 < 2:
                continue  # rules all on the same line -> not a table band
            regions.append(fitz.Rect(xs0, ys0 - 2, xs1, ys1 + 2))
        return regions

    def _find_tables(self, page):
        """Return a list of table dicts: {bbox(Rect), cells:[(rect,text), ...]}.
        Defensive: PyMuPDF's table finder may be absent or raise — fall back
        to no tables in that case."""
        out = []
        try:
            finder = page.find_tables()
        except Exception:
            return out
        for tab in getattr(finder, "tables", []):
            try:
                rows = getattr(tab, "row_count", 0)
                cols = getattr(tab, "col_count", 0)
                if rows < 2 or cols < 2:
                    continue  # not a real grid
                trect = fitz.Rect(tab.bbox)
                cells = []
                for cb in tab.cells:
                    if cb is None:
                        continue
                    crect = fitz.Rect(cb)
                    if crect.is_empty:
                        continue
                    ctext = page.get_textbox(crect).strip()
                    cells.append((crect, ctext))
                if cells:
                    out.append({"bbox": trect, "cells": cells})
            except Exception:
                continue
        return out

    def _parse(self):
        for pno in range(self.doc.page_count):
            page = self.doc.load_page(pno)
            page_blocks = []

            # 1) detect tables first so prose inside them can be excluded.
            #    Combine the line-based finder with a horizontal-rule detector
            #    so borderless (booktabs) tables are caught too.
            tables = self._find_tables(page)
            table_rects = [t["bbox"] for t in tables] + self._rule_regions(page)

            # 2) prose paragraphs (skip any block sitting inside a table region)
            data = page.get_text("dict")
            for bidx, block in enumerate(data.get("blocks", [])):
                if block.get("type", 0) != 0:
                    continue  # skip images (shown via the page bitmap)
                bx = block["bbox"]
                cx = (bx[0] + bx[2]) / 2
                cy = (bx[1] + bx[3]) / 2
                if _rect_center_in(cx, cy, table_rects):
                    continue  # handled as table cells below
                lines = []
                sizes = []
                for line in block.get("lines", []):
                    span_texts = []
                    for s in line.get("spans", []):
                        span_texts.append(s["text"])
                        if s.get("size"):
                            sizes.append(s["size"])
                    lines.append("".join(span_texts))
                text = " ".join(l.strip() for l in lines if l.strip()).strip()
                if not text:
                    continue
                avg_size = sum(sizes) / len(sizes) if sizes else 10.0
                page_blocks.append({
                    "id": "p%d_b%d" % (pno, bidx),
                    "page": pno,
                    "bbox": tuple(bx),
                    "text": text,
                    "size": avg_size,
                    "translatable": _is_translatable(text),
                    "kind": "para",
                })

            # 3) Tables are intentionally NOT translated. The table region is
            #    excluded from prose above, so the original table simply shows
            #    through from the page bitmap in the translation panel — perfect
            #    alignment, no garbling. (table detection is kept only to carve
            #    these regions out of the prose flow.)

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

    def words_in_rect(self, pno, rect):
        """Return [(x0,y0,x1,y1,word), ...] for words intersecting a PDF-space
        rect, in reading order. Used for free-form selection.
        """
        rx0, ry0, rx1, ry1 = rect
        out = []
        for w in self.words.get(pno, []):
            x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
            if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
                out.append((x0, y0, x1, y1, word))
        return out

    def word_at(self, pno, x, y):
        """Return (word_str, bbox) for the word under PDF-space point, else None."""
        for w in self.words.get(pno, []):
            x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return word, (x0, y0, x1, y1)
        return None

    def word_near(self, pno, x, y, tol=4.0):
        """Like word_at but tolerant: if no exact hit, return the closest word
        whose box is within `tol` points. Makes double-click lookup reliable
        even when the click lands between glyphs or just off a word."""
        hit = self.word_at(pno, x, y)
        if hit:
            return hit
        best = None
        best_d = tol
        for w in self.words.get(pno, []):
            x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best_d = d
                best = (word, (x0, y0, x1, y1))
        return best

    def close(self):
        self.doc.close()
