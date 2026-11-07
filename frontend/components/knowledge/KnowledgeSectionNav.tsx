"use client";

import Link from "next/link";
import { FileText, Globe, Lightbulb, ScrollText, Tag, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";
import { cn } from "@/lib/utils";

export const KNOWLEDGE_SECTIONS = [
	{ slug: "instructions", name: "Custom Instructions", icon: ScrollText },
	{ slug: "documents", name: "Documents", icon: FileText },
	{ slug: "websites", name: "Crawled Websites", icon: Globe },
	{ slug: "products", name: "Products", icon: Tag },
	{ slug: "suggestions", name: "Memory Suggestions", icon: Lightbulb },
] as const satisfies readonly { slug: string; name: string; icon: LucideIcon }[];

export type KnowledgeSectionSlug = (typeof KNOWLEDGE_SECTIONS)[number]["slug"];

export function isKnowledgeSection(slug: string): slug is KnowledgeSectionSlug {
	return KNOWLEDGE_SECTIONS.some((s) => s.slug === slug);
}

type Props = {
	active: KnowledgeSectionSlug | null;
	/** On /knowledge the instructions view only shows from md up, so the highlight does too */
	activeOnDesktopOnly?: boolean;
};

// Compact icon + text links; badges kept only where they carry a count to act on.
export default function KnowledgeSectionNav({ active, activeOnDesktopOnly = false }: Props) {
	const { productStats, suggestionCount } = useKnowledge();

	return (
		<nav aria-label="Knowledge sections">
			<ul className="space-y-1">
				{KNOWLEDGE_SECTIONS.map(({ slug, name, icon: Icon }) => {
					const isActive = slug === active;
					return (
						<li key={slug}>
							<Link
								href={`/knowledge/${slug}`}
								aria-current={isActive && !activeOnDesktopOnly ? "page" : undefined}
								className={cn(
									"flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
									isActive && (activeOnDesktopOnly ? "md:bg-primary/10 md:text-primary" : "bg-primary/10 text-primary"),
								)}
							>
								<Icon className="h-4 w-4 shrink-0" />
								<span className="min-w-0 flex-1 truncate">{name}</span>
								{slug === "suggestions" && !!suggestionCount && (
									<Badge className="h-5 px-1.5 text-xs">{suggestionCount}</Badge>
								)}
								{slug === "products" && productStats && productStats !== "unavailable" && productStats.needsInfo > 0 && (
									<Badge className="h-5 px-1.5 text-xs">{productStats.needsInfo}</Badge>
								)}
							</Link>
						</li>
					);
				})}
			</ul>
		</nav>
	);
}
