import csv
import io
import re

from django.db.models import Q

from .catalog import SourceRow, _attr_key, apply_source_row, normalise_name
from .models import Product

TARGETS = ["external_id", "sku", "name", "price", "description", "available", "variant_label", "product_url"]

# ASCII keywords match whole tokens; Bengali ones match as substrings (no reliable word boundaries).
_ASCII = {
	"sku": {"sku"},
	"external_id": {"id", "code", "barcode", "handle"},
	"price": {"price", "cost", "rate", "amount", "mrp"},
	"name": {"name", "title"},
	"description": {"description", "desc", "details", "about"},
	"available": {"available", "availability", "stock", "instock"},
	"product_url": {"url", "link"},
	"variant_label": {"variant", "size", "option"},
}
_BENGALI = {
	"sku": ["এসকেইউ"],
	"external_id": ["কোড", "আইডি", "ক্রমিক"],
	"price": ["দাম", "মূল্য", "মুল্য", "টাকা"],
	"name": ["নাম", "শিরোনাম"],
	"description": ["বিবরণ", "বর্ণনা"],
	"available": ["স্টক", "উপলব্ধ"],
	"product_url": ["লিংক", "লিঙ্ক"],
	"variant_label": ["সাইজ", "ধরন"],
}
_SPLIT = re.compile(r"[\s_\-/.,:;()\[\]]+")
_FALSE = {"no", "n", "false", "0", "out of stock", "outofstock", "unavailable", "sold out", "না", "নেই", "স্টক নেই"}

MAX_FILE_BYTES = 5 * 1024 * 1024


class TableError(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code
		self.message = message


def _cell(value) -> str:
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	return str(value).strip()


def parse_table(filename: str, data: bytes) -> tuple[list[str], list[dict[str, str]]]:
	"""(headers, rows) from a .csv (utf-8-sig) or the first sheet of an .xlsx. The first row is the header."""
	name = (filename or "").lower()
	if name.endswith(".csv"):
		try:
			grid = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))
		except (UnicodeDecodeError, csv.Error):
			raise TableError("invalid_file", "The CSV could not be read. Save it as UTF-8 and try again.")
	elif name.endswith(".xlsx"):
		import openpyxl

		try:
			wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
			try:
				grid = [[_cell(c) for c in r] for r in wb.worksheets[0].iter_rows(values_only=True)]
			finally:
				wb.close()
		except Exception:  # openpyxl raises many types (BadZipFile, KeyError, ValueError, ...) on a bad file
			raise TableError("invalid_file", "The Excel file could not be read.")
	else:
		raise TableError("unsupported_file", "Upload a .csv or .xlsx file")
	grid = [[c.strip() for c in r] for r in grid]
	while grid and not any(grid[0]):
		grid.pop(0)
	if not grid:
		raise TableError("invalid_file", "The file has no header row.")
	headers = grid[0]
	while headers and not headers[-1]:
		headers.pop()
	if not headers or not all(headers) or len(set(headers)) != len(headers):
		raise TableError("invalid_file", "The header row must have a unique name for every column.")
	rows = []
	for r in grid[1:]:
		if not any(r):
			continue
		r = (r + [""] * len(headers))[:len(headers)]
		rows.append(dict(zip(headers, r)))
	return headers, rows


def guess_mapping(headers: list[str]) -> dict[str, str | None]:
	"""Best-effort target -> header guess; heuristic only, no LLM."""
	mapping: dict[str, str | None] = {t: None for t in TARGETS}
	taken: set[str] = set()
	for target in TARGETS:
		for h in headers:
			if h in taken:
				continue
			low = h.lower()
			tokens = {t for t in _SPLIT.split(low) if t}
			if tokens & _ASCII[target] or any(b in low for b in _BENGALI[target]):
				mapping[target] = h
				taken.add(h)
				break
	return mapping


def has_key(mapping: dict) -> bool:
	return bool(mapping.get("external_id") or mapping.get("sku"))


def _to_available(text: str) -> bool:
	return text.strip().lower() not in _FALSE


def _source_row(raw: dict, mapping: dict) -> SourceRow:
	def get(target):
		col = mapping.get(target)
		return None if not col else raw.get(col, "").strip()

	available = get("available")
	variant = get("variant_label") or ""
	return SourceRow(
		external_id=get("external_id") or None, sku=get("sku") or "", name=get("name") or "",
		price_text=get("price"), available=None if available is None or available == "" else _to_available(available),
		description=get("description"), product_url=get("product_url"), variant_label=variant,
		attributes={"variant": variant} if variant else {},
		custom={k[7:]: raw.get(col, "").strip() for k, col in mapping.items() if k.startswith("custom:") and col},
	)


def apply_rows(kb, source, rows: list[dict], mapping: dict, sync_started_at) -> dict:
	"""Apply every row through apply_source_row, then hide this source's products that were not seen."""
	fields = list(kb.product_fields.all())
	result = {"created": 0, "updated": 0, "unchanged": 0, "hidden": 0, "rejected": []}
	seen_ids: set[str] = set()
	seen_names: set[tuple] = set()
	for n, raw in enumerate(rows, start=2):  # row 1 is the header
		row = _source_row(raw, mapping)
		if row.external_id and row.external_id in seen_ids:
			result["rejected"].append({"row": n, "message": f"ID {row.external_id} already appears earlier in this file"})
			continue
		name_key = None if row.external_id else (normalise_name(row.name), _attr_key(row.attributes))
		if name_key and name_key[0] and name_key in seen_names:
			result["rejected"].append({"row": n, "message": "This name already appears earlier in this file"})
			continue
		outcome = apply_source_row(kb, source, row, sync_started_at, fields)
		if outcome == "rejected":
			message = "Row has no name" if not row.name else "Another product already uses this ID"
			result["rejected"].append({"row": n, "message": message})
			continue
		if row.external_id:
			seen_ids.add(row.external_id)
		elif name_key:
			seen_names.add(name_key)
		result[outcome] += 1
	stale = Product.objects.filter(source_ref=source).exclude(status="hidden").filter(
		Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=sync_started_at))
	for product in stale:
		if "status" in (product.locked_fields or []):
			continue
		product.status = "hidden"
		product.save(update_fields=["status", "updated_at"])
		result["hidden"] += 1
	return result
