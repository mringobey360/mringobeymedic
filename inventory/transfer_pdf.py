"""Professional PDF "Stock Transfer Note" for a completed branch stock transfer (V54.89).

Generated on the server with ReportLab so the manager gets a ready-made A4 document with one
click: no browser print dialog, no paper-size / margin / header-footer settings to fiddle with.

Every table cell is a Paragraph (wraps long names instead of overflowing) and every piece of
user-supplied text is XML-escaped so a product called "Vitamin <B> & Co" can never crash the export.
"""
import io
from decimal import Decimal
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from core.pdf_utils import BRAND_TEAL, BRAND_TEAL_LIGHT, INK_200, INK_500, INK_900, ROW_ALT, _draw_footer

PAGE = landscape(A4)
MARGIN = 14 * mm
USABLE = PAGE[0] - 2 * MARGIN

_base = getSampleStyleSheet()['Normal']


def _style(name, **kw):
    kw.setdefault('fontName', 'Helvetica')
    kw.setdefault('fontSize', 8.5)
    kw.setdefault('leading', 11)
    kw.setdefault('textColor', INK_900)
    kw.setdefault('wordWrap', 'CJK')
    return ParagraphStyle(name, parent=_base, **kw)


S_CELL = _style('tf_cell')
S_CELL_R = _style('tf_cell_r', alignment=TA_RIGHT)
S_CELL_B = _style('tf_cell_b', fontName='Helvetica-Bold')
S_CELL_RB = _style('tf_cell_rb', fontName='Helvetica-Bold', alignment=TA_RIGHT)
S_HEAD = _style('tf_head', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.white)
S_HEAD_R = _style('tf_head_r', fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.white, alignment=TA_RIGHT)
S_LABEL = _style('tf_label', fontSize=7.5, leading=9.5, textColor=INK_500)
S_VALUE = _style('tf_value', fontName='Helvetica-Bold', fontSize=11, leading=14)
S_SMALL = _style('tf_small', fontSize=8, leading=10.5, textColor=INK_500)
S_TITLE = _style('tf_title', fontName='Helvetica-Bold', fontSize=20, leading=24, textColor=BRAND_TEAL)
S_REF = _style('tf_ref', fontName='Helvetica-Bold', fontSize=12, leading=15, alignment=TA_RIGHT)
S_META = _style('tf_meta', fontSize=8.5, leading=11.5, textColor=INK_500, alignment=TA_RIGHT)
S_BIG = _style('tf_big', fontName='Helvetica-Bold', fontSize=22, leading=26, textColor=BRAND_TEAL, alignment=TA_CENTER)
S_CENTER = _style('tf_center', fontSize=8, leading=10.5, textColor=INK_500, alignment=TA_CENTER)


def _p(text, style):
    return Paragraph(escape('' if text is None else str(text)), style)


def _money(value):
    try:
        return f'{Decimal(value):,.2f}'
    except Exception:
        return str(value)


def _qty(value):
    try:
        return f'{int(value):,}'
    except Exception:
        return str(value)


def _logo(pharmacy):
    """Branch logo when the file is available; silently skipped otherwise (never breaks the PDF)."""
    try:
        if pharmacy.logo and pharmacy.logo.path:
            from reportlab.lib.utils import ImageReader
            reader = ImageReader(pharmacy.logo.path)
            w, h = reader.getSize()
            height = 14 * mm
            return Image(pharmacy.logo.path, width=height * w / h, height=height)
    except Exception:
        pass
    return None


def _branch_block(label, pharmacy, accent):
    lines = [_p(label, S_LABEL), _p(pharmacy.name, S_VALUE)]
    details = ' · '.join(x for x in (getattr(pharmacy, 'location', ''), getattr(pharmacy, 'phone_number', '')) if x)
    if details:
        lines.append(_p(details, S_SMALL))
    t = Table([[lines]], colWidths=[(USABLE - 60 * mm) / 2])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), accent),
        ('BOX', (0, 0), (-1, -1), 0.6, INK_200),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return t


