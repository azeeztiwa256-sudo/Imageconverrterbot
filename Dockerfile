FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        libheif1 libheif-dev libavif-dev fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TEMP_DIR=/tmp/imageconv
CMD ["python", "bot.py"]
