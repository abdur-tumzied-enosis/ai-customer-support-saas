"use client";

import { useState, useEffect, useCallback, Fragment } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Upload, FileText, Plus, Trash2, File, Loader2, Pencil, Sparkles, Tag, Globe, ChevronRight, ChevronDown, Eye } from "lucide-react";
import { documentApi, customInstructionApi, instructionTemplateApi, type CustomInstruction, type InstructionTemplate } from "@/lib/api";
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

export default function KnowledgePage() {
	const { projects, loading: authLoading } = useAuth();
	const [documents, setDocuments] = useState<Document[]>([]);
	const [loading, setLoading] = useState(false);
	const [instructions, setInstructions] = useState<CustomInstruction[]>([]);
	const [instructionsLoading, setInstructionsLoading] = useState(false);
	const [templates, setTemplates] = useState<InstructionTemplate[]>([]);
	const [templatesLoading, setTemplatesLoading] = useState(false);
	const [showTemplates, setShowTemplates] = useState(false);
	const [uploading, setUploading] = useState(false);
	const [savingInstruction, setSavingInstruction] = useState(false);
	const [deletingInstructionId, setDeletingInstructionId] = useState<string | null>(null);
	const [instructionDialogOpen, setInstructionDialogOpen] = useState(false);
	const [editingInstruction, setEditingInstruction] = useState<CustomInstruction | null>(null);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [newInstruction, setNewInstruction] = useState({ label: "", message: "" });
	const [editInstruction, setEditInstruction] = useState({ label: "", message: "" });
	const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
	const [instructionToDelete, setInstructionToDelete] = useState<CustomInstruction | null>(null);
	const [deleteDocConfirmOpen, setDeleteDocConfirmOpen] = useState(false);
	const [documentToDelete, setDocumentToDelete] = useState<Document | null>(null);
	const [deletingDocumentId, setDeletingDocumentId] = useState<string | null>(null);
	const [alert, setAlert] = useState<{ type: "success" | "error"; message: string } | null>(null);
	
	// Web crawl state
	const [crawlUrl, setCrawlUrl] = useState("");
	const [crawlDepth, setCrawlDepth] = useState("1");
	const [crawlSearchQuery, setCrawlSearchQuery] = useState("");
	const [crawling, setCrawling] = useState(false);
	const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set());
	const [childrenCache, setChildrenCache] = useState<Record<string, any[]>>({});
	const [viewingMarkdown, setViewingMarkdown] = useState<any | null>(null);
	const [markdownDialogOpen, setMarkdownDialogOpen] = useState(false);

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

	const fetchTemplates = useCallback(async () => {
		setTemplatesLoading(true);
		try {
			const response = await instructionTemplateApi.list();
			setTemplates(response.data.templates);
		} catch (error) {
			console.error("Failed to fetch templates:", error);
		} finally {
			setTemplatesLoading(false);
		}
	}, []);

	// Fetch documents, instructions, and templates on mount
	useEffect(() => {
		if (!authLoading && currentProjectId) {
			fetchDocuments();
			fetchInstructions();
			fetchTemplates();
		}
	}, [authLoading, currentProjectId, fetchDocuments, fetchInstructions, fetchTemplates]);

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

	function handleDeleteDocumentClick(doc: Document) {
		setDocumentToDelete(doc);
		setDeleteDocConfirmOpen(true);
	}

	async function handleConfirmDeleteDocument() {
		if (!documentToDelete) return;
		
		setDeletingDocumentId(documentToDelete.id);
		setDeleteDocConfirmOpen(false);
		
		try {
			await documentApi.delete(documentToDelete.id);
			setDocuments((prev) => prev.filter((doc) => doc.id !== documentToDelete.id));
			setAlert({ type: "success", message: "Document deleted successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setDeletingDocumentId(null);
			setDocumentToDelete(null);
		}
	}

	async function handleCrawlSubmit(e: React.FormEvent) {
		e.preventDefault();
		
		if (!crawlUrl.trim()) {
			setAlert({ type: "error", message: "Please enter a URL" });
			return;
		}
		
		if (!currentProjectId) {
			setAlert({ type: "error", message: "No project selected" });
			return;
		}
		
		// Validate URL
		try {
			new URL(crawlUrl);
		} catch {
			setAlert({ type: "error", message: "Please enter a valid URL (e.g., https://example.com)" });
			return;
		}
		
		setCrawling(true);
		setAlert(null);
		
		try {
			const depth = parseInt(crawlDepth);
			await documentApi.crawl(crawlUrl, depth, currentProjectId, "default", crawlSearchQuery.trim() || undefined);
			setAlert({ type: "success", message: "Web crawl started successfully" });
			setCrawlUrl("");
			setCrawlDepth("1");
			setCrawlSearchQuery("");
			
			// Refresh documents list
			await fetchDocuments();
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setCrawling(false);
		}
	}

	function toggleParentExpand(parentId: string) {
		const newExpanded = new Set(expandedParents);
		
		if (newExpanded.has(parentId)) {
			newExpanded.delete(parentId);
		} else {
			newExpanded.add(parentId);
			
			// Filter children from existing documents if not cached
			if (!childrenCache[parentId]) {
				const children = documents.filter((doc) => doc.parent_crawl_id === parentId);
				setChildrenCache((prev) => ({
					...prev,
					[parentId]: children,
				}));
			}
		}
		
		setExpandedParents(newExpanded);
	}

	async function handleViewMarkdown(documentId: string) {
		try {
			const response = await documentApi.getMarkdown(documentId);
			setViewingMarkdown(response.data.document);
			setMarkdownDialogOpen(true);
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
					<p className="text-sm">Upload PDF or text files or crawl a website to get started</p>
				</div>
			);
		}

		// Separate parent crawls (URL sources with no parent) and regular documents
		// Child documents (with parent_crawl_id) are not shown here - they appear when parent is expanded
		const parentCrawls = docs.filter((doc) => doc.source_type === "url" && doc.parent_crawl_id === null);
		const regularDocs = docs.filter((doc) => doc.source_type !== "url");

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
						{/* Regular uploaded documents */}
						{regularDocs.map((doc) => (
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

						{/* Parent crawl documents with collapsible children */}
						{parentCrawls.map((parent) => {
							const isExpanded = expandedParents.has(parent.id);
							const children = childrenCache[parent.id] || [];
							
							return (
								<Fragment key={parent.id}>
									{/* Parent row */}
									<TableRow>
										<TableCell className="font-medium">
											<div className="flex items-center gap-2">
												<button
													onClick={() => toggleParentExpand(parent.id)}
													className="hover:bg-accent p-1 rounded"
													disabled={parent.children_count === 0}
												>
													{parent.children_count > 0 ? (
														isExpanded ? (
															<ChevronDown className="h-4 w-4" />
														) : (
															<ChevronRight className="h-4 w-4" />
														)
													) : (
														<div className="w-4" />
													)}
												</button>
												<Globe className="h-4 w-4 text-muted-foreground" />
												<span className="truncate max-w-md" title={parent.page_url}>
													{parent.page_title || parent.page_url}
												</span>
											</div>
										</TableCell>
										<TableCell>
											<Badge variant="outline">URL</Badge>
										</TableCell>
										<TableCell>
											{parent.children_count > 0 && (
												<Badge variant="secondary">{parent.children_count} pages</Badge>
											)}
										</TableCell>
										<TableCell>
											<Badge variant={getStatusBadgeVariant(parent.status)}>
												{parent.status}
											</Badge>
										</TableCell>
										<TableCell>
											{new Date(parent.created_at).toLocaleDateString()}
										</TableCell>
										<TableCell className="text-right">
											<div className="flex justify-end gap-1">
												{parent.status === "completed" && (
													<Button
														variant="ghost"
														size="icon"
														onClick={() => handleViewMarkdown(parent.id)}
													>
														<Eye className="h-4 w-4" />
													</Button>
												)}
												<Button
													variant="ghost"
													size="icon"
													onClick={() => handleDeleteDocumentClick(parent)}
													disabled={deletingDocumentId === parent.id}
												>
													{deletingDocumentId === parent.id ? (
														<Loader2 className="h-4 w-4 animate-spin" />
													) : (
														<Trash2 className="h-4 w-4" />
													)}
												</Button>
											</div>
										</TableCell>
									</TableRow>

									{/* Child rows (when expanded) */}
									{isExpanded && children.map((child) => (
										<TableRow key={child.id} className="bg-muted/30">
											<TableCell className="font-medium">
												<div className="flex items-center gap-2 pl-12">
													<FileText className="h-3 w-3 text-muted-foreground" />
													<span className="text-sm truncate max-w-md" title={child.page_url}>
														{child.page_title || child.page_url}
													</span>
												</div>
											</TableCell>
											<TableCell>
												<Badge variant="outline" className="text-xs">Page</Badge>
											</TableCell>
											<TableCell>-</TableCell>
											<TableCell>
												<Badge variant={getStatusBadgeVariant(child.status)} className="text-xs">
													{child.status}
												</Badge>
											</TableCell>
											<TableCell className="text-sm">
												{new Date(child.created_at).toLocaleDateString()}
											</TableCell>
											<TableCell className="text-right">
												<div className="flex justify-end gap-1">
													{child.status === "completed" && (
														<Button
															variant="ghost"
															size="icon"
															onClick={() => handleViewMarkdown(child.id)}
														>
															<Eye className="h-4 w-4" />
														</Button>
													)}
													<Button
														variant="ghost"
														size="icon"
														onClick={() => handleDeleteDocumentClick(child)}
														disabled={deletingDocumentId === child.id}
													>
														{deletingDocumentId === child.id ? (
															<Loader2 className="h-4 w-4 animate-spin" />
														) : (
															<Trash2 className="h-4 w-4" />
														)}
													</Button>
												</div>
											</TableCell>
										</TableRow>
									))}
								</Fragment>
							);
						})}
					</TableBody>
				</Table>
			</div>
		);
	}

	async function handleUseTemplate(template: InstructionTemplate) {
		setNewInstruction({
			label: template.label,
			message: template.message,
		});
		setShowTemplates(false);
		
		// Record template usage
		try {
			await instructionTemplateApi.recordUse(template.id);
		} catch (error) {
			console.error("Failed to record template usage:", error);
		}
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
			setShowTemplates(false);
			setAlert({ type: "success", message: "Custom instruction added successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setSavingInstruction(false);
		}
	}

	function handleDeleteClick(instruction: CustomInstruction) {
		setInstructionToDelete(instruction);
		setDeleteConfirmOpen(true);
	}

	async function handleConfirmDelete() {
		if (!instructionToDelete) return;
		
		setDeletingInstructionId(instructionToDelete.id);
		setDeleteConfirmOpen(false);
		
		try {
			await customInstructionApi.delete(instructionToDelete.id);
			setInstructions((prev) => prev.filter((inst) => inst.id !== instructionToDelete.id));
			setAlert({ type: "success", message: "Custom instruction deleted successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setDeletingInstructionId(null);
			setInstructionToDelete(null);
		}
	}

	function handleEditClick(instruction: CustomInstruction) {
		setEditingInstruction(instruction);
		setEditInstruction({ label: instruction.label, message: instruction.message });
		setEditDialogOpen(true);
	}

	async function handleUpdateInstruction() {
		if (!editingInstruction) return;
		
		if (!editInstruction.label.trim() || !editInstruction.message.trim()) {
			setAlert({ type: "error", message: "Please fill in both label and message fields" });
			return;
		}

		setSavingInstruction(true);
		try {
			const response = await customInstructionApi.update(editingInstruction.id, {
				label: editInstruction.label.trim(),
				message: editInstruction.message.trim(),
			});

			setInstructions((prev) =>
				prev.map((inst) =>
					inst.id === editingInstruction.id ? response.data.instruction : inst
				)
			);
			setEditDialogOpen(false);
			setEditingInstruction(null);
			setEditInstruction({ label: "", message: "" });
			setAlert({ type: "success", message: "Custom instruction updated successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setSavingInstruction(false);
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
								Upload PDF and text files to provide context for your agent&apos;s responses
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

			{/* Web Crawl Section */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<Globe className="h-5 w-5" />
						Crawl Website
					</CardTitle>
					<CardDescription>
						Submit a URL to crawl and extract content. Specify depth to crawl linked pages.
					</CardDescription>
				</CardHeader>
				<CardContent>
					<form onSubmit={handleCrawlSubmit} className="space-y-4">
						<div className="flex gap-4">
							<div className="flex-1">
								<Label htmlFor="crawl-url">Website URL</Label>
								<Input
									id="crawl-url"
									type="url"
									placeholder="https://example.com"
									value={crawlUrl}
									onChange={(e) => setCrawlUrl(e.target.value)}
									disabled={crawling}
								/>
							</div>
							<div className="w-32">
								<Label htmlFor="crawl-depth">Depth</Label>
								<select
									id="crawl-depth"
									className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
									value={crawlDepth}
									onChange={(e) => setCrawlDepth(e.target.value)}
									disabled={crawling}
								>
									<option value="1">1</option>
									<option value="2">2</option>
									<option value="3">3</option>
								</select>
								<p className="text-xs text-muted-foreground mt-1">
									Pages deep
								</p>
							</div>
							<div className="flex items-end">
								<Button type="submit" disabled={crawling}>
									{crawling ? (
										<>
											<Loader2 className="mr-2 h-4 w-4 animate-spin" />
											Crawling...
										</>
									) : (
										<>
											<Globe className="mr-2 h-4 w-4" />
											Start Crawl
										</>
									)}
								</Button>
							</div>
						</div>
						<div>
							<Label htmlFor="crawl-search-query">
								What are you looking for?{" "}
								<span className="text-muted-foreground font-normal">(optional)</span>
							</Label>
							<Input
								id="crawl-search-query"
								type="text"
								placeholder="e.g., pricing plans, API documentation, contact information"
								value={crawlSearchQuery}
								onChange={(e) => setCrawlSearchQuery(e.target.value)}
								disabled={crawling}
							/>
							<p className="text-xs text-muted-foreground mt-1">
								Only content relevant to this query will be saved to the knowledge base.
							</p>
						</div>
					</form>
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
							<DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
								<DialogHeader>
									<DialogTitle>Add Custom Instruction</DialogTitle>
									<DialogDescription>
										Add a labeled instruction that guides your agent&apos;s behavior
									</DialogDescription>
								</DialogHeader>
								<div className="space-y-4 py-4">
									{/* Template Toggle Button */}
									<div className="flex justify-between items-center pb-2 border-b">
										<Button
											variant={showTemplates ? "default" : "outline"}
											size="sm"
											onClick={() => setShowTemplates(!showTemplates)}
											className="gap-2"
										>
											<Sparkles className="h-4 w-4" />
											{showTemplates ? "Hide Templates" : "Use Template"}
										</Button>
										{showTemplates && (
											<span className="text-xs text-muted-foreground">
												Click a template to use it
											</span>
										)}
									</div>

									{/* Templates Grid */}
									{showTemplates && (
										<div className="space-y-3">
											{templatesLoading ? (
												<div className="text-center py-4">
													<Loader2 className="h-6 w-6 animate-spin mx-auto text-muted-foreground" />
												</div>
											) : (
												<div className="grid gap-2 max-h-64 overflow-y-auto">
													{templates.map((template) => (
														<Card
															key={template.id}
															className="cursor-pointer hover:border-primary transition-colors"
															onClick={() => handleUseTemplate(template)}
														>
															<CardContent className="p-3">
																<div className="flex items-start justify-between gap-2">
																	<div className="flex-1">
																		<div className="flex items-center gap-2 mb-1">
																			<p className="font-medium text-sm">{template.label}</p>
																			<Badge variant="secondary" className="text-xs">
																				<Tag className="h-3 w-3 mr-1" />
																				{template.category}
																			</Badge>
																		</div>
																		<p className="text-xs text-muted-foreground line-clamp-2">
																			{template.description}
																		</p>
																	</div>
																	{template.usage_count > 0 && (
																		<Badge variant="outline" className="text-xs shrink-0">
																			{template.usage_count} uses
																		</Badge>
																	)}
																</div>
															</CardContent>
														</Card>
													))}
												</div>
											)}
										</div>
									)}

									{/* Form Fields */}
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
												setShowTemplates(false);
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

						{/* Edit Instruction Dialog */}
						<Dialog open={editDialogOpen} onOpenChange={(open) => {
							setEditDialogOpen(open);
							if (!open) {
								setEditingInstruction(null);
								setEditInstruction({ label: "", message: "" });
							}
						}}>
							<DialogContent>
								<DialogHeader>
									<DialogTitle>Edit Custom Instruction</DialogTitle>
									<DialogDescription>
										Update the instruction that guides your agent&apos;s behavior
									</DialogDescription>
								</DialogHeader>
								<div className="space-y-4 py-4">
									<div className="space-y-2">
										<Label htmlFor="edit-instruction-label">Label</Label>
										<Input
											id="edit-instruction-label"
											placeholder="e.g., Greeting Style, Response Tone, Escalation Rules"
											value={editInstruction.label}
											onChange={(e) =>
												setEditInstruction((prev) => ({ ...prev, label: e.target.value }))
											}
										/>
									</div>
									<div className="space-y-2">
										<Label htmlFor="edit-instruction-message">Message</Label>
										<Textarea
											id="edit-instruction-message"
											placeholder="Enter the instruction message that defines how the agent should act..."
											rows={6}
											value={editInstruction.message}
											onChange={(e) =>
												setEditInstruction((prev) => ({ ...prev, message: e.target.value }))
											}
										/>
									</div>
									<div className="flex justify-end gap-2">
										<Button
											variant="outline"
											onClick={() => {
												setEditDialogOpen(false);
												setEditingInstruction(null);
												setEditInstruction({ label: "", message: "" });
											}}
											disabled={savingInstruction}
										>
											Cancel
										</Button>
										<Button onClick={handleUpdateInstruction} disabled={savingInstruction}>
											{savingInstruction ? (
												<>
													<Loader2 className="mr-2 h-4 w-4 animate-spin" />
													Saving...
												</>
											) : (
												"Save Changes"
											)}
										</Button>
									</div>
								</div>
							</DialogContent>
						</Dialog>

						{/* Delete Confirmation Dialog */}
						<Dialog open={deleteConfirmOpen} onOpenChange={(open) => {
							setDeleteConfirmOpen(open);
							if (!open) {
								setInstructionToDelete(null);
							}
						}}>
							<DialogContent>
								<DialogHeader>
									<DialogTitle>Delete Instruction</DialogTitle>
									<DialogDescription>
										Are you sure you want to delete this instruction? This action cannot be undone.
									</DialogDescription>
								</DialogHeader>
								{instructionToDelete && (
									<div className="py-4">
										<div className="rounded-md border p-3 bg-muted/50">
											<p className="text-sm font-medium">{instructionToDelete.label}</p>
											<p className="text-sm text-muted-foreground line-clamp-2 mt-1">
												{instructionToDelete.message}
											</p>
										</div>
									</div>
								)}
								<DialogFooter className="gap-2 sm:gap-0">
									<Button
										variant="outline"
										onClick={() => {
											setDeleteConfirmOpen(false);
											setInstructionToDelete(null);
										}}
									>
										Cancel
									</Button>
									<Button
										variant="destructive"
										onClick={handleConfirmDelete}
									>
										Delete
									</Button>
								</DialogFooter>
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
											<div className="flex items-center gap-1">
												<Button
													variant="ghost"
													size="icon"
													onClick={() => handleEditClick(inst)}
												>
													<Pencil className="h-4 w-4" />
												</Button>
												<Button
													variant="ghost"
													size="icon"
													onClick={() => handleDeleteClick(inst)}
													disabled={deletingInstructionId === inst.id}
												>
													{deletingInstructionId === inst.id ? (
														<Loader2 className="h-4 w-4 animate-spin" />
													) : (
														<Trash2 className="h-4 w-4" />
													)}
												</Button>
											</div>
										</div>
									</CardHeader>
									<CardContent>
										<p className="text-sm text-muted-foreground line-clamp-2">
											{inst.message}
										</p>
									</CardContent>
								</Card>
							))}
						</div>
					) : (
						<div className="text-center py-8 text-muted-foreground">
							<FileText className="h-12 w-12 mx-auto mb-2 opacity-50" />
							<p>No custom instructions added yet</p>
							<p className="text-sm">Add instructions to guide your agent&apos;s behavior</p>
						</div>
					)}
				</CardContent>
			</Card>


			{/* Documents Section */}
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2">
						<FileText className="h-5 w-5" />
						Documents
					</CardTitle>
					<CardDescription>
						Upload PDF and text files to enhance your agent&apos;s knowledge base
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

					{/* Delete Document Confirmation Dialog */}
					<Dialog open={deleteDocConfirmOpen} onOpenChange={(open) => {
						setDeleteDocConfirmOpen(open);
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
										setDeleteDocConfirmOpen(false);
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
				</CardContent>
			</Card>

			{/* Markdown Viewer Modal */}
			<Dialog open={markdownDialogOpen} onOpenChange={setMarkdownDialogOpen}>
				<DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
					<DialogHeader>
						<DialogTitle>
							{viewingMarkdown?.page_title || "Page Content"}
						</DialogTitle>
						<DialogDescription>
							{viewingMarkdown?.page_url && (
								<a 
									href={viewingMarkdown.page_url} 
									target="_blank" 
									rel="noopener noreferrer"
									className="text-blue-600 hover:underline"
								>
									{viewingMarkdown.page_url}
								</a>
							)}
							{viewingMarkdown?.created_at && (
								<span className="text-muted-foreground ml-2">
									• Crawled {new Date(viewingMarkdown.created_at).toLocaleDateString()}
								</span>
							)}
						</DialogDescription>
					</DialogHeader>
					<div className="space-y-6 py-4">
						{/* Markdown Content */}
						{viewingMarkdown?.markdown_content && (
							<div className="prose prose-sm max-w-none">
								<div className="whitespace-pre-wrap bg-muted/30 p-4 rounded-lg">
									{viewingMarkdown.markdown_content}
								</div>
							</div>
						)}

						{/* Image Gallery */}
						{viewingMarkdown?.images_metadata && viewingMarkdown.images_metadata.length > 0 && (
							<div className="space-y-3">
								<h3 className="font-semibold text-sm flex items-center gap-2">
									<FileText className="h-4 w-4" />
									Images ({viewingMarkdown.images_metadata.length})
								</h3>
								<div className="grid grid-cols-2 md:grid-cols-3 gap-4">
									{viewingMarkdown.images_metadata.map((img: any, idx: number) => (
										<div key={idx} className="border rounded-lg p-2 space-y-2">
											<div className="aspect-video bg-muted rounded flex items-center justify-center overflow-hidden">
												<img 
													src={img.src} 
													alt={img.alt || `Image ${idx + 1}`}
													className="max-w-full max-h-full object-contain"
													onError={(e) => {
														(e.target as HTMLImageElement).style.display = 'none';
													}}
												/>
											</div>
											{img.alt && (
												<p className="text-xs text-muted-foreground line-clamp-2">
													{img.alt}
												</p>
											)}
											<a 
												href={img.src} 
												target="_blank" 
												rel="noopener noreferrer"
												className="text-xs text-blue-600 hover:underline block truncate"
											>
												View full size
											</a>
										</div>
									))}
								</div>
							</div>
						)}
					</div>
					<DialogFooter>
						<Button onClick={() => setMarkdownDialogOpen(false)}>
							Close
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
