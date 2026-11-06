from django.urls import path
from .products import (
	FeaturesView, ProductListCreateView, ProductDetailView, ProductImagesAddView,
	ProductImageDetailView, ProductImageFileView, ProductImagesConfirmView,
)
from .catalog_views import (
	CsvSourceCreateView, CsvSourcePreviewView, CsvSourceUploadView, ProductFieldDetailView, ProductFieldListCreateView, SheetSourceCreateView,
	SheetSourcePreviewView,
	product_source_delete, product_source_list, product_source_sync, product_summary,
)
from .candidate_views import candidate_ignore_page, candidate_import, candidate_list, product_extract
from .suggestions import SuggestionDecisionView, SuggestionListView
from .views import (
	KnowledgeBaseListView, KnowledgeBaseDetailView,
	DocumentUploadView, DocumentConfirmView, DocumentListView, DocumentDeleteView,
	WebCrawlSubmitView, DocumentMarkdownView, DocumentChildrenView,
	InstructionTemplateListView, InstructionTemplateUseView,
	CustomInstructionListCreateView, CustomInstructionDetailView, CustomInstructionResolveView
)

urlpatterns = [
	# Knowledge base endpoints
	path("knowledge-bases/", KnowledgeBaseListView.as_view(), name="knowledge-base-list"),
	path("knowledge-bases/<uuid:kb_id>/", KnowledgeBaseDetailView.as_view(), name="knowledge-base-detail"),
	
	# Document endpoints
	path("documents/upload/", DocumentUploadView.as_view(), name="document-upload"),
	path("documents/<uuid:document_id>/confirm/", DocumentConfirmView.as_view(), name="document-confirm"),
	path("documents/", DocumentListView.as_view(), name="document-list"),
	path("documents/<uuid:document_id>/", DocumentDeleteView.as_view(), name="document-delete"),
	path("documents/<uuid:document_id>/markdown/", DocumentMarkdownView.as_view(), name="document-markdown"),
	path("documents/<uuid:parent_id>/children/", DocumentChildrenView.as_view(), name="document-children"),
	
	# Web crawl endpoints
	path("crawl/", WebCrawlSubmitView.as_view(), name="web-crawl-submit"),
	
	# Instruction template endpoints
	path("instruction-templates/", InstructionTemplateListView.as_view(), name="instruction-template-list"),
	path("instruction-templates/<uuid:template_id>/use/", InstructionTemplateUseView.as_view(), name="instruction-template-use"),
	
	# Custom instruction endpoints
	path("instructions/", CustomInstructionListCreateView.as_view(), name="instruction-list-create"),
	path("instructions/resolve/", CustomInstructionResolveView.as_view(), name="instruction-resolve"),
	path("instructions/<uuid:instruction_id>/", CustomInstructionDetailView.as_view(), name="instruction-detail"),

	# Product catalog (image search)
	path("features/", FeaturesView.as_view(), name="kb-features"),
	path("products/", ProductListCreateView.as_view(), name="product-list-create"),
	path("products/summary/", product_summary, name="product-summary"),
	path("products/sources/", product_source_list, name="product-sources"),
	path("products/sources/csv/preview/", CsvSourcePreviewView.as_view(), name="product-source-csv-preview"),
	path("products/sources/csv/", CsvSourceCreateView.as_view(), name="product-source-csv"),
	path("products/sources/sheet/preview/", SheetSourcePreviewView.as_view(), name="product-source-sheet-preview"),
	path("products/sources/sheet/", SheetSourceCreateView.as_view(), name="product-source-sheet"),
	path("products/sources/<uuid:source_id>/sync/", product_source_sync, name="product-source-sync"),
	path("products/sources/<uuid:source_id>/upload/", CsvSourceUploadView.as_view(), name="product-source-upload"),
	path("products/sources/<uuid:source_id>/", product_source_delete, name="product-source-delete"),
	path("products/candidates/", candidate_list, name="product-candidates"),
	path("products/candidates/import/", candidate_import, name="product-candidates-import"),
	path("products/candidates/ignore-page/", candidate_ignore_page, name="product-candidates-ignore-page"),
	path("products/extract/", product_extract, name="product-extract"),
	path("products/<uuid:product_id>/", ProductDetailView.as_view(), name="product-detail"),
	path("products/<uuid:product_id>/images/", ProductImagesAddView.as_view(), name="product-images-add"),
	path("products/images/confirm/", ProductImagesConfirmView.as_view(), name="product-images-confirm"),
	path("products/images/<uuid:image_id>/", ProductImageDetailView.as_view(), name="product-image-detail"),
	path("products/images/<uuid:image_id>/file/", ProductImageFileView.as_view(), name="product-image-file"),

	path("product-fields/", ProductFieldListCreateView.as_view(), name="product-field-list-create"),
	path("product-fields/<uuid:field_id>/", ProductFieldDetailView.as_view(), name="product-field-detail"),

	# Agent memory review queue
	path("suggestions/", SuggestionListView.as_view(), name="suggestion-list"),
	path("suggestions/<uuid:suggestion_id>/<str:decision>/", SuggestionDecisionView.as_view(), name="suggestion-decision"),
]


