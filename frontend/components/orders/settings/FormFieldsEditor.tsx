"use client";

import { useEffect, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi, type CustomField, type OrderSettings } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

export default function FormFieldsEditor({ projectId }: { projectId: string }) {
	const [formFields, setFormFields] = useState<OrderSettings["form_fields"] | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [saved, setSaved] = useState(false);

	useEffect(() => {
		// Projects that never saved fields store {} (no "custom" key), so fill in the defaults.
		orderApi
			.settings(projectId)
			.then((res) => setFormFields({ optional: res.data.form_fields?.optional ?? [], custom: res.data.form_fields?.custom ?? [] }))
			.catch((e) => setError(formatError(e)));
	}, [projectId]);

	if (!formFields) {
		return error ? (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		) : (
			<Loader2 className="h-5 w-5 animate-spin" />
		);
	}

	const setCustom = (custom: CustomField[]) => setFormFields({ ...formFields, custom });

	async function save() {
		setError(null);
		setSaved(false);
		try {
			await orderApi.saveSettings(projectId, { form_fields: formFields! });
			setSaved(true);
		} catch (e) {
			setError(formatError(e));
		}
	}

	return (
		<div className="space-y-3">
			{error && (
				<Alert variant="destructive">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			)}
			{formFields.custom.map((field, i) => (
				<div key={i} className="flex flex-wrap items-center gap-2">
					<Input
						className="w-32"
						placeholder="Key"
						value={field.key}
						onChange={(e) => setCustom(formFields.custom.map((f, j) => (j === i ? { ...f, key: e.target.value } : f)))}
					/>
					<Input
						className="flex-1"
						placeholder="Question for the customer"
						value={field.label}
						onChange={(e) => setCustom(formFields.custom.map((f, j) => (j === i ? { ...f, label: e.target.value } : f)))}
					/>
					<label className="flex items-center gap-1 text-sm">
						<input
							type="checkbox"
							checked={field.required}
							onChange={(e) => setCustom(formFields.custom.map((f, j) => (j === i ? { ...f, required: e.target.checked } : f)))}
						/>
						Required
					</label>
					<Button variant="ghost" size="icon" onClick={() => setCustom(formFields.custom.filter((_, j) => j !== i))}>
						<Trash2 className="h-4 w-4" />
					</Button>
				</div>
			))}
			<div className="flex items-center justify-between">
				<Button variant="outline" size="sm" onClick={() => setCustom([...formFields.custom, { key: "", label: "", required: false }])}>
					<Plus className="mr-1 h-4 w-4" /> Add field
				</Button>
				<div className="flex items-center gap-2 text-sm">
					{saved && <span className="text-muted-foreground">Saved</span>}
					<Button onClick={save}>Save fields</Button>
				</div>
			</div>
		</div>
	);
}
