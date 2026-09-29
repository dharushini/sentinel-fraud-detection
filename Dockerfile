# Sentinel — real-time AI banking fraud detection
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY sentinel/ ./sentinel/
COPY pytest.ini README.md ./

# a pre-trained model is committed in sentinel/artifacts/ — only train if missing
RUN test -f sentinel/artifacts/model.joblib || python -m sentinel.train

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f'http://localhost:{os.getenv(\"PORT\",\"8000\")}/health').status==200 else 1)"

CMD ["sh", "-c", "uvicorn sentinel.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
