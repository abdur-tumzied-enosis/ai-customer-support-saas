"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { documentApi, featuresApi, customInstructionApi, knowledgeBaseApi, productApi, suggestionApi, type CustomInstruction, type KnowledgeBase, type Product } from "@/lib/api";
import { getStoredKbId, pickKb, storeKbId } from "@/lib/knowledgeBase";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";
import { useToast } from "@/components/ui/toast";

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

/** Still being ingested (pending or processing) */
export const isProcessing = (doc: KbDocument) => doc.status !== "completed" && doc.status !== "failed";

export type KnowledgeAlert = { type: "success" | "error"; message: string } | null;
/** null while loading, "unavailable" if the count couldn't be fetched */
export type ProductStats = { products: number; photos: number; needsInfo: number } | "unavailable" | null;

const DOCUMENT_POLL_MS = 5000;

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
	/** For the section summary */
	productStats: ProductStats;
	reportProducts: (products: Product[]) => void;
	suggestionCount: number | null;
	setSuggestionCount: (count: number) => void;
	setAlert: (alert: KnowledgeAlert) => void;
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
	// Answers for a KB that is no longer selected are dropped
	const currentKbId = useRef(selectedKbId);
	currentKbId.current = selectedKbId;
	const [imageSearchOn, setImageSearchOn] = useState<boolean | null>(null);
	const [documents, setDocuments] = useState<KbDocument[]>([]);
	const [documentsLoading, setDocumentsLoading] = useState(false);
	const [instructions, setInstructions] = useState<CustomInstruction[]>([]);
	const [instructionsLoading, setInstructionsLoading] = useState(false);
	const [productStats, setProductStats] = useState<ProductStats>(null);
	const [suggestionCount, setSuggestionCount] = useState<number | null>(null);
	const toast = useToast();
	// Knowledge sections report results through this; it shows a snackbar
	const setAlert = useCallback((a: KnowledgeAlert) => { if (a) toast[a.type](a.message); }, [toast]);

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

	// silent: a background poll, so no spinner and no error banner
	const loadDocuments = useCallback(async (silent: boolean) => {
		const kbId = selectedKbId;
		if (!projectId || !kbId) return;

		if (!silent) setDocumentsLoading(true);
		try {
			const response = await documentApi.list(projectId, kbId);
			if (currentKbId.current === kbId) setDocuments(response.data.documents);
		} catch (error) {
			if (!silent && currentKbId.current === kbId) setAlert({ type: "error", message: formatError(error) });
		} finally {
			if (!silent && currentKbId.current === kbId) setDocumentsLoading(false);
		}
	}, [projectId, selectedKbId]);

	const fetchDocuments = useCallback(() => loadDocuments(false), [loadDocuments]);

	const fetchInstructions = useCallback(async () => {
		const kbId = selectedKbId;
		if (!projectId || !kbId) return;

		setInstructionsLoading(true);
		try {
			// What the bot follows for this KB: shared instructions, then the KB's own
			const response = await customInstructionApi.resolve(projectId, kbId);
			if (currentKbId.current === kbId) setInstructions(response.data.instructions);
		} catch (error) {
			if (currentKbId.current === kbId) setAlert({ type: "error", message: formatError(error) });
		} finally {
			if (currentKbId.current === kbId) setInstructionsLoading(false);
		}
	}, [projectId, selectedKbId]);

	const reportProducts = useCallback((products: Product[]) => {
		const photos = products.reduce((n, p) => n + p.images.length, 0);
		// The catalog polls every few seconds; an unchanged count must not re-render the tree
		const needsInfo = products.filter((p) => p.status === "needs_info").length;
		setProductStats((prev) =>
			prev && prev !== "unavailable" && prev.products === products.length && prev.photos === photos && prev.needsInfo === needsInfo
				? prev
				: { products: products.length, photos, needsInfo }
		);
	}, []);

	useEffect(() => {
		if (!authLoading && projectId) {
			fetchKnowledgeBases();
		}
	}, [authLoading, projectId, fetchKnowledgeBases]);

	// Documents and instructions follow the selected agent memory; the old KB's lists go so the counts show loading
	useEffect(() => {
		setDocuments([]);
		setInstructions([]);
		fetchDocuments();
		fetchInstructions();
	}, [fetchDocuments, fetchInstructions]);

	// Ingestion runs in the background: refresh while anything is still pending or processing
	const hasProcessing = documents.some(isProcessing);
	useEffect(() => {
		if (!hasProcessing) return;
		const timer = setInterval(() => loadDocuments(true), DOCUMENT_POLL_MS);
		return () => clearInterval(timer);
	}, [hasProcessing, loadDocuments]);

	// Product counts for the summary; the catalog reports fresh lists itself while it is open
	useEffect(() => {
		setProductStats(null);
		if (!projectId || !selectedKbId) return;
		let cancelled = false;
		productApi
			.list(projectId, selectedKbId)
			.then((products) => !cancelled && reportProducts(products))
			.catch(() => !cancelled && setProductStats("unavailable"));
		return () => {
			cancelled = true;
		};
	}, [projectId, selectedKbId, reportProducts]);

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

	const value = useMemo<KnowledgeState>(() => ({
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
		setAlert,
	}), [
		authLoading, projectId, isAdmin, knowledgeBases, selectedKb, selectKb, fetchKnowledgeBases, imageSearchOn,
		documents, documentsLoading, fetchDocuments, instructions, instructionsLoading, fetchInstructions,
		productStats, reportProducts, suggestionCount, setAlert,
	]);

	return <KnowledgeContext.Provider value={value}>{children}</KnowledgeContext.Provider>;
}
