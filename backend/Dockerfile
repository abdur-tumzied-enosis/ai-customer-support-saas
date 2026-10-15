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
RUN python -c "from sentence_transformers import SentenceTransformer, CrossEncoder; SentenceTransformer('BAAI/bge-base-en-v1.5'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
ENV HF_HUB_OFFLINE=1

COPY backend /app

# CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--log-level", "info", "core.wsgi:application"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

