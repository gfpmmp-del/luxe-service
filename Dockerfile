FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY presets ./presets

# data/ и data/capes/ создаются самим сервером.
# ВНИМАНИЕ: на бесплатном тарифе Render диск не сохраняется,
# поэтому data/ живёт до первого перезапуска сервиса.
RUN mkdir -p data/capes

CMD ["python", "server.py"]
