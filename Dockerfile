FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PORT=8000
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN python -m playwright install --with-deps chromium

RUN echo "=== INSTALLED PLAYWRIGHT BROWSERS ===" \
    && python -m playwright --version \
    && ls -la /ms-playwright \
    && find /ms-playwright -maxdepth 3 -type f \
       \( -name chrome -o -name headless_shell \) -print

COPY main.py .

EXPOSE 8000

CMD ["python", "-u", "main.py"]
