#!/bin/bash

# Start Ollama server in background
ollama serve &

# Wait for Ollama to be ready
echo "Waiting for Ollama to start..."
sleep 5

# Check if Ollama is responding
until curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do
    echo "Waiting for Ollama API..."
    sleep 2
done

echo "Ollama is ready!"

# Pull the model if not already present
MODEL_NAME="${OLLAMA_MODEL:-gemma3:1b-it-qat}"

echo "Checking for model: $MODEL_NAME"

if ! ollama list | grep -q "$MODEL_NAME"; then
    echo "Pulling model: $MODEL_NAME (this may take a few minutes)..."
    ollama pull "$MODEL_NAME"
    echo "Model $MODEL_NAME downloaded successfully!"
else
    echo "Model $MODEL_NAME already exists."
fi

echo "Ollama is ready with model: $MODEL_NAME"

# Keep container running
wait





