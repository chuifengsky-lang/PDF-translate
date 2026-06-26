"""Diagnostic: show, for one page, which text blocks get translated vs left as
original (and why). Run in your venv:

    python diag.py 4          # page 4 (1-based); default 1

Paste the output back so the exclusion reason for any mis-handled paragraph is
visible (EXCL-table / EXCL-figure / translate / CAPTION-keep).
"""

import sys
from pdf_parser import (PDFDocument, _rect_center_in, _overlaps_any,
                        _CAPTION, _is_translatable)

PDF = sys.argv[2] if len(sys.argv) > 2 else "2202.12837v2.pdf"
pno = (int(sys.argv[1]) - 1) if len(sys.argv) > 1 else 0

doc = PDFDocument(PDF)
page = doc.doc.load_page(pno)
words = page.get_text("words")
data = page.get_text("dict")
pw = page.rect.width

table_rects = [t["bbox"] for t in doc._find_tables(page)] + \
    doc._rule_regions(page, words) + \
    doc._text_table_regions(words, pw)
figure_rects = doc._figure_regions(page, data)


def rr(r):
    return [round(r.x0), round(r.y0), round(r.x1), round(r.y1)]


print("== page", pno + 1, "page width", round(pw), "==")
print("TABLE regions:", [rr(r) for r in table_rects])
print("FIGURE regions:", [rr(r) for r in figure_rects])
print("-- prose blocks --")
for blk in data.get("blocks", []):
    if blk.get("type", 0) != 0:
        continue
    bx = blk["bbox"]
    txt = ""
    for ln in blk.get("lines", []):
        t = "".join(s["text"] for s in ln.get("spans", [])).strip()
        if t:
            txt = t
            break
    if not txt:
        continue
    cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
    if _CAPTION.match(txt):
        status = "CAPTION-keep"
    elif _rect_center_in(cx, cy, table_rects):
        status = "EXCL-table"
    elif _rect_center_in(cx, cy, figure_rects) or _overlaps_any(bx, figure_rects, 0.5):
        status = "EXCL-figure"
    elif not _is_translatable(txt):
        status = "skip-nonletter"
    else:
        status = "translate"
    box = ",".join(str(round(v)) for v in bx)
    print("  %-14s [%s] %r" % (status, box, txt[:55]))
