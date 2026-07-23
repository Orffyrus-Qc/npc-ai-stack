FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
COPY agent_brain/ ./agent_brain/
COPY pest_brain/ ./pest_brain/
# fastembed/huggingface_hub cache the embedding model on first use and need a
# writable HOME - point it at a dir we control instead of relying on one.
ENV HOME=/app/.cache
RUN useradd --system --no-create-home --uid 65532 orchestrator \
    && mkdir -p /app/.cache \
    && chown -R orchestrator /app
USER orchestrator
CMD ["python", "main.py"]
