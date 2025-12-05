"use client";

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface TrendChartProps {
	title: string;
	description?: string;
	data: Array<{
		date: string;
		count: number;
	}>;
	dataKey?: string;
	color?: string;
}

export function TrendChart({ 
	title, 
	description, 
	data, 
	dataKey = "count", 
	color = "#3b82f6" 
}: TrendChartProps) {
	// Format data for chart
	const chartData = data.map((item) => ({
		...item,
		date: new Date(item.date).toLocaleDateString("en-US", { 
			month: "short", 
			day: "numeric" 
		}),
	}));

	return (
		<Card>
			<CardHeader>
				<CardTitle>{title}</CardTitle>
				{description && <CardDescription>{description}</CardDescription>}
			</CardHeader>
			<CardContent>
				<ResponsiveContainer width="100%" height={300}>
					<LineChart data={chartData}>
						<CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
						<XAxis 
							dataKey="date" 
							className="text-xs"
							tick={{ fill: "hsl(var(--muted-foreground))" }}
						/>
						<YAxis 
							className="text-xs"
							tick={{ fill: "hsl(var(--muted-foreground))" }}
						/>
						<Tooltip 
							contentStyle={{
								backgroundColor: "hsl(var(--background))",
								border: "1px solid hsl(var(--border))",
								borderRadius: "6px",
							}}
							labelStyle={{ color: "hsl(var(--foreground))" }}
						/>
						<Line 
							type="monotone" 
							dataKey={dataKey} 
							stroke={color} 
							strokeWidth={2}
							dot={{ fill: color, r: 4 }}
							activeDot={{ r: 6 }}
						/>
					</LineChart>
				</ResponsiveContainer>
			</CardContent>
		</Card>
	);
}

