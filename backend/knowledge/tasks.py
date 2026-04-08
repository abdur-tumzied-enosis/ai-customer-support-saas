import os
import uuid
import asyncio
import logging
from datetime import timedelta
from typing import List
from io import BytesIO
from urllib.parse import urlparse, urljoin
from celery import shared_task
from django.utils import timezone
from django.core.files.storage import default_storage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .models import IngestionJob, Document, IngestionJobStatus, DocumentFileType, KnowledgeBase
from agents.vector_store import add_texts_to_collection

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


def extract_relevant_content_with_agent(
    markdown_content: str,
    search_query: str,
    page_url: str = "",
    page_title: str = "",
) -> str | None:
    """
    Use a LangChain tool-calling agent to intelligently extract content relevant to search_query.

    The agent has three tools that operate on the FULL page content with no character limit:
      - get_page_outline(): returns all headings and total chunk count so the agent can plan
      - read_chunk(index): reads any 4000-char chunk by index, giving full access to the page
      - search_in_page(keyword): keyword search across the full content with 300-char context windows

    The agent decides which sections to read, follows up on promising sections, and compiles
    all relevant content — never missing anything due to a hard truncation limit.

    Returns:
        str: Compiled relevant content in markdown.
        None: If the page contains no relevant content after thorough investigation.
        On agent failure: returns original content so the page is still indexed (graceful degradation).
    """
    # All imports at the top of the function body — any ImportError here is caught
    # by our own try/except below, NOT by the crawl4ai ImportError handler upstream.
    try:
        from agents.llm import get_llm
        from langchain.agents import create_agent
        from langchain_core.tools import tool
        import re as _re
    except ImportError as e:
        logger.warning(f"Agent extraction imports failed: {e} — falling back to full content")
        return markdown_content

    CHUNK_SIZE = 4000
    chunks = [markdown_content[i:i + CHUNK_SIZE] for i in range(0, len(markdown_content), CHUNK_SIZE)]
    total_chunks = len(chunks)

    # Build a heading outline the agent can use to plan its reading
    headings = _re.findall(r'^#{1,6}\s+.+', markdown_content, _re.MULTILINE)
    outline = "\n".join(headings[:60]) if headings else "(no headings — plain content)"

    @tool
    def get_page_outline() -> str:
        """Get the page structure: all headings, total character count, and number of readable chunks."""
        return (
            f"Page title: {page_title}\n"
            f"URL: {page_url}\n"
            f"Total content: {len(markdown_content)} characters in {total_chunks} chunk(s)\n\n"
            f"Headings:\n{outline}"
        )

    @tool
    def read_chunk(chunk_index: int) -> str:
        """Read a specific 4000-character chunk of the page content in full detail.
        chunk_index is an integer from 0 to (total_chunks - 1).
        Use this to read any part of the page without truncation."""
        if chunk_index < 0 or chunk_index >= total_chunks:
            return f"Invalid chunk_index {chunk_index}. Valid range: 0 to {total_chunks - 1}."
        return f"[Chunk {chunk_index + 1} of {total_chunks}]\n\n{chunks[chunk_index]}"

    @tool
    def search_in_page(keyword: str) -> str:
        """Search for a keyword anywhere across the full page content.
        Returns up to 5 matching excerpts, each with 300 characters of surrounding context.
        Use this to quickly locate sections about specific topics before reading them in full."""
        results = []
        content_lower = markdown_content.lower()
        kw_lower = keyword.lower()
        pos = 0
        while len(results) < 5:
            idx = content_lower.find(kw_lower, pos)
            if idx == -1:
                break
            start = max(0, idx - 300)
            end = min(len(markdown_content), idx + len(keyword) + 300)
            chunk_num = idx // CHUNK_SIZE
            results.append(f"[near chunk {chunk_num}] ...{markdown_content[start:end]}...")
            pos = idx + 1
        if not results:
            return f"Keyword '{keyword}' not found anywhere in page content."
        return f"Found {len(results)} occurrence(s) of '{keyword}':\n\n" + "\n\n---\n\n".join(results)

    system_prompt = (
        f"You are a precise content extraction agent. Extract ALL content from a web page that is relevant to a given search query.\n\n"
        f"Search query: {search_query}\n\n"
        f"Your process:\n"
        f"1. Call get_page_outline() first to understand the page structure and total size\n"
        f"2. Use search_in_page() with relevant keywords to quickly locate promising sections\n"
        f"3. Use read_chunk() to read the full content of any section that may contain relevant information\n"
        f"4. For pages with multiple chunks, be thorough — scan all areas that could contain relevant content\n"
        f"5. Compile ALL relevant extracted content into clean, well-structured markdown\n\n"
        f"Rules:\n"
        f"- Never truncate or summarise — preserve the full relevant content verbatim where possible\n"
        f"- When in doubt, include the full surrounding section rather than risk cutting off context\n"
        f"- Only respond with the single word IRRELEVANT if after a thorough investigation there is truly nothing related to the search query\n"
        f"- Your final response must be ONLY the extracted markdown content, or the word IRRELEVANT — no commentary"
    )

    agent_tools = [get_page_outline, read_chunk, search_in_page]
    llm = get_llm(provider="openrouter")
    agent = create_agent(model=llm, tools=agent_tools, system_prompt=system_prompt)

    try:
        result = agent.invoke({
            "messages": [{"role": "user", "content": "Extract all content relevant to the search query from this page."}]
        })
        logger.info('result from agent: ' + str(result)[:500])  # Log the first 500 chars of the result for debugging
        # Extract text from the last message in the result
        output = ""
        if isinstance(result, dict):
            messages = result.get("messages", [])
            if messages:
                last = messages[-1]
                content = getattr(last, "content", None) or (last.get("content") if isinstance(last, dict) else None)
                output = str(content).strip() if content else ""
        if not output or output.upper().startswith("IRRELEVANT"):
            logger.info(f"Agent found no relevant content for query {search_query!r}: {page_url}")
            return None
        logger.info(f"Agent extracted {len(output)} chars from {page_url} for query {search_query!r}")
        return output
    except Exception as e:
        logger.exception(f"Agent extraction failed for {page_url} — full traceback:")
        return markdown_content


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

        # Step 3: Prepare metadata for each chunk
        logger.info(f"Job {job_id}: Step 3 - Preparing metadata")
        job.progress = 60
        job.save()
        
        collection_name = job.kb.vector_collection
        metadatas = []
        chunk_ids = []
        
        for i, chunk in enumerate(text_chunks):
            metadata = {
                'document_id': str(document.id),
                'document_filename': document.filename,
                'kb_id': str(job.kb.id),
                'kb_name': job.kb.name,
                'chunk_index': i,
                'total_chunks': len(text_chunks),
                'source_type': document.source_type,
            }
            metadatas.append(metadata)
            # Generate unique UUID for each chunk (Qdrant requires pure UUIDs, not UUIDs with suffixes)
            chunk_ids.append(str(uuid.uuid4()))

        # Step 4: Store in vector database (Qdrant)
        logger.info(f"Job {job_id}: Step 4 - Storing {len(text_chunks)} chunks in Qdrant collection '{collection_name}'")
        job.progress = 80
        job.save()
        
        try:
            inserted_ids = add_texts_to_collection(
                collection_name=collection_name,
                texts=text_chunks,
                metadatas=metadatas,
                ids=chunk_ids
            )
            
            if not inserted_ids:
                logger.error(f"Job {job_id}: No chunks were inserted into vector database")
                raise ValueError("Failed to insert chunks into vector database")
            
            logger.info(f"Job {job_id}: Successfully inserted {len(inserted_ids)} chunks into Qdrant")
            
            # Save the inserted chunk IDs to the document for easy deletion later
            document.vector_chunk_ids = inserted_ids
            document.save()
            
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
                    }
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


