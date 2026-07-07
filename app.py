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
    'cookies': '',
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
    base = ['yt-dlp', '--socket-timeout', '15', '--retries', '3']
    if os.path.exists('/cookies.txt'):
        base += ['--cookies', '/cookies.txt']
    format_tries = [
        ['--format', 'bestvideo+bestaudio/best'],
        ['--format', 'best'],
        ['--format', 'worst'],
    ]
    client_tries = [
        [],
        ['--extractor-args', 'youtube:client=android'],
        ['--extractor-args', 'youtube:client=android_creator'],
    ]
    for ext in client_tries:
        for fmt in format_tries:
            try:
                cmd = base + ext + fmt + ['-g', source]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    lines = [l.strip() for l in r.stdout.strip().split('\n') if l.strip()]
                    if lines:
                        for l in lines:
                            if '.m3u8' in l or '.mp4' in l or l.startswith('http'):
                                return l
                        return lines[-1]
            except:
                pass
    wr('yt-dlp: all formats/clients failed')
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

def convert_cookies(raw):
    s = raw.strip()
    if s.startswith('[') or s.startswith('{'):
        try:
            jars = json.loads(s)
            if isinstance(jars, dict):
                jars = [jars]
            lines = ['# Netscape HTTP Cookie File']
            for c in jars:
                domain = c.get('domain', '')
                if not domain.startswith('.'):
                    domain = '.' + domain
                flag = 'TRUE'
                path = c.get('path', '/')
                secure = 'TRUE' if c.get('secure', False) else 'FALSE'
                exp = str(int(c.get('expirationDate', 9999999999)))
                name = c.get('name', '')
                val = c.get('value', '')
                lines.append(f'{domain}\t{flag}\t{path}\t{secure}\t{exp}\t{name}\t{val}')
            return '\n'.join(lines)
        except Exception as e:
            wr(f'Cookie JSON parse error: {e}')
    return s

@app.route('/config', methods=['POST'])
def update_config():
    data = request.get_json(force=True)
    cfg = load_config()
    for k in DEFAULTS:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    if cfg.get('cookies'):
        try:
            netscape = convert_cookies(cfg['cookies'])
            with open('/cookies.txt', 'w') as f:
                f.write(netscape)
            wr('Cookies saved')
        except Exception as e:
            wr(f'Cookies save failed: {e}')
    elif os.path.exists('/cookies.txt'):
        os.remove('/cookies.txt')
        wr('Cookies cleared')
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

@app.route('/preview')
def preview():
    cfg = load_config()
    hls = get_hls_url(cfg['source_url'])
    if not hls:
        return jsonify({'ok': False, 'error': 'Cannot resolve URL — check logs'}), 400
    return jsonify({
        'ok': True,
        'hls': hls,
        'source': cfg['source_url'],
        'output_mode': cfg.get('output_mode', 'youtube'),
        'backup_count': len([l for l in cfg.get('backup_list','').split('\n') if l.strip()])
    })

HTML_PANEL = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Twitch Restream Panel</title>
<script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
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
      <div class="form-group">
        <label>YouTube Cookies (Netscape format) — needed for YouTube source</label>
        <textarea name="cookies" id="cookies" placeholder="# Netscape HTTP Cookie File&#10;.youtube.com TRUE / TRUE 1234567890 VISITOR_INFO1_LIVE abc123..." style="min-height:60px;font-size:11px"></textarea>
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

<div class="card" style="margin-top:20px">
  <h2>🎬 Live Preview</h2>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
    <div>
      <label style="font-size:13px;color:#8b949e;display:block;margin-bottom:6px">Stream Preview</label>
      <div style="background:#000;border-radius:6px;overflow:hidden;max-height:240px">
        <video id="streamPreview" controls muted style="width:100%;max-height:240px;display:block"
          poster="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='180'%3E%3Crect fill='%23161b22' width='320' height='180'/%3E%3Ctext x='50%25' y='50%25' fill='%238b949e' font-family='sans-serif' font-size='14' text-anchor='middle' dy='.3em'%3ELoad preview%3C/text%3E%3C/svg%3E">
        </video>
      </div>
      <button class="btn btn-grey btn-sm" onclick="loadStreamPreview()" style="margin-top:8px">▶ Load Stream</button>
      <span id="previewInfo" style="font-size:12px;color:#8b949e;margin-left:8px"></span>
    </div>
    <div>
      <label style="font-size:13px;color:#8b949e;display:block;margin-bottom:6px">Source Info</label>
      <div id="sourceInfo" style="background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px;height:240px;overflow-y:auto;font-size:12px;font-family:monospace;color:#8b949e">
        Click "Preview" to load source info
      </div>
      <button class="btn btn-grey btn-sm" onclick="loadSourceInfo()" style="margin-top:8px">📡 Preview Source</button>
      <span id="sourceCount" style="font-size:12px;color:#8b949e;margin-left:8px"></span>
    </div>
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
function loadStreamPreview() {
  const vid = document.getElementById('streamPreview');
  const info = document.getElementById('previewInfo');
  info.textContent = 'Resolving...';
  fetch('/preview').then(r=>r.json()).then(d=>{
    if(!d.ok) { info.textContent = 'Error: '+d.error; return; }
    info.textContent = d.source;
    if (d.hls.includes('.m3u8')) {
      if (vid.canPlayType('application/vnd.apple.mpegurl')) {
        vid.src = d.hls;
      } else if (window.Hls) {
        const h = new Hls();
        h.loadSource(d.hls);
        h.attachMedia(vid);
      } else {
        info.textContent = 'HLS not supported in this browser';
        return;
      }
    } else {
      vid.src = d.hls;
    }
    vid.play().catch(()=>{});
    info.textContent = '▶ '+d.source+' — '+(d.backup_count||0)+' backups';
  }).catch(e=>{ info.textContent = 'Failed'; });
}
function loadSourceInfo() {
  const box = document.getElementById('sourceInfo');
  const cnt = document.getElementById('sourceCount');
  box.innerHTML = 'Resolving...';
  fetch('/preview').then(r=>r.json()).then(d=>{
    if(!d.ok) { box.innerHTML = 'Not live'; cnt.textContent = ''; return; }
    cnt.textContent = '▶ Live';
    box.innerHTML = 'Source: '+d.source+'\nHLS: '+d.hls+'\nOutput: '+d.output_mode+'\nBackups: '+(d.backup_count||0);
  }).catch(e=>{ box.innerHTML = 'Failed: '+e; });
}
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
  }).catch(()=>{});
}
function fetchLogs() {
  fetch('/logs').then(r=>r.text()).then(t=>{
    const box = document.getElementById('logBox');
    if(t) box.innerHTML = t;
    box.scrollTop = box.scrollHeight;
  }).catch(()=>{});
}
fetch('/status').then(r=>r.json()).then(d=>{ if(d.config) applyForm(d.config); });
setInterval(updateStatus, 3000);
setInterval(fetchLogs, 2000);
toggleMode();
</script>
</body>
</html>'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
