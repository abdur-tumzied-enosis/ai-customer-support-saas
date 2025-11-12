import os
import uuid
from typing import List
from io import BytesIO
from celery import shared_task
from django.utils import timezone
from django.core.files.storage import default_storage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .models import IngestionJob, Document, IngestionJobStatus, DocumentFileType
from agents.vector_store import add_texts_to_collection


def extract_text_from_file(document: Document) -> str:
    """
    Extract text content from supported file types.
    Supports: PDF (using pdfplumber), TXT, MD (Markdown)
    """
    if not document.file:
        raise ValueError("Document has no file attached")

    file_path = document.file.name
    filename = str(document.filename) if document.filename else ''
    file_extension = os.path.splitext(filename)[1].lower()

    # Read file content
    if default_storage.exists(file_path):
        file_content = default_storage.open(file_path, mode='rb').read()
    else:
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
                return '\n\n'.join(text_parts)
        except ImportError:
            raise ImportError("pdfplumber is not installed. Install it: pip install pdfplumber")
        except Exception as e:
            raise ValueError(f"Failed to extract text from PDF: {str(e)}")

    elif file_extension in ['.txt', '.md']:
        # Plain text files (including Markdown)
        try:
            return file_content.decode('utf-8')
        except UnicodeDecodeError:
            # Try other encodings
            for encoding in ['latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    return file_content.decode(encoding)
                except UnicodeDecodeError:
                    continue
            raise ValueError("Could not decode text file with any supported encoding")

    else:
        raise ValueError(f"Unsupported file type: {file_extension}. Only PDF, TXT, and MD files are supported.")


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> List[str]:
    """
    Split text into chunks for embedding.
    Uses RecursiveCharacterTextSplitter from LangChain.
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""]
    )
    
    chunks = text_splitter.split_text(text)
    return chunks


@shared_task
def run_ingestion_job(job_id: str) -> None:
    """
    Process a document ingestion job:
    1. Extract text from the file (PDF, DOC, etc.)
    2. Chunk the text
    3. Generate embeddings and store in vector database (Qdrant)
    """
    job = None
    document = None
    
    try:
        # Get the job
        job = IngestionJob.objects.select_related('document', 'kb').get(id=job_id)
        document = job.document

        if not document:
            job.status = IngestionJobStatus.FAILED.value
            job.error = 'No document associated with this job'
            job.save()
            return

        # Update job status to processing
        job.status = IngestionJobStatus.PROCESSING.value
        job.progress = 10
        job.save()

        # Step 1: Extract text from file
        job.progress = 20
        job.save()
        
        try:
            extracted_text = extract_text_from_file(document)
            if not extracted_text or not extracted_text.strip():
                raise ValueError("No text could be extracted from the document")
        except Exception as e:
            raise ValueError(f"Failed to extract text: {str(e)}")

        # Step 2: Chunk the text
        job.progress = 40
        job.save()
        
        text_chunks = chunk_text(extracted_text)
        if not text_chunks:
            raise ValueError("No text chunks were created from the document")

        # Step 3: Prepare metadata for each chunk
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
                raise ValueError("Failed to insert chunks into vector database")
            
            # Save the inserted chunk IDs to the document for easy deletion later
            document.vector_chunk_ids = inserted_ids
            document.save()
                
        except Exception as e:
            raise ValueError(f"Failed to store embeddings in Qdrant: {str(e)}")

        # Mark as completed
        job.status = IngestionJobStatus.COMPLETED.value
        job.progress = 100
        job.completed_at = timezone.now()
        job.save()

        # Update document status
        document.status = 'completed'
        document.save()

    except Exception as e:
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
        except Exception:
            # If we can't even save the error, just pass
            pass
