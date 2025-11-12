"use client";

import { FormEvent, useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type ChatInputProps = {
	onSend: (content: string) => void;
	disabled?: boolean;
};

export default function ChatInput({ onSend, disabled }: ChatInputProps) {
	const [value, setValue] = useState("");
	const [submitting, setSubmitting] = useState(false);

	function onSubmit(e: FormEvent) {
		e.preventDefault();
		if (!value.trim() || submitting || disabled) return;
		setSubmitting(true);
		onSend(value.trim());
		setValue("");
		setSubmitting(false);
	}

	return (
		<Card className="p-3">
			<form onSubmit={onSubmit} className="space-y-3">
				<Textarea
					placeholder="Type a message..."
					rows={3}
					value={value}
					onChange={(e) => setValue(e.target.value)}
					disabled={disabled || submitting}
				/>
				<div className="flex items-center justify-end gap-2">
					<Button
						type="submit"
						disabled={disabled || submitting || !value.trim()}
					>
						Send
					</Button>
				</div>
			</form>
		</Card>
	);
}


