"use client";

import { FormEvent, useState, useRef, useEffect } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Send } from "lucide-react";
import { cn } from "@/lib/utils";

type ChatInputProps = {
	onSend: (content: string) => void;
	disabled?: boolean;
};

export default function ChatInput({ onSend, disabled }: ChatInputProps) {
	const [value, setValue] = useState("");
	const [submitting, setSubmitting] = useState(false);
	const textareaRef = useRef<HTMLTextAreaElement>(null);

	// Auto-resize textarea
	useEffect(() => {
		if (textareaRef.current) {
			textareaRef.current.style.height = "auto";
			textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
		}
	}, [value]);

	function onSubmit(e: FormEvent) {
		e.preventDefault();
		if (!value.trim() || submitting || disabled) return;
		setSubmitting(true);
		onSend(value.trim());
		setValue("");
		setSubmitting(false);
		// Reset textarea height
		if (textareaRef.current) {
			textareaRef.current.style.height = "auto";
		}
	}

	function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			onSubmit(e as any);
		}
	}

	return (
		<div className="border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
			<form onSubmit={onSubmit} className="p-4">
				<div className="flex items-end gap-3">
					<div className="flex-1 relative">
						<Textarea
							ref={textareaRef}
							placeholder="Type a message..."
							value={value}
							onChange={(e) => setValue(e.target.value)}
							onKeyDown={handleKeyDown}
							disabled={disabled || submitting}
							rows={1}
							className={cn(
								"min-h-[44px] max-h-[120px] resize-none",
								"rounded-2xl px-4 py-3 pr-12",
								"border-2 focus-visible:ring-0 focus-visible:ring-offset-0",
								"bg-muted/50 border-border/50",
								"transition-all duration-200"
							)}
						/>
					</div>
					<Button
						type="submit"
						size="icon"
						disabled={disabled || submitting || !value.trim()}
						className={cn(
							"h-11 w-11 rounded-full shrink-0",
							"shadow-md hover:shadow-lg transition-shadow",
							"disabled:opacity-50 disabled:cursor-not-allowed"
						)}
					>
						<Send className="h-4 w-4" />
						<span className="sr-only">Send message</span>
					</Button>
				</div>
				<div className="mt-2 text-xs text-muted-foreground text-center">
					Press Enter to send, Shift+Enter for new line
				</div>
			</form>
		</div>
	);
}


