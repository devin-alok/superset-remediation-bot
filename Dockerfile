FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir requests==2.32.3
ENV REPORT_PATH=/reports/report.html
COPY remediate.py github_api.py devin_api.py report.py .

ENTRYPOINT ["python", "remediate.py"]
