"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Lightbulb } from "lucide-react";
import SuggestionsQueue from "@/components/knowledge/SuggestionsQueue";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";

export default function SuggestionsSection() {
	const { projectId, fetchDocuments, fetchKnowledgeBases, fetchInstructions, setSuggestionCount } = useKnowledge();

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2"><Lightbulb className="h-5 w-5" /> Suggestions from conversations</CardTitle>
				<CardDescription>
					Drafted from staff replies and handoffs. Edit if needed, then approve to teach the bot. Remove anything about one specific customer.
				</CardDescription>
			</CardHeader>
			<CardContent>
				{projectId && (
					<SuggestionsQueue
						projectId={projectId}
						onApproved={() => {
							fetchDocuments();
							fetchKnowledgeBases();
							fetchInstructions();
						}}
						onCountChange={setSuggestionCount}
					/>
				)}
			</CardContent>
		</Card>
	);
}
