# Public deployment of the explorer — browsing, search and every skill's relation graph.
#
# It carries data/skills.json + data/relations.json (33 MB), both committed. Nothing else.
# This repository is what the site runs: it is deployed from here and nowhere else, so what
# is committed here is what is live.
# The embeddings are a gigabyte and regenerable, so they stay out; without them the site loses
# step highlighting and add-a-skill, and the server says so through /api/caps. See .dockerignore.
FROM python:3.11-slim

WORKDIR /app

# dependencies first, so a content-only change does not reinstall them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY explorer/ ./explorer/
COPY data/skills.json data/relations.json ./data/

ENV PORT=8080 PYTHONUNBUFFERED=1

# Two workers, one per core. The threads are not the concurrency story they were assumed to be:
# scanning the relation graph is CPU-bound Python, so the GIL serialises it and eight threads on
# one core measured a hard ceiling of ~5 requests a second. Parallelism has to come from
# processes. Each holds its own copy of the parsed corpus (~420 MB), which is why the machine
# needs 2 GB to run two of them — and why the count tracks the core count rather than exceeding it.
CMD exec gunicorn --bind "0.0.0.0:$PORT" \
      --workers 2 --threads 4 --timeout 120 \
      --access-logfile - --error-logfile - \
      explorer.wsgi:app
