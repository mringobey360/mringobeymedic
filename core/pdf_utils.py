"""
Reusable, professionally-branded PDF report builder used by every
"export to PDF" feature in the system (sales, products, profit, payroll reports).

Kept as one shared utility so every report looks consistent — same
header, same color scheme, same footer with page numbers — without
duplicating layout code in every view.

IMPORTANT: every table cell is wrapped in a Paragraph (not a plain string).
ReportLab does NOT word-wrap plain strings in a Table — a long product name
or batch number will silently overflow into the next column instead of
wrapping to a second line. Using Paragraph objects for every cell (including
headers) is what makes columns "fit exactly and behave well" as requested.
"""
import io
import os
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image

BRAND_TEAL = colors.HexColor('#0f766e')
BRAND_TEAL_LIGHT = colors.HexColor('#f0fdfa')
INK_900 = colors.HexColor('#0f172a')
INK_500 = colors.HexColor('#64748b')
INK_200 = colors.HexColor('#e2e8f0')
ROW_ALT = colors.HexColor('#f8fafc')

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm
USABLE_WIDTH = PAGE_WIDTH - (2 * MARGIN)  # the only width every table must ever add up to

_styles = getSampleStyleSheet()
_CELL_STYLE = ParagraphStyle(
    'TableCell', parent=_styles['Normal'], fontSize=8.5, leading=10.5, textColor=INK_900, wordWrap='CJK'
)
_HEADER_CELL_STYLE = ParagraphStyle(
    'TableHeaderCell', parent=_styles['Normal'], fontSize=9, leading=11,
    textColor=colors.white, fontName='Helvetica-Bold', wordWrap='CJK'
)


def _draw_footer(canvas, doc):
    canvas.saveState()
    page_width, page_height = doc.pagesize
    canvas.setFillColor(colors.HexColor('#f1f5f9'))
    canvas.setFont('Helvetica-Bold', 30)
    canvas.translate(page_width/2, page_height/2)
    canvas.rotate(35)
    canvas.drawCentredString(0, 0, 'SmartPOS')
    canvas.restoreState()
    canvas.saveState()
    canvas.setStrokeColor(INK_200)
    canvas.line(MARGIN, 16 * mm, page_width - MARGIN, 16 * mm)
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(INK_500)
    canvas.drawString(MARGIN, 11 * mm, f"Generated on {timezone.localtime():%d %b %Y, %H:%M}")
    canvas.drawRightString(page_width - MARGIN, 11 * mm, f"Page {doc.page}")
    canvas.setFont('Helvetica-Oblique', 7.5)
    canvas.drawCentredString(page_width / 2, 11 * mm, "SmartPOS · © 2026 Mutech Apex Innovations · All rights reserved")
    canvas.restoreState()


def _normalize_column_widths(column_widths, num_columns, page_width=None):
    """
    Always returns widths (in points) that sum EXACTLY to USABLE_WIDTH, so a
    table can never run off the page or leave a mismatched gap. If the caller's
    widths don't add up (or weren't given at all), they're scaled proportionally.
    """
    usable_width = (page_width - (2 * MARGIN)) if page_width else USABLE_WIDTH
    if not column_widths:
        equal = usable_width / num_columns
        return [equal] * num_columns

    widths_pt = [w * mm for w in column_widths]
    total = sum(widths_pt)
    if total <= 0:
        equal = usable_width / num_columns
        return [equal] * num_columns

    scale = usable_width / total
    return [w * scale for w in widths_pt]


def _para(text, style):
    """Build a Paragraph that can never crash a whole report.

    ReportLab parses cell text as mini-markup. A product, customer or staff name
    such as "Vitamin <B>" or "Tom & <Jerry>" raises ValueError and would turn the
    whole PDF download into a server error. We first try the text as-is (so
    intentional <b>...</b> markup keeps working) and, if the parser rejects it,
    fall back to the fully escaped text.
    """
    text = '-' if text is None else str(text)
    try:
        return Paragraph(text, style)
    except Exception:
        return Paragraph(_xml_escape(text), style)


def _wrap_row(values, style):
    return [_para(v, style) for v in values]


