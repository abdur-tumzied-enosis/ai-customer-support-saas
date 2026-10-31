"use client";

import InstructionsSection from "@/components/knowledge/sections/InstructionsSection";

// Desktop shows Custom Instructions next to the section list; on mobile the layout hides this and shows only the list.
// Rendered in place rather than redirected, so phones never flash a redirect.
export default function KnowledgePage() {
	return <InstructionsSection />;
}
