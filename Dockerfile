FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY govuk_corpus ./govuk_corpus
COPY webapp ./webapp

RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV CORPUS_DB=/data/pilot.db

EXPOSE 8600

CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8600"]
