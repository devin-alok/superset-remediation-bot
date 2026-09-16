FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir requests==2.32.3
COPY remediate.py .

ENTRYPOINT ["python", "remediate.py"]
