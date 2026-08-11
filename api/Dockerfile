FROM python:3.12-slim

# Install system tools needed for PDF/image processing
RUN apt-get update && apt-get install -y \
    pdftk \
    imagemagick \
    img2pdf \
    ocrmypdf \
    tesseract-ocr \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Fix ImageMagick PDF policy (disabled by default in Debian)
RUN sed -i 's/rights="none" pattern="PDF"/rights="read|write" pattern="PDF"/' /etc/ImageMagick-6/policy.xml || true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
