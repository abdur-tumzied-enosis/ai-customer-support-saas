"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useSelectedLayoutSegment } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import KnowledgeBaseBar from "@/components/knowledge/KnowledgeBaseBar";
import KnowledgeSectionNav, { isKnowledgeSection } from "@/components/knowledge/KnowledgeSectionNav";
import { KnowledgeProvider, useKnowledge } from "@/components/knowledge/KnowledgeContext";
import { cn } from "@/lib/utils";

export default function KnowledgeLayout({ children }: { children: React.ReactNode }) {
	return (
		<KnowledgeProvider>
			<KnowledgeShell>{children}</KnowledgeShell>
		</KnowledgeProvider>
	);
}

// Desktop: section list on the left, the open section on the right. Mobile: /knowledge is the list, each section its own page.
function KnowledgeShell({ children }: { children: React.ReactNode }) {
	const segment = useSelectedLayoutSegment();
	const { authLoading, projectId, knowledgeBases, selectedKb, selectKb, fetchKnowledgeBases, alert, setAlert } = useKnowledge();
	// /knowledge itself shows Custom Instructions from md up (see page.tsx)
	const isIndex = segment === null;
	const active = isIndex ? "instructions" : isKnowledgeSection(segment) ? segment : null;

	// A message from one section doesn't belong on the next
	useEffect(() => {
		setAlert(null);
	}, [segment, setAlert]);

	// Show loading state while auth is loading
	if (authLoading) {
		return (
			<div className="flex items-center justify-center min-h-[400px]">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	return (
		<div className="space-y-6">
			<div className="flex flex-wrap items-center justify-between gap-4">
				<h1 className="text-2xl font-semibold">Knowledge Base</h1>
				{projectId && (
					<KnowledgeBaseBar
						projectId={projectId}
						knowledgeBases={knowledgeBases}
						selected={selectedKb}
						onSelect={selectKb}
						onSaved={(kb) => fetchKnowledgeBases(kb.id)}
					/>
				)}
			</div>

			{alert && (
				<Alert className={alert.type === "error" ? "border-destructive" : ""}>
					<AlertDescription>{alert.message}</AlertDescription>
				</Alert>
			)}

			<div className="md:grid md:grid-cols-[16rem_minmax(0,1fr)] md:items-start md:gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
				<aside className={cn("md:sticky md:top-20", !isIndex && "hidden md:block")}>
					<KnowledgeSectionNav active={active} activeOnDesktopOnly={isIndex} />
				</aside>
				<div className={cn("min-w-0 space-y-4", isIndex && "hidden md:block")}>
					{!isIndex && (
						<Link
							href="/knowledge"
							className="inline-flex items-center gap-1 rounded-sm text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
						>
							<ArrowLeft className="h-4 w-4" />
							Knowledge
						</Link>
					)}
					{children}
				</div>
			</div>
		</div>
	);
}
