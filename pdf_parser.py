"""PDF parsing and rendering with PyMuPDF (fitz).

Extracts text blocks (paragraphs) with bounding boxes and per-word boxes so the
UI can do hit-testing for sentence/word selection. Also renders pages to pixmaps.
"""

import re
import fitz  # PyMuPDF

# A real table-of-contents line is "Title .... <page number>" — dot leader
# followed by a page number at the END of the line. This deliberately does NOT
# match a math ellipsis like "(x1,y1)...(xk,yk)" (dots mid-line, no trailing #).
_DOT_LEADER = re.compile(r"(\.\s?){3,}\s*\d{1,4}\s*$")
_CAPTION = re.compile(r"^\s*(Figure|Fig\.?|Table|图|表)\s*\d", re.I)  # captions
# Section heading: "4 Ground", "4.1 Gold labels", "A Details" (number/letter +
# space + a Capitalized word). NOT "12 models" (followed by lowercase).
_HEADING = re.compile(r"^\s*(\d+(\.\d+)*|[A-Z])\s+[A-Z]")


def _is_translatable(text):
    """True only if the text contains at least one Latin letter. Pure numbers,
    percentages, symbols (e.g. '12.3%', '±0.4', 'n/a') are left untranslated."""
    return any(c.isascii() and c.isalpha() for c in text)


def _rect_center_in(cx, cy, rects):
    for r in rects:
        if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
            return True
    return False


_MATH_CHARS = set("∈∉≤≥±×÷√∞∑∏∫∂∇→←↔⇒⇔≈≠≡∝⊂⊆⊃⊇∪∩…∼·|⟨⟩"
                  "αβγδϵεζηθλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ")


def _is_formula(text):
    """A standalone display equation (keep as original, don't translate). It has
    math symbols, almost no real (>=3-letter) words, and is short. Inline math
    inside a real paragraph has many words, so paragraphs still translate."""
    t = text.strip()
    if not t:
        return False
    words = re.findall(r"[A-Za-z]{3,}", t)
    has_math = any(c in _MATH_CHARS for c in t)
    return has_math and len(words) <= 1 and len(t) <= 50


def _near_any(bbox, rects, pad):
    """True if `bbox`, inflated by `pad`, intersects any rect."""
    bx0, by0, bx1, by1 = bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad
    for r in rects:
        if bx0 < r.x1 and bx1 > r.x0 and by0 < r.y1 and by1 > r.y0:
            return True
    return False


