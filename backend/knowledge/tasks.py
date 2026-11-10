import os
import uuid
import asyncio
import logging
from datetime import timedelta
from decimal import Decimal
from typing import List
from io import BytesIO
from urllib.parse import urlparse, urljoin, urldefrag
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.core.files.storage import default_storage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from django.core.files.base import ContentFile
from core.storage import ObjectTooLarge, delete_keys, read_capped, thumb_key
from .models import IngestionJob, Document, IngestionJobStatus, DocumentFileType, KnowledgeBase, MAX_PRODUCT_IMAGE_BYTES, Product, ProductImage, ProductSource, product_image_path
from agents.vector_store import add_texts_to_collection
from agents.image_search import NotAnImage, delete_images, embed_image, load_image, make_variants, upsert_image
from usage.utils import record_image_usage

logger = logging.getLogger(__name__)


def make_absolute_image_urls(images: List[dict], base_url: str) -> List[dict]:
    """
    Convert relative image URLs to absolute URLs.
    
    Args:
        images: List of image metadata dicts with 'src' field
        base_url: The base URL of the page the images were found on
        
    Returns:
        List of image dicts with absolute URLs in 'src' field
    """
    if not images:
        return images
    
    result = []
    for img in images:
        img_copy = img.copy() if isinstance(img, dict) else {}
        src = img_copy.get('src', '')
        
        if src:
            # Convert relative URLs to absolute
            if src.startswith('/') or not src.startswith(('http://', 'https://', 'data:')):
                img_copy['src'] = urljoin(base_url, src)
        
        result.append(img_copy)
    
    return result


