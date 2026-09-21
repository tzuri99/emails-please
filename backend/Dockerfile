# Build context is the project root (see docker-compose.yml), so the image
# can carry both the application and the dataset it reads.
FROM python:3.12-slim

# poppler/libgl back pdfplumber's page rasterisation, which the OCR path
# needs to turn a scanned page into an image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
# Baked in so the deployed API has the inbox without a mounted volume.
# Compose overlays the same path read-only for local development.
COPY data /data

ENV DATA_SOURCE=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
