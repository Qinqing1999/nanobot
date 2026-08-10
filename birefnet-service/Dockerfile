# ===========================================================================
# Subject Segmentation Service — Dockerfile (onnxruntime + u2netp, 4.7 MB model)
#
# Direct ONNX inference, no rembg/scipy/numba. Runtime memory ~100 MB.
# Build:  docker build -t birefnet-seg:latest birefnet-service/
# Run:    docker run -d --name birefnet -p 8001:8001 birefnet-seg:latest
# ===========================================================================

FROM python:3.11-slim

WORKDIR /app

# Install runtime Python dependencies (Tsinghua PyPI mirror for speed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# Copy pre-downloaded u2netp model (4.7 MB) and application code
COPY models/u2netp.onnx models/
COPY app.py .

ENV MODEL_PATH=models/u2netp.onnx
ENV ORT_THREADS=2

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
