#!/bin/bash
YT_URL="${YT_URL:-https://www.twitch.tv/inoxtag}"
OUTPUT_URL="${OUTPUT_URL:-}"

BACKUP_STREAMERS=(
    "https://www.twitch.tv/xqc"
    "https://www.twitch.tv/summit1g"
    "https://www.twitch.tv/lirik"
    "https://www.twitch.tv/timthetatman"
    "https://www.twitch.tv/sodapoppin"
    "https://www.twitch.tv/asmongold"
    "https://www.twitch.tv/cohhcarnage"
    "https://www.twitch.tv/chlorinequeen"
    "https://www.twitch.tv/itmejp"
    "https://www.twitch.tv/towelliee"
    "https://www.twitch.tv/sacriel"
    "https://www.twitch.tv/avoidingthepuddle"
    "https://www.twitch.tv/quinngabletv"
    "https://www.twitch.tv/gamesdonequick"
    "https://www.twitch.tv/twitchrivals"
)

echo "[twitch] Starting..."
echo "[twitch] PRIMARY_URL=$YT_URL"
echo "[twitch] OUTPUT_URL=$OUTPUT_URL"

if [ -z "$YT_URL" ]; then echo "Missing YT_URL"; exit 1; fi
if [ -z "$OUTPUT_URL" ]; then echo "Missing OUTPUT_URL"; exit 1; fi

try_stream() {
    local url="$1"
    echo "[twitch] Trying: $url" >&2
    local result
    result=$(yt-dlp -g --socket-timeout 15 --retries 2 "$url" 2>/dev/null | tail -1)
    if [ -n "$result" ] && ! echo "$result" | grep -qi "error\|warn"; then
        echo "$result"
        return 0
    fi
    return 1
}

get_live_url() {
    local result
    result=$(try_stream "$YT_URL") && { echo "$result"; return 0; }

    local shuffled=("${BACKUP_STREAMERS[@]}")
    for i in "${!shuffled[@]}"; do
        local j=$((RANDOM % (i + 1)))
        local tmp="${shuffled[$i]}"
        shuffled[$i]="${shuffled[$j]}"
        shuffled[$j]="$tmp"
    done

    for backup in "${shuffled[@]}"; do
        result=$(try_stream "$backup") && { echo "$result"; return 0; }
    done
    return 1
}

while true; do
    echo "[twitch] Looking for a live stream..."
    source_url=$(get_live_url)
    if [ -z "$source_url" ]; then
        echo "[twitch] No live streams found, retrying in 30s..."
        sleep 30
        continue
    fi
    echo "[twitch] Stream URL: $source_url"
    echo "[twitch] Starting ffmpeg..."

    retries=0
    max_retries=30
    while [ $retries -lt $max_retries ]; do
        fresh_url=$(get_live_url)
        if [ -z "$fresh_url" ]; then
            echo "[twitch] No live source found for retry $retries" >&2
            sleep 10
            retries=$((retries + 1))
            continue
        fi
        echo "[twitch] Connecting to $OUTPUT_URL ..." >&2
        ffmpeg -re -timeout 30000000 -analyzeduration 50M -probesize 50M \
            -protocol_whitelist "file,http,https,tcp,tls,crypto,rtmp" \
            -fflags +discardcorrupt -seekable 0 \
            -max_reload 999 \
            -i "$fresh_url" \
            -map 0:v -map 0:a -c copy -bsf:v h264_mp4toannexb \
            -f flv "$OUTPUT_URL" \
            -loglevel warning -stats 2>&1
        rc=$?
        if [ $rc -eq 0 ]; then
            echo "[twitch] Stream finished cleanly" >&2
            break
        fi
        retries=$((retries + 1))
        echo "[twitch] ffmpeg error (retry $retries/$max_retries), waiting 10s..." >&2
        sleep 10
    done
    if [ $retries -ge $max_retries ]; then
        echo "[twitch] Gave up after $max_retries retries" >&2
    fi

    echo "[twitch] Stream ended, finding next in 5s..."
    sleep 5
done
