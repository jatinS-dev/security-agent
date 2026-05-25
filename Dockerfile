FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY policies ./policies
COPY agents ./agents

RUN python -m pip install --upgrade pip \
    && python -m pip install .

EXPOSE 8080

CMD ["sentient-api", "--policy", "policies/default_policy.json", "--host", "0.0.0.0", "--port", "8080"]
