FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip ffmpeg curl bash nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip3 install --break-system-packages --no-cache-dir flask yt-dlp

COPY restream.sh /restream.sh
COPY app.py /app.py

RUN chmod +x /restream.sh /app.py

CMD ["python3", "/app.py"]
