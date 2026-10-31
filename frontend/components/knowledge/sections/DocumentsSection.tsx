"use client";

import { useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Upload, FileText, Trash2, File, Loader2 } from "lucide-react";
import { documentApi } from "@/lib/api";
import { useKnowledge, type KbDocument as Document } from "@/components/knowledge/KnowledgeContext";
import { formatFileSize, getStatusBadgeVariant, useDeleteDocument } from "@/components/knowledge/sections/documents";
import { formatError } from "@/lib/errors";

function getFileType(filename: string): string {
	const ext = filename.split(".").pop()?.toLowerCase();
	if (ext === "pdf") return "PDF";
	if (ext === "txt" || ext === "md") return "TEXT";
	return ext?.toUpperCase() || "UNKNOWN";
}

// Uploaded files only; crawled pages live in the Websites section.
export default function DocumentsSection() {
	const {
		projectId: currentProjectId,
		selectedKb,
		documents,
		documentsLoading: loading,
		fetchDocuments,
		fetchKnowledgeBases,
		setAlert,
	} = useKnowledge();
	const [uploading, setUploading] = useState(false);
	const { deletingDocumentId, requestDelete: handleDeleteDocumentClick, dialog: deleteDocumentDialog } = useDeleteDocument();
	const files = documents.filter((doc) => doc.source_type !== "url");

	async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
		const files = e.target.files;
		if (!files || files.length === 0) return;
		if (!currentProjectId) {
			setAlert({ type: "error", message: "No project selected. Please refresh the page." });
			return;
		}
		if (!selectedKb) return;

		setUploading(true);
		setAlert(null);

		try {
			const uploadPromises = Array.from(files).map(async (file) => {
				// Validate file type
				const fileExt = file.name.split(".").pop()?.toLowerCase();
				if (!fileExt || !["pdf", "txt", "md"].includes(fileExt)) {
					throw new Error(`${file.name} is not a supported file type. Only PDF, TXT, and MD files are allowed.`);
				}

				await documentApi.upload(file, currentProjectId, selectedKb.id);
			});

			await Promise.all(uploadPromises);
			const what = files.length === 1 ? files[0].name : `${files.length} files`;
			setAlert({ type: "success", message: `Uploaded ${what} to ${selectedKb.name}` });

			// Refresh documents and the per-KB document counts
			await Promise.all([fetchDocuments(), fetchKnowledgeBases()]);
			
			// Reset file input
			e.target.value = "";
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setUploading(false);
		}
	}

	function renderDocumentTable(docs: Document[]) {
		if (loading) {
			return (
				<div className="text-center py-8 text-muted-foreground">
					<Loader2 className="h-12 w-12 mx-auto mb-2 opacity-50 animate-spin" />
					<p>Loading documents...</p>
				</div>
			);
		}

		if (docs.length === 0) {
			return (
				<div className="text-center py-8 text-muted-foreground">
					<File className="h-12 w-12 mx-auto mb-2 opacity-50" />
					<p>No documents in <span className="font-medium">{selectedKb?.name ?? "this knowledge base"}</span> yet</p>
					<p className="text-sm">Upload PDF or text files to get started</p>
				</div>
			);
		}

		return (
			<div className="border rounded-md">
				<Table>
					<TableHeader>
						<TableRow>
							<TableHead>Name</TableHead>
							<TableHead>Type</TableHead>
							<TableHead>Size</TableHead>
							<TableHead>Status</TableHead>
							<TableHead>Date</TableHead>
							<TableHead className="text-right">Actions</TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{docs.map((doc) => (
							<TableRow key={doc.id}>
								<TableCell className="font-medium">{doc.filename}</TableCell>
								<TableCell>
									<Badge variant="secondary">{getFileType(doc.filename)}</Badge>
								</TableCell>
								<TableCell>{formatFileSize(doc.file_size)}</TableCell>
								<TableCell>
									<Badge variant={getStatusBadgeVariant(doc.status)}>
										{doc.status}
									</Badge>
									{doc.ingestion_job?.progress !== undefined && doc.ingestion_job.progress > 0 && (
										<span className="ml-2 text-xs text-muted-foreground">
											({doc.ingestion_job.progress}%)
										</span>
									)}
								</TableCell>
								<TableCell>
									{new Date(doc.created_at).toLocaleDateString()}
								</TableCell>
								<TableCell className="text-right">
									<Button
										variant="ghost"
										size="icon"
										onClick={() => handleDeleteDocumentClick(doc)}
										disabled={deletingDocumentId === doc.id}
									>
										{deletingDocumentId === doc.id ? (
											<Loader2 className="h-4 w-4 animate-spin" />
										) : (
											<Trash2 className="h-4 w-4" />
										)}
									</Button>
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			</div>
		);
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" /> Documents</CardTitle>
				<CardDescription>Upload PDF and text files to enhance your agent&apos;s knowledge base</CardDescription>
			</CardHeader>
			<CardContent className="space-y-4">
				<div className="flex flex-wrap items-center gap-4">
					<Label htmlFor="file-upload" className="cursor-pointer">
						<Button type="button" variant="outline" asChild disabled={uploading}>
							<span>
								<Upload className="mr-2 h-4 w-4" />
								{uploading ? "Uploading..." : "Upload Files"}
							</span>
						</Button>
					</Label>
					<Input
						id="file-upload"
						type="file"
						accept=".pdf,.txt,.md,text/*,application/pdf"
						multiple
						className="hidden"
						onChange={handleFileUpload}
						disabled={uploading}
					/>
					<span className="text-sm text-muted-foreground">
						Accepted formats: PDF, TXT, MD (Max 10MB)
					</span>
				</div>

				{renderDocumentTable(files)}

				{deleteDocumentDialog}
			</CardContent>
		</Card>
	);
}
