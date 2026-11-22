"""Barcode helpers shared by the POS, the forms, the scan endpoints and the Excel import."""


def barcode_variants(code):
    """Equivalent forms of one retail number (EAN-13 / UPC-A / GTIN-14 / EAN-8 zero padding).

    Scanners and manufacturers disagree on leading zeros: the same pack can be read as
    6291041500213 or 06291041500213. Matching on every padded form avoids false "not found"
    and, more importantly, stops the same pack being registered twice.
    Non-numeric codes (PM…, SKU-…, custom labels) are returned unchanged.
    """
    code = (code or '').strip()
    out = [code] if code else []
    if code.isdigit() and 8 <= len(code) <= 14:
        stripped = code.lstrip('0') or '0'
        for width in (8, 12, 13, 14):
            if len(stripped) <= width:
                v = stripped.zfill(width)
                if v not in out:
                    out.append(v)
    return out
