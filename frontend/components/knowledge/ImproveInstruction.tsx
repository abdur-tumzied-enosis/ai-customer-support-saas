"use client";

import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { orderInstructionApi } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

type Props = { projectId: string | null; text: string; onUse: (suggestion: string) => void };

// Asks the backend for a guarded rewrite of `text`; nothing is applied until the user clicks "Use this".
export function ImproveInstruction({ projectId, text, onUse }: Props) {
	const toast = useToast();
	const [busy, setBusy] = useState(false);
	const [suggestion, setSuggestion] = useState<{ suggestion: string; why: string } | null>(null);

	async function improve() {
		if (!projectId) return;
		setBusy(true);
		try {
			const res = await orderInstructionApi.improve(projectId, text, "general");
			setSuggestion(res.data);
		} catch (e) {
			toast.error(formatError(e));
		} finally {
			setBusy(false);
		}
	}

	return (
		<div className="space-y-2">
			<Button type="button" variant="outline" size="sm" className="gap-2" onClick={improve} disabled={busy || !projectId || !text.trim()}>
				{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
				Improve
			</Button>
			{suggestion && (
				<div className="space-y-2 rounded-md bg-muted p-3 text-sm">
					<p className="whitespace-pre-wrap">{suggestion.suggestion}</p>
					<p className="text-muted-foreground">{suggestion.why}</p>
					<div className="flex gap-2">
						<Button
							type="button"
							size="sm"
							onClick={() => {
								onUse(suggestion.suggestion);
								setSuggestion(null);
							}}
						>
							Use this
						</Button>
						<Button type="button" size="sm" variant="ghost" onClick={() => setSuggestion(null)}>
							Dismiss
						</Button>
					</div>
				</div>
			)}
		</div>
	);
}
