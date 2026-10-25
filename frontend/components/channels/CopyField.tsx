"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function CopyField({ label, value }: { label: string; value: string }) {
	const [copied, setCopied] = useState(false);
	return (
		<div className="space-y-1">
			<Label className="text-xs text-muted-foreground">{label}</Label>
			<div className="flex gap-2">
				<Input readOnly value={value} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
				<Button
					type="button"
					variant="outline"
					size="icon"
					aria-label={`Copy ${label}`}
					onClick={() => {
						navigator.clipboard?.writeText(value);
						setCopied(true);
						setTimeout(() => setCopied(false), 1500);
					}}
				>
					{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
				</Button>
			</div>
		</div>
	);
}
