FROM python:3.12-slim
WORKDIR /app
COPY deploy/gcp/gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY deploy/gcp/gateway/main.py .
ENV TENANTS_NAMESPACE=gemcode-tenants
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
