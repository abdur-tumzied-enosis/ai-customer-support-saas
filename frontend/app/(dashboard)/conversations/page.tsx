import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

export default function ConversationsPage() {
	// Placeholder list
	const threads = [
		{ id: "demo-1", title: "Welcome conversation" },
		{ id: "demo-2", title: "Product questions" },
	];

	return (
		<div className="space-y-4">
			<h1 className="text-2xl font-semibold">Conversations</h1>
			<Card>
				<ul>
					{threads.map((t, idx) => (
						<li key={t.id}>
							<CardContent className="flex items-center justify-between p-4">
								<div>
									<div className="font-medium">{t.title}</div>
									<div className="text-xs text-muted-foreground">ID: {t.id}</div>
								</div>
								<Button variant="ghost" asChild>
									<Link href={`/conversations/${t.id}`}>
										Open →
									</Link>
								</Button>
							</CardContent>
							{idx < threads.length - 1 && <Separator />}
						</li>
					))}
				</ul>
			</Card>
		</div>
	);
}