def generate_pdf_report(title, subtitle, headers, rows, filename, summary=None, column_widths=None, pagesize=A4):
    """
    Build and return an HttpResponse containing a styled PDF report.

    - title / subtitle: shown at the top of the report
    - headers: list of column header strings
    - rows: list of lists (already formatted as display strings)
    - summary: optional list of (label, value) tuples shown in a highlighted box
    - column_widths: optional list of relative widths (in mm) matching len(headers) —
      these are auto-scaled to fit the page exactly, so they only need to reflect
      RELATIVE proportions (e.g. [40, 20, 20] is the same shape as [60, 30, 30]).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize,
        topMargin=MARGIN, bottomMargin=22 * mm, leftMargin=MARGIN, rightMargin=MARGIN,
        title=title,
    )
    styles = getSampleStyleSheet()

    brand_style = ParagraphStyle('Brand', parent=styles['Normal'], textColor=BRAND_TEAL,
                                  fontSize=11, fontName='Helvetica-Bold', spaceAfter=10)
    title_style = ParagraphStyle('ReportTitle', parent=styles['Heading1'], textColor=INK_900,
                                  fontSize=18, spaceAfter=2, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('ReportSubtitle', parent=styles['Normal'], textColor=INK_500,
                                     fontSize=10, spaceAfter=16)

    elements = []
    page_width = pagesize[0]

    logo_path = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.jpg')
    if os.path.exists(logo_path):
        try:
            elements.append(Image(logo_path, width=12 * mm, height=12 * mm))
            elements.append(Spacer(1, 6))
        except Exception:
            pass

    elements.append(Paragraph("SmartPOS", brand_style))
    elements.append(_para(title, title_style))
    elements.append(_para(subtitle, subtitle_style))

    if summary:
        summary_rows = [[_para(f"<b>{label}</b>", _CELL_STYLE),
                          _para(value, _CELL_STYLE)] for label, value in summary]
        summary_widths = _normalize_column_widths([55, 119], 2, page_width=page_width)  # label column narrower than value column
        summary_table = Table(summary_rows, colWidths=summary_widths)
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BRAND_TEAL_LIGHT),
            ('BOX', (0, 0), (-1, -1), 0.6, INK_200),
            ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.white),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 14))

    if rows:
        header_row = _wrap_row(headers, _HEADER_CELL_STYLE)
        body_rows = [_wrap_row(row, _CELL_STYLE) for row in rows]
        table_data = [header_row] + body_rows

        widths = _normalize_column_widths(column_widths, len(headers), page_width=page_width)
        table = Table(table_data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_TEAL),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ROW_ALT]),
            ('GRID', (0, 0), (-1, -1), 0.4, INK_200),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No data available for this report period.", styles['Normal']))

    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response



def generate_multi_section_pdf_report(title, subtitle, sections, filename, summary=None, pagesize=A4):
    """Build a branded PDF containing multiple tables/analysis sections.

    This uses the exact same header/footer, typography, logo, margins and table
    styling as generate_pdf_report. Each section is a dict with:
      title, headers, rows, column_widths (optional), note (optional).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize,
        topMargin=MARGIN, bottomMargin=22 * mm, leftMargin=MARGIN, rightMargin=MARGIN,
        title=title,
    )
    styles = getSampleStyleSheet()
    brand_style = ParagraphStyle('BrandMulti', parent=styles['Normal'], textColor=BRAND_TEAL,
                                  fontSize=11, fontName='Helvetica-Bold', spaceAfter=10)
    title_style = ParagraphStyle('ReportTitleMulti', parent=styles['Heading1'], textColor=INK_900,
                                  fontSize=18, spaceAfter=2, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('ReportSubtitleMulti', parent=styles['Normal'], textColor=INK_500,
                                     fontSize=10, spaceAfter=16)
    section_style = ParagraphStyle('ReportSectionMulti', parent=styles['Heading2'], textColor=INK_900,
                                   fontSize=12, leading=15, spaceBefore=8, spaceAfter=7,
                                   fontName='Helvetica-Bold')
    note_style = ParagraphStyle('ReportNoteMulti', parent=styles['Normal'], textColor=INK_500,
                                fontSize=8.5, leading=11, spaceAfter=7)

    elements = []
    page_width = pagesize[0]
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.jpg')
    if os.path.exists(logo_path):
        try:
            elements.append(Image(logo_path, width=12 * mm, height=12 * mm))
            elements.append(Spacer(1, 6))
        except Exception:
            pass
    elements += [Paragraph('SmartPOS', brand_style),
                 _para(title, title_style),
                 _para(subtitle, subtitle_style)]

    if summary:
        summary_rows = [[_para(f'<b>{label}</b>', _CELL_STYLE),
                         _para(value, _CELL_STYLE)] for label, value in summary]
        summary_table = Table(summary_rows, colWidths=_normalize_column_widths([55, 119], 2, page_width=page_width))
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BRAND_TEAL_LIGHT),
            ('BOX', (0, 0), (-1, -1), 0.6, INK_200),
            ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.white),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements += [summary_table, Spacer(1, 12)]

    for section in sections:
        elements.append(_para(section.get('title', 'Report Section'), section_style))
        if section.get('note'):
            elements.append(_para(section['note'], note_style))
        headers = section.get('headers') or []
        rows = section.get('rows') or []
        if not headers:
            continue
        if rows:
            data = [_wrap_row(headers, _HEADER_CELL_STYLE)] + [_wrap_row(r, _CELL_STYLE) for r in rows]
        else:
            data = [[Paragraph('No data available for this section.', _CELL_STYLE)]]
            headers = ['']
        widths = _normalize_column_widths(section.get('column_widths'), len(headers), page_width=page_width)
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BRAND_TEAL),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ROW_ALT]),
            ('GRID', (0, 0), (-1, -1), 0.4, INK_200),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements += [table, Spacer(1, 10)]

    doc.build(elements, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    buffer.seek(0)
    response = HttpResponse(buffer.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
