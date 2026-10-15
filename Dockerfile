FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

CMD ["python", "-u", "main.py"]
