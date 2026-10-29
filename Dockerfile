FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY examples ./examples
EXPOSE 8741
CMD ["uvicorn", "aeo.main:app", "--host", "0.0.0.0", "--port", "8741"]
