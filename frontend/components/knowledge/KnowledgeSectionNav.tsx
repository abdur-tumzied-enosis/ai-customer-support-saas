"use client";

import Link from "next/link";
import { ChevronRight, FileText, Globe, Lightbulb, ScrollText, Tag, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";
import { cn } from "@/lib/utils";

export const KNOWLEDGE_SECTIONS = [
	{ slug: "instructions", name: "Custom Instructions", icon: ScrollText },
	{ slug: "documents", name: "Documents", icon: FileText },
	{ slug: "websites", name: "Crawled Websites", icon: Globe },
	{ slug: "products", name: "Product Catalog", icon: Tag },
	{ slug: "suggestions", name: "Memory Suggestions", icon: Lightbulb },
] as const satisfies readonly { slug: string; name: string; icon: LucideIcon }[];

export type KnowledgeSectionSlug = (typeof KNOWLEDGE_SECTIONS)[number]["slug"];

export function isKnowledgeSection(slug: string): slug is KnowledgeSectionSlug {
	return KNOWLEDGE_SECTIONS.some((s) => s.slug === slug);
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
const LOADING = "Loading...";

type Props = {
	active: KnowledgeSectionSlug | null;
	/** On /knowledge the instructions view only shows from md up, so the highlight does too */
	activeOnDesktopOnly?: boolean;
};

// One card per section, summarised from the lists the layout already holds.
export default function KnowledgeSectionNav({ active, activeOnDesktopOnly = false }: Props) {
	const { selectedKb, imageSearchOn, documents, documentsLoading, instructions, instructionsLoading, productStats, suggestionCount } = useKnowledge();

	const docsPending = !selectedKb || (documentsLoading && documents.length === 0);
	const files = documents.filter((d) => d.source_type !== "url");
	const processing = files.filter((d) => d.status !== "completed" && d.status !== "failed").length;
	const sites = documents.filter((d) => d.source_type === "url" && d.parent_crawl_id === null).length;
	const pages = documents.filter((d) => d.source_type === "url" && d.parent_crawl_id !== null).length;

	const summaries: Record<KnowledgeSectionSlug, string> = {
		instructions: !selectedKb || (instructionsLoading && instructions.length === 0)
			? LOADING
			: `${plural(instructions.length, "instruction")} · API access ${instructions.some((i) => i.api_tool_enabled) ? "on" : "off"}`,
		documents: docsPending ? LOADING : `${plural(files.length, "file")}${processing > 0 ? ` · ${processing} processing` : ""}`,
		websites: docsPending ? LOADING : `${plural(sites, "site")}${pages > 0 ? ` · ${plural(pages, "page")}` : ""}`,
		products: productStats ? `${plural(productStats.products, "product")} · ${plural(productStats.photos, "photo")}` : LOADING,
		suggestions: suggestionCount === null ? LOADING : `${suggestionCount} waiting for review`,
	};

	return (
		<nav aria-label="Knowledge sections">
			<ul className="space-y-2">
				{KNOWLEDGE_SECTIONS.filter((s) => s.slug !== "products" || imageSearchOn).map(({ slug, name, icon: Icon }) => {
					const isActive = slug === active;
					return (
						<li key={slug}>
							<Link
								href={`/knowledge/${slug}`}
								aria-current={isActive && !activeOnDesktopOnly ? "page" : undefined}
								className={cn(
									"flex items-center gap-3 rounded-lg border bg-card p-4 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
									isActive && (activeOnDesktopOnly ? "md:border-primary md:bg-primary/5" : "border-primary bg-primary/5"),
								)}
							>
								<Icon className="h-5 w-5 shrink-0 text-primary" />
								<div className="min-w-0 flex-1">
									<div className="flex items-center gap-2">
										<span className="text-sm font-medium">{name}</span>
										{slug === "suggestions" && !!suggestionCount && (
											<Badge className="h-5 px-1.5 text-xs">{suggestionCount}</Badge>
										)}
									</div>
									<p className="truncate text-sm text-muted-foreground">{summaries[slug]}</p>
								</div>
								<ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground md:hidden" />
							</Link>
						</li>
					);
				})}
			</ul>
		</nav>
	);
}