def _signature_block(label):
    t = Table([[_p(label, S_LABEL)], [''], [_p('Name / Signature / Date', S_SMALL)]],
              colWidths=[(USABLE - 12 * mm) / 2], rowHeights=[6 * mm, 11 * mm, 6 * mm])
    t.setStyle(TableStyle([('LINEBELOW', (0, 1), (-1, 1), 0.7, INK_900),
                           ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    return t


def build_transfer_pdf(transfer, items, total_cost):
    """Return the PDF bytes for ``transfer`` (BranchStockTransfer) and its ``items``."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=10 * mm, bottomMargin=20 * mm,
        title=f'Stock Transfer {transfer.reference}', author='SmartPOS', subject='Branch stock transfer note',
    )
    src, dst = transfer.source_pharmacy, transfer.destination_pharmacy
    created = timezone.localtime(transfer.created_at)
    story = []

    # ---- Header: brand / title on the left, reference + metadata on the right ----
    left = []
    logo = _logo(src)
    if logo:
        left.append(logo)
        left.append(Spacer(1, 2 * mm))
    left += [_p('STOCK TRANSFER NOTE', S_TITLE), _p('Inter-branch inventory movement · all-or-nothing, audited', S_SMALL)]
    right = [
        _p(transfer.reference, S_REF),
        _p(f'Date: {created:%d %b %Y, %H:%M}', S_META),
        _p(f'Status: {transfer.get_status_display()}', S_META),
        _p(f'Prepared by: {transfer.created_by or "System"}', S_META),
    ]
    header = Table([[left, right]], colWidths=[USABLE * 0.6, USABLE * 0.4])
    header.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 2 * mm), HRFlowable(width='100%', thickness=1.4, color=BRAND_TEAL), Spacer(1, 4 * mm)]

    # ---- From -> To with the totals in the middle ----
    middle = Table([[_p(_qty(transfer.quantity), S_BIG)],
                    [_p(f'total units · {transfer.item_count} batch{"" if transfer.item_count == 1 else "es"}', S_CENTER)]],
                   colWidths=[60 * mm])
    middle.setStyle(TableStyle([('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    route = Table([[_branch_block('FROM (SOURCE BRANCH)', src, ROW_ALT), middle,
                    _branch_block('TO (DESTINATION BRANCH)', dst, BRAND_TEAL_LIGHT)]],
                  colWidths=[(USABLE - 60 * mm) / 2, 60 * mm, (USABLE - 60 * mm) / 2])
    route.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story += [route, Spacer(1, 4 * mm)]

    # ---- Items table ----
    heads = ['#', 'Product', 'Source batch', 'Destination batch', 'Qty', 'Left at source', 'Cost / unit (TSh)', 'Price / unit (TSh)', 'Expiry']
    right_cols = {4, 5, 6, 7}
    rows = [[_p(h, S_HEAD_R if i in right_cols else S_HEAD) for i, h in enumerate(heads)]]
    for n, it in enumerate(items, 1):
        dest_batch = getattr(it.destination_batch, 'batch_number', '') or ''
        rows.append([
            _p(n, S_CELL), _p(it.product_name, S_CELL_B), _p(it.source_batch_number or '-', S_CELL), _p(dest_batch or '-', S_CELL),
            _p(f'{_qty(it.quantity)} {it.unit_display}', S_CELL_RB), _p(_qty(it.source_quantity_after), S_CELL_R),
            _p(_money(it.purchase_price), S_CELL_R), _p(_money(it.selling_price), S_CELL_R),
            _p(f'{it.expiry_date:%d %b %Y}' if it.expiry_date else '-', S_CELL),
        ])
    if not items:
        rows.append([_p('No item lines recorded.', S_CELL)] + [''] * 8)
    rows.append([_p('', S_CELL), _p('TOTAL', S_CELL_B), '', '', _p(_qty(transfer.quantity), S_CELL_RB), '',
                 _p('Stock value at cost', S_CELL_R), _p(_money(total_cost), S_CELL_RB), ''])
    w = [6, 50, 36, 46, 26, 26, 32, 32, 26]
    widths = [USABLE * x / sum(w) for x in w]
    table = Table(rows, colWidths=widths, repeatRows=1)
    last = len(rows) - 1
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BRAND_TEAL),
        ('ROWBACKGROUNDS', (0, 1), (-1, last - 1), [colors.white, ROW_ALT]),
        ('LINEBELOW', (0, 1), (-1, last - 1), 0.4, INK_200),
        ('BACKGROUND', (0, last), (-1, last), BRAND_TEAL_LIGHT),
        ('LINEABOVE', (0, last), (-1, last), 1, BRAND_TEAL),
        ('SPAN', (1, last), (3, last)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    # ---- Note + sign-off (kept together so they never split across pages) ----
    note = Table([[_p('REASON / NOTE', S_LABEL)], [_p(transfer.note or 'No note provided.', S_CELL)]], colWidths=[USABLE])
    note.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, INK_200), ('LEFTPADDING', (0, 0), (-1, -1), 8),
                              ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    sign = Table([[_signature_block(f'Dispatched by · {src.name}'), '', _signature_block(f'Received by · {dst.name}')]],
                 colWidths=[(USABLE - 12 * mm) / 2, 12 * mm, (USABLE - 12 * mm) / 2])
    sign.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    story.append(KeepTogether([note, Spacer(1, 4 * mm), sign, Spacer(1, 2 * mm),
                               _p('This note was generated automatically by SmartPOS and is a record of a completed stock transfer. '
                                  'Stock was deducted from the source branch and received at the destination branch at the time shown above.', S_SMALL)]))

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return buf.getvalue()
