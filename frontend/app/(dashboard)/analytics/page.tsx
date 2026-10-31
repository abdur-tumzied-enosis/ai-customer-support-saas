"use client";

import { useState, useEffect } from "react";
import { analyticsApi, AnalyticsData } from "@/lib/api";
import { MetricCard } from "@/components/analytics/MetricCard";
import { TrendChart } from "@/components/analytics/TrendChart";
import { DistributionChart } from "@/components/analytics/DistributionChart";
import { StatsGrid } from "@/components/analytics/StatsGrid";
import { 
	MessageSquare, 
	FileText, 
	Users, 
	TrendingUp,
	Database,
	Zap,
	Cpu,
	Camera
} from "lucide-react";
import { Button } from "@/components/ui/button";

type TimeRange = "7d" | "30d" | "90d";

export default function AnalyticsPage() {
	const [timeRange, setTimeRange] = useState<TimeRange>("30d");
	const [data, setData] = useState<AnalyticsData | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		fetchAnalytics();
	}, [timeRange]);

	const fetchAnalytics = async () => {
		try {
			setLoading(true);
			setError(null);

			// Calculate date range
			const endDate = new Date();
			const startDate = new Date();
			
			switch (timeRange) {
				case "7d":
					startDate.setDate(startDate.getDate() - 7);
					break;
				case "30d":
					startDate.setDate(startDate.getDate() - 30);
					break;
				case "90d":
					startDate.setDate(startDate.getDate() - 90);
					break;
			}

			const response = await analyticsApi.getOverview(
				startDate.toISOString(),
				endDate.toISOString()
			);

			setData(response.data);
		} catch (err) {
			console.error("Failed to fetch analytics:", err);
			setError(err instanceof Error ? err.message : "Failed to fetch analytics");
		} finally {
			setLoading(false);
		}
	};

	const formatStorage = (bytes: number) => {
		if (bytes === 0) return "0 Bytes";
		const k = 1024;
		const sizes = ["Bytes", "KB", "MB", "GB"];
		const i = Math.floor(Math.log(bytes) / Math.log(k));
		return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + " " + sizes[i];
	};

	const formatTokens = (tokens: number) => {
		if (tokens === 0) return "0";
		if (tokens >= 1000000) {
			return (tokens / 1000000).toFixed(2) + "M";
		}
		if (tokens >= 1000) {
			return (tokens / 1000).toFixed(2) + "K";
		}
		return tokens.toString();
	};

	if (loading) {
		return (
			<div className="space-y-6">
				<div className="flex items-center justify-between">
					<h1 className="text-2xl font-semibold">Analytics</h1>
				</div>
				<div className="flex items-center justify-center h-64">
					<div className="text-muted-foreground">Loading analytics...</div>
				</div>
			</div>
		);
	}

	if (error) {
		return (
			<div className="space-y-6">
				<div className="flex items-center justify-between">
					<h1 className="text-2xl font-semibold">Analytics</h1>
				</div>
				<div className="flex items-center justify-center h-64">
					<div className="text-red-600">Error: {error}</div>
				</div>
			</div>
		);
	}

	if (!data) {
		return null;
	}

	// Prepare chart data
	const messagesByPlatform = data.messages.by_platform.map((item) => ({
		name: item.contact__platform || "Unknown",
		value: item.count,
	}));

	const messagesByStatus = Object.entries(data.messages.by_status).map(([status, count]) => ({
		name: status,
		value: count,
	}));

	const documentsByStatus = Object.entries(data.knowledge.documents_by_status).map(([status, count]) => ({
		name: status,
		value: count,
	}));

	const tokensByKind = Object.entries(data.token_usage.by_kind)
		.filter(([kind]) => kind !== "image") // photo indexing is counted in the tiles, not as token spend
		.map(([kind, total]) => ({
		name: kind.replace("chat_", "").replace("_", " "),
		value: total,
	}));

	const tokensOverTime = data.token_usage.trend.map((item) => ({
		date: item.date,
		count: item.total_tokens,
	}));

	return (
		<div className="space-y-6">
			{/* Header */}
			<div className="flex items-center justify-between">
				<h1 className="text-2xl font-semibold">Analytics</h1>
				
				{/* Time Range Selector */}
				<div className="flex gap-2">
					<Button
						variant={timeRange === "7d" ? "default" : "outline"}
						size="sm"
						onClick={() => setTimeRange("7d")}
					>
						Last 7 days
					</Button>
					<Button
						variant={timeRange === "30d" ? "default" : "outline"}
						size="sm"
						onClick={() => setTimeRange("30d")}
					>
						Last 30 days
					</Button>
					<Button
						variant={timeRange === "90d" ? "default" : "outline"}
						size="sm"
						onClick={() => setTimeRange("90d")}
					>
						Last 90 days
					</Button>
				</div>
			</div>

			{/* Token Usage Metrics */}
			<div className="space-y-4">
				<h2 className="text-xl font-semibold">Token Usage</h2>
				<StatsGrid columns={4}>
					<MetricCard
						title="Total Tokens"
						value={formatTokens(data.token_usage.total)}
						description="All token usage"
						icon={Cpu}
					/>
					<MetricCard
						title="Input Tokens"
						value={formatTokens(data.token_usage.input_tokens)}
						description="Chat input tokens"
						icon={TrendingUp}
					/>
					<MetricCard
						title="Output Tokens"
						value={formatTokens(data.token_usage.output_tokens)}
						description="Chat output tokens"
						icon={MessageSquare}
					/>
					<MetricCard
						title="Embedding Tokens"
						value={formatTokens(data.token_usage.embedding_tokens)}
						description="Document embedding tokens"
						icon={Database}
					/>
				</StatsGrid>
				<StatsGrid columns={2}>
					<MetricCard
						title="Photos indexed"
						value={data.token_usage.photos_indexed ?? 0}
						description="Product photos added to image search"
						icon={Camera}
					/>
					<MetricCard
						title="Photo searches"
						value={data.token_usage.photo_searches ?? 0}
						description="Customer photos matched against the catalog"
						icon={Camera}
					/>
				</StatsGrid>
				<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
					{tokensOverTime.length > 0 && (
						<TrendChart
							title="Token Usage Over Time"
							description="Daily token consumption"
							data={tokensOverTime}
							color="#8b5cf6"
						/>
					)}
					{tokensByKind.length > 0 && (
						<DistributionChart
							title="Token Usage by Type"
							description="Breakdown by usage type"
							data={tokensByKind}
							color="#06b6d4"
						/>
					)}
				</div>
			</div>

			{/* Overview Metrics */}
			<div className="space-y-4">
				<h2 className="text-xl font-semibold">Overview</h2>
				<StatsGrid columns={4}>
					<MetricCard
						title="Total Conversations"
						value={data.conversations.total}
						description="Conversations in selected period"
						icon={MessageSquare}
					/>
					<MetricCard
						title="Total Messages"
						value={data.messages.total}
						description={`${data.messages.inbound} in / ${data.messages.outbound} out`}
						icon={TrendingUp}
					/>
					<MetricCard
						title="Documents"
						value={data.knowledge.total_documents}
						description={`${data.knowledge.knowledge_bases} knowledge bases`}
						icon={FileText}
					/>
					<MetricCard
						title="Active Users"
						value={data.users.active}
						description={`${data.users.new_signups} new sign-ups`}
						icon={Users}
					/>
				</StatsGrid>
			</div>

			{/* Conversation & Message Trends */}
			<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
				<TrendChart
					title="Conversation Trend"
					description="Daily conversation count"
					data={data.conversations.trend}
					color="#3b82f6"
				/>
				<TrendChart
					title="Message Trend"
					description="Daily message count"
					data={data.messages.trend}
					color="#10b981"
				/>
			</div>

			{/* Message Analytics */}
			<div className="space-y-4">
				<h2 className="text-xl font-semibold">Message Analytics</h2>
				<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
					{messagesByPlatform.length > 0 && (
						<DistributionChart
							title="Messages by Platform"
							description="Distribution across channels"
							data={messagesByPlatform}
							color="#8b5cf6"
						/>
					)}
					{messagesByStatus.length > 0 && (
						<DistributionChart
							title="Messages by Status"
							description="Processing status breakdown"
							data={messagesByStatus}
							color="#f59e0b"
						/>
					)}
				</div>
			</div>

			{/* Knowledge Base Analytics */}
			<div className="space-y-4">
				<h2 className="text-xl font-semibold">Knowledge Base Analytics</h2>
				<StatsGrid columns={3}>
					<MetricCard
						title="Total Storage"
						value={formatStorage(data.knowledge.total_storage_bytes)}
						description="Document storage used"
						icon={Database}
					/>
					<MetricCard
						title="Ingestion Jobs"
						value={data.knowledge.ingestion_jobs.total}
						description="Document processing jobs"
						icon={Zap}
					/>
					<MetricCard
						title="Knowledge Bases"
						value={data.knowledge.knowledge_bases}
						description="Total knowledge bases"
						icon={FileText}
					/>
				</StatsGrid>
				{documentsByStatus.length > 0 && (
					<div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
						<DistributionChart
							title="Documents by Status"
							description="Document processing status"
							data={documentsByStatus}
							color="#ef4444"
						/>
					</div>
				)}
			</div>

			{/* User Analytics */}
			{data.users.trend.length > 0 && (
				<div className="space-y-4">
					<h2 className="text-xl font-semibold">User Analytics</h2>
					<TrendChart
						title="User Sign-ups"
						description="New user registrations over time"
						data={data.users.trend}
						color="#ec4899"
					/>
				</div>
			)}

			{/* Channel Analytics */}
			{data.channels.total > 0 && (
				<div className="space-y-4">
					<h2 className="text-xl font-semibold">Channel Analytics</h2>
					<StatsGrid columns={3}>
						<MetricCard
							title="Active Channels"
							value={data.channels.total}
							description="Connected channels"
							icon={MessageSquare}
						/>
						{Object.entries(data.channels.by_type).map(([channel, count]) => (
							<MetricCard
								key={channel}
								title={channel.charAt(0).toUpperCase() + channel.slice(1)}
								value={count}
								description={`${channel} channels`}
							/>
						))}
					</StatsGrid>
				</div>
			)}
		</div>
	);
}

