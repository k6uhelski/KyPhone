"""upload_server.py — add books, music and contacts to the phone from a computer on the same Wi-Fi.

Settings > Add from a computer shows an address and a four-digit code. While that screen is open (and only then),
this serves one small web page on the home network: drop EPUBs, audio files (optionally into an album folder) or a
contacts file (.vcf), and they land in data/books/, data/music/ and data/contacts.json. Leaving the screen stops it.

Standard library only (http.server), importable without hardware, so the whole thing is tested on the Mac.

Safety, since anyone on the Wi-Fi can reach the page while it is open:
    * every upload must carry the code shown on the phone; after MAX_BAD_CODES wrong codes the server stops itself
    * only the listed file types are taken; a file name is reduced to plain characters and can never leave its
      folder (no paths, no leading dots); an existing file is never overwritten ("Song (2).mp3" instead)
    * each file has a size limit and there must be room on the disk for it; it is written to a temporary name and
      only renamed into place once it has all arrived, so a cut-off upload leaves nothing behind
    * the page itself carries no data from the phone: nothing is listed or read back, uploads only
"""

import os
import re
import secrets
import shutil
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8080
MAX_BAD_CODES = 10
BOOK_TYPES = ('.epub',)
MUSIC_TYPES = ('.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav')
MAX_BOOK_BYTES = 100 * 1024 * 1024
MAX_MUSIC_BYTES = 500 * 1024 * 1024
MAX_CONTACTS_BYTES = 5 * 1024 * 1024
DISK_SPARE_BYTES = 200 * 1024 * 1024      # always leave this much free
CHUNK = 64 * 1024


def new_code():
    return f'{secrets.randbelow(10000):04d}'


