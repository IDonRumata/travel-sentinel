FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir pip --upgrade

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY src/ src/

EXPOSE 8100

CMD ["uvicorn", "src.api.app:app", "--host", "0.0.0.0", "--port", "8100"]
