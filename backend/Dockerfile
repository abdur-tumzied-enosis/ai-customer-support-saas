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

RUN playwright install chrome

# Bake in the embedding and reranker models (agents/vector_store.py). Loaded from the Hugging Face hub at runtime,
# each worker process's first reply spent minutes on hub checks and hit process_reply's time limit.
# Changing EMBEDDING_MODEL / RERANKER_MODEL means changing this line and rebuilding.
# fastembed's default cache is under /tmp; pin it so the baked BM25 model is found at runtime.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('intfloat/multilingual-e5-base'); CrossEncoder('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"
RUN python -c "from fastembed import SparseTextEmbedding; SparseTextEmbedding('Qdrant/bm25')"
ENV HF_HUB_OFFLINE=1

COPY backend /app

# CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--log-level", "info", "core.wsgi:application"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

