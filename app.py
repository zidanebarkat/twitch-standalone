import subprocess
import threading
import os
import sys
import base64
import json
from flask import Flask

app = Flask(__name__)

def log(msg):
    print(f"[twitch] {msg}", flush=True)

def setup_cookies():
    cookies_b64 = os.environ.get('COOKIES_B64', '')
    if cookies_b64:
        try:
            data = json.loads(base64.b64decode(cookies_b64).decode())
            with open('/cookies.txt', 'w') as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in data:
                    domain = c.get('domain', '')
                    flag = "TRUE" if domain.startswith('.') else "FALSE"
                    path = c.get('path', '/')
                    secure = "TRUE" if c.get('secure') else "FALSE"
                    expires = str(int(c.get('expirationDate', 0)))
                    name = c.get('name', '')
                    value = c.get('value', '')
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
            log(f"Cookies saved to /cookies.txt ({len(data)} cookies)")
        except Exception as e:
            log(f"Failed to decode cookies: {e}")

def start_restream():
    yt_url = os.environ.get('YT_URL', '')
    stream_url = os.environ.get('STREAM_URL', '')
    output_url = os.environ.get('OUTPUT_URL', '')
    if not stream_url and not yt_url:
        log("Missing YT_URL or STREAM_URL")
        return
    if not output_url:
        log("Missing OUTPUT_URL")
        return
    if stream_url:
        log(f"Using direct stream URL (yt-dlp bypassed)")
    env = os.environ.copy()
    log(f"Starting restream: {yt_url}")
    while True:
        log("Launching restream.sh...")
        proc = subprocess.Popen(
            ['/restream.sh'],
            env=env,
            stdout=sys.stdout,
            stderr=sys.stderr
        )
        proc.wait()
        log(f"restream.sh exited (code {proc.returncode}), restarting in 10s...")
        import time
        time.sleep(10)

@app.route('/')
def index():
    return 'Twitch Standalone Restream running', 200

@app.route('/health')
def health():
    return 'OK', 200

if __name__ == '__main__':
    setup_cookies()
    t = threading.Thread(target=start_restream, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=8080)
