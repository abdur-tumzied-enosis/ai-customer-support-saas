"""
Web Crawl Retriever - Returns a retriever for web-crawled content stored in Qdrant.

Web-crawled documents are embedded at crawl time into a dedicated collection
named `{kb.vector_collection}_web`. This module simply connects to that
persistent collection at query time.
"""

import logging
from typing import Optional
from langchain_core.retrievers import BaseRetriever

logger = logging.getLogger(__name__)


def get_web_crawl_retriever(knowledge_base_id: str, k: int = 5) -> Optional[BaseRetriever]:
    """
    Get a retriever for web-crawled content from a knowledge base.

    Returns None if the collection does not exist or has no content.
    """
    try:
        from knowledge.models import KnowledgeBase
        from agents.vector_store import get_retriever

        kb = KnowledgeBase.objects.get(id=knowledge_base_id)
        collection_name = kb.vector_collection + "_web"
        return get_retriever(collection_name, k=k)
    except Exception as e:
        logger.warning(f"Web crawl retriever unavailable for KB {knowledge_base_id}: {e}")
        return None
