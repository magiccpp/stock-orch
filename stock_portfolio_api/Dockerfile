FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1 \
	FLASK_HOST=0.0.0.0 \
	FLASK_PORT=5001

WORKDIR /app

RUN apt-get update \
	&& apt-get install -y --no-install-recommends build-essential \
	&& rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
	&& pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

EXPOSE 5001

CMD ["python", "-u", "app.py"]
