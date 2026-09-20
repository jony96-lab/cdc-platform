ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
WORKDIR /tests
CMD ["pytest", "-v", "--tb=short", "/tests"]
