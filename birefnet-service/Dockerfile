FROM python:3.11-slim

WORKDIR /app

# Proxy for build-time downloads
ARG HTTP_PROXY=http://host.docker.internal:7897
ARG HTTPS_PROXY=http://host.docker.internal:7897
ENV http_proxy=$HTTP_PROXY https_proxy=$HTTPS_PROXY

# Install CPU-only PyTorch first
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Keep proxy for runtime model download (cleared after first run via env)
ENV BIREFNET_MODEL_ID=ZhengPeng7/BiRefNet
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=300s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