def extract_text_from_file(document: Document) -> str:
    """
    Extract text content from supported file types.
    Supports: PDF (using pdfplumber), TXT, MD (Markdown)
    """
    if not document.file:
        logger.error(f"Document {document.id} has no file attached")
        raise ValueError("Document has no file attached")

    file_path = document.file.name
    filename = str(document.filename) if document.filename else ''
    file_extension = os.path.splitext(filename)[1].lower()
    
    logger.info(f"Extracting text from document {document.id}: {filename} (type: {file_extension})")

    # Read file content
    if default_storage.exists(file_path):
        file_content = default_storage.open(file_path, mode='rb').read()
        logger.debug(f"Read {len(file_content)} bytes from {file_path}")
    else:
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")

    # Extract text based on file type
    if file_extension == '.pdf':
        try:
            import pdfplumber
            
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                text_parts = []
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                extracted_text = '\n\n'.join(text_parts)
                logger.info(f"Extracted {len(extracted_text)} chars from {len(pdf.pages)} PDF pages")
                return extracted_text
        except ImportError:
            logger.error("pdfplumber is not installed")
            raise ImportError("pdfplumber is not installed. Install it: pip install pdfplumber")
        except Exception as e:
            logger.error(f"Failed to extract text from PDF: {str(e)}")
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")

    elif file_extension in ['.txt', '.md']:
        # Plain text files (including Markdown)
        try:
            text = file_content.decode('utf-8')
            logger.info(f"Extracted {len(text)} chars from text file")
            return text
        except UnicodeDecodeError:
            # Try other encodings
            for encoding in ['latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    text = file_content.decode(encoding)
                    logger.info(f"Extracted {len(text)} chars using {encoding} encoding")
                    return text
                except UnicodeDecodeError:
                    continue
            logger.error("Could not decode text file with any supported encoding")
            raise ValueError("Could not decode text file with any supported encoding")

    else:
        logger.error(f"Unsupported file type: {file_extension}")
        raise ValueError(f"Unsupported file type: {file_extension}. Only PDF, TXT, and MD files are supported.")


def embed_crawled_document(document: Document, collection_name: str) -> None:
    """Chunk and embed a single crawled document into the web vector collection."""
    content = document.markdown_content or ""
    if not content.strip():
        logger.info(f"Skipping embedding for document {document.id} — no content")
        return
    chunks = chunk_text(content)
    if not chunks:
        return
    metadatas = [
        {
            'document_id': str(document.id),
            'page_url': document.page_url or '',
            'page_title': document.page_title or '',
            'source_type': 'url',
            'crawl_depth': document.crawl_depth,
        }
        for _ in chunks
    ]
    ids = [str(uuid.uuid4()) for _ in chunks]
    inserted = add_texts_to_collection(collection_name=collection_name, texts=chunks, metadatas=metadatas, ids=ids)
    document.vector_chunk_ids = inserted
    document.save(update_fields=['vector_chunk_ids'])
    logger.info(f"Embedded {len(inserted)} chunks for document {document.id} into '{collection_name}'")


def embed_uploaded_chunks(document: Document, chunks: List[str]) -> list[str]:
    """Embed an uploaded document's chunks into its KB collection; saves and returns the chunk IDs."""
    kb = document.kb
    metadatas = [
        {
            'document_id': str(document.id),
            'document_filename': document.filename,
            'kb_id': str(kb.id),
            'kb_name': kb.name,
            'chunk_index': i,
            'total_chunks': len(chunks),
            'source_type': document.source_type,
        }
        for i in range(len(chunks))
    ]
    # Qdrant requires pure UUIDs, not UUIDs with suffixes
    ids = [str(uuid.uuid4()) for _ in chunks]
    inserted = add_texts_to_collection(collection_name=kb.vector_collection, texts=chunks, metadatas=metadatas, ids=ids) or []
    # Saved on the document for deleting its vectors later
    document.vector_chunk_ids = inserted
    document.save(update_fields=['vector_chunk_ids'])
    return inserted


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
    """
    Split text into chunks for embedding.
    Uses RecursiveCharacterTextSplitter from LangChain.
    """
    logger.debug(f"Chunking {len(text)} chars with chunk_size={chunk_size}, overlap={chunk_overlap}")
    
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    
    chunks = text_splitter.split_text(text)
    logger.info(f"Created {len(chunks)} chunks from text")
    return chunks


def is_page_relevant(markdown_content: str, search_query: str, page_url: str = "", page_title: str = "") -> bool:
    """
    Ask the LLM a single yes/no question: does this page relate to search_query?

    The page content itself is stored verbatim — the LLM only gates it, never rewrites it.
    Fails open (returns True) on any LLM error so pages are not silently dropped.
    """
    if not markdown_content.strip():
        return False
    # ponytail: only the first 8000 chars are judged; enough for title/intro/listing, raise if long pages get misjudged
    preview = markdown_content[:8000]
    prompt = (
        "Decide if a web page contains information relevant to a search query.\n"
        f"Search query: {search_query}\n"
        f"Page title: {page_title}\nURL: {page_url}\n\n"
        f"Page content (may be truncated):\n{preview}\n\n"
        "Answer with exactly one word: YES or NO."
    )
    try:
        from agents.llm import get_llm
        content = get_llm().invoke(prompt).content
        if isinstance(content, list):  # Gemini may return content parts
            content = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        relevant = not str(content).strip().upper().startswith("NO")
        logger.info(f"Relevance check for {page_url!r} (query {search_query!r}): {'YES' if relevant else 'NO'}")
        return relevant
    except Exception:
        logger.exception(f"Relevance check failed for {page_url} — keeping page")
        return True


@shared_task
def run_ingestion_job(job_id: str) -> None:
    """
    Process a document ingestion job:
    1. Extract text from the file (PDF, DOC, etc.)
    2. Chunk the text
    3. Generate embeddings and store in vector database (Qdrant)
    """
    logger.info(f"Starting ingestion job {job_id}")
    job = None
    document = None
    
    try:
        # Get the job
        job = IngestionJob.objects.select_related('document', 'kb').get(id=job_id)
        document = job.document
        logger.info(f"Processing job {job_id} for document {document.id if document else 'None'} in KB {job.kb.name}")

        if not document:
            logger.error(f"Job {job_id} has no document associated")
            job.status = IngestionJobStatus.FAILED.value
            job.error = 'No document associated with this job'
            job.save()
            return

        # Update job status to processing
        job.status = IngestionJobStatus.PROCESSING.value
        job.started_at = timezone.now()
        job.progress = 10
        job.save()
        logger.debug(f"Job {job_id}: status set to PROCESSING")

        # Step 1: Extract text from file
        logger.info(f"Job {job_id}: Step 1 - Extracting text from file")
        job.progress = 20
        job.save()
        
        try:
            extracted_text = extract_text_from_file(document)
            if not extracted_text or not extracted_text.strip():
                raise ValueError("No text could be extracted from the document")
            logger.info(f"Job {job_id}: Extracted {len(extracted_text)} characters")
        except Exception as e:
            logger.error(f"Job {job_id}: Text extraction failed: {str(e)}")
            raise ValueError(f"Failed to extract text: {str(e)}")

        # Step 2: Chunk the text
        logger.info(f"Job {job_id}: Step 2 - Chunking text")
        job.progress = 40
        job.save()
        
        text_chunks = chunk_text(extracted_text)
        if not text_chunks:
            logger.error(f"Job {job_id}: No chunks created from document")
            raise ValueError("No text chunks were created from the document")
        logger.info(f"Job {job_id}: Created {len(text_chunks)} chunks")

        # Step 3: Store in vector database (Qdrant)
        logger.info(f"Job {job_id}: Step 3 - Storing {len(text_chunks)} chunks in Qdrant collection '{job.kb.vector_collection}'")
        job.progress = 80
        job.save()

        try:
            inserted_ids = embed_uploaded_chunks(document, text_chunks)

            if not inserted_ids:
                logger.error(f"Job {job_id}: No chunks were inserted into vector database")
                raise ValueError("Failed to insert chunks into vector database")

            logger.info(f"Job {job_id}: Successfully inserted {len(inserted_ids)} chunks into Qdrant")

            # Track embedding token usage
            try:
                from usage.utils import track_token_usage, estimate_tokens
                total_embedding_tokens = sum(estimate_tokens(chunk) for chunk in text_chunks)
                track_token_usage(
                    str(job.kb.project_id),
                    "embed",
                    total_embedding_tokens,
                    {
                        "document_id": str(document.id),
                        "document_filename": document.filename,
                        "num_chunks": len(text_chunks)
                    },
                    cost_usd=Decimal(0),  # self-hosted embeddings
                )
                logger.debug(f"Job {job_id}: Tracked {total_embedding_tokens} embedding tokens")
            except Exception as e:
                # Don't fail ingestion if token tracking fails
                logger.warning(f"Job {job_id}: Failed to track embedding token usage: {e}")
                
        except Exception as e:
            logger.error(f"Job {job_id}: Failed to store embeddings in Qdrant: {str(e)}")
            raise ValueError(f"Failed to store embeddings in Qdrant: {str(e)}")

        # Mark as completed
        job.status = IngestionJobStatus.COMPLETED.value
        job.progress = 100
        job.completed_at = timezone.now()
        job.save()

        # Update document status
        document.status = 'completed'
        document.save()
        
        logger.info(f"Job {job_id}: COMPLETED successfully - {len(text_chunks)} chunks ingested")

    except Exception as e:
        logger.error(f"Job {job_id}: FAILED with error: {str(e)}")
        # Mark job as failed
        try:
            if job:
                job.status = IngestionJobStatus.FAILED.value
                job.error = str(e)
                job.save()

            # Update document status
            if document:
                document.status = 'failed'
                document.error_message = str(e)
                document.save()
        except Exception as save_error:
            # If we can't even save the error, log it
            logger.error(f"Job {job_id}: Failed to save error state: {str(save_error)}")


MAX_CRAWL_PAGES = 50  # total pages per crawl, parent included
CRAWL_CONCURRENCY = 5  # browser pages / LLM checks in flight at once
SKIP_LINK_EXTENSIONS = (
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico',
    '.zip', '.rar', '.mp3', '.mp4', '.avi', '.mov', '.css', '.js', '.xml',
)


def normalize_crawl_url(href: str, base_url: str) -> str | None:
    """Resolve href against base_url, drop the #fragment, and return None for links not worth crawling."""
    if not href:
        return None
    url, _ = urldefrag(urljoin(base_url, href.strip()))
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return None
    if parsed.netloc.lower() != urlparse(base_url).netloc.lower():
        return None
    if parsed.path.lower().endswith(SKIP_LINK_EXTENSIONS):
        return None
    return url.rstrip('/')


def _extract_page_fields(result, url: str) -> dict:
    """Pull the fields we persist from a crawl4ai result (same logic for parent and child pages)."""
    md = result.markdown
    markdown_content = (
        getattr(md, 'fit_markdown', '') or getattr(md, 'raw_markdown', '') or (str(md) if md else '')
    )
    # metadata['title'] can be present but None — `or` covers both missing and null
    page_title = ((result.metadata or {}).get('title') or url)[:500]
    links = result.links or {}
    return {
        'page_title': page_title,
        'markdown_content': markdown_content,
        'images_metadata': make_absolute_image_urls((result.media or {}).get('images', []), url),
        'links_metadata': {
            'internal': links.get('internal', []),
            'external': links.get('external', []),
        },
    }


def _delete_previous_crawl_output(parent_doc: Document, web_collection: str) -> None:
    """Make crawl re-runs (e.g. via recover_stuck_jobs) idempotent: drop old child docs and stale vectors."""
    from agents.vector_store import delete_vectors_from_collection

    old_children = list(Document.objects.filter(parent_crawl_id=parent_doc))
    for doc in [parent_doc] + old_children:
        if doc.vector_chunk_ids:
            try:
                delete_vectors_from_collection(web_collection, doc.vector_chunk_ids)
            except Exception as e:
                logger.warning(f"Crawl: failed to delete old vectors for document {doc.id}: {e}")
    if parent_doc.vector_chunk_ids:
        parent_doc.vector_chunk_ids = []
        parent_doc.save(update_fields=['vector_chunk_ids'])
    for child in old_children:
        child.vector_chunk_ids = []  # already removed from the web collection above
        child.delete()
    if old_children:
        logger.info(f"Crawl: removed {len(old_children)} child documents from a previous run of {parent_doc.id}")


# Soft limit fires before recover_stuck_jobs considers the job stuck, so a slow crawl is
# marked failed instead of being re-queued while still running.
@shared_task(
    soft_time_limit=max(settings.STUCK_JOB_THRESHOLD_MINUTES - 5, 5) * 60,
    time_limit=max(settings.STUCK_JOB_THRESHOLD_MINUTES - 2, 6) * 60,
)
def crawl_website(url: str, depth: int, kb_id: str, parent_doc_id: str, job_id: str | None = None) -> None:
    """
    Crawl a website using crawl4ai and store each page as a separate Document.

    Args:
        url: The starting URL to crawl
        depth: Maximum depth to crawl (1-3); depth 1 = the start page only
        kb_id: Knowledge base ID
        parent_doc_id: Parent document ID to link child pages
        job_id: Optional IngestionJob ID to track progress
    """
    logger.info(f"Starting web crawl: url={url}, depth={depth}, kb_id={kb_id}, parent_doc_id={parent_doc_id}")

    parent_doc = None
    kb = None
    job = None

    try:
        # Get the parent document and knowledge base
        parent_doc = Document.objects.select_related('kb').get(id=parent_doc_id)
        kb = parent_doc.kb
        search_query = parent_doc.search_query  # "" if not set
        web_collection = kb.vector_collection + "_web"
        logger.info(f"Crawl: Found parent document {parent_doc_id} in KB '{kb.name}'")

        # Get ingestion job if provided
        if job_id:
            try:
                job = IngestionJob.objects.get(id=job_id)
                job.status = IngestionJobStatus.PROCESSING.value
                job.started_at = timezone.now()
                job.progress = 10
                job.save()
                logger.debug(f"Crawl: Tracking progress via job {job_id}")
            except IngestionJob.DoesNotExist:
                logger.warning(f"Crawl: IngestionJob {job_id} not found, continuing without job tracking")
                job = None

        # Update parent status to processing
        parent_doc.status = 'processing'
        parent_doc.save()
        logger.debug(f"Crawl: Parent document status set to 'processing'")

        _delete_previous_crawl_output(parent_doc, web_collection)

        if job:
            job.progress = 20
            job.save()

        # Run the async crawl in a synchronous context
        logger.info(f"Crawl: Starting async crawl for {url} with depth {depth}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(
                _crawl_website_async(url, depth, kb, parent_doc, search_query, job.id if job else None)
            )
        finally:
            loop.close()

        if job:
            job.refresh_from_db(fields=['progress'])
            job.progress = 80
            job.save()

        # Update parent document with results
        if result['success']:
            parent_doc.status = 'completed'
            parent_doc.page_url = url
            parent_doc.page_title = result.get('page_title', url)
            parent_doc.markdown_content = result.get('markdown_content', '')
            parent_doc.images_metadata = result.get('images_metadata', [])
            parent_doc.links_metadata = result.get('links_metadata', {})

            markdown_len = len(result.get('markdown_content', ''))
            child_count = result.get('child_count', 0)
            logger.info(f"Crawl COMPLETED: {url} - {markdown_len} chars markdown, {child_count} child pages")

            # Embed parent + child pages into the web vector collection (in parallel)
            docs_to_embed = [parent_doc] + list(
                Document.objects.filter(parent_crawl_id=parent_doc, status='completed')
            )
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor() as executor:
                futures = {executor.submit(embed_crawled_document, doc, web_collection): doc for doc in docs_to_embed}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.warning(f"Embedding failed for document {futures[future].id}: {e}")

            # Mark job as completed
            if job:
                job.status = IngestionJobStatus.COMPLETED.value
                job.progress = 100
                job.completed_at = timezone.now()
                job.save()
        else:
            error_msg = result.get('error', 'Unknown error during crawl')
            parent_doc.status = 'failed'
            parent_doc.error_message = error_msg
            logger.error(f"Crawl FAILED: {url} - {error_msg}")

            # Mark job as failed
            if job:
                job.status = IngestionJobStatus.FAILED.value
                job.error = error_msg
                job.save()

        parent_doc.save()

    except Exception as e:
        if isinstance(e, SoftTimeLimitExceeded):
            e = RuntimeError("Crawl exceeded its time limit — try a smaller depth")
        logger.error(f"Crawl EXCEPTION for {url}: {str(e)}")
        # Mark parent as failed
        if parent_doc:
            parent_doc.status = 'failed'
            parent_doc.error_message = str(e)
            parent_doc.save()

        # Mark job as failed
        if job:
            try:
                job.status = IngestionJobStatus.FAILED.value
                job.error = str(e)
                job.save()
            except Exception as save_error:
                logger.error(f"Crawl: Failed to save error state for job {job_id}: {str(save_error)}")


async def _crawl_website_async(
    url: str,
    depth: int,
    kb: KnowledgeBase,
    parent_doc: Document,
    search_query: str = "",
    job_id=None,
) -> dict:
    """
    Breadth-first crawl with crawl4ai: level 0 is the start page, each further level follows
    internal links of the previous one, up to `depth` levels and MAX_CRAWL_PAGES pages total.

    Returns:
        dict with success status and the parent page fields (child pages are saved as Documents)
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError:
        logger.error("Async crawl: crawl4ai is not installed")
        return {
            'success': False,
            'error': 'crawl4ai is not installed. Please install it with: pip install crawl4ai'
        }

    from asgiref.sync import sync_to_async

    try:
        logger.debug(f"Async crawl: Initializing crawler for {url}")

        # Use chromium as it's installed via playwright, not chrome
        browser_config = BrowserConfig(
            browser_type="chromium",
            headless=True,
            verbose=False
        )

        crawler_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for_images=True,
            screenshot=False,
            pdf=False,
            verbose=False,
            markdown_generator=DefaultMarkdownGenerator()
        )

        semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)
        pages_done = 0
        child_count = 0
        start_url = normalize_crawl_url(url, url) or url
        seen = {start_url}

        async def report_progress() -> None:
            # Crawl phase maps to 30-80% of the job; denominator = pages discovered so far
            if job_id:
                progress = 30 + int(50 * pages_done / max(len(seen), 1))
                await sync_to_async(IngestionJob.objects.filter(id=job_id).update)(progress=progress)

        async def fetch(page_url: str) -> tuple[dict, bool] | None:
            """Crawl one page and run the relevance check. Returns (fields, relevant) or None on failure."""
            nonlocal pages_done
            async with semaphore:
                try:
                    result = await crawler.arun(url=page_url, config=crawler_config)
                    if not result.success:
                        logger.warning(f"Async crawl: Page failed - {page_url}: {result.error_message}")
                        return None
                    fields = _extract_page_fields(result, page_url)
                    relevant = True
                    if search_query:
                        # Blocking LLM call → thread, so the event loop keeps driving other pages
                        relevant = await asyncio.to_thread(
                            is_page_relevant, fields['markdown_content'], search_query, page_url, fields['page_title']
                        )
                    return fields, relevant
                except Exception as e:
                    logger.warning(f"Async crawl: Exception crawling {page_url}: {e}")
                    return None
                finally:
                    pages_done += 1
                    await report_progress()

        async def crawl_child(page_url: str, level: int) -> dict | None:
            """Crawl and persist one child page. Returns its fields (for link discovery) or None."""
            nonlocal child_count
            fetched = await fetch(page_url)
            if not fetched:
                return None
            fields, relevant = fetched
            if not relevant:
                logger.info(f"Async crawl: Skipping irrelevant page (links still followed): {page_url}")
                return fields
            try:
                await sync_to_async(Document.objects.create)(
                    kb=kb,
                    source_type='url',
                    uri=page_url,
                    page_url=page_url,
                    parent_crawl_id=parent_doc,
                    crawl_depth=level,
                    status='completed',
                    **fields,
                )
                child_count += 1
                logger.info(f"Async crawl: Child page saved (level {level}) - '{fields['page_title']}' ({len(fields['markdown_content'])} chars)")
            except Exception as e:
                logger.warning(f"Async crawl: Failed to save child page {page_url}: {e}")
            return fields

        async with AsyncWebCrawler(config=browser_config) as crawler:
            logger.info(f"Async crawl: Crawling parent URL {url}")
            fetched = await fetch(url)
            if not fetched:
                return {'success': False, 'error': f"Failed to crawl URL: {url}"}

            parent_fields, parent_relevant = fetched
            logger.info(
                f"Async crawl: Parent page '{parent_fields['page_title']}' - {len(parent_fields['markdown_content'])} chars, "
                f"{len(parent_fields['images_metadata'])} images, "
                f"{len(parent_fields['links_metadata']['internal'])} internal links, "
                f"{len(parent_fields['links_metadata']['external'])} external links"
            )

            frontier = [(url, parent_fields)]
            for level in range(1, depth):
                next_urls = []
                for base_url, fields in frontier:
                    for link in fields['links_metadata']['internal']:
                        if len(seen) >= MAX_CRAWL_PAGES:
                            break
                        link_url = normalize_crawl_url(link.get('href', ''), base_url)
                        if link_url and link_url not in seen:
                            seen.add(link_url)
                            next_urls.append(link_url)
                if not next_urls:
                    break

                logger.info(f"Async crawl: Level {level} - crawling {len(next_urls)} pages (concurrency={CRAWL_CONCURRENCY})")
                results = await asyncio.gather(*[crawl_child(u, level) for u in next_urls])
                frontier = [(u, f) for u, f in zip(next_urls, results) if f]

            logger.info(f"Async crawl: Saved {child_count} child pages ({len(seen)} URLs visited)")

            if not parent_relevant:
                # Keep the parent record (it anchors the children) but don't index irrelevant content
                parent_fields['markdown_content'] = ""
            return {'success': True, 'child_count': child_count, **parent_fields}

    except Exception as e:
        logger.error(f"Async crawl: Exception - {str(e)}")
        return {
            'success': False,
            'error': f"Crawl failed: {str(e)}"
        }


@shared_task
def recover_stuck_jobs() -> None:
    """
    Find IngestionJobs stuck in 'processing' (i.e. the worker was killed mid-task)
    and re-queue them. A job is considered stuck if it has been in 'processing' status
    for more than STUCK_THRESHOLD_MINUTES without completing.
    """
    threshold = timezone.now() - timedelta(minutes=settings.STUCK_JOB_THRESHOLD_MINUTES)

    stuck_jobs = IngestionJob.objects.filter(
        status=IngestionJobStatus.PROCESSING.value,
        started_at__lt=threshold,
    ).select_related('document', 'kb')

    if not stuck_jobs.exists():
        logger.debug("recover_stuck_jobs: no stuck jobs found")
        return

    logger.warning(f"recover_stuck_jobs: found {stuck_jobs.count()} stuck job(s) — re-queuing")

    for job in stuck_jobs:
        logger.warning(f"Recovering stuck job {job.id} (started_at={job.started_at})")

        # Reset document back to pending so it shows correctly in the UI
        if job.document:
            job.document.status = 'pending'
            job.document.error_message = ''
            job.document.save(update_fields=['status', 'error_message'])

        # Reset job state
        job.status = IngestionJobStatus.PENDING.value
        job.started_at = None
        job.progress = 0
        job.error = ''
        job.save(update_fields=['status', 'started_at', 'progress', 'error'])

        # Re-dispatch using stored kwargs when available
        task_kwargs = job.celery_task_kwargs
        try:
            if task_kwargs.get('task') == 'crawl_website':
                kwargs = task_kwargs.get('kwargs', {})
                crawl_website.delay(**kwargs)
                logger.info(f"Re-queued crawl_website for job {job.id}")
            else:
                # Default: file ingestion task
                run_ingestion_job.delay(str(job.id))
                logger.info(f"Re-queued run_ingestion_job for job {job.id}")
        except Exception as e:
            logger.error(f"Failed to re-queue job {job.id}: {e}")
            job.status = IngestionJobStatus.FAILED.value
            job.error = f"Recovery failed: {e}"
            job.save(update_fields=['status', 'error'])


def _finalize_product_image(image: ProductImage) -> bytes | None:
    """Bytes to embed. A fresh upload (under tmp/) is replaced by its display + thumb WebP variants first;
    None when the row was deleted meanwhile (its variants are removed again)."""
    try:
        data = read_capped(image.file.name, MAX_PRODUCT_IMAGE_BYTES)  # never pull an oversized object into memory
    except ObjectTooLarge as exc:
        raise NotAnImage("image too large") from exc
    if not image.file.name.startswith("tmp/"):
        return data  # already final: a re-embed
    display, thumb = make_variants(data, MAX_PRODUCT_IMAGE_BYTES)
    name = default_storage.save(product_image_path(image.product.kb, str(image.id), "webp"), ContentFile(display))
    default_storage.save(thumb_key(name), ContentFile(thumb))
    if not ProductImage.objects.filter(id=image.id).update(file=name):
        delete_keys(name, thumb_key(name), image.file.name)
        return None
    delete_keys(image.file.name)
    image.file.name = name
    return display


@shared_task
def embed_product_image(image_id: str) -> None:
    """Embed one product photo into the KB's image collection. A row deleted while queued is a no-op."""
    image = ProductImage.objects.select_related("product__kb__project").filter(id=image_id).first()
    if image is None:
        return
    kb = image.product.kb
    try:
        data = _finalize_product_image(image)
        if data is None:
            return
        vector = embed_image(load_image(data, MAX_PRODUCT_IMAGE_BYTES))
        upsert_image(kb.image_collection, image_id, vector, str(image.product_id))
    except Exception as exc:
        logger.warning(f"Embedding product image {image_id} failed: {exc}")
        if isinstance(exc, NotAnImage) and image.file.name.startswith("tmp/"):
            delete_keys(image.file.name)
        ProductImage.objects.filter(id=image_id).update(status="failed", error=type(exc).__name__)
        return
    if not ProductImage.objects.filter(id=image_id).update(status="ready", error=""):
        # Deleted while being embedded: drop the point we just wrote, and do not meter it
        delete_images(kb.image_collection, [image_id])
        return
    record_image_usage(str(kb.project_id), "index", image_id=image_id)


@shared_task
def cleanup_abandoned_uploads() -> None:
    """Drop uploads presigned over an hour ago and never confirmed (tab closed, PUT failed): their rows, their R2
    objects, and products left with no photos at all."""
    cutoff = timezone.now() - timedelta(hours=1)
    images = list(ProductImage.objects.filter(status="uploading", created_at__lt=cutoff))
    product_ids = {i.product_id for i in images}
    for img in images:
        delete_keys(img.file.name)
    ProductImage.objects.filter(id__in=[i.id for i in images]).delete()  # queryset delete: no Qdrant points exist yet
    Product.objects.filter(id__in=product_ids, images__isnull=True).delete()
    docs = list(Document.objects.filter(status="uploading", created_at__lt=cutoff))
    for doc in docs:
        delete_keys(doc.file.name if doc.file else "")
    Document.objects.filter(id__in=[d.id for d in docs]).delete()
    if images or docs:
        logger.info(f"Cleaned up {len(images)} abandoned photo and {len(docs)} document uploads")


@shared_task
def extract_product_candidates(kb_id: str, document_ids: list[str] | None = None) -> int:
    """Read a KB's pages and write review candidates (never products). Returns how many were created."""
    from .product_extract import extract_candidates

    # no retries: a retried page would make new candidate ids and charge again (spec 8.4)
    kb = KnowledgeBase.objects.select_related("project").get(id=kb_id)
    return extract_candidates(kb, document_ids)


@shared_task
def fetch_product_images(product_id: str, image_srcs: list[str]) -> None:
    """Download imported product photos (runs in the worker, never in the import request)."""
    from .image_fetch import attach_product_images

    product = Product.objects.select_related("kb__project").filter(id=product_id).first()
    if product is None:
        return
    attach_product_images(product, image_srcs)


SYNC_LOCK_TIMEOUT = timedelta(minutes=30)  # a worker that died mid-sync gives its claim up after this
SYNC_RETRY_BASE = timedelta(minutes=5)  # a failed scheduled sync retries after 5, 10, 20 ... minutes, never past the interval


def _claim_sync(source_id) -> bool:
    """Take the sync lock in one UPDATE. False while another run holds it and has not gone stale."""
    now = timezone.now()
    return ProductSource.objects.filter(id=source_id).filter(
        Q(sync_locked_at__isnull=True) | Q(sync_locked_at__lt=now - SYNC_LOCK_TIMEOUT)
    ).update(sync_locked_at=now) == 1


def _finish_sync(source, started, error: str) -> None:
    """Save the outcome, release the lock, and set when the next run is due."""
    source.refresh_from_db()  # the user may have toggled the schedule while the sync ran
    interval = ProductSource.SCHEDULE_INTERVALS[source.schedule]
    if error:
        source.sync_failures += 1
        retry = min(SYNC_RETRY_BASE * 2 ** min(source.sync_failures - 1, 6), interval)
    else:
        source.sync_failures = 0
        source.last_synced_at = started
        retry = interval
    source.last_error = error
    source.next_sync_at = timezone.now() + retry if source.auto_sync_enabled else None
    source.sync_locked_at = None
    source.save(update_fields=["last_error", "last_synced_at", "sync_failures", "next_sync_at", "sync_locked_at"])


def _read_source_rows(source) -> tuple[list[str], list[dict]]:
    """(headers, rows) from the source's live data: a public Sheet or the REST API with its saved request."""
    from .product_api import decrypt_blob, fetch_api_table
    from .product_sources import fetch_sheet_csv, parse_table, sheet_export_url

    if source.kind == "api":
        saved = decrypt_blob(source.secret_ciphertext)
        return fetch_api_table({**source.api_config, "body": saved.get("body")}, saved.get("secret", ""))
    return parse_table("sheet.csv", fetch_sheet_csv(sheet_export_url(source.sheet_url)))


@shared_task
def sync_product_source(source_id: str) -> dict:
    """Re-read one Sheet source with its saved mapping. Failures are saved on the source, never raised.
    One run per source at a time: a second run returns already_syncing without touching anything. Safe to retry: the apply is idempotent."""
    from .product_sources import SheetNotPublic, TableError, apply_rows, check_headers

    source = ProductSource.objects.select_related("kb").filter(id=source_id, kind__in=["sheet", "api"]).first()
    if source is None:
        return {"error": "not_found"}
    if not _claim_sync(source.id):
        return {"error": "already_syncing", "message": "This source is already syncing"}
    started = timezone.now()
    error, result = "", {}
    try:
        headers, rows = _read_source_rows(source)
        check_headers(source.mapping, headers)
        result = apply_rows(source.kb, source, rows, source.mapping, started)
    except SheetNotPublic as e:
        error = f"sheet_not_public: {e}"
    except (TableError, ValueError) as e:
        error = f"{getattr(e, 'code', 'invalid_sheet')}: {e}"
    except Exception:
        logger.exception("Product source %s sync crashed", source.id)
        error = "sync_failed: The sync failed unexpectedly. It will be retried."
    _finish_sync(source, started, error)
    if error:
        code, _, message = error.partition(": ")
        return {"error": code, "message": message}
    return result


@shared_task
def sync_product_sources() -> None:
    """Every 5 minutes: run each enabled Sheet or API source that is due. One failing source must not stop the others."""
    now = timezone.now()
    due = ProductSource.objects.filter(kind__in=["sheet", "api"], auto_sync_enabled=True).filter(
        Q(next_sync_at__isnull=True) | Q(next_sync_at__lte=now)).values_list("id", flat=True)
    for source_id in list(due):
        try:
            # One task per source: a slow API cannot hold up the others, and the per-source lock stops overlap
            sync_product_source.delay(str(source_id))
        except Exception:
            logger.exception("Product source %s could not be queued", source_id)
