FROM python:3.12-slim-bookworm

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

ENV PORT=8080

EXPOSE 8080

CMD ["bash", "-c", "python -m playwright install firefox && python -u main.py"]
