"use client";

import { notFound, useParams } from "next/navigation";
import { isKnowledgeSection, type KnowledgeSectionSlug } from "@/components/knowledge/KnowledgeSectionNav";
import InstructionsSection from "@/components/knowledge/sections/InstructionsSection";
import DocumentsSection from "@/components/knowledge/sections/DocumentsSection";
import WebsitesSection from "@/components/knowledge/sections/WebsitesSection";
import ProductsSection from "@/components/knowledge/sections/ProductsSection";
import SuggestionsSection from "@/components/knowledge/sections/SuggestionsSection";

const SECTIONS: Record<KnowledgeSectionSlug, () => React.JSX.Element> = {
	instructions: InstructionsSection,
	documents: DocumentsSection,
	websites: WebsitesSection,
	products: ProductsSection,
	suggestions: SuggestionsSection,
};

export default function KnowledgeSectionPage() {
	const { section } = useParams<{ section: string }>();
	if (!isKnowledgeSection(section)) notFound();
	const Section = SECTIONS[section];
	return <Section />;
}
