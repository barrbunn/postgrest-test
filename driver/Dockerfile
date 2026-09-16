FROM python:3.12-alpine

RUN apk add --no-cache curl postgresql16-client jq \
 && pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

CMD ["sleep", "infinity"]
