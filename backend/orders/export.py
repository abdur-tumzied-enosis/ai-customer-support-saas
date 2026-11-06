import csv

HEADER = [
	"number", "date", "status", "channel", "is_test", "customer name", "customer phone", "customer address",
	"zone", "items", "subtotal", "zone charge", "total", "advance", "collect amount", "tracking number",
]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")  # a spreadsheet would run these as formulas


def _cell(value) -> str:
	text = "" if value is None else str(value)
	return "'" + text if text[:1] in FORMULA_START else text


def _items(order) -> str:
	return "; ".join(f"{i.quantity} x {i.name} ({i.product_external_id or i.sku})" for i in order.items.all())


def _row(order) -> list:
	return [
		order.number or "", order.created_at.date().isoformat(), order.status, order.channel,
		"yes" if order.is_test else "no", order.customer_name, order.customer_phone, order.customer_address,
		order.zone_name, _items(order), order.subtotal, order.zone_charge, order.total, order.advance_paid,
		order.collect_amount, order.tracking_number,
	]


def write_orders_csv(stream, orders) -> None:
	stream.write("﻿")  # the byte-order mark makes Excel read the file as UTF-8, so Bengali text survives
	writer = csv.writer(stream)
	writer.writerow(HEADER)
	for order in orders:
		writer.writerow([_cell(c) for c in _row(order)])
