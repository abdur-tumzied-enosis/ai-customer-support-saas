"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { FileText, Plus, Trash2, Loader2, Pencil, Sparkles, Tag, Layers, Database } from "lucide-react";
import { customInstructionApi, instructionTemplateApi, type ApiDomain, type CustomInstruction, type InstructionTemplate } from "@/lib/api";
import { ApiAccessEditor, toApiDomainsPayload } from "@/components/knowledge/ApiAccessEditor";
import { useKnowledge } from "@/components/knowledge/KnowledgeContext";
import { formatError } from "@/lib/errors";

const EMPTY_INSTRUCTION = { label: "", message: "", api_tool_enabled: false, api_domains: [] as ApiDomain[] };

export default function InstructionsSection() {
	const {
		projectId: currentProjectId,
		selectedKb,
		instructions,
		setInstructions,
		instructionsLoading,
		fetchInstructions,
		setAlert,
	} = useKnowledge();
	const [templates, setTemplates] = useState<InstructionTemplate[]>([]);
	const [templatesLoading, setTemplatesLoading] = useState(false);
	const [showTemplates, setShowTemplates] = useState(false);
	const [savingInstruction, setSavingInstruction] = useState(false);
	const [deletingInstructionId, setDeletingInstructionId] = useState<string | null>(null);
	const [instructionDialogOpen, setInstructionDialogOpen] = useState(false);
	const [editingInstruction, setEditingInstruction] = useState<CustomInstruction | null>(null);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [newInstruction, setNewInstruction] = useState(EMPTY_INSTRUCTION);
	const [newInstructionScope, setNewInstructionScope] = useState<"shared" | "kb">("shared");
	const [editInstruction, setEditInstruction] = useState(EMPTY_INSTRUCTION);
	const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
	const [instructionToDelete, setInstructionToDelete] = useState<CustomInstruction | null>(null);

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

	useEffect(() => {
		fetchTemplates();
	}, [fetchTemplates]);

	function renderScopeBadge(inst: CustomInstruction) {
		return inst.knowledge_base_id ? (
			<Badge variant="secondary" className="gap-1"><Database className="h-3 w-3" />Only {inst.knowledge_base_name}</Badge>
		) : (
			<Badge variant="secondary" className="gap-1"><Layers className="h-3 w-3" />Shared · all knowledge bases</Badge>
		);
	}

	function renderInstructionGroup(title: React.ReactNode, items: CustomInstruction[], emptyText: string) {
		return (
			<div className="space-y-3">
				<div className="flex items-center gap-2 text-sm font-medium">
					{title}
					<Badge variant="outline">{items.length}</Badge>
				</div>
				{items.length === 0 ? (
					<p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">{emptyText}</p>
				) : (
					items.map((inst) => (
						<Card key={inst.id}>
							<CardHeader className="pb-3">
								<div className="flex items-start justify-between">
									<div className="flex items-center gap-2">
										<Badge variant="outline">{inst.label}</Badge>
										{inst.api_tool_enabled && <Badge variant="secondary">API</Badge>} {inst.scope === "order" && <Badge>Orders</Badge>} {inst.scope === "off" && <Badge variant="outline">Off</Badge>}
										<span className="text-xs text-muted-foreground">
											Added {new Date(inst.created_at).toLocaleDateString()}
										</span>
									</div>
									<div className="flex items-center gap-1">
										<Button variant="ghost" size="icon" onClick={() => handleEditClick(inst)} title="Edit instruction">
											<Pencil className="h-4 w-4" />
										</Button>
										<Button
											variant="ghost"
											size="icon"
											onClick={() => handleDeleteClick(inst)}
											disabled={deletingInstructionId === inst.id}
											title="Delete instruction"
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
								<p className="text-sm text-muted-foreground line-clamp-2">{inst.message}</p>
							</CardContent>
						</Card>
					))
				)}
			</div>
		);
	}

	async function handleUseTemplate(template: InstructionTemplate) {
		setNewInstruction({ ...EMPTY_INSTRUCTION, label: template.label, message: template.message });
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

		const newDomains = toApiDomainsPayload(newInstruction.api_domains);
		if (newInstruction.api_tool_enabled && newDomains.length === 0) {
			setAlert({ type: "error", message: "Add at least one allowed domain to enable API calls" });
			return;
		}

		if (!currentProjectId) {
			setAlert({ type: "error", message: "No project selected" });
			return;
		}

		setSavingInstruction(true);
		try {
			const forKb = newInstructionScope === "kb" ? selectedKb : undefined;
			await customInstructionApi.create({
				project_id: currentProjectId,
				knowledge_base_id: forKb?.id ?? null, // null: shared by every knowledge base
				label: newInstruction.label.trim(),
				message: newInstruction.message.trim(),
				api_tool_enabled: newInstruction.api_tool_enabled,
				api_domains: newDomains,
			});

			await fetchInstructions(); // keeps the shared / KB grouping and order
			setNewInstruction(EMPTY_INSTRUCTION);
			setNewInstructionScope("shared");
			setInstructionDialogOpen(false);
			setShowTemplates(false);
			setAlert({
				type: "success",
				message: forKb ? `Instruction added for ${forKb.name} only` : "Instruction added for every knowledge base",
			});
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
		setEditInstruction({
			label: instruction.label,
			message: instruction.message,
			api_tool_enabled: instruction.api_tool_enabled ?? false,
			api_domains: (instruction.api_domains ?? []).map((d) => ({ ...d, secret: "" })),
		});
		setEditDialogOpen(true);
	}

	async function handleUpdateInstruction() {
		if (!editingInstruction) return;
		
		if (!editInstruction.label.trim() || !editInstruction.message.trim()) {
			setAlert({ type: "error", message: "Please fill in both label and message fields" });
			return;
		}

		const editDomains = toApiDomainsPayload(editInstruction.api_domains);
		if (editInstruction.api_tool_enabled && editDomains.length === 0) {
			setAlert({ type: "error", message: "Add at least one allowed domain to enable API calls" });
			return;
		}

		setSavingInstruction(true);
		try {
			const response = await customInstructionApi.update(editingInstruction.id, {
				label: editInstruction.label.trim(),
				message: editInstruction.message.trim(),
				api_tool_enabled: editInstruction.api_tool_enabled,
				api_domains: editDomains,
			});

			setInstructions((prev) =>
				prev.map((inst) =>
					inst.id === editingInstruction.id ? response.data.instruction : inst
				)
			);
			setEditDialogOpen(false);
			setEditingInstruction(null);
			setEditInstruction(EMPTY_INSTRUCTION);
			setAlert({ type: "success", message: "Custom instruction updated successfully" });
		} catch (error) {
			setAlert({ type: "error", message: formatError(error) });
		} finally {
			setSavingInstruction(false);
		}
	}

	return (
		<Card>
			<CardHeader>
				<CardTitle className="flex items-center gap-2"><FileText className="h-5 w-5" /> Custom Instructions</CardTitle>
				<CardDescription>
					Define how your agent should behave, act, and take actions with customers. Shared instructions apply to every knowledge base; instructions for one knowledge base are added on top of them.
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

									{/* Scope */}
									<div className="space-y-2">
										<Label>Applies to</Label>
										<div className="grid gap-2 sm:grid-cols-2">
											{([
												["shared", "Shared", "Every knowledge base"],
												["kb", `Only ${selectedKb?.name ?? "this knowledge base"}`, "Added on top of the shared instructions"],
											] as const).map(([value, title, hint]) => (
												<button
													key={value}
													type="button"
													aria-pressed={newInstructionScope === value}
													disabled={value === "kb" && !selectedKb}
													onClick={() => setNewInstructionScope(value)}
													className={`rounded-md border p-3 text-left transition-colors disabled:opacity-50 ${
														newInstructionScope === value ? "border-primary bg-primary/5" : "hover:bg-muted"
													}`}
												>
													<span className="flex items-center gap-2 text-sm font-medium">
														{value === "shared" ? <Layers className="h-4 w-4" /> : <Database className="h-4 w-4" />}
														{title}
													</span>
													<span className="text-xs text-muted-foreground">{hint}</span>
												</button>
											))}
										</div>
									</div>

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
									<ApiAccessEditor
										value={newInstruction}
										message={newInstruction.message}
										onChange={(v) => setNewInstruction((prev) => ({ ...prev, ...v }))}
										onInsertExample={(text) =>
											setNewInstruction((prev) => ({ ...prev, message: prev.message ? `${prev.message}\n\n${text}` : text }))
										}
									/>
									<div className="flex justify-end gap-2">
										<Button
											variant="outline"
											onClick={() => {
												setInstructionDialogOpen(false);
												setNewInstruction(EMPTY_INSTRUCTION);
												setNewInstructionScope("shared");
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
								setEditInstruction(EMPTY_INSTRUCTION);
							}
						}}>
							<DialogContent className="max-w-3xl max-h-[90vh] overflow-y-auto">
								<DialogHeader>
									<DialogTitle>Edit Custom Instruction</DialogTitle>
									<DialogDescription>
										Update the instruction that guides your agent&apos;s behavior
									</DialogDescription>
								</DialogHeader>
								<div className="space-y-4 py-4">
									{editingInstruction && (
										<p className="flex items-center gap-2 text-sm text-muted-foreground">
											Applies to:
											{renderScopeBadge(editingInstruction)}
										</p>
									)}
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
									<ApiAccessEditor
										value={editInstruction}
										message={editInstruction.message}
										onChange={(v) => setEditInstruction((prev) => ({ ...prev, ...v }))}
										onInsertExample={(text) =>
											setEditInstruction((prev) => ({ ...prev, message: prev.message ? `${prev.message}\n\n${text}` : text }))
										}
									/>
									<div className="flex justify-end gap-2">
										<Button
											variant="outline"
											onClick={() => {
												setEditDialogOpen(false);
												setEditingInstruction(null);
												setEditInstruction(EMPTY_INSTRUCTION);
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
						<div className="space-y-6">
							{renderInstructionGroup(
								<><Layers className="h-4 w-4" /> Shared · all knowledge bases</>,
								instructions.filter((inst) => !inst.knowledge_base_id),
								"No shared instructions yet.",
							)}
							{selectedKb && renderInstructionGroup(
								<><Database className="h-4 w-4" /> Only {selectedKb.name}</>,
								instructions.filter((inst) => inst.knowledge_base_id === selectedKb.id),
								`Nothing just for ${selectedKb.name} yet. Add an instruction and choose "Only ${selectedKb.name}".`,
							)}
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
	);
}
