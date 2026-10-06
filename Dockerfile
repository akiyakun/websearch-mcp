FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY --chmod=644 requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chmod=644 server.py usage_limits.py ./

RUN mkdir -p /data && chown 10001:10001 /data
ENV SEARCH_USAGE_DB=/data/usage.sqlite3

USER 10001:10001
EXPOSE 8000

CMD ["python", "server.py", "--http"]