@shared_task
def crawl_website(url: str, depth: int, kb_id: str, parent_doc_id: str, job_id: str | None = None) -> None:
    """
    Crawl a website using crawl4ai and store each page as a separate Document.
    
    Args:
        url: The starting URL to crawl
        depth: Maximum depth to crawl (1-3)
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
        
        if job:
            job.progress = 20
            job.save()
        
        # Run the async crawl in a synchronous context
        logger.info(f"Crawl: Starting async crawl for {url} with depth {depth}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            if job:
                job.progress = 30
                job.save()
            result = loop.run_until_complete(_crawl_website_async(url, depth, kb, parent_doc, search_query))
        finally:
            loop.close()
        
        if job:
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
            web_collection = kb.vector_collection + "_web"
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


async def _crawl_website_async(url: str, depth: int, kb: KnowledgeBase, parent_doc: Document, search_query: str = "") -> dict:
    """
    Async function to crawl website using crawl4ai.
    
    Returns:
        dict with success status and crawl results
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        from asgiref.sync import sync_to_async
        
        logger.debug(f"Async crawl: Initializing crawler for {url}")
        
        # Configure browser and crawler
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
        
        crawled_urls = set()
        child_count = 0
        
        async with AsyncWebCrawler(config=browser_config) as crawler:
            # Crawl the starting URL first (parent page)
            logger.info(f"Async crawl: Crawling parent URL {url}")
            result = await crawler.arun(url=url, config=crawler_config)
            
            if not result.success:
                logger.error(f"Async crawl: Parent URL failed - {result.error_message}")
                return {
                    'success': False,
                    'error': f"Failed to crawl URL: {result.error_message}"
                }
            
            # Extract data from parent page
            # fit_markdown requires a content filter; fall back to raw_markdown when empty
            if hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown:
                markdown_content = result.markdown.fit_markdown
            elif hasattr(result.markdown, 'raw_markdown') and result.markdown.raw_markdown:
                markdown_content = result.markdown.raw_markdown
            else:
                markdown_content = str(result.markdown)
            page_title = result.metadata.get('title', url) if result.metadata else url
            internal_links = result.links.get('internal', []) if result.links else []
            external_links = result.links.get('external', []) if result.links else []
            
            # Convert relative image URLs to absolute
            raw_images = result.media.get('images', []) if result.media else []
            images_metadata = make_absolute_image_urls(raw_images, url)
            
            logger.info(f"Async crawl: Parent page '{page_title}' - {len(markdown_content)} chars, {len(images_metadata)} images, {len(internal_links)} internal links, {len(external_links)} external links")

            # If a search query was specified, use agent to extract only relevant content
            if search_query:
                try:
                    extracted = extract_relevant_content_with_agent(markdown_content, search_query, url, page_title)
                    markdown_content = extracted if extracted is not None else ""
                except Exception as agent_err:
                    logger.warning(f"Async crawl: Agent extraction error for parent {url}: {agent_err} — using full content")

            parent_data = {
                'success': True,
                'page_title': page_title,
                'markdown_content': markdown_content,
                'images_metadata': images_metadata,
                'links_metadata': {
                    'internal': internal_links,
                    'external': external_links
                }
            }
            
            crawled_urls.add(url)
            
            # If depth > 1, crawl child pages
            if depth > 1:
                # Limit the number of child pages to crawl (prevent too many pages)
                max_child_pages = 50
                links_to_crawl = [
                    ld for ld in internal_links[:max_child_pages]
                    if ld.get('href', '') and ld.get('href', '') not in crawled_urls
                ]

                logger.info(f"Async crawl: Crawling {len(links_to_crawl)} child pages in parallel (depth={depth})")

                async def crawl_child(link_data: dict) -> bool:
                    """Crawl a single child page and persist it. Returns True on success."""
                    link_url = link_data.get('href', '')
                    try:
                        child_result = await crawler.arun(url=link_url, config=crawler_config)
                        if not child_result.success:
                            logger.warning(f"Async crawl: Child page failed - {link_url}: {child_result.error_message}")
                            return False

                        child_title = child_result.metadata.get('title', link_url) if child_result.metadata else link_url
                        child_markdown = child_result.markdown.raw_markdown if hasattr(child_result.markdown, 'raw_markdown') else str(child_result.markdown)

                        # Convert relative image URLs to absolute
                        child_raw_images = child_result.media.get('images', []) if child_result.media else []
                        child_images = make_absolute_image_urls(child_raw_images, link_url)

                        # If a search query was specified, use agent to extract only relevant content
                        if search_query:
                            try:
                                child_extracted = extract_relevant_content_with_agent(
                                    child_markdown, search_query, link_url, child_title
                                )
                                if child_extracted is None:
                                    logger.info(f"Async crawl: Skipping irrelevant child page: {link_url}")
                                    return False
                                child_markdown = child_extracted
                            except Exception as agent_err:
                                logger.warning(f"Async crawl: Agent extraction error for child {link_url}: {agent_err} — using full content")

                        await sync_to_async(Document.objects.create)(
                            kb=kb,
                            source_type='url',
                            uri=link_url,
                            page_url=link_url,
                            page_title=child_title,
                            markdown_content=child_markdown,
                            images_metadata=child_images,
                            links_metadata={
                                'internal': child_result.links.get('internal', []) if child_result.links else [],
                                'external': child_result.links.get('external', []) if child_result.links else []
                            },
                            parent_crawl_id=parent_doc,
                            crawl_depth=1,
                            status='completed'
                        )
                        logger.info(f"Async crawl: Child page saved - '{child_title}' ({len(child_markdown)} chars)")
                        return True
                    except Exception as e:
                        logger.warning(f"Async crawl: Exception crawling child page {link_url}: {str(e)}")
                        return False

                results = await asyncio.gather(*[crawl_child(ld) for ld in links_to_crawl])
                child_count = sum(results)
                logger.info(f"Async crawl: Completed crawling {child_count} child pages")
            
            parent_data['child_count'] = child_count
            return parent_data
            
    except ImportError:
        logger.error("Async crawl: crawl4ai is not installed")
        return {
            'success': False,
            'error': 'crawl4ai is not installed. Please install it with: pip install crawl4ai'
        }
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
    from django.conf import settings
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
