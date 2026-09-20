ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY static ./static
EXPOSE 8085
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8085"]
