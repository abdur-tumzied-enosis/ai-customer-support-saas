"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { orderApi } from "@/lib/orders-api";
import { formatError } from "@/lib/errors";

export default function PromptPreview({ projectId }: { projectId: string }) {
	const [text, setText] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		orderApi.settings(projectId).then((res) => setText(res.data.prompt_preview)).catch((e) => setError(formatError(e)));
	}, [projectId]);

	if (error) {
		return (
			<Alert variant="destructive">
				<AlertDescription>{error}</AlertDescription>
			</Alert>
		);
	}
	if (text === null) return <Loader2 className="h-5 w-5 animate-spin" />;
	return <pre className="whitespace-pre-wrap rounded-md bg-muted p-3 text-xs">{text}</pre>;
}
