# ===========================================================================
# Subject Segmentation Service — Dockerfile (onnxruntime)
#
# Supports two model types (auto-detected from MODEL_PATH):
#   - silueta (~43 MB, 320×320, ~200-300 MB runtime)
#   - rmbg14  (~168 MB, 640×640, ~400 MB runtime)
#
# Build:  docker build -t seg-service:latest seg-service/
# Run (silueta):  docker run -d --name seg-service -p 8001:8001 seg-service:latest
# Run (rmbg14):   docker run -d --name seg-service -p 8001:8001 \
#                   -e MODEL_PATH=models/model_dynamic.onnx \
#                   -e ORT_THREADS=2 \
#                   -e MAX_DIMENSION=1024 \
#                   -v $(pwd)/rmbg14_deploy/models/model_dynamic.onnx:/app/models/model_dynamic.onnx \
#                   --mem-limit 512m \
#                   seg-service:latest
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

# Default: silueta model. Override with -e MODEL_PATH=models/model_dynamic.onnx for RMBG-1.4
ENV MODEL_PATH=models/silueta.onnx
ENV ORT_THREADS=1
ENV MAX_DIMENSION=512

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
