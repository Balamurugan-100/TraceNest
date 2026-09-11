FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY sample-django-lite-app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy SDK source & install in development mode
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN pip install --no-cache-dir -e .

# Copy application code
COPY sample-django-lite-app/app.py /app/app.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["gunicorn", "app:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2"]
