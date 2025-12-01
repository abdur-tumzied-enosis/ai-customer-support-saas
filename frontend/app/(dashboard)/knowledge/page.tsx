"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Upload, FileText, Plus, Trash2, File, Loader2 } from "lucide-react";
import { documentApi, customInstructionApi, type CustomInstruction } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";
import { formatError } from "@/lib/errors";

type Document = {
	id: string;
	filename: string;
	file_size: number;
	status: string;
	error_message: string | null;
	checksum: string;
	created_at: string;
	ingestion_job: {
		id: string | null;
		status: string | null;
		progress: number;
		error: string | null;
		created_at: string | null;
		completed_at: string | null;
	} | null;
};

export default function KnowledgePage() {
	const { projects, loading: authLoading } = useAuth();
	const [documents, setDocuments] = useState<Document[]>([]);
	const [loading, setLoading] = useState(false);
	const [instructions, setInstructions] = useState<CustomInstruction[]>([]);
	const [instructionsLoading, setInstructionsLoading] = useState(false);
	const [uploading, setUploading] = useState(false);
	const [savingInstruction, setSavingInstruction] = useState(false);
	const [deletingInstructionId, setDeletingInstructionId] = useState<string | null>(null);
	const [instructionDialogOpen, setInstructionDialogOpen] = useState(false);
	const [newInstruction, setNewInstruction] = useState({ label: "", message: "" });
	const [alert, setAlert] = useState<{ type: "success" | "error"; message: string } | null>(null);

	// Get the first project (for now, we'll use the first one)
	const currentProjectId = projects && projects.length > 0 ? String(projects[0].id) : null;

	const fetchDocuments = useCallback(async () => {
		if (!currentProjectId) return;

		setLoading(true);
		try {
			const response = await documentApi.list(currentProjectId);
			setDocuments(response.data.documents);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setLoading(false);
		}
	}, [currentProjectId]);

	const fetchInstructions = useCallback(async () => {
		if (!currentProjectId) return;

		setInstructionsLoading(true);
		try {
			// Fetch project-level instructions (knowledge_base_id = null)
			const response = await customInstructionApi.list(currentProjectId, null);
			setInstructions(response.data.instructions);
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setInstructionsLoading(false);
		}
	}, [currentProjectId]);

	// Fetch documents and instructions on mount
	useEffect(() => {
		if (!authLoading && currentProjectId) {
			fetchDocuments();
			fetchInstructions();
		}
	}, [authLoading, currentProjectId, fetchDocuments, fetchInstructions]);

	function formatFileSize(bytes: number): string {
		if (bytes < 1024) return bytes + " B";
		if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(2) + " KB";
		return (bytes / (1024 * 1024)).toFixed(2) + " MB";
	}

	function getFileType(filename: string): string {
		const ext = filename.split(".").pop()?.toLowerCase();
		if (ext === "pdf") return "PDF";
		if (ext === "txt" || ext === "md") return "TEXT";
		return ext?.toUpperCase() || "UNKNOWN";
	}

	function getStatusBadgeVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
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

	async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
		const files = e.target.files;
		if (!files || files.length === 0) return;
		if (!currentProjectId) {
			setAlert({ type: "error", message: "No project selected. Please refresh the page." });
			return;
		}

		setUploading(true);
		setAlert(null);

		try {
			const uploadPromises = Array.from(files).map(async (file) => {
				// Validate file type
				const fileExt = file.name.split(".").pop()?.toLowerCase();
				if (!fileExt || !["pdf", "txt", "md"].includes(fileExt)) {
					throw new Error(`${file.name} is not a supported file type. Only PDF, TXT, and MD files are allowed.`);
				}

				await documentApi.upload(file, currentProjectId);
			});

			await Promise.all(uploadPromises);
			setAlert({ type: "success", message: `Successfully uploaded ${files.length} file(s)` });
			
			// Refresh documents list
			await fetchDocuments();
			
			// Reset file input
			e.target.value = "";
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setUploading(false);
		}
	}

	async function handleDeleteDocument(id: string) {
		try {
			await documentApi.delete(id);
			setDocuments((prev) => prev.filter((doc) => doc.id !== id));
			setAlert({ type: "success", message: "Document deleted successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
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
					<p>No documents found</p>
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
							<TableHead>Uploaded</TableHead>
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
										onClick={() => handleDeleteDocument(doc.id)}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</TableCell>
							</TableRow>
						))}
					</TableBody>
				</Table>
			</div>
		);
	}

	async function handleAddInstruction() {
		if (!newInstruction.label.trim() || !newInstruction.message.trim()) {
			setAlert({ type: "error", message: "Please fill in both label and message fields" });
			return;
		}

		if (!currentProjectId) {
			setAlert({ type: "error", message: "No project selected" });
			return;
		}

		setSavingInstruction(true);
		try {
			const response = await customInstructionApi.create({
				project_id: currentProjectId,
				knowledge_base_id: null, // Project-level instruction
				label: newInstruction.label.trim(),
				message: newInstruction.message.trim(),
			});

			setInstructions((prev) => [response.data.instruction, ...prev]);
			setNewInstruction({ label: "", message: "" });
			setInstructionDialogOpen(false);
			setAlert({ type: "success", message: "Custom instruction added successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setSavingInstruction(false);
		}
	}

	async function handleDeleteInstruction(id: string) {
		setDeletingInstructionId(id);
		try {
			await customInstructionApi.delete(id);
			setInstructions((prev) => prev.filter((inst) => inst.id !== id));
			setAlert({ type: "success", message: "Custom instruction deleted successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setDeletingInstructionId(null);
		}
	}

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
			<div className="space-y-4">
				<h1 className="text-2xl font-semibold">Knowledge Base</h1>
				<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
					<div className="flex items-start gap-3 p-4 rounded-lg border bg-card">
						<FileText className="h-5 w-5 text-primary mt-0.5" />
						<div>
							<h3 className="font-medium text-sm mb-1">Documents</h3>
							<p className="text-sm text-muted-foreground">
								Upload PDF and text files to provide context for your agent's responses
							</p>
						</div>
					</div>
					<div className="flex items-start gap-3 p-4 rounded-lg border bg-card">
						<Plus className="h-5 w-5 text-primary mt-0.5" />
						<div>
							<h3 className="font-medium text-sm mb-1">Custom Instructions</h3>
							<p className="text-sm text-muted-foreground">
								Define how your agent behaves, acts, and takes actions with customers
							</p>
						</div>
					</div>
				</div>
			</div>

			{alert && (
				<Alert className={alert.type === "error" ? "border-destructive" : ""}>
					<AlertDescription>{alert.message}</AlertDescription>
				</Alert>
			)}

			{/* Documents Section */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<FileText className="h-5 w-5" />
						Documents
					</CardTitle>
					<CardDescription>
						Upload PDF and text files to enhance your agent's knowledge base
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="flex items-center gap-4">
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

					<Tabs defaultValue="documents" className="w-full">
						<TabsList>
							<TabsTrigger value="documents">
								<FileText className="mr-2 h-4 w-4" />
								Documents ({documents.length})
							</TabsTrigger>
						</TabsList>
						<TabsContent value="documents" className="mt-4">
							{renderDocumentTable(documents)}
						</TabsContent>
					</Tabs>
				</CardContent>
			</Card>

			{/* Custom Instructions Section */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<FileText className="h-5 w-5" />
						Custom Instructions
					</CardTitle>
					<CardDescription>
						Define how your agent should behave, act, and take actions with customers
					</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					<div className="flex justify-end">
						<Dialog open={instructionDialogOpen} onOpenChange={setInstructionDialogOpen}>
							<DialogTrigger asChild>
								<Button>
									<Plus className="mr-2 h-4 w-4" />
									Add Instruction
								</Button>
							</DialogTrigger>
							<DialogContent>
								<DialogHeader>
									<DialogTitle>Add Custom Instruction</DialogTitle>
									<DialogDescription>
										Add a labeled instruction that guides your agent's behavior
									</DialogDescription>
								</DialogHeader>
								<div className="space-y-4 py-4">
									<div className="space-y-2">
										<Label htmlFor="instruction-label">Label</Label>
										<Input
											id="instruction-label"
											placeholder="e.g., Greeting Style, Response Tone, Escalation Rules"
											value={newInstruction.label}
											onChange={(e) =>
												setNewInstruction((prev) => ({ ...prev, label: e.target.value }))
											}
										/>
									</div>
									<div className="space-y-2">
										<Label htmlFor="instruction-message">Message</Label>
										<Textarea
											id="instruction-message"
											placeholder="Enter the instruction message that defines how the agent should act..."
											rows={6}
											value={newInstruction.message}
											onChange={(e) =>
												setNewInstruction((prev) => ({ ...prev, message: e.target.value }))
											}
										/>
									</div>
									<div className="flex justify-end gap-2">
										<Button
											variant="outline"
											onClick={() => {
												setInstructionDialogOpen(false);
												setNewInstruction({ label: "", message: "" });
											}}
											disabled={savingInstruction}
										>
											Cancel
										</Button>
										<Button onClick={handleAddInstruction} disabled={savingInstruction}>
											{savingInstruction ? (
												<>
													<Loader2 className="mr-2 h-4 w-4 animate-spin" />
													Saving...
												</>
											) : (
												"Add Instruction"
											)}
										</Button>
									</div>
								</div>
							</DialogContent>
						</Dialog>
					</div>

					{instructionsLoading ? (
						<div className="text-center py-8 text-muted-foreground">
							<Loader2 className="h-12 w-12 mx-auto mb-2 opacity-50 animate-spin" />
							<p>Loading instructions...</p>
						</div>
					) : instructions.length > 0 ? (
						<div className="space-y-3">
							{instructions.map((inst) => (
								<Card key={inst.id}>
									<CardHeader className="pb-3">
										<div className="flex items-start justify-between">
											<div className="flex items-center gap-2">
												<Badge variant="outline">{inst.label}</Badge>
												<span className="text-xs text-muted-foreground">
													Added {new Date(inst.created_at).toLocaleDateString()}
												</span>
											</div>
											<Button
												variant="ghost"
												size="icon"
												onClick={() => handleDeleteInstruction(inst.id)}
												disabled={deletingInstructionId === inst.id}
											>
												{deletingInstructionId === inst.id ? (
													<Loader2 className="h-4 w-4 animate-spin" />
												) : (
													<Trash2 className="h-4 w-4" />
												)}
											</Button>
										</div>
									</CardHeader>
									<CardContent>
										<p className="text-sm whitespace-pre-wrap">{inst.message}</p>
									</CardContent>
								</Card>
							))}
						</div>
					) : (
						<div className="text-center py-8 text-muted-foreground">
							<FileText className="h-12 w-12 mx-auto mb-2 opacity-50" />
							<p>No custom instructions added yet</p>
							<p className="text-sm">Add instructions to guide your agent's behavior</p>
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