def lan_address():
    """This computer's address on the local network, or None when there is no network. (A UDP 'connect' sends
    nothing; it only asks the system which interface would be used.)"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        return None if ip.startswith('127.') or ip == '0.0.0.0' else ip
    except OSError:
        return None
    finally:
        s.close()


def safe_name(name, allowed):
    """A file name reduced to letters, digits, spaces and . _ - ( ) — or None if nothing usable is left or the type
    is not one of `allowed`. Never a path: only the last component is kept, and it cannot start with a dot."""
    name = os.path.basename(str(name).replace('\\', '/'))
    name = re.sub(r'[^A-Za-z0-9 ._()\-]', '_', name).strip(' .')
    name = re.sub(r'_+', '_', name)[:120]
    root, ext = os.path.splitext(name)
    if not root or ext.lower() not in allowed:
        return None
    return root + ext.lower()


def safe_folder(name):
    """An album folder name, cleaned like a file name; '' for none."""
    name = re.sub(r'[^A-Za-z0-9 ._()\-]', '_', os.path.basename(str(name).replace('\\', '/'))).strip(' .')
    return re.sub(r'_+', '_', name)[:80]


def unique_path(folder, name):
    root, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path = os.path.join(folder, f'{root} ({n}){ext}')
        n += 1
    return path


# ─── Contacts (vCard) ─────────────────────────────────────────────────────────

def parse_vcards(text):
    """[(first, last, number)] from a .vcf file (vCard 2.1, 3.0 or 4.0, as Google, iCloud and phones export them).
    The first TEL of each card is used; a card with no name is skipped. Folded lines are unfolded."""
    lines = re.sub(r'\r?\n[ \t]', '', text).splitlines()
    cards, card = [], None
    for line in lines:
        key, _, value = line.partition(':')
        name = key.split(';')[0].strip().upper()
        if '.' in name:                                        # grouped properties: item1.TEL
            name = name.split('.', 1)[1]
        if name == 'BEGIN' and value.strip().upper() == 'VCARD':
            card = {'n': None, 'fn': '', 'tel': ''}
        elif name == 'END' and card is not None:
            cards.append(card)
            card = None
        elif card is None:
            continue
        elif name == 'N':
            parts = (value.split(';') + ['', ''])[:2]
            card['n'] = (parts[1].strip(), parts[0].strip())     # (first, last)
        elif name == 'FN':
            card['fn'] = value.strip()
        elif name == 'TEL' and not card['tel']:
            card['tel'] = value.strip()
    out = []
    for c in cards:
        first, last = c['n'] or ('', '')
        if not first and not last:                             # no N: split the display name
            bits = c['fn'].split(' ', 1)
            first, last = bits[0], bits[1] if len(bits) > 1 else ''
        if not first and last:
            first, last = last, ''
        if first:
            out.append((first, last, c['tel']))
    return out


# ─── The web page ─────────────────────────────────────────────────────────────

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Add to KyPhone</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f4f1;color:#111;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Helvetica Neue",Helvetica,Arial,sans-serif}
main{max-width:640px;margin:0 auto;padding:40px 20px 60px}
h1{font:700 15px/1 Menlo,Monaco,monospace;letter-spacing:.2em;margin:0 0 28px}
h2{font:600 13px/1 Menlo,Monaco,monospace;letter-spacing:.12em;margin:0 0 12px;color:#444}
.card{background:#fff;border:2px solid #111;padding:22px;margin-bottom:18px}
.step{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.code{display:flex;gap:10px}
.code input{width:56px;height:68px;border:2px solid #111;font:700 34px/1 Menlo,Monaco,monospace;text-align:center;
  background:#fff;color:#111;outline:none}
.code input:focus{background:#111;color:#fff}
.bad .code input{border-color:#b00020;animation:shake .3s}
@keyframes shake{25%{transform:translateX(-4px)}75%{transform:translateX(4px)}}
.hint{color:#555;font-size:14px;margin:10px 0 0}
.status{font:600 14px/1.3 Menlo,Monaco,monospace;margin-left:6px}
.status.ok{color:#1b7a2c}.status.err{color:#b00020}
#drop{border:2px dashed #111;background:#fff;padding:46px 20px;text-align:center;cursor:pointer;transition:background .15s}
#drop.over{background:#111;color:#fff}
#drop.off{opacity:.35;pointer-events:none}
#drop b{display:block;font-size:20px;margin-bottom:6px}
#drop span{color:inherit;opacity:.7;font-size:14px}
.album{margin-top:14px;font-size:14px;color:#444}
.album input{font:inherit;padding:6px 8px;border:2px solid #111;width:15em}
ul{list-style:none;margin:0;padding:0}
li{display:grid;grid-template-columns:22px 1fr auto;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid #ddd}
li:last-child{border-bottom:0}
.kind{font:700 11px/1 Menlo,Monaco,monospace;letter-spacing:.08em;color:#666}
.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{grid-column:2/4;height:4px;background:#e6e6e2}
.bar i{display:block;height:100%;width:0;background:#111;transition:width .1s}
.res{font:600 13px/1 Menlo,Monaco,monospace;white-space:nowrap}
.res.ok{color:#1b7a2c}.res.err{color:#b00020}
#summary{font:600 15px/1.4 Menlo,Monaco,monospace;margin-top:14px}
footer{color:#777;font-size:13px;margin-top:22px}
</style></head><body><main>
<h1>ADD TO KYPHONE</h1>

<section class="card" id="codecard">
  <h2>1 &nbsp;TYPE THE CODE ON THE PHONE</h2>
  <div class="step"><div class="code" id="code">
    <input inputmode="numeric" maxlength="1" aria-label="digit 1"><input inputmode="numeric" maxlength="1" aria-label="digit 2">
    <input inputmode="numeric" maxlength="1" aria-label="digit 3"><input inputmode="numeric" maxlength="1" aria-label="digit 4">
  </div><span class="status" id="codestatus"></span></div>
  <p class="hint">On the phone: Settings &rarr; Add from a computer. Keep that screen open while you add things.</p>
</section>

<section class="card">
  <h2>2 &nbsp;DROP FILES OR FOLDERS</h2>
  <div id="drop" class="off" tabindex="0">
    <b>Drop books, music or contacts here</b>
    <span>or click to choose files &middot; EPUB books &middot; MP3, M4A, FLAC, Ogg, Opus, WAV music &middot; a .vcf of contacts</span>
  </div>
  <input type="file" id="picker" multiple hidden>
  <div class="album">Songs dropped on their own go into album: <input id="album" placeholder="(no album folder)"></div>
  <p class="hint">A dropped folder keeps its name as the album. Anything else is skipped and marked here.</p>
</section>

<section class="card" id="listcard" hidden>
  <h2>3 &nbsp;ON THE WAY</h2>
  <ul id="list"></ul>
  <div id="summary"></div>
</section>
<footer>Nothing is read back from the phone; this page only adds. It stops when the phone leaves that screen.</footer>
</main>
<script>
const BOOK = ['epub'], MUSIC = ['mp3','m4a','aac','flac','ogg','opus','wav'], CONTACTS = ['vcf'];
const boxes = [...document.querySelectorAll('#code input')];
const drop = document.getElementById('drop'), picker = document.getElementById('picker');
const list = document.getElementById('list'), summary = document.getElementById('summary');
let code = sessionStorage.getItem('kyphone-code') || '';
let queue = [], busy = false, stopped = false, counts = {books: 0, music: 0, contacts: 0, failed: 0, skipped: 0};

function setCode(c) {
  code = c; boxes.forEach((b, i) => b.value = c[i] || '');
  const ok = /^\d{4}$/.test(c);
  drop.classList.toggle('off', !ok);
  document.getElementById('codecard').classList.remove('bad');
  const st = document.getElementById('codestatus');
  st.textContent = ok ? 'READY' : ''; st.className = 'status' + (ok ? ' ok' : '');
  if (ok) sessionStorage.setItem('kyphone-code', c);
}
boxes.forEach((b, i) => {
  b.addEventListener('input', () => {
    b.value = b.value.replace(/\D/g, '').slice(-1);
    if (b.value && i < 3) boxes[i + 1].focus();
    setCode(boxes.map(x => x.value).join(''));
  });
  b.addEventListener('keydown', e => { if (e.key === 'Backspace' && !b.value && i > 0) boxes[i - 1].focus(); });
  b.addEventListener('paste', e => { const t = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 4);
    if (t) { e.preventDefault(); setCode(t); boxes[Math.min(3, t.length)].focus(); } });
});
setCode(code); (code ? drop : boxes[0]).focus();

function kindOf(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  return BOOK.includes(ext) ? 'books' : MUSIC.includes(ext) ? 'music' : CONTACTS.includes(ext) ? 'contacts' : null;
}
function addRow(file, kind) {
  const li = document.createElement('li');
  li.innerHTML = '<span class="kind"></span><span class="name"></span><span class="res"></span><div class="bar"><i></i></div>';
  li.querySelector('.kind').textContent = {books: 'BK', music: 'MU', contacts: 'CT'}[kind] || '--';
  li.querySelector('.name').textContent = file.name;
  document.getElementById('listcard').hidden = false; list.appendChild(li);
  return li;
}
function finish(li, ok, text) {
  const r = li.querySelector('.res'); r.textContent = (ok ? '✓ ' : '✕ ') + text; r.className = 'res ' + (ok ? 'ok' : 'err');
  li.querySelector('.bar i').style.width = ok ? '100%' : '0';
}
function enqueue(file, folder) {
  const kind = kindOf(file.name), li = addRow(file, kind);
  if (!kind) { finish(li, false, 'not a book, song or contacts file'); counts.skipped++; showSummary(); return; }
  queue.push({file, kind, folder, li}); pump();
}
function showSummary() {
  const bits = [];
  if (counts.books) bits.push(counts.books + (counts.books > 1 ? ' books' : ' book'));
  if (counts.music) bits.push(counts.music + (counts.music > 1 ? ' songs' : ' song'));
  if (counts.contacts) bits.push('contacts');
  let s = bits.length ? bits.join(', ') + ' added' : '';
  if (counts.failed) s += (s ? ' · ' : '') + counts.failed + ' failed';
  if (counts.skipped) s += (s ? ' · ' : '') + counts.skipped + ' skipped';
  if (!queue.length && !busy && s) s += '. Done.';
  summary.textContent = s;
}
function pump() {
  if (busy || stopped || !queue.length) { showSummary(); return; }
  busy = true;
  const {file, kind, folder, li} = queue.shift();
  const album = kind === 'music' ? (folder || document.getElementById('album').value.trim()) : '';
  const q = new URLSearchParams({kind, name: file.name, folder: album});
  const x = new XMLHttpRequest();
  x.open('PUT', '/upload?' + q); x.setRequestHeader('X-Code', code);
  x.upload.onprogress = e => { if (e.lengthComputable) li.querySelector('.bar i').style.width = (100 * e.loaded / e.total) + '%'; };
  x.onload = () => {
    const ok = x.status === 200;
    finish(li, ok, ok ? (kind === 'contacts' ? x.responseText.replace(/^Contacts: /, '') : 'added') : x.responseText);
    if (ok) counts[kind] = kind === 'contacts' ? 1 : counts[kind] + 1; else counts.failed++;
    if (x.status === 403) {
      stopped = true; setCode(''); document.getElementById('codecard').classList.add('bad');
      const st = document.getElementById('codestatus'); st.textContent = x.responseText.toUpperCase(); st.className = 'status err';
      boxes[0].focus(); queue.forEach(it => finish(it.li, false, 'not sent')); queue = [];
    }
    busy = false; pump();
  };
  x.onerror = () => {
    finish(li, false, 'the phone stopped answering'); counts.failed++; stopped = true;
    queue.forEach(it => finish(it.li, false, 'not sent')); queue = []; busy = false; showSummary();
  };
  x.send(file);
}
function readEntry(entry, folder) {
  if (entry.isFile) { entry.file(f => enqueue(f, folder)); return; }
  if (!entry.isDirectory) return;
  const reader = entry.createReader(), album = folder || entry.name;
  const more = () => reader.readEntries(ents => { if (ents.length) { ents.forEach(e => readEntry(e, album)); more(); } });
  more();
}
function takeDrop(dt) {
  stopped = false;
  const items = [...(dt.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) items.forEach(e => readEntry(e, ''));
  else [...dt.files].forEach(f => enqueue(f, ''));
}
['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => takeDrop(e.dataTransfer));
drop.addEventListener('click', () => picker.click());
drop.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') picker.click(); });
picker.addEventListener('change', () => { stopped = false; [...picker.files].forEach(f => enqueue(f, '')); picker.value = ''; });
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());
</script></body></html>"""


