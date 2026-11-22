"""Branch-customisable sales receipt renderer (80 mm and 58 mm thermal rolls).

This module deliberately has NO Django imports so the receipt layout can be unit
tested with plain objects.  Everything that is branch-specific (colour, font,
logo, layout, which details are shown, extra texts) arrives through the
``style`` dictionary produced by ``pharmacies.receipt_utils.get_receipt_style``.

A branch that never opened the "Receipt Design" page gets ``DEFAULT_STYLE``,
which reproduces the original SmartPOS receipt look.
"""
import io
import re
from xml.sax.saxutils import escape

HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')

FONT_FAMILIES = {
    'HELVETICA': ('Helvetica', 'Helvetica-Bold'),
    'TIMES': ('Times-Roman', 'Times-Bold'),
    'COURIER': ('Courier', 'Courier-Bold'),
}
LOGO_MM = {'SMALL': 12, 'MEDIUM': 17, 'LARGE': 24}

DEFAULT_PRIMARY = '#2F8F3B'
DEFAULT_TEXT = '#172033'
DEFAULT_BACKGROUND = '#FFFFFF'
PANEL_GREY = '#F2F4F7'
DEFAULT_TITLE = 'OFFICIAL SALES RECEIPT'
DEFAULT_FOOTER = 'Thank you for choosing us. Please keep this receipt for your records.'

DEFAULT_STYLE = {
    'primary_color': DEFAULT_PRIMARY,
    'text_color': DEFAULT_TEXT,
    'background_color': DEFAULT_BACKGROUND,
    'panel_style': 'TINT',      # TINT | GREY | OUTLINE
    'title_style': 'LINES',     # LINES | BAND
    'font_family': 'HELVETICA',
    'paper_width_mm': 80,
    'header_layout': 'LEFT',
    'logo_size': 'MEDIUM',
    'show_logo': True,
    'title_text': DEFAULT_TITLE,
    'show_address': True,
    'show_contacts': True,
    'show_registration': True,
    'show_customer': True,
    'show_served_by': True,
    'show_batch_details': True,
    'show_barcode': True,
    'show_watermark': True,
    'watermark_text': '',
    'return_policy': '',
}


# ---------- colour helpers (also used by the settings form validation) ----------
def normalize_hex(value, fallback=DEFAULT_PRIMARY):
    value = (value or '').strip()
    if value and not value.startswith('#'):
        value = '#' + value
    return value.upper() if HEX_RE.match(value) else fallback


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def tint(hex_color, ratio=0.92):
    """Mix a colour with white. ratio=0.92 -> a very light panel background."""
    r, g, b = _rgb(normalize_hex(hex_color))
    mix = lambda c: int(round(c + (255 - c) * ratio))
    return '#%02X%02X%02X' % (mix(r), mix(g), mix(b))


