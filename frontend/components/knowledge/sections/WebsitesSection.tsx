"use client";

import { useState, Fragment } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FileText, Trash2, Loader2, Globe, ChevronRight, ChevronDown, Eye } from "lucide-react";
import { documentApi } from "@/lib/api";
import { useKnowledge, type KbDocument as Document } from "@/components/knowledge/KnowledgeContext";
import { getStatusBadgeVariant, useDeleteDocument } from "@/components/knowledge/sections/documents";
import { formatError } from "@/lib/errors";

// The crawl form plus every crawled site, with its pages nested under it.
export default function WebsitesSection() {
	const {
		projectId: currentProjectId,
		selectedKb,
		documents,
		documentsLoading: loading,
		fetchDocuments,
		fetchKnowledgeBases,
		setAlert,
	} = useKnowledge();
	const { deletingDocumentId, requestDelete: handleDeleteDocumentClick, dialog: deleteDocumentDialog } = useDeleteDocument();

	// Web crawl state
	const [crawlUrl, setCrawlUrl] = useState("");
	const [crawlDepth, setCrawlDepth] = useState("1");
	const [crawlSearchQuery, setCrawlSearchQuery] = useState("");
	const [crawling, setCrawling] = useState(false);
	const [expandedParents, setExpandedParents] = useState<Set<string>>(new Set());
	const [childrenCache, setChildrenCache] = useState<Record<string, any[]>>({});
	const [viewingMarkdown, setViewingMarkdown] = useState<any | null>(null);
	const [markdownDialogOpen, setMarkdownDialogOpen] = useState(false);

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
		if (!selectedKb) return;

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
			await documentApi.crawl(crawlUrl, depth, currentProjectId, selectedKb.id, crawlSearchQuery.trim() || undefined);
			setAlert({ type: "success", message: `Web crawl started for ${selectedKb.name}` });
			setCrawlUrl("");
			setCrawlDepth("1");
			setCrawlSearchQuery("");

			// Refresh documents and the per-KB document counts
			await Promise.all([fetchDocuments(), fetchKnowledgeBases()]);
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

	function renderCrawlTable(docs: Document[]) {
		if (loading) {
			return (
				<div className="text-center py-8 text-muted-foreground">
					<Loader2 className="h-12 w-12 mx-auto mb-2 opacity-50 animate-spin" />
					<p>Loading documents...</p>
				</div>
			);
		}

		// Parent crawls are the sites; their pages (with parent_crawl_id) appear when a parent is expanded
		const parentCrawls = docs.filter((doc) => doc.source_type === "url" && doc.parent_crawl_id === null);

		if (parentCrawls.length === 0) {
			return (
				<div className="text-center py-8 text-muted-foreground">
					<Globe className="h-12 w-12 mx-auto mb-2 opacity-50" />
					<p>No websites crawled into <span className="font-medium">{selectedKb?.name ?? "this agent memory"}</span> yet</p>
					<p className="text-sm">Enter a URL above to crawl a website</p>
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

	return (
		<>
			<Card>
				<CardHeader>
					<CardTitle className="flex items-center gap-2"><Globe className="h-5 w-5" /> Crawled Websites</CardTitle>
					<CardDescription>Submit a URL to crawl and extract content. Specify depth to crawl linked pages.</CardDescription>
				</CardHeader>
				<CardContent className="space-y-6">
					<form onSubmit={handleCrawlSubmit} className="space-y-4">
						<div className="flex flex-col gap-4 sm:flex-row">
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
							<div className="sm:w-32">
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
								Only content relevant to this query will be saved to the agent memory.
							</p>
						</div>
					</form>

					{renderCrawlTable(documents)}

					{deleteDocumentDialog}
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
		</>
	);
}
