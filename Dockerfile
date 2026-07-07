FROM alpine:3.20

RUN apk add --no-cache \
    ffmpeg \
    python3 \
    py3-pip \
    yt-dlp \
    bash

RUN pip3 install flask

COPY restream.sh /restream.sh
COPY app.py /app.py
RUN chmod +x /restream.sh

CMD ["python3", "/app.py"]
