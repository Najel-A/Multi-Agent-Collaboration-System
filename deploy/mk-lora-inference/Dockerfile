FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ADAPTER_PATH=/adapters \
    MODEL_PROFILE=mistral \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install -r requirements.txt \
 && python -c "import torch; assert hasattr(torch.nn.Module, 'set_submodule'), torch.__version__"

COPY app.py .

# Copy mistral adapter files into image
COPY adapters/mistral/ /adapters/

EXPOSE 8000

CMD ["sh", "-c", "python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
