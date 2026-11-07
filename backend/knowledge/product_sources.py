import csv
import io
import re
from datetime import timedelta
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .catalog import SourceRow, _attr_key, apply_source_row, normalise_name
from .models import MAX_IMAGES_PER_PRODUCT, Product, ProductImage

TARGETS = ["external_id", "sku", "name", "price", "description", "available", "variant_label", "product_url", "image_url"]

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
	"image_url": {"image", "images", "img", "photo", "photos", "picture", "pic"},
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
	"image_url": ["ছবি"],
}
_SPLIT = re.compile(r"[\s_\-/.,:;()\[\]]+")
_FALSE = {"no", "n", "false", "0", "out of stock", "outofstock", "unavailable", "sold out", "না", "নেই", "স্টক নেই"}

MAX_FILE_BYTES = 5 * 1024 * 1024


class TableError(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code
		self.message = message


_URL_SPLIT = re.compile(r"[\s,;]+")  # a URL has no spaces, so spaces, commas and semicolons can separate them


def split_image_urls(text: str) -> list[str]:
    """Links in one cell, in order, without repeats."""
    return list(dict.fromkeys(p for p in _URL_SPLIT.split(text or "") if p))


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
		image_urls=split_image_urls(get("image_url") or ""),
		attributes={"variant": variant} if variant else {},
		custom={k[7:]: raw.get(col, "").strip() for k, col in mapping.items() if k.startswith("custom:") and col},
	)


def check_headers(mapping: dict, headers: list[str]) -> None:
	"""A mapped column that is gone from the file would blank that field on every row: refuse the whole sync."""
	missing = [col for col in mapping.values() if col and col not in headers]
	if missing:
		raise TableError("mapping_mismatch", "Columns missing from the file: " + ", ".join(missing))


@transaction.atomic
def apply_rows(kb, source, rows: list[dict], mapping: dict, sync_started_at) -> dict:
	"""Apply every row through apply_source_row, then hide this source's products that were not seen."""
	fields = list(kb.product_fields.all())
	result = {"created": 0, "updated": 0, "unchanged": 0, "hidden": 0, "rejected": [], "images_queued": 0}
	image_jobs: list[tuple] = []  # (product, url) for rows that name an image
	seen_ids: set[str] = set()
	seen_names: set[tuple] = set()
	for n, raw in enumerate(rows, start=2):  # row 1 is the header
		row = _source_row(raw, mapping)
		if row.external_id and row.external_id in seen_ids:
			result["rejected"].append({"row": n, "message": f"ID {row.external_id} already appears earlier in this file"})
			continue
		name_key = None if row.external_id or row.sku else (normalise_name(row.name), _attr_key(row.attributes))
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
		if row.image_urls:
			image_jobs.append((row.product, row.image_urls))
	# Only URLs a product does not already have: an hourly sync must not download the same photo again
	# A failed link is tried again only after a day: a dead image URL must not be downloaded on every hourly sync
	retry_after = timezone.now() - timedelta(hours=24)
	attached = set(ProductImage.objects.filter(product__source_ref=source).filter(
		~Q(status="failed") | Q(created_at__gt=retry_after)).values_list("product_id", "source_url"))
	queued: dict = {}  # product id -> its new URLs; the product's photo limit is enforced by attach_product_images
	for product, urls in image_jobs:
		fresh = [u for u in urls if (product.id, u) not in attached][:MAX_IMAGES_PER_PRODUCT]
		if fresh:
			queued[product.id] = fresh
	if queued:
		from .tasks import fetch_product_images  # tasks imports this module; a top-level import would loop

		# One task per product, run in the worker after the commit, so slow or broken images never hold up the sync
		jobs = list(queued.items())
		transaction.on_commit(lambda: [fetch_product_images.delay(str(pid), urls) for pid, urls in jobs])
		result["images_queued"] = sum(len(urls) for _, urls in jobs)
	stale = Product.objects.filter(source_ref=source).exclude(status="hidden").filter(
		Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=sync_started_at))
	for product in stale:
		if "status" in (product.locked_fields or []):
			continue
		product.status = "hidden"
		product.save(update_fields=["status", "updated_at"])
		result["hidden"] += 1
	return result


class SheetNotPublic(Exception):
	pass


_SHEET_ID = re.compile(r"^/spreadsheets/d/([A-Za-z0-9_-]+)")


def sheet_export_url(sheet_url: str) -> str:
	"""CSV export URL for a Google Sheet link. The host is fixed to docs.google.com (SSRF guard)."""
	u = urlparse((sheet_url or "").strip())
	m = _SHEET_ID.match(u.path)
	if u.scheme != "https" or u.hostname != "docs.google.com" or not m:
		raise ValueError("Paste a Google Sheets link (docs.google.com/spreadsheets/...)")
	gid = (parse_qs(u.query).get("gid") or parse_qs(u.fragment).get("gid") or ["0"])[0]
	if not gid.isdigit():
		raise ValueError("Paste a Google Sheets link (docs.google.com/spreadsheets/...)")
	return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv&gid={gid}"


MAX_REDIRECTS = 3


def _google_host(url: str) -> bool:
	u = urlparse(url)
	host = u.hostname or ""
	return u.scheme == "https" and (host == "docs.google.com" or host.endswith(".googleusercontent.com"))


def fetch_sheet_csv(url: str) -> bytes:
	msg = "Share the sheet as 'Anyone with the link can view'."
	# Redirects are followed by hand so every hop stays on Google (SSRF guard); a login page is off the allowlist.
	for _ in range(MAX_REDIRECTS + 1):
		try:
			r = requests.get(url, timeout=15, allow_redirects=False)
		except requests.RequestException:
			raise SheetNotPublic("Could not reach Google Sheets. Try again later.")
		if r.status_code not in (301, 302, 303, 307, 308):
			break
		url = urljoin(url, r.headers.get("Location") or "")
		if not r.headers.get("Location") or not _google_host(url):
			raise SheetNotPublic(msg)
	else:
		raise SheetNotPublic(msg)
	if r.status_code != 200 or "text/html" in r.headers.get("Content-Type", "").lower() or r.content[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
		raise SheetNotPublic(msg)
	if len(r.content) > MAX_FILE_BYTES:
		raise TableError("file_too_large", "The sheet is too large to import")
	return r.content
