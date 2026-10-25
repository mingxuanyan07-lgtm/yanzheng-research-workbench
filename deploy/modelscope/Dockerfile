FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/webapp:/app/deploy/modelscope \
    YANZHENG_DEMO_MODEL_ENABLED=0 \
    YANZHENG_DEMO_STATE_ROOT=/tmp/yanzheng-demo

WORKDIR /app
# Build only from build_bundle.py's stage directory, never from the personal checkout.
COPY runtime/webapp/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 1000 demo
COPY --chown=1000:1000 runtime/ /app/
COPY --chown=1000:1000 SOURCE-MANIFEST.json /app/SOURCE-MANIFEST.json

USER 1000:1000
EXPOSE 7860
CMD ["python", "-m", "uvicorn", "cloud_app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log"]
