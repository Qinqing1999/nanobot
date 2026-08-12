# ===========================================================================
# Subject Segmentation Service — Dockerfile (onnxruntime)
#
# Default model: RMBG-1.4 (model_dynamic.onnx, ~169 MB, 640×640, ~400 MB runtime)
# Also supports silueta (~43 MB, 320×320, ~200-300 MB runtime) via MODEL_PATH override.
#
# Build:  docker build -t seg-service:latest seg-service/
# Run (rmbg14, default):  docker run -d --name seg-service -p 8001:8001 seg-service:latest
# Run (silueta):  docker run -d --name seg-service -p 8001:8001 \
#                   -e MODEL_PATH=models/silueta.onnx \
#                   -e ORT_THREADS=1 \
#                   -e MAX_DIMENSION=512 \
#                   seg-service:latest
# ===========================================================================

FROM python:3.11-slim

WORKDIR /app

# Install runtime Python dependencies (Tsinghua PyPI mirror for speed)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# Copy pre-downloaded models and application code
# Default: RMBG-1.4 (model_dynamic.onnx, ~169 MB) for higher accuracy
# Also copy silueta (~43 MB) as a lightweight alternative
COPY models/model_dynamic.onnx models/
COPY models/silueta.onnx models/
COPY app.py .

# Default: RMBG-1.4 model. Override with -e MODEL_PATH=models/silueta.onnx for lightweight silueta
ENV MODEL_PATH=models/model_dynamic.onnx
ENV ORT_THREADS=2
ENV MAX_DIMENSION=1024

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
