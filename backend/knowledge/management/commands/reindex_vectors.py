"""
Rebuild every knowledge base's Qdrant collections from the source documents (backlog Q7, hybrid search):

    python manage.py reindex_vectors

Deletes each KB's collections (`{vector_collection}` and `{vector_collection}_web`) and re-embeds every
document that had vectors: uploads from their stored file, crawled pages from their saved markdown.
Qdrant only holds data derived from Postgres and the uploaded files, so a wiped Qdrant volume is fine.
While a KB is rebuilding its replies get no search results, so run it when traffic is low.
Embedding usage is not billed to the projects.
"""
from django.core.management.base import BaseCommand

from agents.vector_store import delete_collection
from knowledge.models import KnowledgeBase
from knowledge.tasks import chunk_text, embed_crawled_document, embed_uploaded_chunks, extract_text_from_file


class Command(BaseCommand):
	help = "Delete and rebuild all knowledge base vector collections with hybrid (dense + BM25) vectors."

	def handle(self, **options):
		failed = []
		for kb in KnowledgeBase.objects.all():
			web_collection = kb.vector_collection + "_web"
			delete_collection(kb.vector_collection)
			delete_collection(web_collection)
			docs = kb.documents.exclude(vector_chunk_ids=[])
			self.stdout.write(f"{kb}: {docs.count()} documents")
			for doc in docs:
				try:
					if doc.source_type == "url":
						embed_crawled_document(doc, web_collection)
					else:
						embed_uploaded_chunks(doc, chunk_text(extract_text_from_file(doc)))
				except Exception as e:
					# Old vectors are already gone; keep going and list it so the document can be re-uploaded
					doc.vector_chunk_ids = []
					doc.save(update_fields=["vector_chunk_ids"])
					failed.append(f"{doc.id} {doc.filename or doc.page_url}: {e}")
		for line in failed:
			self.stderr.write(f"FAILED {line}")
		self.stdout.write(f"Done, {len(failed)} documents failed.")
