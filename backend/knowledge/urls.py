from django.urls import path
from .products import (
	FeaturesView, ProductListCreateView, ProductDetailView, ProductImagesAddView,
	ProductImageDetailView, ProductImageFileView, ProductImagesConfirmView,
)
from .catalog_views import ProductFieldDetailView, ProductFieldListCreateView, product_summary
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


