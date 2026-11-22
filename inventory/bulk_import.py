"""Professional bulk-import engine for products / stock.

This module is intentionally free of Django imports so it can be unit-tested
with nothing but ``openpyxl``.  It does three jobs:

1. ``read_sheet``      – read the workbook, normalise headers, tolerate the
                         usual Excel mess (extra spaces, aliases, blank rows).
2. ``validate_rows``   – clean every cell, collect *all* problems per row
                         (errors block a row, warnings only inform), run the
                         cross-row and catalog checks and describe the action
                         each row would perform.
3. ``build_fix_workbook`` – write a ready-to-edit Excel containing the rows the
                         user could not / did not import, with a ``problems``
                         column and the offending cells highlighted, so they
                         can fix and upload it again.
"""
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import get_close_matches

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel
from openpyxl.worksheet.datavalidation import DataValidation


MAX_ROWS = 2000

BULK_UPLOAD_HEADERS = [
    'name', 'sku', 'category', 'batch_number', 'unit', 'barcode',
    'purchase_unit', 'units_per_purchase_unit', 'purchase_quantity',
    'purchase_price', 'selling_price', 'reorder_level', 'expiry_date',
    'requires_expiry',
]
RESTOCK_TEMPLATE_HEADERS = [
    'name', 'sku', 'barcode', 'category', 'unit', 'current_stock',
    'reorder_level', 'suggested_purchase_qty', 'purchase_unit',
    'units_per_purchase_unit', 'purchase_quantity', 'purchase_price',
    'selling_price', 'batch_number', 'expiry_date', 'requires_expiry',
]
LEGACY_HEADERS = ['quantity', 'purchase_price_per_purchase_unit']
KNOWN_HEADERS = set(BULK_UPLOAD_HEADERS) | set(RESTOCK_TEMPLATE_HEADERS) | set(LEGACY_HEADERS)

HEADER_ALIASES = {
    'product': 'name', 'product_name': 'name', 'item': 'name', 'item_name': 'name', 'jina': 'name',
    'stock_keeping_unit': 'sku', 'code': 'sku',
    'category_name': 'category', 'aina': 'category',
    'batch': 'batch_number', 'batch_no': 'batch_number', 'batchnumber': 'batch_number', 'lot': 'batch_number',
    'base_unit': 'unit', 'uom': 'unit',
    'bar_code': 'barcode',
    'purchase_uom': 'purchase_unit',
    'units_per_purchase': 'units_per_purchase_unit', 'conversion': 'units_per_purchase_unit',
    'qty': 'purchase_quantity', 'purchase_qty': 'purchase_quantity', 'quantity_purchased': 'purchase_quantity',
    'cost': 'purchase_price', 'cost_price': 'purchase_price', 'buying_price': 'purchase_price',
    'price': 'selling_price', 'sell_price': 'selling_price', 'sale_price': 'selling_price',
    'reorder': 'reorder_level', 'min_stock': 'reorder_level',
    'expiry': 'expiry_date', 'expire_date': 'expiry_date', 'exp_date': 'expiry_date', 'expiration_date': 'expiry_date',
    'expires': 'requires_expiry', 'has_expiry': 'requires_expiry',
}

UNIT_ALIASES = {
    'TAB': 'TABLET', 'TABS': 'TABLET', 'CAP': 'CAPSULE', 'CAPS': 'CAPSULE',
    'BTL': 'BOTTLE', 'BTLS': 'BOTTLE', 'BX': 'BOX', 'PC': 'PIECE', 'PCS': 'PIECE', 'PCE': 'PIECE',
    'PKT': 'PACK', 'PKTS': 'PACK', 'PACKET': 'PACK', 'CTN': 'CARTON', 'CTNS': 'CARTON',
    'SACHETS': 'SACHET', 'AMPOULE': 'VIAL', 'STRIPS': 'STRIP', 'PAIRS': 'PAIR', 'ROLLS': 'ROLL',
}

TRUTHY = {'1', 'TRUE', 'YES', 'Y', 'NDIYO', 'NDIO'}
FALSY = {'0', 'FALSE', 'NO', 'N', 'HAPANA'}

MAX_NAME = 200
MAX_SKU = 40
MAX_CATEGORY = 100
MAX_BATCH = 100
MAX_BARCODE = 32
MAX_MONEY = Decimal('9999999999.99')   # 12 digits, 2 dp (DB limit)
MAX_QTY = 10_000_000
HIGH_QTY_WARN = 100_000
MARKUP_WARN = Decimal('10')
EXPIRY_SOON_DAYS = 30
EXPIRY_FAR_YEARS = 10

