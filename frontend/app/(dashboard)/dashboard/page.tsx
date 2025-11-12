import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function DashboardPage() {
	return (
		<div className="space-y-6">
			<h1 className="text-2xl font-semibold">Dashboard</h1>
			<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
				<Card>
					<CardHeader>
						<CardTitle>Conversations</CardTitle>
						<CardDescription>
							Start chatting with your agent and track ongoing threads.
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Link className="text-sm font-medium text-primary hover:underline" href="/conversations">
							Open conversations →
						</Link>
					</CardContent>
				</Card>
				<Card>
					<CardHeader>
						<CardTitle>Knowledge Base</CardTitle>
						<CardDescription>
							Manage sources and ingestion jobs.
						</CardDescription>
					</CardHeader>
					<CardContent>
						<Link className="text-sm font-medium text-primary hover:underline" href="/knowledge">
							Go to knowledge →
						</Link>
					</CardContent>
				</Card>
			</div>
		</div>
	);
}

