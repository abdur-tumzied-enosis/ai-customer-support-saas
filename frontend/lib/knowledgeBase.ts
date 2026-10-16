import type { KnowledgeBase } from "@/lib/api";

// The KB last picked on the knowledge page, preselected for new test chats. Per browser, per project.
const storageKey = (projectId: string) => `kb:${projectId}`;

export function getStoredKbId(projectId: string): string | null {
	try {
		return localStorage.getItem(storageKey(projectId));
	} catch {
		return null; // storage blocked (private mode): fall back to the default KB
	}
}

export function storeKbId(projectId: string, kbId: string): void {
	try {
		localStorage.setItem(storageKey(projectId), kbId);
	} catch {
		// storage blocked: the selection just isn't remembered
	}
}

/** The stored KB if it still exists in this project, else the default one. */
export function pickKb(kbs: KnowledgeBase[], storedId: string | null): KnowledgeBase | undefined {
	return kbs.find((kb) => kb.id === storedId) ?? kbs.find((kb) => kb.is_default) ?? kbs[0];
}

/** "Products · 12 docs · default" */
export function kbOptionLabel(kb: KnowledgeBase): string {
	const docs = `${kb.document_count} doc${kb.document_count === 1 ? "" : "s"}`;
	return `${kb.name} · ${docs}${kb.is_default ? " · default" : ""}`;
}
