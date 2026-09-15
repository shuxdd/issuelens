FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir uv==0.12.7

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY backend/src ./backend/src

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/backend/src"

EXPOSE 8000

CMD ["uvicorn", "issuelens.api:app", "--host", "0.0.0.0", "--port", "8000"]