def contrast_with_white(hex_color):
    """WCAG contrast ratio of a colour against a white receipt background."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(normalize_hex(hex_color))
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 1.05 / (lum + 0.05)


def contrast_ratio(hex_a, hex_b):
    """WCAG contrast ratio between two colours."""
    def lum(h):
        def lin(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = _rgb(normalize_hex(h))
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    la, lb = lum(hex_a), lum(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def receipt_number_for(sale):
    return f"PM-{sale.pharmacy.id}-{(sale.sale_number or sale.id):08d}"


def build_receipt_pdf(sale, style=None, logo_source=None, localtime=None):
    """Return the receipt PDF as bytes.

    ``sale`` needs the attributes used below (a real ``sales.Sale`` or a sample
    object).  ``logo_source`` is a file path or file-like object, or None.
    """
    from reportlab.graphics.barcode import code128
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    s = dict(DEFAULT_STYLE)
    s.update({k: v for k, v in (style or {}).items() if v is not None})
    localtime = localtime or (lambda d: d)

    pharmacy = sale.pharmacy
    receipt_no = receipt_number_for(sale)
    status = str(getattr(sale, 'status', 'COMPLETED'))
    is_voided = status == 'VOIDED'
    is_amended = status == 'PARTIALLY_RETURNED'

    # ---------- paper ----------
    compact = int(s['paper_width_mm']) == 58
    paper_mm = 58 if compact else 80
    page_width = paper_mm * mm
    side_margin = (3.5 if compact else 4.5) * mm
    usable_width = page_width - (2 * side_margin)
    top_margin = 4 * mm
    bottom_margin = 12 * mm

    # ---------- colours & fonts ----------
    primary_hex = normalize_hex(s['primary_color'])
    primary = colors.HexColor(primary_hex)
    bg_hex = normalize_hex(s['background_color'], DEFAULT_BACKGROUND)
    paper = colors.HexColor(bg_hex)
    panel_style = s['panel_style'] if s['panel_style'] in ('TINT', 'GREY', 'OUTLINE') else 'TINT'
    title_band = s['title_style'] == 'BAND'
    if panel_style == 'TINT':
        panel_hex = tint(primary_hex)
    elif panel_style == 'GREY':
        panel_hex = PANEL_GREY
    else:
        panel_hex = None                      # outline only, paper shows through
    soft = colors.HexColor(panel_hex) if panel_hex else None
    panel_gap = colors.white if panel_hex else colors.HexColor('#d5dbe1')
    text_hex = normalize_hex(s['text_color'], DEFAULT_TEXT)
    ink = colors.HexColor(text_hex)
    # Secondary (grey) text keeps the standard grey, or follows a custom text colour.
    muted = colors.HexColor('#667085' if text_hex == DEFAULT_TEXT else tint(text_hex, 0.35))
    line = colors.HexColor('#d5dbe1')
    watermark_color = colors.HexColor('#e8ecef')

    reg, bold = FONT_FAMILIES.get(s['font_family'], FONT_FAMILIES['HELVETICA'])
    fscale = 0.88 if s['font_family'] == 'COURIER' else 1.0   # Courier is much wider
    fs = lambda size: round(size * fscale, 2)

    styles = getSampleStyleSheet()

    def P(name, parent=None, **kw):
        return ParagraphStyle(name, parent=parent or styles['Normal'], **kw)

    brand = P('RBrand', fontName=bold, fontSize=fs(13), leading=fs(14.5), textColor=primary)
    pharmacy_line = P('RPhLine', fontName=reg, fontSize=fs(7.1), leading=fs(8.5), textColor=ink)
    tiny = P('RTiny', fontName=reg, fontSize=fs(6.5), leading=fs(7.8), textColor=muted)
    tiny_center = P('RTinyC', tiny, alignment=TA_CENTER)
    title_len = len((s['title_text'] or DEFAULT_TITLE).strip())
    title_size = 10.8 if title_len <= 24 and not compact else (9.2 if title_len <= 30 else 8)
    title = P('RTitle', fontName=bold, fontSize=fs(title_size), leading=fs(title_size + 1.7),
              textColor=colors.white if title_band else primary, alignment=TA_CENTER)
    label = P('RLabel', fontName=bold, fontSize=fs(6.5), leading=fs(7.5), textColor=ink)
    value = P('RValue', fontName=reg, fontSize=fs(6.6), leading=fs(7.7), textColor=ink)
    item_name = P('RItem', fontName=bold, fontSize=fs(7.1), leading=fs(8.5), textColor=ink)
    money = P('RMoney', fontName=reg, fontSize=fs(6.9), leading=fs(8), textColor=ink, alignment=TA_RIGHT)
    qty = P('RQty', fontName=reg, fontSize=fs(6.8), leading=fs(8), textColor=ink, alignment=TA_CENTER)
    total = P('RTotal', fontName=bold, fontSize=fs(9.8), leading=fs(11), textColor=ink, alignment=TA_RIGHT)

    def safe(v):
        return escape(str(v or ''))

    elems = []

    # ---------- header (logo + identity) ----------
    layout = s['header_layout'] if s['header_layout'] in ('LEFT', 'CENTER') else 'LEFT'
    logo_mm = LOGO_MM.get(s['logo_size'], 17)
    if compact and layout == 'LEFT' and logo_mm > 14:
        layout = 'CENTER'      # a large logo would squeeze the text on a 58 mm roll
    align = TA_CENTER if layout == 'CENTER' else TA_LEFT
    c_brand = P('RBrandA', brand, alignment=align)
    c_line = P('RPhLineA', pharmacy_line, alignment=align)
    c_tiny = P('RTinyA', tiny, alignment=align)

    identity = [Paragraph(safe(pharmacy.name), c_brand)]
    if s['show_address'] and getattr(pharmacy, 'location', ''):
        identity.append(Paragraph(safe(pharmacy.location), c_line))
    if s['show_contacts']:
        contacts = [safe(x) for x in (getattr(pharmacy, 'phone_number', ''), getattr(pharmacy, 'email', '')) if x]
        if contacts:
            identity.append(Paragraph(' &nbsp;|&nbsp; '.join(contacts), c_line))
    if s['show_registration']:
        if getattr(pharmacy, 'pharmacy_council_fin', ''):
            identity.append(Paragraph(f"FIN: {safe(pharmacy.pharmacy_council_fin)}", c_tiny))
        elif getattr(pharmacy, 'registration_number', ''):
            identity.append(Paragraph(f"Registration / Licence: {safe(pharmacy.registration_number)}", c_tiny))
        if getattr(pharmacy, 'permit_number', ''):
            permit = f"Permit: {safe(pharmacy.permit_number)}"
            if getattr(pharmacy, 'permit_expiry_date', None):
                permit += f" &nbsp;|&nbsp; Exp: {pharmacy.permit_expiry_date.strftime('%d %b %Y')}"
            identity.append(Paragraph(permit, c_tiny))
    if getattr(pharmacy, 'receipt_header', ''):
        identity.append(Paragraph(safe(pharmacy.receipt_header), c_tiny))

    logo = None
    if s['show_logo'] and logo_source is not None:
        try:
            source = logo_source
            if bg_hex != DEFAULT_BACKGROUND:
                # Logos usually have a white box around them; multiply-blend the
                # image with the paper colour so it sits naturally on tinted paper.
                try:
                    from PIL import Image as PILImage, ImageChops
                    if hasattr(logo_source, 'seek'):
                        logo_source.seek(0)
                    base = PILImage.open(logo_source).convert('RGB')
                    blended = ImageChops.multiply(base, PILImage.new('RGB', base.size, _rgb(bg_hex)))
                    source = io.BytesIO()
                    blended.save(source, format='PNG')
                    source.seek(0)
                except Exception:
                    source = logo_source
                    if hasattr(logo_source, 'seek'):
                        logo_source.seek(0)
            logo = Image(source, width=logo_mm * mm, height=logo_mm * mm, kind='proportional')
            logo.hAlign = 'CENTER' if layout == 'CENTER' else 'LEFT'
        except Exception:
            logo = None

    if logo is not None and layout == 'LEFT':
        gap = (logo_mm + 1) * mm
        header = Table([[logo, identity]], colWidths=[gap, usable_width - gap])
    elif logo is not None:
        header = Table([[logo], [identity]], colWidths=[usable_width])
    else:
        header = Table([[identity]], colWidths=[usable_width])
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER' if layout == 'CENTER' else 'LEFT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 1.5),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
    ]))
    elems.append(header)

    # ---------- title ----------
    if is_voided:
        title_text = 'VOIDED - NOT VALID'
    elif is_amended:
        title_text = 'AMENDED SALES RECEIPT'
    else:
        title_text = (s['title_text'] or DEFAULT_TITLE).strip().upper()
    title_box = Table([[Paragraph(safe(title_text), title)]], colWidths=[usable_width])
    title_box.setStyle(TableStyle(
        ([('BACKGROUND', (0, 0), (-1, -1), primary)] if title_band else
         [('LINEABOVE', (0, 0), (-1, -1), 0.75, primary), ('LINEBELOW', (0, 0), (-1, -1), 0.75, primary)]) + [
        ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
    ]))
    elems += [Spacer(1, 2), title_box, Spacer(1, 3.5)]

    # ---------- receipt details ----------
    served_by = '—'
    if getattr(sale, 'served_by', None):
        served_by = sale.served_by.get_full_name() or sale.served_by.username
    created = localtime(sale.created_at)
    fields = [('Receipt No.', receipt_no), ('Payment', getattr(sale, 'payment_summary', None) or sale.get_payment_method_display())]
    if s['show_customer']:
        fields.append(('Customer', sale.customer_name or 'Walk-in Customer'))
    # A late entry (paper-notebook sale typed in later) shows the day the sale REALLY happened.
    is_late = bool(getattr(sale, 'is_late_entry', False)) and getattr(sale, 'business_date', None)
    fields.append(('Date', sale.business_date.strftime('%d %b %Y') if is_late else created.strftime('%d %b %Y')))
    if s['show_customer']:
        fields.append(('Phone', sale.customer_phone or '—'))
    fields.append(('Time', '—' if is_late else created.strftime('%H:%M')))
    if s['show_served_by']:
        fields.append(('Served by', served_by))
    fields.append(('Branch', pharmacy.name))

    padded = False
    if compact:
        meta = [[Paragraph(l, label), Paragraph(safe(v), value)] for l, v in fields]
        meta_widths = [15 * mm, usable_width - 15 * mm]
    else:
        padded = len(fields) % 2 == 1
        if padded:
            fields.append(('', ''))
        meta = []
        for i in range(0, len(fields), 2):
            (l1, v1), (l2, v2) = fields[i], fields[i + 1]
            meta.append([Paragraph(l1, label), Paragraph(safe(v1), value),
                         Paragraph(l2, label), Paragraph(safe(v2), value)])
        meta_widths = [13 * mm, 24 * mm, 13 * mm, usable_width - 50 * mm]
    meta_table = Table(meta, colWidths=meta_widths)
    meta_extra = [('SPAN', (1, -1), (3, -1))] if (not compact and padded) else []
    meta_table.setStyle(TableStyle(meta_extra + ([('BACKGROUND', (0, 0), (-1, -1), soft)] if soft else []) + [
        ('BOX', (0, 0), (-1, -1), 0.45, line),
        ('INNERGRID', (0, 0), (-1, -1), 0.25, panel_gap), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
        ('LEFTPADDING', (0, 0), (-1, -1), 1.7), ('RIGHTPADDING', (0, 0), (-1, -1), 1.7),
    ]))
    elems += [meta_table, Spacer(1, 4)]

    # ---------- items ----------
    if compact:
        item_rows = [[Paragraph('ITEM', label), Paragraph('QTY', tiny_center), Paragraph('AMOUNT', tiny_center)]]
    else:
        item_rows = [[Paragraph('ITEM', label), Paragraph('QTY', tiny_center),
                      Paragraph('UNIT', tiny_center), Paragraph('AMOUNT', tiny_center)]]
    for item in sale.items.all():
        batch = item.batch
        meta_parts = [item.product.get_unit_display()]
        if compact:
            meta_parts.append(f"@ TZS {item.unit_price:,.0f}")
        if s['show_batch_details']:
            if batch and batch.batch_number:
                meta_parts.append(f"Batch: {safe(batch.batch_number)}")
            if batch and batch.expiry_date:
                meta_parts.append(f"Exp: {batch.expiry_date.strftime('%m/%Y')}")
        name_html = safe(item.product.name)
        if item.quantity == 0:
            name_html = f"<strike>{name_html}</strike>"
        returned_html = ''
        if item.returned_quantity:
            returned_html = (
                f"<br/><font name='{reg}' size='{fs(6)}' color='#b42318'>"
                f"Returned {item.returned_quantity} of {item.original_quantity}</font>"
            )
        name_cell = Paragraph(
            f"{name_html}<br/><font name='{reg}' size='{fs(6)}'>{safe(' | '.join(meta_parts))}</font>{returned_html}",
            item_name)
        if compact:
            item_rows.append([name_cell, Paragraph(str(item.quantity), qty),
                              Paragraph(f"TZS {item.subtotal:,.0f}", money)])
        else:
            item_rows.append([name_cell, Paragraph(str(item.quantity), qty),
                              Paragraph(f"TZS {item.unit_price:,.0f}", money),
                              Paragraph(f"TZS {item.subtotal:,.0f}", money)])

    if compact:
        item_widths = [usable_width - 8 * mm - 17 * mm, 8 * mm, 17 * mm]
    else:
        item_widths = [32 * mm, 8 * mm, 15 * mm, usable_width - 55 * mm]
    items_table = Table(item_rows, colWidths=item_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, 0), 0.55, primary), ('LINEBELOW', (0, 1), (-1, -1), 0.25, line),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 2.2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2), ('LEFTPADDING', (0, 0), (-1, -1), 1.2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 1.2),
    ]))
    elems += [items_table, Spacer(1, 3.5)]

    # ---------- totals ----------
    summary_rows = []
    if sale.returned_total > 0:
        summary_rows.append([Paragraph('Original total', tiny), Paragraph(f"TZS {sale.original_total:,.0f}", money)])
        summary_rows.append([Paragraph('Returned / voided', tiny), Paragraph(f"- TZS {sale.returned_total:,.0f}", money)])
    summary_rows.append([Paragraph('TOTAL', label), Paragraph(f"TZS {sale.total_amount:,.0f}", total)])
    summary = Table(summary_rows, colWidths=[usable_width * 0.40, usable_width * 0.60] if compact
                    else [usable_width * 0.48, usable_width * 0.52])
    summary.setStyle(TableStyle(([('BACKGROUND', (0, 0), (-1, -1), soft)] if soft else []) + [
        ('BOX', (0, 0), (-1, -1), 0.7, primary),
        ('TOPPADDING', (0, 0), (-1, -1), 4.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 4.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))
    elems += [summary, Spacer(1, 3)]

    # ---------- barcode ----------
    if s['show_barcode']:
        bar_w = 0.22 * mm
        bc = code128.Code128(receipt_no, barHeight=6 * mm, barWidth=bar_w)
        while bc.width > usable_width * 0.98 and bar_w > 0.1 * mm:
            bar_w *= 0.92
            bc = code128.Code128(receipt_no, barHeight=6 * mm, barWidth=bar_w)
        barcode_table = Table([[bc], [Paragraph(safe(receipt_no), tiny_center)]], colWidths=[usable_width])
        barcode_table.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('TOPPADDING', (0, 0), (-1, -1), 0.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0.5),
        ]))
        elems += [barcode_table, Spacer(1, 2.5)]

    # ---------- footer ----------
    footer_text = getattr(pharmacy, 'receipt_footer', '') or DEFAULT_FOOTER
    footer_rows = [[Paragraph(safe(footer_text), tiny_center)]]
    if (s['return_policy'] or '').strip():
        footer_rows.append([Paragraph(safe(s['return_policy'].strip()),
                                      P('RPolicy', tiny_center, fontSize=fs(6), leading=fs(7.2)))])
    footer_box = Table(footer_rows, colWidths=[usable_width])
    footer_box.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (-1, 0), 0.45, line),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
    ]))
    elems += [footer_box,
              Paragraph('SmartPOS · © 2026 Mutech Apex Innovations · All rights reserved.', tiny_center),
              Paragraph('System generated · ' + ('Original receipt' if status == 'COMPLETED'
                                                  else 'Amended receipt - returns applied'), tiny_center)]

    # ---------- exact paper height ----------
    measured_height = 0
    for flowable in elems:
        _, h = flowable.wrap(usable_width, 10000 * mm)
        measured_height += h
    # Thermal receipts are one continuous roll/page.  Give the page enough
    # height for the complete story so ReportLab never has to create a second
    # page or split an item table.  The extra breathing room also absorbs tiny
    # rounding differences between measurement and the final layout pass.
    page_height = max(145 * mm, measured_height + top_margin + bottom_margin + 12 * mm)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=(page_width, page_height),
        rightMargin=side_margin, leftMargin=side_margin,
        topMargin=top_margin, bottomMargin=bottom_margin,
        title=f"Receipt {receipt_no}", author="SmartPOS",
        allowSplitting=0,
    )

    watermark_text = (s['watermark_text'] or '').strip() or pharmacy.name

    def page_decor(canvas, doc_obj):
        if bg_hex != DEFAULT_BACKGROUND:
            canvas.saveState()
            canvas.setFillColor(paper)
            canvas.rect(0, 0, page_width, page_height, stroke=0, fill=1)
            canvas.restoreState()
        draw_mark = is_voided or s['show_watermark']
        if draw_mark:
            canvas.saveState()
            if is_voided:
                canvas.setFillColor(colors.HexColor('#f6cfcb'))
                canvas.setFont(bold, 34 if not compact else 28)
                mark_text = 'VOIDED'
            else:
                mark_text = watermark_text[:28].upper()
                size = max(8, min(17, 17 * 16 / max(len(mark_text), 1)))
                if compact:
                    size = min(size, 13)
                canvas.setFillColor(watermark_color)
                canvas.setFont(bold, size * fscale)
            canvas.translate(page_width / 2, page_height / 2)
            canvas.rotate(32)
            canvas.drawCentredString(0, 0, mark_text)
            canvas.restoreState()

        canvas.saveState()
        canvas.setStrokeColor(line)
        canvas.line(side_margin, 7 * mm, page_width - side_margin, 7 * mm)
        canvas.setFillColor(muted)
        canvas.setFont(reg, 5.5)
        canvas.drawString(side_margin, 3.5 * mm, 'SmartPOS')
        canvas.restoreState()

    doc.build(elems, onFirstPage=page_decor, onLaterPages=page_decor)
    buffer.seek(0)
    return buffer.read()
