from flask import Flask, Response, request, jsonify
import subprocess, os, signal, sys, threading, time, json

app = Flask(__name__)

stream_active = False
ffmpeg_proc = None
bg_thread = None
log_buffer = []
log_lock = threading.Lock()
bg_lock = threading.Lock()

DEFAULTS = {
    'source_url': 'https://www.twitch.tv/inoxtag',
    'output_url': '',
    'output_mode': 'youtube',
    'backup_list': 'https://www.twitch.tv/xqc\nhttps://www.twitch.tv/summit1g\nhttps://www.twitch.tv/lirik',
    'bitrate': '192k',
}
config_path = '/tmp/panel_config.json'

def load_config():
    try:
        with open(config_path) as f:
            return json.load(f)
    except:
        return dict(DEFAULTS)

def save_config(cfg):
    with open(config_path, 'w') as f:
        json.dump(cfg, f)

def wr(msg):
    with log_lock:
        ts = time.strftime('%H:%M:%S')
        log_buffer.append(f'[{ts}] {msg}')
        if len(log_buffer) > 500:
            log_buffer[:] = log_buffer[-500:]
    print(f'[twitch] {msg}', flush=True)

def kill_ffmpeg():
    global ffmpeg_proc
    p = ffmpeg_proc
    if p and p.poll() is None:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            p.wait(timeout=5)
        except:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except:
                pass
    ffmpeg_proc = None

def get_hls_url(source):
    try:
        r = subprocess.run(['yt-dlp', '-g', '--socket-timeout', '15',
            '--retries', '2', source],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip()]
            for l in lines:
                if '.m3u8' in l or '.mp4' in l:
                    return l
            return lines[-1] if lines else None
    except Exception as e:
        wr(f'yt-dlp error: {e}')
    return None

def bg_loop(cfg):
    global stream_active, ffmpeg_proc
    source = cfg['source_url']
    output = cfg['output_url']
    mode = cfg.get('output_mode', 'youtube')
    bitrate = cfg.get('bitrate', '192k')
    backup_text = cfg.get('backup_list', '')
    backups = [l.strip() for l in backup_text.split('\n') if l.strip()]

    if not output:
        wr('No output URL configured')
        stream_active = False
        return

    all_sources = [source] + backups

    while stream_active:
        found = False
        for src in all_sources:
            if not stream_active:
                break
            if not src:
                continue
            wr(f'Checking: {src}')
            hls = get_hls_url(src)
            if not hls:
                wr(f'Not live: {src}')
                continue

            wr(f'HLS: {hls[:80]}...')
            wr('Starting ffmpeg...')

            cmd = ['ffmpeg', '-nostdin', '-re',
                '-timeout', '30000000',
                '-analyzeduration', '50M', '-probesize', '50M',
                '-fflags', '+discardcorrupt',
                '-max_reload', '999',
                '-i', hls,
                '-map', '0:v', '-map', '0:a',
                '-c:v', 'copy', '-c:a', 'copy',
                '-f', 'flv', output]

            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     preexec_fn=os.setsid)
            except Exception as e:
                wr(f'ffmpeg failed: {e}')
                continue

            ffmpeg_proc = p
            found = True

            for line in iter(p.stdout.readline, b''):
                if not stream_active:
                    kill_ffmpeg()
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if text and 'size=' in text and 'time=' in text:
                    pass
                elif text:
                    wr(text)
            p.wait()
            rc = p.returncode
            wr(f'Ended (exit {rc})')
            ffmpeg_proc = None
            if not stream_active:
                break
            wr('Source ended, next...')
            time.sleep(2)

        if not stream_active:
            break
        if not found:
            wr('No live sources, retry 30s...')
            time.sleep(30)

    wr('Stopped')
    ffmpeg_proc = None
    stream_active = False

@app.route('/')
def index():
    return HTML_PANEL

@app.route('/start')
def start_stream():
    global stream_active, bg_thread
    with bg_lock:
        if stream_active:
            return jsonify({'ok': False, 'error': 'Already live'})
        cfg = load_config()
        if not cfg.get('source_url') or not cfg.get('output_url'):
            return jsonify({'ok': False, 'error': 'Missing source or output URL'})
        wr('=== GOING LIVE ===')
        stream_active = True
        bg_thread = threading.Thread(target=bg_loop, args=(cfg,), daemon=True)
        bg_thread.start()
    return jsonify({'ok': True})

@app.route('/stop')
def stop_stream():
    global stream_active
    with bg_lock:
        if not stream_active:
            return jsonify({'ok': False, 'error': 'Not live'})
        wr('=== STOPPING ===')
        stream_active = False
        kill_ffmpeg()
    return jsonify({'ok': True})

