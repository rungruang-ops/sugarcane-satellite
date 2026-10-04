# เบิ่งไฮ่ (BerngHai) LINE webhook + LIFF API — production image (Railway / any container host).
# Runtime config comes from env vars only (see docs/railway.md); no secrets are baked in.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CANESAT_CACHE_DIR=/tmp/canesat-cache

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --system --uid 10001 app
USER app

# Railway injects $PORT; serve.py binds 0.0.0.0:$PORT and runs DB migrations first.
EXPOSE 8080
CMD ["python", "-m", "canesat.line.serve"]
