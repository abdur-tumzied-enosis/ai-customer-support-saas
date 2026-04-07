# Ollama Setup for Auto-Magic-Pro

This directory contains the Ollama configuration for local LLM inference.

## Model: gemma3:1b-it-qat

A lightweight 1B parameter model optimized for instruction following.

## Usage

### Start with Docker Compose

```bash
docker-compose up ollama -d
```

### Check if model is loaded

```bash
# Check Ollama status
curl http://localhost:11434/api/tags

# Test the model
curl http://localhost:11434/api/generate -d '{
  "model": "gemma3:1b-it-qat",
  "prompt": "Hello, how are you?",
  "stream": false
}'
```

### Using in Python (Crawl4AI)

```python
from crawl4ai import LLMConfig

llm_config = LLMConfig(
    provider="ollama/gemma3:1b-it-qat",
    api_token=None,
    base_url="http://localhost:11434",  # or "http://ollama:11434" inside Docker
    temperature=0.1
)
```

### Change Model

Set the `OLLAMA_MODEL` environment variable:

```bash
OLLAMA_MODEL=llama3.2 docker-compose up ollama -d
```

## Resource Requirements

- **RAM**: ~2GB for gemma3:1b-it-qat
- **Disk**: ~1GB for model files
- **GPU**: Optional (uses CPU if no GPU available)

## GPU Support (Optional)

For NVIDIA GPU acceleration, uncomment the GPU section in `docker-compose.yml`.





