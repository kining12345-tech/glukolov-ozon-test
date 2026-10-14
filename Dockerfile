FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install --with-deps firefox

COPY main.py .

ENV PORT=8080

EXPOSE 8080

CMD ["python", "-u", "main.py"]
