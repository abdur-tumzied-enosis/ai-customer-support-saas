"""
Rebuild every knowledge base's Qdrant collections from the source documents (backlog Q7, hybrid search):

    python manage.py reindex_vectors

Deletes each KB's collections (`{vector_collection}` and `{vector_collection}_web`) and re-embeds every
document that had vectors: uploads from their stored file, crawled pages from their saved markdown.
Qdrant only holds data derived from Postgres and the uploaded files, so a wiped Qdrant volume is fine.
While a KB is rebuilding its replies get no search results, so run it when traffic is low.
Embedding usage is not billed to the projects.

Every step is logged (knowledge.management.commands.reindex_vectors), one line per document, so a run can be
followed in the console. Documents with no content end up with no vectors and are logged as WARNING.
"""
import logging
import time

from django.core.management.base import BaseCommand

from agents.vector_store import delete_collection
from knowledge.models import KnowledgeBase
from knowledge.tasks import chunk_text, embed_crawled_document, embed_uploaded_chunks, extract_text_from_file

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = "Delete and rebuild all knowledge base vector collections with hybrid (dense + BM25) vectors."

	def handle(self, **options):
		started = time.monotonic()
		kbs = list(KnowledgeBase.objects.all())
		logger.info(f"reindex_vectors: start, {len(kbs)} knowledge base(s)")
		failed = []
		total_docs = total_chunks = 0
		for kb in kbs:
			web_collection = kb.vector_collection + "_web"
			docs = list(kb.documents.exclude(vector_chunk_ids=[]))
			logger.info(
				f"reindex_vectors: KB {kb.id} '{kb.name}': dropping collections '{kb.vector_collection}' and "
				f"'{web_collection}', then re-embedding {len(docs)} document(s)"
			)
			delete_collection(kb.vector_collection)
			delete_collection(web_collection)
			kb_chunks = 0
			for i, doc in enumerate(docs, 1):
				label = doc.page_url or doc.filename or "-"
				collection = web_collection if doc.source_type == "url" else kb.vector_collection
				# Clear the old chunk IDs first: an empty document returns without writing new ones,
				# and stale IDs would make later deletes target points that no longer exist
				old_count = len(doc.vector_chunk_ids)
				doc.vector_chunk_ids = []
				doc.save(update_fields=["vector_chunk_ids"])
				try:
					if doc.source_type == "url":
						embed_crawled_document(doc, web_collection)
					else:
						embed_uploaded_chunks(doc, chunk_text(extract_text_from_file(doc)))
				except Exception as e:
					logger.exception(f"reindex_vectors: FAILED doc {doc.id} [{doc.source_type}] {label}: {e}")
					failed.append(f"{doc.id} {label}: {e}")
					continue
				new_count = len(doc.vector_chunk_ids)
				total_docs += 1
				kb_chunks += new_count
				if new_count == 0:
					logger.warning(
						f"reindex_vectors: doc {doc.id} [{doc.source_type}] {label}: no chunks produced "
						f"(had {old_count} before); document has no content to embed into '{collection}'"
					)
				else:
					logger.info(
						f"reindex_vectors: doc {doc.id} [{doc.source_type}] {label}: {new_count} chunk(s) "
						f"into '{collection}' (was {old_count})"
					)
			total_chunks += kb_chunks
			logger.info(f"reindex_vectors: KB {kb.id} '{kb.name}' done: {kb_chunks} chunk(s)")
		elapsed = time.monotonic() - started
		logger.info(
			f"reindex_vectors: finished in {elapsed:.1f}s: {total_docs} document(s), {total_chunks} chunk(s), "
			f"{len(failed)} failed"
		)
		for line in failed:
			self.stderr.write(f"FAILED {line}")
		self.stdout.write(f"Done, {len(failed)} documents failed.")