def _overlaps_any(bbox, rects, frac=0.35):
    """True if `bbox` overlaps any rect by at least `frac` of bbox's area —
    used to pull figure labels (whose centre may sit just outside) into the
    figure region."""
    bx0, by0, bx1, by1 = bbox
    area = max(1.0, (bx1 - bx0) * (by1 - by0))
    for r in rects:
        ix = min(bx1, r.x1) - max(bx0, r.x0)
        iy = min(by1, r.y1) - max(by0, r.y0)
        if ix > 0 and iy > 0 and (ix * iy) / area >= frac:
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
        # units = translation units; a unit may span several fragment blocks
        # (e.g. a paragraph continuing across a column or page break)
        self.units = []
        self._page_sizes = {}
        self._parse()
        self._build_units()

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

    @staticmethod
    def _group_rows(words):
        """Group words into VISUAL rows by y-coordinate. PyMuPDF often splits a
        table's cells into separate blocks/lines, so grouping by (block,line)
        misses the row structure — grouping by y reconstructs the real rows."""
        ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0]))
        rows = []
        cur = []
        cur_y = None
        for w in ws:
            yc = (w[1] + w[3]) / 2.0
            h = max(4.0, w[3] - w[1])
            if cur_y is None or abs(yc - cur_y) <= 0.6 * h:
                cur.append(w)
                if cur_y is None:
                    cur_y = yc
            else:
                rows.append(cur)
                cur = [w]
                cur_y = yc
        if cur:
            rows.append(cur)
        return rows

    @staticmethod
    def _row_column_count(row, gap_thresh):
        """Number of column groups in a visual row (words split where the gap
        between them exceeds gap_thresh)."""
        row = sorted(row, key=lambda w: w[0])
        groups = 1
        for i in range(1, len(row)):
            if row[i][0] - row[i - 1][2] > gap_thresh:
                groups += 1
        return groups

    def _is_tabular_band(self, words, band, pw):
        """Whether the text in `band` (a rule-bounded region, single column width)
        is laid out in columns. Uses visual-row grouping so it works even when
        table cells are separate blocks (and even when symbol columns like
        checkmarks aren't extractable). Prose -> 1 column; tables -> >=2."""
        gap_thresh = max(15.0, 0.035 * pw)
        inb = [w for w in words
               if w[1] >= band.y0 - 1 and w[3] <= band.y1 + 1
               and w[0] >= band.x0 - 1 and w[2] <= band.x1 + 1
               and 0.09 * pw <= (w[0] + w[2]) / 2.0 <= 0.91 * pw]
        rows = self._group_rows(inb)
        total = 0
        tabular = 0
        for r in rows:
            total += 1
            if len(r) >= 2 and self._row_column_count(r, gap_thresh) >= 2:
                tabular += 1
        if total == 0:
            return False
        return tabular >= 2 and tabular / total >= 0.5

    def _rule_regions(self, page, words):
        """Detect borderless (booktabs) tables via horizontal rules, but only
        keep regions whose content is actually column-structured. This stops
        titles/abstracts/body text that merely sit between rules from being
        excluded from translation."""
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
                    if it[0] == "l":
                        p1, p2 = it[1], it[2]
                        if abs(p1.y - p2.y) <= 1.0 and abs(p2.x - p1.x) >= minlen:
                            x0, x1 = sorted([p1.x, p2.x])
                            rules.append([x0, x1, (p1.y + p2.y) / 2])
                    elif it[0] == "re":
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

        # cluster rules that overlap horizontally
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

        MAXGAP = 220.0  # don't let far-apart rules merge across body text
        regions = []
        for g in groups.values():
            rs = sorted(g, key=lambda i: rules[i][2])
            # split the group into vertically-contiguous runs of rules
            run = [rs[0]]
            runs = []
            for idx in rs[1:]:
                if rules[idx][2] - rules[run[-1]][2] <= MAXGAP:
                    run.append(idx)
                else:
                    runs.append(run)
                    run = [idx]
            runs.append(run)
            for run in runs:
                if len(run) < 2:
                    continue
                xs0 = min(rules[i][0] for i in run)
                xs1 = max(rules[i][1] for i in run)
                ys0 = min(rules[i][2] for i in run)
                ys1 = max(rules[i][2] for i in run)
                if ys1 - ys0 < 2:
                    continue
                band = fitz.Rect(xs0, ys0 - 2, xs1, ys1 + 2)
                if self._is_tabular_band(words, band, pw):
                    regions.append(band)
        return regions

    @staticmethod
    def _aligned(run, tol=12.0):
        """A real table has a column boundary that lines up across (almost) all
        rows. Math / irregular prose has gaps at random x. Return True only if
        some gap-x is shared (within tol) by all-but-one of the run's rows."""
        need = max(2, len(run) - 1)
        for r in run:
            for g in r[4]:                        # candidate boundary x
                cnt = sum(1 for o in run
                          if any(abs(gg - g) <= tol for gg in o[4]))
                if cnt >= need:
                    return True
        return False

    def _text_table_regions(self, words, pw):
        """Rule-independent table detection from text layout. Per half-page
        column (so two-column prose isn't merged across the gutter). A run of
        >=2 consecutive rows that each have a column gap AND share an aligned
        column boundary becomes a table region. The alignment test rejects
        math-heavy prose, whose large gaps don't line up."""
        gap_thresh = max(18.0, 0.04 * pw)
        mid = pw / 2.0
        # Ignore words in the far-left/right page margins (rotated arXiv stamp,
        # line numbers). They otherwise create a fake aligned column boundary
        # that turns a whole prose column into a "table".
        words = [w for w in words
                 if 0.09 * pw <= (w[0] + w[2]) / 2.0 <= 0.91 * pw]
        regions = []
        for lo, hi in ((0.0, mid), (mid, pw)):
            half = [w for w in words if lo <= (w[0] + w[2]) / 2.0 < hi]
            rows = []  # (y0, y1, x0, x1, [gap_x, ...])
            for r in self._group_rows(half):
                if not r:
                    continue
                rs = sorted(r, key=lambda w: w[0])
                gaps = []
                for i in range(len(rs) - 1):
                    if rs[i + 1][0] - rs[i][2] > gap_thresh:
                        gaps.append((rs[i][2] + rs[i + 1][0]) / 2.0)
                rows.append((min(w[1] for w in rs), max(w[3] for w in rs),
                             min(w[0] for w in rs), max(w[2] for w in rs), gaps))
            rows.sort(key=lambda t: t[0])
            n = len(rows)
            i = 0
            while i < n:
                if not rows[i][4]:
                    i += 1
                    continue
                line_h = max(6.0, rows[i][1] - rows[i][0])
                last_y1 = rows[i][1]
                k = i + 1
                while k < n and rows[k][4] and \
                        (rows[k][0] - last_y1) <= 2.5 * line_h:
                    last_y1 = rows[k][1]
                    k += 1
                run = rows[i:k]
                if len(run) >= 3 and self._aligned(run):
                    ys0 = run[0][0]
                    ys1 = run[-1][1]
                    xs0 = min(r[2] for r in run)
                    xs1 = max(r[3] for r in run)
                    regions.append(fitz.Rect(xs0 - 2, ys0 - 2, xs1 + 2, ys1 + 2))
                i = max(k, i + 1)
        return regions

    @staticmethod
    def _merge_rects(rects, pad=10.0):
        """Union rects that overlap (after inflating by pad) so a figure's
        scattered pieces become one region."""
        boxes = [fitz.Rect(r) for r in rects]
        changed = True
        while changed:
            changed = False
            out = []
            for r in boxes:
                placed = False
                ri = fitz.Rect(r.x0 - pad, r.y0 - pad, r.x1 + pad, r.y1 + pad)
                for o in out:
                    oi = fitz.Rect(o.x0 - pad, o.y0 - pad, o.x1 + pad, o.y1 + pad)
                    if ri.intersects(oi):
                        o |= r
                        placed = True
                        changed = True
                        break
                if not placed:
                    out.append(fitz.Rect(r))
            boxes = out
        return boxes

    def _figure_regions(self, page, data):
        """Regions to leave as the original: figures/charts/diagrams. Collect ALL
        vector drawings (boxes, arrows, gray blocks) plus raster images, merge
        dense clusters, and return sizable regions. The whole figure — including
        its scattered text labels (Demonstrations / Test input / Prediction …) —
        is then excluded from translation. The caption below stays prose."""
        pw, ph = page.rect.width, page.rect.height
        regs = []
        for b in data.get("blocks", []):
            if b.get("type") == 1:                # raster image
                regs.append(fitz.Rect(b["bbox"]))
        try:
            for dr in page.get_drawings():
                r = fitz.Rect(dr.get("rect"))
                if r.is_empty or r.is_infinite:
                    continue
                if r.width < 1 and r.height < 1:
                    continue
                if r.width > 0.95 * pw and r.height > 0.95 * ph:
                    continue                      # full-page background
                regs.append(r)
        except Exception:
            pass
        merged = self._merge_rects(regs, pad=14.0)
        figs = []
        for r in merged:
            if r.width >= 50 and r.height >= 30:  # a real figure cluster
                # grow a little to swallow adjacent labels; almost nothing
                # downward so the caption underneath stays translatable
                figs.append(fitz.Rect(r.x0 - 10, r.y0 - 14, r.x1 + 10, r.y1 + 2))
        return figs

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
            words = page.get_text("words")
            page_w = page.rect.width

            # 1) detect tables first so prose inside them can be excluded.
            #    Combine the line-based finder with a horizontal-rule detector
            #    (gated by column structure) so borderless tables are caught
            #    without misclassifying titles/abstracts/body as tables.
            tables = self._find_tables(page)
            data = page.get_text("dict")
            table_rects = [t["bbox"] for t in tables] + \
                self._rule_regions(page, words) + \
                self._text_table_regions(words, page.rect.width)
            figure_rects = self._figure_regions(page, data)

            # 2) prose paragraphs (skip any block inside a table/figure region).
            for bidx, block in enumerate(data.get("blocks", [])):
                if block.get("type", 0) != 0:
                    continue  # skip images (shown via the page bitmap)
                bx = block["bbox"]

                block_lines = block.get("lines", [])
                line_texts = []
                line_meta = []  # (text, bbox, avg_size)
                for line in block_lines:
                    span_texts = []
                    lsizes = []
                    for s in line.get("spans", []):
                        st = s["text"]
                        # Drop ACL/review line numbers: a pure 1-4 digit number
                        # sitting in the far-left/right page margin.
                        sb = s.get("bbox", bx)
                        scx = (sb[0] + sb[2]) / 2.0
                        if st.strip().isdigit() and len(st.strip()) <= 4 and \
                                (scx < 0.09 * page_w or scx > 0.91 * page_w):
                            continue
                        span_texts.append(st)
                        if s.get("size"):
                            lsizes.append(s["size"])
                    ltext = "".join(span_texts).strip()
                    if ltext:
                        line_texts.append(ltext)
                        line_meta.append((
                            ltext, line.get("bbox", bx),
                            sum(lsizes) / len(lsizes) if lsizes else 10.0))

                if not line_texts:
                    continue

                joined = " ".join(line_texts).strip()
                # Figure/Table captions are ALWAYS translated, even if they sit
                # right at the edge of the figure/table region.
                is_caption = bool(_CAPTION.match(line_texts[0]))
                if not is_caption:
                    cx = (bx[0] + bx[2]) / 2
                    cy = (bx[1] + bx[3]) / 2
                    if _rect_center_in(cx, cy, table_rects):
                        continue
                    if _rect_center_in(cx, cy, figure_rects) or \
                            _overlaps_any(bx, figure_rects, 0.5):
                        continue
                    # short labels next to a figure (legends, axis titles, e.g.
                    # "Multi-choice", "F: Format", single letters) -> keep original
                    short = len(joined) <= 25 or len(joined.split()) <= 4
                    if short and _near_any(bx, figure_rects, 40.0):
                        continue

                # Table-of-contents / list: lines with dot leaders. Translate
                # each line on its own so entries + page numbers stay aligned
                # instead of being merged into one run.
                dotted = sum(1 for t in line_texts if _DOT_LEADER.search(t))
                if dotted >= 2 and len(line_meta) >= 2:
                    for li, (ltext, lbbox, lsize) in enumerate(line_meta):
                        page_blocks.append({
                            "id": "p%d_b%d_l%d" % (pno, bidx, li),
                            "page": pno,
                            "bbox": tuple(lbbox),
                            "text": ltext,
                            "size": lsize,
                            "translatable": _is_translatable(ltext),
                            "kind": "toc",
                        })
                    continue

                # normal paragraph: join wrapped lines with spaces
                text = " ".join(line_texts).strip()
                avg_size = sum(m[2] for m in line_meta) / len(line_meta)

                # Skip non-prose blocks: emails/URLs, and the rotated arXiv-style
                # side stamp (a tall, very narrow block). These are left as the
                # original to avoid garbled, overlapping output.
                bw = bx[2] - bx[0]
                bh = bx[3] - bx[1]
                pw = page.rect.width
                is_email = ("@" in text)
                is_vertical = (bh > 3.0 * max(bw, 1.0) and bw < 0.08 * pw
                               and len(text) > 4)
                translatable = _is_translatable(text) and not is_email \
                    and not is_vertical and not _is_formula(text)

                page_blocks.append({
                    "id": "p%d_b%d" % (pno, bidx),
                    "page": pno,
                    "bbox": tuple(bx),
                    "text": text,
                    "size": avg_size,
                    "translatable": translatable,
                    "kind": "para",
                })

            # 3) Tables are intentionally NOT translated. The table region is
            #    excluded from prose above, so the original table simply shows
            #    through from the page bitmap in the translation panel — perfect
            #    alignment, no garbling. (table detection is kept only to carve
            #    these regions out of the prose flow.)

            self.blocks[pno] = page_blocks
            self.words[pno] = words

    # ------------------------------------------------------------------ #
    # Paragraph grouping across columns / pages
    # ------------------------------------------------------------------ #
    @staticmethod
    def _continues(a, b):
        """True if paragraph fragment `b` is a continuation of `a` (the text
        was split by a column or page break, not by a real paragraph end)."""
        at = a["text"].rstrip()
        bt = b["text"].lstrip()
        if not at or not bt:
            return False
        # Section headings ("4 Ground Truth", "4.1 Gold labels", "A Details")
        # are standalone: never merge into them or out of them. A bare number
        # followed by a lowercase word ("12 models") is NOT a heading.
        if _HEADING.match(bt) or _HEADING.match(at):
            return False
        last = at[-1]
        if last == "-":               # hyphenated word split across the break
            return True
        if last in '.?!:;)]}"”’。！？；："':
            return False               # a's sentence/paragraph clearly ended
        fc = bt[0]
        if fc.isalpha() and fc.islower():
            return True                # lowercase start -> mid-sentence
        if fc.isdigit() or fc in "([":
            return True
        return False

    def _ordered_paras(self):
        """All translatable prose paragraphs across all pages, in reading order
        (per page: left column top->bottom, then right column)."""
        ordered = []
        for p in range(self.doc.page_count):
            pw = self.page_size(p)[0]
            mid = pw / 2.0
            paras = [b for b in self.blocks.get(p, [])
                     if b["kind"] == "para" and b.get("translatable", True)]

            def colkey(b):
                cx = (b["bbox"][0] + b["bbox"][2]) / 2.0
                return 0 if cx < mid else 1

            paras.sort(key=lambda b: (colkey(b), b["bbox"][1]))
            ordered.extend(paras)
        return ordered

    @staticmethod
    def _join_fragments(group):
        s = ""
        for b in group:
            t = b["text"].strip()
            if not t:
                continue
            if not s:
                s = t
            elif s.endswith("-"):
                s = s[:-1] + t        # de-hyphenate
            else:
                s = s + " " + t
        return s

    def _build_units(self):
        ordered = self._ordered_paras()
        groups = []
        if ordered:
            cur = [ordered[0]]
            for k in range(1, len(ordered)):
                if self._continues(ordered[k - 1], ordered[k]):
                    cur.append(ordered[k])
                else:
                    groups.append(cur)
                    cur = [ordered[k]]
            groups.append(cur)

        units = []
        for g in groups:
            text = self._join_fragments(g)
            units.append({
                "id": "u_%s" % g[0]["id"],
                "fragments": [b["id"] for b in g],
                "pages": sorted(set(b["page"] for b in g)),
                "text": text,
                "translatable": _is_translatable(text),
                "weights": [max(1, len(b["text"])) for b in g],
            })

        # non-paragraph blocks (TOC lines, etc.) stay as their own units
        for p in range(self.doc.page_count):
            for b in self.blocks.get(p, []):
                if b["kind"] == "para":
                    continue
                units.append({
                    "id": "u_%s" % b["id"],
                    "fragments": [b["id"]],
                    "pages": [p],
                    "text": b["text"],
                    "translatable": b.get("translatable", True),
                    "weights": [max(1, len(b["text"]))],
                })
        self.units = units

    def page_size(self, pno):
        """Return (width, height) in PDF points."""
        if pno in self._page_sizes:
            return self._page_sizes[pno]
        r = self.doc.load_page(pno).rect
        self._page_sizes[pno] = (r.width, r.height)
        return self._page_sizes[pno]

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
