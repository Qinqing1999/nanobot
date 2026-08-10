# ===========================================================================
# Subject Segmentation Service — Dockerfile (onnxruntime + silueta, ~43 MB model)
#
# Direct ONNX inference, no rembg/scipy/numba. Runtime memory ~200-300 MB.
# Build:  docker build -t seg-service:latest seg-service/
# Run:    docker run -d --name seg-service -p 8001:8001 seg-service:latest
# ===========================================================================

FROM python:3.11-slim

WORKDIR /app

# Install runtime Python dependencies (Tsinghua PyPI mirror for speed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# Copy pre-downloaded silueta model (~43 MB) and application code
COPY models/silueta.onnx models/
COPY app.py .

ENV MODEL_PATH=models/silueta.onnx
ENV ORT_THREADS=1
ENV MAX_DIMENSION=512

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
