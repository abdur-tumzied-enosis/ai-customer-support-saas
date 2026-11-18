"use client";

import { useCallback, useEffect, useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { CheckCircle2, AlertCircle, XCircle, Loader2, RefreshCw } from "lucide-react";
import { channelApi, type ChannelStatus } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { useToast } from "@/components/ui/toast";

type Props = {
	channelId: string | null;
	channelName: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
};

const ICONS = {
	ok: <CheckCircle2 className="h-5 w-5 text-green-600 shrink-0" />,
	missing: <AlertCircle className="h-5 w-5 text-amber-500 shrink-0" />,
	error: <XCircle className="h-5 w-5 text-red-600 shrink-0" />,
};

export default function ConnectionStatusDialog({ channelId, channelName, open, onOpenChange }: Props) {
	const [status, setStatus] = useState<ChannelStatus | null>(null);
	const [checkedAt, setCheckedAt] = useState("");
	const [loading, setLoading] = useState(false);
	const [requestError, setRequestError] = useState("");
	const [subscribing, setSubscribing] = useState(false);
	const toast = useToast();

	const check = useCallback(async () => {
		if (!channelId) return;
		setLoading(true);
		setRequestError("");
		try {
			const res = await channelApi.status(channelId);
			setStatus(res.data);
			setCheckedAt(new Date().toLocaleTimeString());
		} catch (err) {
			setRequestError(formatError(err));
		} finally {
			setLoading(false);
		}
	}, [channelId]);

	useEffect(() => {
		if (open) {
			setStatus(null);
			check();
		}
	}, [open, check]);

	const handleSubscribe = async () => {
		if (!channelId) return;
		setSubscribing(true);
		try {
			const res = await channelApi.subscribe(channelId);
			setStatus(res.data);
			setCheckedAt(new Date().toLocaleTimeString());
			toast.success("Page subscribed to messages");
		} catch (err) {
			toast.error(formatError(err));
		} finally {
			setSubscribing(false);
		}
	};

	const missingCount = status?.checks.filter((c) => c.status !== "ok").length ?? 0;
	const hasMissing =
		status?.checks.some((c) => c.key.startsWith("permission:") && c.status === "missing") ?? false;
	const subscriptionMissing =
		status?.checks.some((c) => c.key === "subscription" && c.status === "missing") ?? false;

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
				<DialogHeader>
					<DialogTitle>Connection status: {channelName}</DialogTitle>
					<DialogDescription>
						Checked live with Meta{checkedAt ? ` at ${checkedAt}` : ""}.
					</DialogDescription>
				</DialogHeader>

				{loading && !status && (
					<div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
						<Loader2 className="h-4 w-4 animate-spin" /> Checking with Meta...
					</div>
				)}

				{requestError && (
					<p className="text-sm text-red-600">{requestError}</p>
				)}

				{status && (
					<div className="space-y-4">
						<p className={`text-sm font-medium ${status.ok ? "text-green-700" : "text-amber-700"}`}>
							{status.ok
								? "Ready. Meta confirms this channel can receive and send messages."
								: status.error
									? status.error
									: `${missingCount} item${missingCount === 1 ? "" : "s"} need attention.`}
						</p>

						{status.checks.length > 0 && (
							<ul className="divide-y rounded-md border">
								{status.checks.map((check) => (
									<li key={check.key} className="flex items-start gap-3 p-3">
										{ICONS[check.status]}
										<div className="min-w-0">
											<p className="text-sm font-medium">{check.label}</p>
											<p className="text-xs text-muted-foreground">{check.detail}</p>
										</div>
									</li>
								))}
							</ul>
						)}

						{hasMissing && (
							<p className="text-xs text-muted-foreground">
								Missing permissions are granted in Meta, not here. Give the Page token the permission
								listed above, paste the new token into this channel, then check again.
							</p>
						)}
					</div>
				)}

				<div className="flex justify-end gap-2">
					<Button variant="outline" onClick={check} disabled={loading || subscribing || !channelId}>
						{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
						Check again
					</Button>
					{subscriptionMissing && (
						<Button onClick={handleSubscribe} disabled={subscribing || loading || !channelId}>
							{subscribing && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
							Subscribe now
						</Button>
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}
