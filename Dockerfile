# Stage 1: parse the PDF into data.json (pdfplumber never ships in the final image)
FROM python:3.15-slim AS parse
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY parse_pdf.py textnorm.py nutrition_table.pdf products.lock ./
RUN python parse_pdf.py nutrition_table.pdf data.json --check

# Stage 2: runtime
FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
# the server doesn't need the PDF parser
RUN grep -v pdfplumber requirements.txt > server-requirements.txt \
 && pip install --no-cache-dir -r server-requirements.txt
COPY server.py textnorm.py index.html styles.css app.js ./
COPY --from=parse /build/data.json .
RUN useradd --system --uid 10001 app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/categories', timeout=2)"
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
