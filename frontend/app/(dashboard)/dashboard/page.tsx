"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookOpen, Coins, MessageSquare, Radio, Send } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { analyticsApi, type AnalyticsData } from "@/lib/api";
import { billingApi, formatCredits, type Wallet } from "@/lib/billing-api";
import { MetricCard } from "@/components/analytics/MetricCard";
import { StatsGrid } from "@/components/analytics/StatsGrid";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

const LINKS = [
	{ href: "/conversations", title: "Conversations", text: "Read and reply to customer threads.", icon: MessageSquare },
	{ href: "/knowledge", title: "Knowledge base", text: "Manage the sources your agent learns from.", icon: BookOpen },
	{ href: "/channels", title: "Channels", text: "Connect Facebook, WhatsApp and more.", icon: Radio },
	{ href: "/credits", title: "Credits", text: "Check your balance and add more.", icon: Coins },
] as const;

export default function DashboardPage() {
	const { projects } = useAuth();
	const projectId = projects && projects.length > 0 ? String(projects[0].id) : "";
	const [wallet, setWallet] = useState<Wallet | null>(null);
	const [stats, setStats] = useState<AnalyticsData | null>(null);

	useEffect(() => {
		if (!projectId) return;
		billingApi.wallet(projectId).then(setWallet).catch(() => setWallet(null));
	}, [projectId]);

	useEffect(() => {
		const start = new Date();
		start.setDate(start.getDate() - 7);
		analyticsApi.getOverview(start.toISOString(), new Date().toISOString()).then((r) => setStats(r.data)).catch(() => setStats(null));
	}, []);

	return (
		<div className="mx-auto max-w-5xl space-y-8">
			<div>
				<h1 className="text-2xl font-semibold">Dashboard</h1>
				<p className="text-sm text-muted-foreground">Your agent at a glance, for the last 7 days.</p>
			</div>

			<StatsGrid columns={3}>
				<MetricCard
					title="Credits"
					value={wallet ? formatCredits(wallet.balance) : "-"}
					description={wallet?.empty ? "Out of credits" : wallet?.low ? "Running low" : "Available balance"}
					icon={Coins}
				/>
				<MetricCard
					title="Conversations"
					value={stats?.conversations.total ?? "-"}
					description="Started this week"
					icon={MessageSquare}
				/>
				<MetricCard
					title="Messages"
					value={stats?.messages.total ?? "-"}
					description={stats ? `${stats.messages.inbound} in / ${stats.messages.outbound} out` : "This week"}
					icon={Send}
				/>
			</StatsGrid>

			<div className="space-y-3">
				<h2 className="text-lg font-semibold">Quick links</h2>
				<div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
					{LINKS.map(({ href, title, text, icon: Icon }) => (
						<Link key={href} href={href} className="group">
							<Card className="h-full transition-colors group-hover:bg-muted/50">
								<CardHeader className="flex-row items-start gap-3 space-y-0">
									<Icon className="mt-0.5 size-5 text-muted-foreground" aria-hidden />
									<div className="space-y-1">
										<CardTitle className="text-base">{title}</CardTitle>
										<CardDescription>{text}</CardDescription>
									</div>
								</CardHeader>
							</Card>
						</Link>
					))}
				</div>
			</div>
		</div>
	);
}
