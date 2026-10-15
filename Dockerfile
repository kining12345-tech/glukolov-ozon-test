FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PORT=8000
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install --with-deps chromium firefox

RUN echo "=== PLAYWRIGHT BROWSER INSTALLATION ===" \
    && python -m playwright --version \
    && ls -la /ms-playwright \
    && find /ms-playwright -maxdepth 4 -type f \
       \( -name chrome -o -name headless_shell -o -name firefox \) -print

COPY main.py .

EXPOSE 8000

CMD ["python", "-u", "main.py"]
