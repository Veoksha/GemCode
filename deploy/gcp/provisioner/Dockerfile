FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY deploy/gcp/provisioner/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY deploy/gcp/provisioner/main.py /app/main.py
COPY deploy/gcp/k8s/tenant/manifest.yaml.template /config/manifest.yaml.template

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
