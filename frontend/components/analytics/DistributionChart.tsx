"use client";

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

interface DistributionChartProps {
	title: string;
	description?: string;
	data: Array<{
		name: string;
		value: number;
	}>;
	color?: string;
}

export function DistributionChart({ 
	title, 
	description, 
	data, 
	color = "#3b82f6" 
}: DistributionChartProps) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>{title}</CardTitle>
				{description && <CardDescription>{description}</CardDescription>}
			</CardHeader>
			<CardContent>
				<ResponsiveContainer width="100%" height={300}>
					<BarChart data={data}>
						<CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
						<XAxis 
							dataKey="name" 
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
						<Bar 
							dataKey="value" 
							fill={color}
							radius={[4, 4, 0, 0]}
						/>
					</BarChart>
				</ResponsiveContainer>
			</CardContent>
		</Card>
	);
}