@app.route('/config', methods=['POST'])
def update_config():
    data = request.get_json(force=True)
    cfg = load_config()
    for k in DEFAULTS:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    wr('Config saved')
    return jsonify({'ok': True, 'config': cfg})

@app.route('/status')
def get_status():
    return jsonify({
        'live': stream_active,
        'config': load_config()
    })

@app.route('/logs')
def get_logs():
    with log_lock:
        return '\n'.join(log_buffer[-100:]), 200, {'Content-Type': 'text/plain'}

@app.route('/resolve')
def resolve_source():
    cfg = load_config()
    hls = get_hls_url(cfg['source_url'])
    if hls:
        return jsonify({'ok': True, 'hls': hls, 'source': cfg['source_url']})
    return jsonify({'ok': False, 'error': 'Not live'}), 400

HTML_PANEL = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Twitch Restream Panel</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0d1117;color:#c9d1d9}
.container{max-width:1200px;margin:0 auto;padding:20px}
h1{font-size:24px;margin-bottom:20px;color:#fff;display:flex;align-items:center;gap:12px}
h1 small{font-size:13px;color:#8b949e}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:20px}
.card h2{font-size:16px;margin-bottom:16px;color:#f0f6fc}
.form-group{margin-bottom:14px}
.form-group label{display:block;font-size:13px;color:#8b949e;margin-bottom:4px}
.form-group input,.form-group select,.form-group textarea{width:100%;padding:8px 12px;background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;font-size:14px;font-family:inherit}
.form-group input:focus,.form-group select:focus,.form-group textarea:focus{outline:none;border-color:#58a6ff}
.form-group textarea{resize:vertical;min-height:80px}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.btn{display:inline-flex;align-items:center;gap:8px;padding:10px 24px;border:none;border-radius:6px;font-size:15px;font-weight:600;cursor:pointer;transition:.2s;text-decoration:none}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-green{background:#238636;color:#fff}
.btn-green:hover:not(:disabled){background:#2ea043}
.btn-red{background:#da3633;color:#fff}
.btn-red:hover:not(:disabled){background:#f85149}
.btn-blue{background:#1f6feb;color:#fff}
.btn-blue:hover:not(:disabled){background:#388bfd}
.btn-grey{background:#21262d;color:#c9d1d9;border:1px solid #30363d}
.btn-grey:hover:not(:disabled){background:#30363d}
.btn-sm{padding:6px 14px;font-size:13px}
.actions{display:flex;gap:12px;align-items:center;margin-top:16px;flex-wrap:wrap}
.status-dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}
.status-dot.live{background:#3fb950;box-shadow:0 0 8px #3fb950}
.status-dot.stopped{background:#f85149}
.status-text{font-size:14px;font-weight:600}
.log-box{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:12px;height:400px;overflow-y:auto;font-family:monospace;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-all}
.log-box .info{color:#8b949e}
.log-box .warn{color:#d29922}
.log-box .err{color:#f85149}
.log-box .ok{color:#3fb950}
.status-bar{display:flex;align-items:center;gap:16px;padding:12px 16px;background:#0d1117;border:1px solid #30363d;border-radius:6px;margin-bottom:16px;flex-wrap:wrap}
.status-bar .stat{font-size:13px;color:#8b949e}
.status-bar .stat strong{color:#c9d1d9}
@media(max-width:768px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="container">
<h1>📺 Twitch Restream Panel <small>v1</small></h1>
<div class="status-bar"><span><span class="status-dot" id="statusDot"></span><span class="status-text" id="statusText">Checking...</span></span></div>
<div class="grid">
  <div class="card">
    <h2>Configuration</h2>
    <form id="configForm">
      <div class="form-group">
        <label>Twitch Streamer URL</label>
        <input type="url" name="source_url" id="source_url" placeholder="https://www.twitch.tv/streamer">
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>Output Mode</label>
          <select name="output_mode" id="output_mode" onchange="toggleMode()">
            <option value="youtube">YouTube RTMPS</option>
            <option value="kick">Kick RTMP</option>
            <option value="twitch">Twitch RTMP</option>
            <option value="custom">Custom</option>
          </select>
        </div>
        <div class="form-group">
          <label>Audio Bitrate</label>
          <select name="bitrate" id="bitrate">
            <option value="128k">128 kbps</option>
            <option value="192k" selected>192 kbps</option>
            <option value="256k">256 kbps</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Output URL (stream key)</label>
        <input type="text" name="output_url" id="output_url">
      </div>
      <div class="form-group">
        <label>Backup Streamers (one per line)</label>
        <textarea name="backup_list" id="backup_list"></textarea>
      </div>
      <div style="margin-top:12px;display:flex;gap:8px">
        <button type="button" class="btn btn-blue btn-sm" onclick="saveConfig()">💾 Save</button>
        <button type="button" class="btn btn-grey btn-sm" onclick="testSource()">🔍 Test Source</button>
      </div>
      <div id="testResult" style="margin-top:8px;font-size:12px;color:#8b949e"></div>
    </form>
  </div>
  <div class="card">
    <h2>Control & Logs</h2>
    <div class="actions">
      <button class="btn btn-green" id="btnGoLive" onclick="goLive()">▶ Go Live</button>
      <button class="btn btn-red" id="btnStop" onclick="stopStream()" disabled>⏹ Stop</button>
      <button class="btn btn-grey btn-sm" onclick="clearLogs()">🗑 Clear</button>
    </div>
    <div class="log-box" id="logBox">Waiting...</div>
  </div>
</div>
</div>
<script>
function toggleMode() {
  const m = document.getElementById('output_mode').value;
  const u = document.getElementById('output_url');
  if (m === 'youtube') u.placeholder = 'rtmps://a.rtmp.youtube.com:443/live2/KEY';
  else if (m === 'kick') u.placeholder = 'rtmp://ingest.kick.com/live/KEY';
  else if (m === 'twitch') u.placeholder = 'rtmp://live.twitch.tv/app/STREAM_KEY';
  else u.placeholder = 'rtmp:// or rtmps://...';
}
function applyForm(c) {
  if (!c) return;
  for (const [k,v] of Object.entries(c)) {
    const el = document.getElementById(k);
    if (el) el.value = v;
  }
}
function readForm() {
  const d = {};
  document.querySelectorAll('#configForm input,#configForm select,#configForm textarea').forEach(el => {
    d[el.name] = el.value;
  });
  return d;
}
function saveConfig() {
  fetch('/config', {method:'POST', body:JSON.stringify(readForm()), headers:{'Content-Type':'application/json'}})
    .then(r=>r.json()).then(d=>addLog('Config saved','ok')).catch(e=>addLog('Save failed','err'));
}
function testSource() {
  document.getElementById('testResult').textContent = 'Checking...';
  fetch('/resolve').then(r=>r.json()).then(d=>{
    document.getElementById('testResult').textContent = d.ok ? '✓ Live — HLS resolved' : '✗ Not live';
  }).catch(()=>document.getElementById('testResult').textContent='✗ Failed');
}
function goLive() {
  saveConfig();
  document.getElementById('btnGoLive').disabled = true;
  addLog('Starting...','info');
  fetch('/start').then(r=>r.json()).then(d=>{
    if(!d.ok) { addLog('Error: '+d.error,'err'); document.getElementById('btnGoLive').disabled = false; }
  }).catch(e=>{ addLog('Start failed','err'); document.getElementById('btnGoLive').disabled = false; });
}
function stopStream() {
  document.getElementById('btnStop').disabled = true;
  addLog('Stopping...','warn');
  fetch('/stop').then(r=>r.json()).then(d=>addLog(d.ok?'Stopped':'Error: '+d.error, d.ok?'warn':'err'));
}
function clearLogs() { document.getElementById('logBox').innerHTML = ''; }
function addLog(msg,cls='info') {
  const box = document.getElementById('logBox');
  box.innerHTML += '<span class="'+cls+'">['+new Date().toLocaleTimeString()+'] '+msg+'</span>\n';
  box.scrollTop = box.scrollHeight;
}
function updateStatus() {
  fetch('/status').then(r=>r.json()).then(d=>{
    const dot = document.getElementById('statusDot');
    const txt = document.getElementById('statusText');
    if(d.live) {
      dot.className = 'status-dot live';
      txt.textContent = '● LIVE';
      document.getElementById('btnGoLive').disabled = true;
      document.getElementById('btnStop').disabled = false;
    } else {
      dot.className = 'status-dot stopped';
      txt.textContent = '○ Stopped';
      document.getElementById('btnGoLive').disabled = false;
      document.getElementById('btnStop').disabled = true;
    }
    if(d.config) applyForm(d.config);
  }).catch(()=>{});
}
function fetchLogs() {
  fetch('/logs').then(r=>r.text()).then(t=>{
    const box = document.getElementById('logBox');
    if(t) box.innerHTML = t;
    box.scrollTop = box.scrollHeight;
  }).catch(()=>{});
}
updateStatus();
setInterval(updateStatus, 3000);
setInterval(fetchLogs, 2000);
toggleMode();
</script>
</body>
</html>'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