class UploadServer:
    """One upload session. `on_event(text)` is told about each file received (for the phone's screen);
    `on_contacts(cards)` merges parsed vCards and returns a one-line summary; `on_stopped(reason)` is called if
    the server stops itself (too many wrong codes)."""

    def __init__(self, books_dir, music_dir, on_event, on_contacts, on_stopped=None, code=None,
                 host='0.0.0.0', port=PORT):
        self.books_dir, self.music_dir = books_dir, music_dir
        self.on_event, self.on_contacts, self.on_stopped = on_event, on_contacts, on_stopped
        self.code = code or new_code()
        self.host, self.port = host, port
        self.bad_codes = 0
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):                    # quiet: nothing about uploads goes to the log
                pass

            def reply(self, status, text, ctype='text/plain; charset=utf-8'):
                body = text.encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlparse(self.path).path in ('/', '/index.html'):
                    self.reply(200, PAGE, 'text/html; charset=utf-8')
                else:
                    self.reply(404, 'not found')

            def do_PUT(self):
                url = urlparse(self.path)
                if url.path != '/upload':
                    return self.reply(404, 'not found')
                if not server._check_code(self.headers.get('X-Code', '')):
                    self.close_connection = True
                    return self.reply(403, 'wrong code' if server.running else 'too many wrong codes: stopped')
                try:
                    length = int(self.headers.get('Content-Length', ''))
                except ValueError:
                    return self.reply(411, 'no length')
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                status, text = server._receive(q.get('kind', ''), q.get('name', ''), q.get('folder', ''),
                                               length, self.rfile)
                self.reply(status, text)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()

    def _check_code(self, given):
        with self._lock:
            if not self.running:
                return False
            if secrets.compare_digest(str(given), self.code):
                return True
            self.bad_codes += 1
            stop = self.bad_codes >= MAX_BAD_CODES
        if stop:
            self.stop()
            if self.on_stopped:
                self.on_stopped('too many wrong codes')
        return False

    def _receive(self, kind, name, folder, length, stream):
        if kind == 'contacts':
            if length > MAX_CONTACTS_BYTES:
                return 413, 'too large'
            text = _read(stream, length).decode('utf-8', 'replace')
            summary = self.on_contacts(parse_vcards(text))
            self.on_event(summary)
            return 200, summary
        if kind == 'books':
            allowed, limit, base = BOOK_TYPES, MAX_BOOK_BYTES, self.books_dir
        elif kind == 'music':
            allowed, limit, base = MUSIC_TYPES, MAX_MUSIC_BYTES, self.music_dir
            sub = safe_folder(folder)
            if sub:
                base = os.path.join(base, sub)
        else:
            return 400, 'unknown kind'
        clean = safe_name(name, allowed)
        if clean is None:
            _drain(stream, length)
            return 415, 'not a file type the phone can use'
        if length > limit:
            _drain(stream, length)
            return 413, 'too large'
        os.makedirs(base, exist_ok=True)
        if shutil.disk_usage(base).free < length + DISK_SPARE_BYTES:
            _drain(stream, length)
            return 507, 'not enough room on the phone'
        path = unique_path(base, clean)
        tmp = os.path.join(base, '.upload-' + secrets.token_hex(6))
        try:
            with open(tmp, 'wb') as f:
                left = length
                while left > 0:
                    chunk = stream.read(min(CHUNK, left))
                    if not chunk:
                        raise IOError('cut off')
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return 400, 'the upload was cut off'
        self.on_event(os.path.basename(path))
        return 200, 'added ' + os.path.basename(path)


def _read(stream, length):
    data, left = [], length
    while left > 0:
        chunk = stream.read(min(CHUNK, left))
        if not chunk:
            break
        data.append(chunk)
        left -= len(chunk)
    return b''.join(data)


def _drain(stream, length):
    left = length
    while left > 0:
        chunk = stream.read(min(CHUNK, left))
        if not chunk:
            break
        left -= len(chunk)
