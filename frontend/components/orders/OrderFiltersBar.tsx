"use client";

import { Input } from "@/components/ui/input";
import type { OrderFilters } from "@/lib/orders-api";

const STATUSES = ["", "new", "confirmed", "shipped", "delivered", "cancelled", "draft", "abandoned"];
const CHANNELS = ["", "facebook", "instagram", "whatsapp", "widget", "self"];

const selectClass = "h-10 rounded-md border border-input bg-background px-3 text-sm";

type Props = { value: OrderFilters; onChange: (value: OrderFilters) => void };

export default function OrderFiltersBar({ value, onChange }: Props) {
	const set = (patch: OrderFilters) => onChange({ ...value, ...patch });
	return (
		<div className="grid gap-2 sm:grid-cols-3">
			<Input placeholder="Search name, phone or number" value={value.q ?? ""} onChange={(e) => set({ q: e.target.value })} />
			<select className={selectClass} value={value.status ?? ""} onChange={(e) => set({ status: e.target.value })}>
				{STATUSES.map((s) => (
					<option key={s} value={s}>
						{s || "All statuses"}
					</option>
				))}
			</select>
			<select className={selectClass} value={value.channel ?? ""} onChange={(e) => set({ channel: e.target.value })}>
				{CHANNELS.map((c) => (
					<option key={c} value={c}>
						{c || "All channels"}
					</option>
				))}
			</select>
			<Input type="date" aria-label="From date" value={value.date_from ?? ""} onChange={(e) => set({ date_from: e.target.value })} />
			<Input type="date" aria-label="To date" value={value.date_to ?? ""} onChange={(e) => set({ date_to: e.target.value })} />
			<select className={selectClass} value={value.is_test ?? ""} onChange={(e) => set({ is_test: e.target.value })}>
				<option value="">All orders</option>
				<option value="false">Live only</option>
				<option value="true">Test only</option>
			</select>
		</div>
	);
}