_CTRL = re.compile(r'[\x00-\x1f\x7f​‌‍﻿]')
_SPACES = re.compile(r'\s+')
_CURRENCY = re.compile(r'(?i)(tzs|tshs|tsh|shs|sh|usd|/=|=/)')
_SKU_OK = re.compile(r'^[A-Z0-9][A-Z0-9._\-/]*$')
_BARCODE_OK = re.compile(r'^[A-Za-z0-9\-._]+$')
_SCI = re.compile(r'^\d+(\.\d+)?[eE][+-]?\d+$')


FIELD_LABELS = {
    'name': 'Product name', 'sku': 'SKU', 'category': 'Category', 'batch_number': 'Batch number',
    'unit': 'Unit', 'barcode': 'Barcode', 'purchase_unit': 'Purchase unit',
    'units_per_purchase_unit': 'Units per purchase unit', 'purchase_quantity': 'Quantity',
    'quantity': 'Quantity', 'purchase_price': 'Buying price', 'selling_price': 'Selling price',
    'reorder_level': 'Reorder level', 'expiry_date': 'Expiry date', 'requires_expiry': 'Requires expiry',
}


class BulkFileError(Exception):
    """The workbook itself cannot be used (unreadable, wrong columns, too big...)."""


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def normalise_header(value):
    h = _SPACES.sub('_', str(value or '').strip().lower().replace('-', '_'))
    h = re.sub(r'[^a-z0-9_]', '', h).strip('_')
    return HEADER_ALIASES.get(h, h)


def read_sheet(raw_bytes):
    """Return ``(headers, rows)``; rows is a list of ``(excel_row_number, dict)``."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
        ws = wb.worksheets[0]
    except Exception:
        raise BulkFileError('Could not read the Excel file. Please use the provided .xlsx template and do not password-protect it.')

    first = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
    if not first:
        raise BulkFileError('The Excel file is empty.')
    headers = [normalise_header(v) for v in first]
    if 'name' not in headers:
        raise BulkFileError("The first row must be the header row and contain a 'name' column. Please download the current template.")

    seen, dupes = set(), set()
    for h in headers:
        if h and h in seen:
            dupes.add(h)
        seen.add(h)
    if dupes:
        raise BulkFileError(f"Duplicate column(s) in header: {', '.join(sorted(dupes))}. Each column may appear only once.")

    missing = []
    if 'selling_price' not in seen:
        missing.append('selling_price')
    if 'purchase_quantity' not in seen and 'quantity' not in seen:
        missing.append('purchase_quantity')
    if 'purchase_price' not in seen and 'purchase_price_per_purchase_unit' not in seen:
        missing.append('purchase_price')
    if missing:
        raise BulkFileError(f"Missing required column(s): {', '.join(missing)}. Please download the current template.")

    rows = []
    for idx, values in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        data = {}
        for h, v in zip(headers, values):
            if h:
                data[h] = v
        if not any(v not in (None, '') and str(v).strip() != '' for v in data.values()):
            continue
        rows.append((idx, data))
        if len(rows) > MAX_ROWS:
            raise BulkFileError(f'Too many rows. Upload at most {MAX_ROWS} product rows at a time and split larger files.')
    if not rows:
        raise BulkFileError('The Excel file contains no stock rows below the header.')
    return headers, rows


# --------------------------------------------------------------------------- #
# Cell cleaning helpers
# --------------------------------------------------------------------------- #
def clean_text(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return _SPACES.sub(' ', _CTRL.sub(' ', str(v))).strip()


def parse_decimal(v, label, *, allow_zero=False, max_value=MAX_MONEY):
    """Return Decimal or None when blank.  Accepts 'TSh 4,200', '1 500', '4.200,50'."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError(f'{label} must be a number.')
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
    else:
        s = _CURRENCY.sub('', str(v)).replace(' ', '').replace(' ', '').strip()
        if not s:
            raise ValueError(f"{label} '{v}' is not a number.")
        if ',' in s and '.' in s:
            s = s.replace(',', '') if s.rfind('.') > s.rfind(',') else s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '') if re.fullmatch(r'\d{1,3}(,\d{3})+', s) else s.replace(',', '.')
        try:
            d = Decimal(s)
        except InvalidOperation:
            raise ValueError(f"{label} '{clean_text(v)}' is not a number. Use digits only, e.g. 4200.")
    if not d.is_finite():
        raise ValueError(f'{label} is not a valid number.')
    if d < 0:
        raise ValueError(f'{label} cannot be a negative number.')
    if d == 0 and not allow_zero:
        raise ValueError(f'{label} must be more than zero.')
    if d > max_value:
        raise ValueError(f'{label} looks too large ({d:,}). Please check for extra digits.')
    return d


