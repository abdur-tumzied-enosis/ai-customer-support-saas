FROM python:3.12.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	POETRY_VIRTUALENVS_CREATE=false 

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

RUN pip install crawl4ai==0.7.8
RUN crawl4ai-setup

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Bake in the embedding and reranker models (agents/vector_store.py), BM25, and the CLIP image model for product
# image search (agents/image_search.py). Loaded from the Hugging Face hub at runtime, each worker process's first
# reply spent minutes on hub checks and hit process_reply's time limit.
# HF_HUB_OFFLINE=1 below means only baked models load: changing EMBEDDING_MODEL / RERANKER_MODEL /
# IMAGE_EMBEDDING_MODEL means changing the matching RUN line and rebuilding.
# fastembed's default cache is under /tmp; pin it so the baked BM25 model is found at runtime.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('intfloat/multilingual-e5-base'); CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"
RUN python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25')"
RUN python -c "from fastembed import ImageEmbedding; ImageEmbedding('Qdrant/clip-ViT-B-32-vision')"
ENV HF_HUB_OFFLINE=1

COPY backend /app

# 4 processes x 8 threads = 32 parallel requests (widget polls are short DB reads). Each thread keeps its own
# Postgres connection (CONN_MAX_AGE), so workers x threads + Celery concurrency must stay under max_connections (100).
# Local dev: GUNICORN_CMD_ARGS=--reload in .env restores auto-reload.
CMD ["gunicorn", "-k", "gthread", "-w", "4", "--threads", "8", "--timeout", "60", "-b", "0.0.0.0:8000", "--log-level", "info", "core.wsgi:application"]

