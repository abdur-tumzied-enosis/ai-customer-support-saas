FROM python:3.12.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	POETRY_VIRTUALENVS_CREATE=false 

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

RUN pip install crawl4ai==0.7.8
RUN crawl4ai-setup

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN playwright install chrome
COPY backend/req_v2.txt /app/req_v2.txt
RUN pip install --no-cache-dir -r /app/req_v2.txt
COPY backend /app

# CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--log-level", "info", "core.wsgi:application"]
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