def parse_int(v, label, *, allow_zero=False, max_value=MAX_QTY):
    d = parse_decimal(v, label, allow_zero=allow_zero, max_value=Decimal(max_value))
    if d is None:
        return None
    if d != d.to_integral_value():
        raise ValueError(f'{label} must be a whole number (you entered {d.normalize()}).')
    return int(d)


_DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y/%m/%d', '%d/%m/%y', '%d-%m-%y', '%d %b %Y', '%d %B %Y', '%b %Y', '%B %Y')


def parse_date(v):
    """Return ``(date|None, note|None)``. Raises ValueError on garbage."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    if isinstance(v, datetime):
        return v.date(), None
    if isinstance(v, date):
        return v, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 <= float(v) <= 80000:  # Excel serial date
            return from_excel(float(v)).date(), None
        raise ValueError(f"'{v}' is not a valid date. Please use YYYY-MM-DD, for example 2027-01-31.")
    s = clean_text(v)
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        if fmt in ('%b %Y', '%B %Y'):  # month only -> last day of month
            nxt = date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
            d = date.fromordinal(nxt.toordinal() - 1)
            return d, f"Month-only expiry '{s}' was read as the last day of that month ({d.isoformat()})."
        if fmt != '%Y-%m-%d':
            return d, f"Date '{s}' was read as day/month/year → {d.isoformat()}."
        return d, None
    raise ValueError(f"'{s}' is not a valid date. Please use YYYY-MM-DD, for example 2027-01-31.")


def parse_flag(v):
    s = clean_text(v).upper()
    if not s:
        return None
    if s in TRUTHY:
        return True
    if s in FALSY:
        return False
    raise ValueError(f"requires_expiry '{clean_text(v)}' is not valid. Use YES/NO (or NDIYO/HAPANA), or leave blank.")


def normalise_unit(raw, valid_units, label):
    text = clean_text(raw)
    if text:
        for unit in valid_units:                      # exact match (ignoring case), also for custom units like "Carton of 24"
            if unit.casefold() == text.casefold():
                return unit
    s = re.sub(r'[^A-Z]', '', text.upper())
    if not s:
        return ''
    if s in valid_units:
        return s
    if s in UNIT_ALIASES and UNIT_ALIASES[s] in valid_units:
        return UNIT_ALIASES[s]
    for suffix in ('ES', 'S'):
        if s.endswith(suffix) and s[:-len(suffix)] in valid_units:
            return s[:-len(suffix)]
    hint = get_close_matches(s, valid_units, n=1, cutoff=0.6)
    msg = f"{label} '{clean_text(raw)}' is not a recognised unit."
    msg += f" Did you mean {hint[0]}?" if hint else f" Allowed: {', '.join(valid_units)}."
    raise ValueError(msg)


def clean_barcode(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return ''
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        raise ValueError('Barcode looks like a decimal number. Format the barcode column as Text in Excel.')
    s = clean_text(v).replace(' ', '')
    if _SCI.match(s):
        raise ValueError(f"Barcode '{s}' was converted to scientific notation by Excel. Format the column as Text and re-type it.")
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    if len(s) > MAX_BARCODE:
        raise ValueError(f'Barcode is longer than {MAX_BARCODE} characters.')
    if len(s) < 4:
        raise ValueError('Barcode is too short (minimum 4 characters). Leave blank to auto-generate.')
    if not _BARCODE_OK.match(s):
        raise ValueError('Barcode may only contain letters, digits, dash, dot or underscore.')
    return s


def barcode_key(code):
    """Canonical form so 6291041500213 and 06291041500213 count as the same barcode."""
    code = (code or '').strip()
    if code.isdigit() and 8 <= len(code) <= 14:
        return (code.lstrip('0') or '0').zfill(14)
    return code


def _squash(name):
    return re.sub(r'[^a-z0-9]', '', name.casefold())


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class Catalog:
    """Snapshot of the branch catalog used for validation (built by the view).

    ``products`` is an iterable of dicts with: pk, name, sku, barcode, unit,
    requires_expiry, is_active, stock.  ``categories`` is an iterable of names.
    """

    def __init__(self, products, categories):
        self.by_name, self.by_sku, self.by_barcode, self.by_squash = {}, {}, {}, {}
        for p in products:
            self.by_name[p['name'].casefold()] = p
            if p.get('sku'):
                self.by_sku[p['sku'].upper()] = p
            if p.get('barcode'):
                self.by_barcode[barcode_key(p['barcode'])] = p
            self.by_squash.setdefault(_squash(p['name']), p)
        self.categories = {c.casefold(): c for c in categories}


def _is_blank_qty(value):
    """True when a quantity cell is empty, whitespace or zero (= nothing received)."""
    if value is None:
        return True
    text = str(value).strip().replace(',', '')
    if not text:
        return True
    try:
        return float(text) == 0
    except ValueError:
        return False


def validate_rows(rows, mode, catalog, valid_units, today):
    """Validate every row. Returns a list of result dicts (see module docstring)."""
    valid_units = list(valid_units)
    results = []
    seen_keys = {}
    sku_owner, barcode_owner, name_unit = {}, {}, {}
    new_categories = set()

    for row_idx, d in rows:
        errors, warnings = [], []

        def err(field, msg, also=()):
            errors.append({'field': field, 'label': FIELD_LABELS.get(field, field), 'message': msg, 'also': list(also)})

        def warn(field, msg):
            warnings.append({'field': field, 'label': FIELD_LABELS.get(field, field), 'message': msg})

        rec = {'row': row_idx}
        raw_display = {k: ('' if v is None else v) for k, v in d.items()}

        # ---- RESTOCK: rows the user did not work on are left untouched ------
        # The restock template lists the whole catalog. A row with no purchase
        # quantity means "no delivery for this product", so it is skipped quietly:
        # no stock change, no new batch and - importantly - no validation errors
        # from the pre-filled reference columns.
        if mode == 'RESTOCK':
            qty_cell = d.get('purchase_quantity')
            if _is_blank_qty(qty_cell) and not _is_blank_qty(d.get('quantity')):
                qty_cell = d.get('quantity')
            if _is_blank_qty(qty_cell):
                skip_name = clean_text(d.get('name')) or '(missing)'
                results.append({
                    'row': row_idx, 'status': 'skipped', 'errors': [], 'warnings': [],
                    'rec': None, 'raw': raw_display, 'action': 'Unchanged - no purchase quantity entered',
                    'display': {
                        'name': skip_name, 'sku': clean_text(d.get('sku')).upper(),
                        'category': clean_text(d.get('category')), 'qty': '', 'cost': '', 'price': '',
                        'expiry': '', 'batch': '',
                    },
                })
                continue

        # ---- name ----------------------------------------------------------
        name = clean_text(d.get('name'))
        if not name:
            err('name', 'Please enter the product name.')
        elif len(name) < 2:
            err('name', 'The product name is too short - use at least 2 characters.')
        elif len(name) > MAX_NAME:
            err('name', f'The product name is too long (maximum {MAX_NAME} characters).')
        elif name.replace(' ', '').isdigit():
            warn('name', 'The product name has only digits. Please check that a barcode or price was not typed in the name column.')
        rec['name'] = name

        # ---- sku -----------------------------------------------------------
        sku = clean_text(d.get('sku')).upper().replace(' ', '')
        if sku:
            if len(sku) > MAX_SKU:
                err('sku', f'The SKU is too long (maximum {MAX_SKU} characters).')
            elif not _SKU_OK.match(sku):
                err('sku', 'The SKU can only have letters, numbers and - . _ / (no spaces or other symbols).')
        rec['sku'] = sku

        # ---- category ------------------------------------------------------
        cat = clean_text(d.get('category'))
        if len(cat) > MAX_CATEGORY:
            err('category', f'Category is longer than {MAX_CATEGORY} characters.')
        elif cat:
            existing = catalog.categories.get(cat.casefold())
            if existing:
                cat = existing  # canonical spelling/case
            elif cat.casefold() not in new_categories:
                new_categories.add(cat.casefold())
                warn('category', f"New category '{cat}' will be created.")
        rec['category_name'] = cat

        # ---- units ---------------------------------------------------------
        unit = ''
        unit_given = False
        try:
            unit = normalise_unit(d.get('unit'), valid_units, 'Base unit')
            unit_given = bool(unit)
            unit = unit or 'PIECE'
        except ValueError as exc:
            err('unit', str(exc))
        rec['unit'] = unit

        purchase_unit = ''
        try:
            purchase_unit = normalise_unit(d.get('purchase_unit'), valid_units, 'Purchase unit')
        except ValueError as exc:
            err('purchase_unit', str(exc))

        # ---- barcode -------------------------------------------------------
        barcode = ''
        try:
            barcode = clean_barcode(d.get('barcode'))
        except ValueError as exc:
            err('barcode', str(exc))
        rec['barcode'] = barcode

        batch_number = clean_text(d.get('batch_number'))
        if len(batch_number) > MAX_BATCH:
            err('batch_number', f'Batch number is longer than {MAX_BATCH} characters.')
        rec['batch_number'] = batch_number

        # ---- reorder level -------------------------------------------------
        reorder = 10
        try:
            val = parse_int(d.get('reorder_level'), 'Reorder level', allow_zero=True, max_value=MAX_QTY)
            reorder = 10 if val is None else val
        except ValueError as exc:
            err('reorder_level', str(exc))
        rec['reorder_level'] = reorder

        # ---- packaging -----------------------------------------------------
        conversion = 1
        try:
            val = parse_int(d.get('units_per_purchase_unit'), 'Units per purchase unit', max_value=100_000)
            conversion = 1 if val is None else val
        except ValueError as exc:
            err('units_per_purchase_unit', str(exc))
        if conversion > 1 and not purchase_unit and not any(e['field'] == 'purchase_unit' for e in errors):
            err('purchase_unit', 'You entered more than 1 unit per pack, so please also choose the purchase unit (for example BOX).')

        # ---- quantity & price (new or legacy format) ----------------------
        purchase_qty, unit_price = None, None
        legacy_qty = d.get('quantity')
        if d.get('purchase_quantity') in (None, '') and legacy_qty not in (None, ''):
            try:
                purchase_qty = parse_int(legacy_qty, 'Quantity')
            except ValueError as exc:
                err('quantity', str(exc))
            conversion, purchase_unit = 1, unit
        else:
            try:
                purchase_qty = parse_int(d.get('purchase_quantity'), 'Purchase quantity')
            except ValueError as exc:
                err('purchase_quantity', str(exc))
        if purchase_qty is None and not any(e['field'] in ('purchase_quantity', 'quantity') for e in errors):
            err('purchase_quantity', 'Please enter the quantity you purchased.')

        price_raw = d.get('purchase_price')
        if price_raw in (None, ''):
            price_raw = d.get('purchase_price_per_purchase_unit')
        try:
            unit_price = parse_decimal(price_raw, 'Purchase price')
            if unit_price is None:
                err('purchase_price', 'Please enter the buying price.')
            elif unit_price != unit_price.quantize(Decimal('0.01')):
                warn('purchase_price', 'Purchase price has more than 2 decimals and was rounded.')
                unit_price = unit_price.quantize(Decimal('0.01'))
        except ValueError as exc:
            err('purchase_price', str(exc))

        selling_price = None
        try:
            selling_price = parse_decimal(d.get('selling_price'), 'Selling price')
            if selling_price is None:
                err('selling_price', 'Please enter the selling price.')
            elif selling_price != selling_price.quantize(Decimal('0.01')):
                warn('selling_price', 'Selling price has more than 2 decimals and was rounded.')
                selling_price = selling_price.quantize(Decimal('0.01'))
        except ValueError as exc:
            err('selling_price', str(exc))

        purchase_unit_given = bool(purchase_unit)
        if not purchase_unit:
            purchase_unit = unit
        rec.update(purchase_unit=purchase_unit, conversion=conversion, purchase_qty=purchase_qty or 0,
                   purchase_unit_price=unit_price or Decimal('0'), selling_price=selling_price or Decimal('0'))

        base_qty = (purchase_qty or 0) * conversion
        base_cost = (unit_price / Decimal(conversion)).quantize(Decimal('0.01')) if unit_price else Decimal('0')
        rec.update(base_quantity=base_qty, base_cost=base_cost)
        if base_qty > MAX_QTY:
            err('purchase_quantity', f'Total quantity {base_qty:,} is unrealistically large. Check quantity and units per purchase unit.')
        elif base_qty >= HIGH_QTY_WARN:
            warn('purchase_quantity', f'Very large quantity ({base_qty:,} {unit or "units"}). Please confirm.')

        # ---- expiry --------------------------------------------------------
        expiry = None
        try:
            expiry, note = parse_date(d.get('expiry_date'))
            if note:
                warn('expiry_date', note)
            if expiry is not None:
                if expiry < today:
                    err('expiry_date', f'This item already expired on {expiry.isoformat()}, so it cannot be added to stock.'
                        + (' The date in this row was copied from the previous batch - type the expiry date of the NEW delivery.' if mode == 'RESTOCK' else ''))
                elif (expiry - today).days <= EXPIRY_SOON_DAYS:
                    warn('expiry_date', f'This item expires very soon ({expiry.isoformat()}, within {EXPIRY_SOON_DAYS} days).')
                elif expiry.year - today.year > EXPIRY_FAR_YEARS:
                    warn('expiry_date', f'The expiry date is more than {EXPIRY_FAR_YEARS} years away ({expiry.isoformat()}). Please check the year.')
        except ValueError as exc:
            err('expiry_date', str(exc))
        rec['expiry_date'] = expiry

        flag = None
        try:
            flag = parse_flag(d.get('requires_expiry'))
        except ValueError as exc:
            err('requires_expiry', str(exc))
        rec['requires_expiry_override'] = flag

        # ---- catalog checks ------------------------------------------------
        existing = None
        if name:
            existing = catalog.by_name.get(name.casefold())
            sku_prod = catalog.by_sku.get(sku) if sku else None
            if sku_prod and existing is None:
                existing = sku_prod
                if sku_prod['name'].casefold() != name.casefold():
                    err('sku', f"SKU '{sku}' already belongs to '{sku_prod['name']}', not '{name}'.")
            elif sku_prod and sku_prod['pk'] != existing['pk']:
                err('sku', f"SKU '{sku}' already belongs to another product ('{sku_prod['name']}').")
            if sku and existing and existing.get('sku') and existing['sku'].upper() != sku and sku not in catalog.by_sku:
                warn('sku', f"This product already has SKU '{existing['sku']}'; the SKU in the file will replace it.")
            if barcode:
                owner = catalog.by_barcode.get(barcode_key(barcode))
                if owner and (existing is None or owner['pk'] != existing['pk']):
                    err('barcode', f"Barcode '{barcode}' is already assigned to '{owner['name']}'.")

        if existing and not unit_given and not any(e['field'] == 'unit' for e in errors):
            # blank unit on an existing product: keep the product's own unit
            unit = existing['unit']
            rec['unit'] = unit
            if not purchase_unit_given:
                purchase_unit = unit
                rec['purchase_unit'] = unit
        if selling_price and base_cost:
            per = unit or 'unit'
            if selling_price < base_cost:
                hint = ''
                if conversion == 1 and unit_price and selling_price < unit_price:
                    hint = (' If the buying price is for a whole box/carton, fill "purchase_unit" and '
                            '"units_per_purchase_unit" so the system can work out the price of one ' + per.lower() + '.')
                err('selling_price',
                    f"You would sell at a loss: selling price TSh {selling_price:,.2f} is lower than the buying cost "
                    f"TSh {base_cost:,.2f} per {per.lower()}. Raise the selling price or correct the buying price.{hint}",
                    also=['purchase_price'])
            elif selling_price == base_cost:
                warn('selling_price', f'Selling price equals the buying cost (TSh {base_cost:,.2f} per {per.lower()}), so there is no profit on this item.')
            elif selling_price / base_cost > MARKUP_WARN:
                warn('selling_price', f'Selling price is more than {MARKUP_WARN}x the buying cost (TSh {selling_price:,.2f} vs TSh {base_cost:,.2f}). Please check for an extra digit.')

        if existing:
            if existing['unit'] != unit and unit and existing.get('stock', 0) > 0:
                err('unit', f"'{existing['name']}' already has stock in {existing['unit']}; the file says {unit}. Edit the product instead.")
            if not existing.get('is_active', True):
                warn('name', 'This product was deleted/inactive and will be reactivated.')
            if mode == 'OPENING' and existing.get('stock', 0) > 0:
                warn('name', f"Product already has {existing['stock']:,} in stock; this adds a NEW batch. Use Restock mode if this is a purchase.")
        elif name and not errors:
            near = catalog.by_squash.get(_squash(name))
            if near:
                warn('name', f"Looks like existing product '{near['name']}' (spelling differs). A new product will be created - fix the name if it is the same item.")

        if mode == 'RESTOCK':
            # SKU is optional: many products have none. The row is matched by SKU,
            # else by exact product name, and must match an existing product.
            if name and existing is None:
                err('name', 'Restock rows must match an existing product. Use Opening Stock mode to add new products.')

        if existing is not None:
            eff_requires = flag if flag is not None else existing.get('requires_expiry', True)
        else:
            eff_requires = True if flag is None else flag
        if eff_requires and expiry is None and not any(e['field'] == 'expiry_date' for e in errors):
            err('expiry_date', "This product needs an expiry date. Enter it as YYYY-MM-DD, or set 'requires_expiry' to NO if the item does not expire.")

        # ---- cross-row checks ---------------------------------------------
        if name and not any(e['field'] == 'name' for e in errors):
            key = (name.casefold(), batch_number.casefold(), expiry, base_cost, selling_price, purchase_unit, conversion)
            if key in seen_keys:
                err('name', f'Duplicate of row {seen_keys[key]} (same product, batch, expiry and prices). Delete this row or add the quantities together.')
            else:
                seen_keys[key] = row_idx
            prev_unit = name_unit.get(name.casefold())
            if prev_unit and unit and prev_unit[0] != unit:
                err('unit', f"Row {prev_unit[1]} uses base unit {prev_unit[0]} for this product but this row uses {unit}.")
            elif unit:
                name_unit.setdefault(name.casefold(), (unit, row_idx))
            if sku:
                owner = sku_owner.get(sku)
                if owner and owner[0] != name.casefold():
                    err('sku', f"SKU '{sku}' is also used by '{owner[2]}' in row {owner[1]}.")
                else:
                    sku_owner.setdefault(sku, (name.casefold(), row_idx, name))
            if barcode:
                owner = barcode_owner.get(barcode_key(barcode))
                if owner and owner[0] != name.casefold():
                    err('barcode', f"Barcode '{barcode}' is also used by '{owner[2]}' in row {owner[1]}.")
                else:
                    barcode_owner.setdefault(barcode_key(barcode), (name.casefold(), row_idx, name))

        # ---- action label --------------------------------------------------
        if errors:
            action = 'Fix required'
        elif existing is None:
            action = 'New product + batch'
        elif mode == 'RESTOCK':
            action = 'Restock existing product'
        else:
            action = 'New batch for existing product'

        status = 'error' if errors else ('warning' if warnings else 'ok')
        results.append({
            'row': row_idx, 'status': status, 'errors': errors, 'warnings': warnings,
            'rec': rec, 'raw': raw_display, 'action': action,
            'display': {
                'name': name or '(missing)', 'sku': sku, 'category': cat,
                'qty': (f"{purchase_qty:,} {purchase_unit}" + (f" × {conversion:,} = {base_qty:,} {unit}" if conversion > 1 else '')) if purchase_qty else '',
                'cost': f'{base_cost:,.2f}' if base_cost else '',
                'price': f'{selling_price:,.2f}' if selling_price else '',
                'expiry': expiry.isoformat() if expiry else '',
                'batch': batch_number,
            },
        })
    return results


def summarise(results):
    return {
        'total': len(results),
        'ok': sum(1 for r in results if r['status'] == 'ok'),
        'warning': sum(1 for r in results if r['status'] == 'warning'),
        'error': sum(1 for r in results if r['status'] == 'error'),
        'skipped': sum(1 for r in results if r['status'] == 'skipped'),
    }


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def add_dropdowns(ws, headers, valid_units, last_row=500):
    """Excel dropdowns for unit / purchase_unit / requires_expiry columns."""
    def col_range(h):
        c = get_column_letter(headers.index(h) + 1)
        return f'{c}2:{c}{last_row}'
    listed, size = [], 2
    for unit in valid_units:                          # Excel allows at most 255 characters in an in-cell list
        if ',' in unit or size + len(unit) + 1 > 250:
            continue
        listed.append(unit)
        size += len(unit) + 1
    unit_dv = DataValidation(type='list', formula1='"' + ','.join(listed) + '"', allow_blank=True, showErrorMessage=True,
                             errorTitle='Unit', error='Choose a unit from the list.')
    flag_dv = DataValidation(type='list', formula1='"YES,NO"', allow_blank=True, showErrorMessage=True,
                             errorTitle='Requires expiry', error='Choose YES or NO.')
    ws.add_data_validation(unit_dv)
    ws.add_data_validation(flag_dv)
    for h in ('unit', 'purchase_unit'):
        if h in headers:
            unit_dv.add(col_range(h))
    if 'requires_expiry' in headers:
        flag_dv.add(col_range('requires_expiry'))


def text_columns(ws, headers, names=('barcode', 'sku', 'batch_number'), last_row=500):
    for n in names:
        if n in headers:
            c = get_column_letter(headers.index(n) + 1)
            for r in range(2, last_row + 1):
                ws[f'{c}{r}'].number_format = '@'


def build_fix_workbook(headers, results, valid_units, reasons=None):
    """Excel with only the given result rows, plus a ``problems`` column.

    ``reasons`` optionally maps row number -> extra text (e.g. 'Deselected by you').
    Returns bytes.  The upload engine ignores the ``problems`` column, so the
    corrected file can be uploaded straight back.
    """
    reasons = reasons or {}
    out_headers = [h for h in headers if h in KNOWN_HEADERS] or list(BULK_UPLOAD_HEADERS)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Products to fix'
    ws.append(out_headers + ['problems'])
    head_fill = PatternFill('solid', fgColor='0F766E')
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = head_fill
        c.alignment = Alignment(vertical='center', wrap_text=True)
    ws.cell(row=1, column=len(out_headers) + 1).fill = PatternFill('solid', fgColor='B91C1C')

    bad = PatternFill('solid', fgColor='FECACA')
    warn_fill = PatternFill('solid', fgColor='FEF3C7')
    text_columns(ws, out_headers)
    for r_i, res in enumerate(results, start=2):
        for c_i, h in enumerate(out_headers, start=1):
            v = res['raw'].get(h, '')
            cell = ws.cell(row=r_i, column=c_i, value=v if v != '' else None)
            if isinstance(v, (datetime, date)):
                cell.number_format = 'yyyy-mm-dd'
            elif h in ('barcode', 'sku', 'batch_number'):
                cell.number_format = '@'
        problems = []
        for item, fill in ((res['errors'], bad), (res['warnings'], warn_fill)):
            for p in item:
                tag = 'ERROR' if fill is bad else 'CHECK'
                problems.append(f"[{tag}] {p['message']}")
                for fld in [p['field']] + list(p.get('also', [])):
                    if fld in out_headers:
                        cell = ws.cell(row=r_i, column=out_headers.index(fld) + 1)
                        if fill is bad or cell.fill.fgColor.rgb in (None, '00000000'):
                            cell.fill = fill
                        if fill is bad:
                            cell.comment = Comment(p['message'], 'SmartPOS')
        if reasons.get(res['row']):
            problems.insert(0, reasons[res['row']])
        pc = ws.cell(row=r_i, column=len(out_headers) + 1, value='\n'.join(problems))
        pc.alignment = Alignment(wrap_text=True, vertical='top')
        pc.font = Font(color='B91C1C') if res['errors'] else Font(color='92400E')

    for i, h in enumerate(out_headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(16, min(28, len(h) + 4))
    ws.column_dimensions[get_column_letter(len(out_headers) + 1)].width = 70
    ws.freeze_panes = 'B2'
    add_dropdowns(ws, out_headers, valid_units)

    info = wb.create_sheet('How to fix')
    for line in (
        'How to fix and re-upload this file',
        '',
        '1. Read the "problems" column at the end of each row. Red cells need fixing; yellow cells only need a quick check.',
        '2. Correct the values in the product columns (you can also delete the "problems" column - it is ignored on upload).',
        '3. Keep the header row exactly as it is. Do not rename or reorder columns.',
        '4. Save the file as .xlsx and upload it again on Bulk Upload. You will see a new preview before anything is saved.',
        '',
        'Tips: dates use YYYY-MM-DD (2027-01-31). Barcodes, SKUs and batch numbers must be Text, not numbers.',
        'Rows you deselected on purpose appear here with the note "Deselected by you" - they have no errors.',
    ):
        info.append([line])
    info['A1'].font = Font(bold=True, size=14)
    info.column_dimensions['A'].width = 120

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
