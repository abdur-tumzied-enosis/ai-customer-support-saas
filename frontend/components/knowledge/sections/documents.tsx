"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { File } from "lucide-react";
import { documentApi } from "@/lib/api";
import { formatError } from "@/lib/errors";
import { useKnowledge, type KbDocument } from "@/components/knowledge/KnowledgeContext";

export function formatFileSize(bytes: number): string {
	if (bytes < 1024) return bytes + " B";
	if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
	return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

export function getStatusBadgeVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
	switch (status) {
		case "completed":
			return "default";
		case "processing":
			return "secondary";
		case "failed":
			return "destructive";
		default:
			return "outline";
	}
}

/** Delete confirmation for an uploaded file or a crawled page; returns the row spinner id and the dialog. */
export function useDeleteDocument() {
	const { setDocuments, fetchKnowledgeBases, setAlert } = useKnowledge();
	const [confirmOpen, setConfirmOpen] = useState(false);
	const [documentToDelete, setDocumentToDelete] = useState<KbDocument | null>(null);
	const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);

	function requestDelete(doc: KbDocument) {
		setDocumentToDelete(doc);
		setConfirmOpen(true);
	}

	async function handleConfirmDeleteDocument() {
		if (!documentToDelete) return;

		setDeletingDocumentId(documentToDelete.id);
		setConfirmOpen(false);

		try {
			await documentApi.delete(documentToDelete.id);
			// Deleting a crawled site deletes its pages too (backend cascade)
			setDocuments((prev) => prev.filter((doc) => doc.id !== documentToDelete.id && doc.parent_crawl_id !== documentToDelete.id));
			setAlert({ type: "success", message: "Document deleted successfully" });
			fetchKnowledgeBases();
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setDeletingDocumentId(null);
			setDocumentToDelete(null);
		}
	}

	const dialog = (
		<Dialog open={confirmOpen} onOpenChange={(open) => {
			setConfirmOpen(open);
			if (!open) {
				setDocumentToDelete(null);
			}
		}}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>Delete Document</DialogTitle>
					<DialogDescription>
						Are you sure you want to delete this document? This action cannot be undone.
					</DialogDescription>
				</DialogHeader>
				{documentToDelete && (
					<div className="py-4">
						<div className="rounded-md border p-3 bg-muted/50">
							<div className="flex items-center gap-2">
								<File className="h-4 w-4 text-muted-foreground" />
								<p className="text-sm font-medium">{documentToDelete.filename}</p>
							</div>
							<p className="text-xs text-muted-foreground mt-1">
								{formatFileSize(documentToDelete.file_size)} • Uploaded {new Date(documentToDelete.created_at).toLocaleDateString()}
							</p>
						</div>
					</div>
				)}
				<DialogFooter className="gap-2 sm:gap-0">
					<Button
						variant="outline"
						onClick={() => {
							setConfirmOpen(false);
							setDocumentToDelete(null);
						}}
					>
						Cancel
					</Button>
					<Button
						variant="destructive"
						onClick={handleConfirmDeleteDocument}
					>
						Delete
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);

	return { deletingDocumentId, requestDelete, dialog };
}
