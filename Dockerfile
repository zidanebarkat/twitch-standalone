FROM alpine:3.20

RUN apk add --no-cache ffmpeg python3 py3-pip py3-flask yt-dlp bash

COPY restream.sh /restream.sh
COPY app.py /app.py

RUN chmod +x /restream.sh /app.py

CMD ["python3", "/app.py"]
