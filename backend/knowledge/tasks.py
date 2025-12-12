import os
import uuid
import asyncio
import logging
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
        logger.info(f"Crawl: Found parent document {parent_doc_id} in KB '{kb.name}'")
        
        # Get ingestion job if provided
        if job_id:
            try:
                job = IngestionJob.objects.get(id=job_id)
                job.status = IngestionJobStatus.PROCESSING.value
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
            result = loop.run_until_complete(_crawl_website_async(url, depth, kb, parent_doc))
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


async def _crawl_website_async(url: str, depth: int, kb: KnowledgeBase, parent_doc: Document) -> dict:
    """
    Async function to crawl website using crawl4ai.
    
    Returns:
        dict with success status and crawl results
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
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
            verbose=False
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
            markdown_content = result.markdown.fit_markdown if hasattr(result.markdown, 'fit_markdown') else str(result.markdown)
            page_title = result.metadata.get('title', url) if result.metadata else url
            internal_links = result.links.get('internal', []) if result.links else []
            external_links = result.links.get('external', []) if result.links else []
            
            # Convert relative image URLs to absolute
            raw_images = result.media.get('images', []) if result.media else []
            images_metadata = make_absolute_image_urls(raw_images, url)
            
            logger.info(f"Async crawl: Parent page '{page_title}' - {len(markdown_content)} chars, {len(images_metadata)} images, {len(internal_links)} internal links, {len(external_links)} external links")
            
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
                links_to_crawl = internal_links[:max_child_pages]
                
                logger.info(f"Async crawl: Crawling up to {len(links_to_crawl)} child pages (depth={depth})")
                
                for i, link_data in enumerate(links_to_crawl):
                    link_url = link_data.get('href', '')
                    if not link_url or link_url in crawled_urls:
                        continue
                    
                    try:
                        # Crawl child page
                        logger.info(f"Async crawl: Crawling child {i+1}/{len(links_to_crawl)}: {link_url}")
                        child_result = await crawler.arun(url=link_url, config=crawler_config)
                        
                        if child_result.success:
                            child_title = child_result.metadata.get('title', link_url) if child_result.metadata else link_url
                            child_markdown = child_result.markdown.raw_markdown if hasattr(child_result.markdown, 'raw_markdown') else str(child_result.markdown)
                            
                            # Convert relative image URLs to absolute
                            child_raw_images = child_result.media.get('images', []) if child_result.media else []
                            child_images = make_absolute_image_urls(child_raw_images, link_url)
                            
                            # Create child document using sync_to_async wrapper
                            child_doc = await sync_to_async(Document.objects.create)(
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
                            
                            crawled_urls.add(link_url)
                            child_count += 1
                            logger.info(f"Async crawl: Child page saved - '{child_title}' ({len(child_markdown)} chars)")
                        else:
                            logger.warning(f"Async crawl: Child page failed - {link_url}: {child_result.error_message}")
                            
                    except Exception as e:
                        # Log error but continue with other pages
                        logger.warning(f"Async crawl: Exception crawling child page {link_url}: {str(e)}")
                        continue
                
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
