"use client";

import { createContext, useCallback, useContext, useEffect, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { documentApi, featuresApi, customInstructionApi, knowledgeBaseApi, productApi, suggestionApi, type CustomInstruction, type KnowledgeBase, type Product } from "@/lib/api";
import { getStoredKbId, pickKb, storeKbId } from "@/lib/knowledgeBase";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";

export type KbDocument = {
	id: string;
	filename: string;
	file_size: number;
	status: string;
	error_message: string | null;
	checksum: string;
	created_at: string;
	source_type: string;
	page_url: string;
	page_title: string;
	crawl_depth: number;
	parent_crawl_id: string | null;
	children_count: number;
	ingestion_job: {
		id: string | null;
		status: string | null;
		progress: number;
		error: string | null;
		created_at: string | null;
		completed_at: string | null;
	} | null;
};

export type KnowledgeAlert = { type: "success" | "error"; message: string } | null;
type ProductStats = { products: number; photos: number };

type KnowledgeState = {
	authLoading: boolean;
	projectId: string | null;
	isAdmin: boolean;
	knowledgeBases: KnowledgeBase[];
	selectedKb: KnowledgeBase | undefined;
	selectKb: (kb: KnowledgeBase) => void;
	fetchKnowledgeBases: (selectId?: string) => Promise<void>;
	/** null until the features call answers */
	imageSearchOn: boolean | null;
	documents: KbDocument[];
	setDocuments: Dispatch<SetStateAction<KbDocument[]>>;
	documentsLoading: boolean;
	fetchDocuments: () => Promise<void>;
	instructions: CustomInstruction[];
	setInstructions: Dispatch<SetStateAction<CustomInstruction[]>>;
	instructionsLoading: boolean;
	fetchInstructions: () => Promise<void>;
	/** For the section summary; null until loaded */
	productStats: ProductStats | null;
	reportProducts: (products: Product[]) => void;
	suggestionCount: number | null;
	setSuggestionCount: (count: number) => void;
	alert: KnowledgeAlert;
	setAlert: Dispatch<SetStateAction<KnowledgeAlert>>;
};

const KnowledgeContext = createContext<KnowledgeState | null>(null);

export function useKnowledge(): KnowledgeState {
	const ctx = useContext(KnowledgeContext);
	if (!ctx) throw new Error("useKnowledge must be used inside KnowledgeProvider");
	return ctx;
}

// Selected KB plus the lists the section summaries count. Sections mutate through these setters/fetchers so the counts never go stale.
export function KnowledgeProvider({ children }: { children: ReactNode }) {
	const { projects, loading: authLoading } = useAuth();
	// Get the first project (for now, we'll use the first one)
	const projectId = projects && projects.length > 0 ? String(projects[0].id) : null;
	const isAdmin = projects?.[0]?.role === "admin";

	const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
	const [selectedKb, setSelectedKb] = useState<KnowledgeBase | undefined>();
	const selectedKbId = selectedKb?.id;
	const [imageSearchOn, setImageSearchOn] = useState<boolean | null>(null);
	const [documents, setDocuments] = useState<KbDocument[]>([]);
	const [documentsLoading, setDocumentsLoading] = useState(false);
	const [instructions, setInstructions] = useState<CustomInstruction[]>([]);
	const [instructionsLoading, setInstructionsLoading] = useState(false);
	const [productStats, setProductStats] = useState<ProductStats | null>(null);
	const [suggestionCount, setSuggestionCount] = useState<number | null>(null);
	const [alert, setAlert] = useState<KnowledgeAlert>(null);

	useEffect(() => {
		if (!projectId) return;
		featuresApi
			.get(projectId)
			.then((f) => setImageSearchOn(f.image_search))
			.catch(() => setImageSearchOn(false));
	}, [projectId]);

	const fetchKnowledgeBases = useCallback(async (selectId?: string) => {
		if (!projectId) return;
		try {
			const response = await knowledgeBaseApi.list(projectId);
			const kbs = response.data.knowledge_bases;
			setKnowledgeBases(kbs);
			const kb = pickKb(kbs, selectId ?? getStoredKbId(projectId));
			setSelectedKb(kb);
			if (kb) storeKbId(projectId, kb.id);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		}
	}, [projectId]);

	const fetchDocuments = useCallback(async () => {
		if (!projectId || !selectedKbId) return;

		setDocumentsLoading(true);
		try {
			const response = await documentApi.list(projectId, selectedKbId);
			setDocuments(response.data.documents);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setDocumentsLoading(false);
		}
	}, [projectId, selectedKbId]);

	const fetchInstructions = useCallback(async () => {
		if (!projectId || !selectedKbId) return;

		setInstructionsLoading(true);
		try {
			// What the bot follows for this KB: shared instructions, then the KB's own
			const response = await customInstructionApi.resolve(projectId, selectedKbId);
			setInstructions(response.data.instructions);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setInstructionsLoading(false);
		}
	}, [projectId, selectedKbId]);

	const reportProducts = useCallback((products: Product[]) => {
		setProductStats({ products: products.length, photos: products.reduce((n, p) => n + p.images.length, 0) });
	}, []);

	useEffect(() => {
		if (!authLoading && projectId) {
			fetchKnowledgeBases();
		}
	}, [authLoading, projectId, fetchKnowledgeBases]);

	// Documents and instructions follow the selected knowledge base
	useEffect(() => {
		fetchDocuments();
		fetchInstructions();
	}, [fetchDocuments, fetchInstructions]);

	// Product counts for the summary; the catalog reports fresh lists itself while it is open
	useEffect(() => {
		setProductStats(null);
		if (!imageSearchOn || !projectId || !selectedKbId) return;
		let cancelled = false;
		productApi
			.list(projectId, selectedKbId)
			.then((products) => !cancelled && reportProducts(products))
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [imageSearchOn, projectId, selectedKbId, reportProducts]);

	// Pending suggestions are per project; the queue reports changes itself while it is open
	useEffect(() => {
		if (!projectId) return;
		let cancelled = false;
		suggestionApi
			.list(projectId)
			.then((res) => !cancelled && setSuggestionCount(res.data.suggestions.length))
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [projectId]);

	const selectKb = useCallback((kb: KnowledgeBase) => {
		setSelectedKb(kb);
		if (projectId) storeKbId(projectId, kb.id);
	}, [projectId]);

	return (
		<KnowledgeContext.Provider
			value={{
				authLoading,
				projectId,
				isAdmin,
				knowledgeBases,
				selectedKb,
				selectKb,
				fetchKnowledgeBases,
				imageSearchOn,
				documents,
				setDocuments,
				documentsLoading,
				fetchDocuments,
				instructions,
				setInstructions,
				instructionsLoading,
				fetchInstructions,
				productStats,
				reportProducts,
				suggestionCount,
				setSuggestionCount,
				alert,
				setAlert,
			}}
		>
			{children}
		</KnowledgeContext.Provider>
	);
}
