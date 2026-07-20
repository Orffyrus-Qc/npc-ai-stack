FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py .
RUN useradd --system --no-create-home --uid 65532 orchestrator \
    && chown -R orchestrator /app
USER orchestrator
CMD ["python", "main.py"]
