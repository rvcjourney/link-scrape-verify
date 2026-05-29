FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=4008

EXPOSE 4008

CMD ["gunicorn", "app:app", "--worker-class", "gthread", "--workers", "2", "--threads", "8", "--bind", "0.0.0.0:4008", "--timeout", "120"]
