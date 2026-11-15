FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ORIGAMI_DATA_DIR=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends bash ca-certificates openssh-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd -g 10001 origami && useradd -u 10001 -g 10001 -d /app -s /usr/sbin/nologin origami && mkdir -p /app/data && chown origami:origami /app/data
COPY app /app/app
COPY agents /app/agents
COPY static /app/static
USER origami
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=4s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3)" || exit 1
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1","--proxy-headers","--forwarded-allow-ips","127.0.0.1"]
