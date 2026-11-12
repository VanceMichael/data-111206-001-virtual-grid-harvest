FROM mcr.microsoft.com/playwright/python:v1.56.0-noble@sha256:a7f6cf3ae520c9d670ad956572c13747ed5abdbba5123a01526f873ed1662528
WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install --no-cache-dir --break-system-packages -r requirements.txt
RUN useradd --create-home --uid 1800 collector
COPY . .
RUN chown -R collector:collector /app
USER collector
CMD ["python3", "app.py", "demo"]
