FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install --with-deps chromium

COPY main.py .

EXPOSE 8080

CMD ["python", "-u", "main.py"]
