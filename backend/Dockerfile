FROM python:3.12-slim

RUN useradd -m -u 1000 -s /bin/bash athena

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create mount-point directories with correct ownership before switching user
# Named volumes overlay these, inheriting the uid/gid from the image
RUN mkdir -p /app/chroma_db /app/chat_sessions /logs \
    && chown -R athena:athena /app /logs

# Pre-download ChromaDB's default embedding model AS the athena user
# At runtime, the container has no internet (internal network), so this must happen here
USER athena
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['test'])"

EXPOSE 5001

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5001/api/health')" || exit 1

CMD ["python", "server.py"]
