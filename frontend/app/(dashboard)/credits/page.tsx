"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/hooks/useAuth";
import { billingApi, formatCredits, type CreditTxn, type Wallet } from "@/lib/billing-api";
import CreditRequestDialog from "@/components/billing/CreditRequestDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const PAGE = 50;
const KIND_LABEL: Record<string, string> = {
	grant: "Credits added",
	adjust: "Adjustment",
	initial_allocation: "Starting credits",
	signup_bonus: "Welcome credits",
};

function describe(t: CreditTxn) {
	if (t.kind === "charge") return t.action_label || "Charge";
	return t.note || KIND_LABEL[t.kind] || t.kind;
}

export default function CreditsPage() {
	const { projects, loading: authLoading } = useAuth();
	const project = projects && projects.length > 0 ? projects[0] : null;
	const isAdmin = project?.role === "admin";
	const projectId = project ? String(project.id) : "";

	const [wallet, setWallet] = useState<Wallet | null>(null);
	const [rows, setRows] = useState<CreditTxn[]>([]);
	const [count, setCount] = useState(0);
	const [kind, setKind] = useState("all");
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState("");

	const load = useCallback(
		async (offset: number) => {
			setLoading(true);
			setError("");
			try {
				const r = await billingApi.transactions(projectId, { kind: kind === "all" ? undefined : kind, offset, limit: PAGE });
				setRows((prev) => (offset ? [...prev, ...r.results] : r.results));
				setCount(r.count);
			} catch {
				setError("Could not load credit history. Try again.");
			} finally {
				setLoading(false);
			}
		},
		[projectId, kind]
	);

	useEffect(() => {
		if (!projectId || !isAdmin) return;
		load(0);
	}, [projectId, isAdmin, load]);

	useEffect(() => {
		if (!projectId || !isAdmin) return;
		billingApi.wallet(projectId).then(setWallet).catch(() => setWallet(null));
	}, [projectId, isAdmin]);

	if (authLoading) return null;
	if (!isAdmin) return <p className="text-sm text-muted-foreground">Only project admins can see credit history.</p>;

	return (
		<div className="mx-auto max-w-4xl space-y-8">
			<div>
				<h1 className="text-2xl font-semibold tracking-tight">Credits</h1>
				<p className="text-sm text-muted-foreground">Every credit added to or used by your project.</p>
			</div>

			<Card className={wallet?.empty ? "border-destructive/50" : wallet?.low ? "border-amber-500/50" : ""}>
				<CardContent className="flex flex-wrap items-center justify-between gap-6 p-6">
					<div className="space-y-1">
						<div className="text-sm font-medium text-muted-foreground">Available balance</div>
						<div className={`text-4xl font-semibold tabular-nums ${wallet?.empty ? "text-destructive" : ""}`}>
							{wallet ? formatCredits(wallet.balance) : "-"}
							<span className="ml-2 text-base font-normal text-muted-foreground">credits</span>
						</div>
						{wallet?.empty ? (
							<p className="text-sm text-destructive">You are out of credits. Add more to keep going.</p>
						) : wallet?.low ? (
							<p className="text-sm text-amber-600 dark:text-amber-500">Running low. Consider adding more credits.</p>
						) : null}
					</div>
					<CreditRequestDialog projectId={projectId} />
				</CardContent>
			</Card>

			<div className="space-y-3">
				<div className="flex items-center justify-between gap-4">
					<h2 className="text-lg font-semibold">History</h2>
					<div className="w-44">
						<Select value={kind} onValueChange={setKind}>
							<SelectTrigger aria-label="Filter by type">
								<SelectValue />
							</SelectTrigger>
							<SelectContent>
								<SelectItem value="all">All activity</SelectItem>
								<SelectItem value="charge">Charges</SelectItem>
								<SelectItem value="grant">Credits added</SelectItem>
							</SelectContent>
						</Select>
					</div>
				</div>

				{error && <p className="text-sm text-destructive">{error}</p>}

				{!loading && !error && rows.length === 0 ? (
					<div className="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">No credit activity yet. Use “Request credits” above to add some.</div>
				) : (
					<div className="overflow-hidden rounded-lg border">
						<Table>
							<TableHeader>
								<TableRow className="bg-muted/40">
									<TableHead>Date</TableHead>
									<TableHead>Type</TableHead>
									<TableHead>Details</TableHead>
									<TableHead className="text-right">Credits</TableHead>
									<TableHead className="text-right">Balance</TableHead>
								</TableRow>
							</TableHeader>
							<TableBody>
								{rows.map((t) => {
									const positive = Number(t.amount) > 0;
									return (
										<TableRow key={t.id}>
											<TableCell className="whitespace-nowrap text-muted-foreground">
												{new Date(t.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
											</TableCell>
											<TableCell>
												<Badge variant={positive ? "secondary" : "outline"}>{t.kind === "charge" ? "Charge" : "Added"}</Badge>
											</TableCell>
											<TableCell>
												{t.conversation_id ? (
													<Link href={`/conversations/${t.conversation_id}`} className="underline-offset-4 hover:underline">
														{describe(t)}
													</Link>
												) : (
													describe(t)
												)}
											</TableCell>
											<TableCell className={`text-right font-medium tabular-nums ${positive ? "text-green-600 dark:text-green-500" : ""}`}>
												{positive ? "+" : ""}
												{formatCredits(t.amount)}
											</TableCell>
											<TableCell className="text-right tabular-nums text-muted-foreground">{formatCredits(t.balance_after)}</TableCell>
										</TableRow>
									);
								})}
							</TableBody>
						</Table>
					</div>
				)}

				{rows.length < count && (
					<div className="flex justify-center">
						<Button variant="outline" disabled={loading} onClick={() => load(rows.length)}>
							Load more
						</Button>
					</div>
				)}
			</div>
		</div>
	);
}
