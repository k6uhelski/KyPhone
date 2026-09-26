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

PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>KyPhone</title><style>
body{font:16px/1.4 -apple-system,Helvetica,sans-serif;max-width:560px;margin:40px auto;padding:0 16px;color:#111}
h1{font-size:22px;letter-spacing:.08em}section{border:2px solid #111;padding:16px;margin:16px 0}
h2{font-size:15px;margin:0 0 8px;letter-spacing:.08em}input[type=text]{font:inherit;padding:6px;width:9em}
#log{white-space:pre-wrap;font:14px/1.5 Menlo,monospace}.muted{color:#666;font-size:14px}
</style></head><body>
<h1>KYPHONE</h1>
<p>Code shown on the phone: <input id="code" type="text" inputmode="numeric" maxlength="4" autocomplete="off"></p>
<section><h2>BOOKS</h2><p class="muted">EPUB files.</p><input type="file" id="books" multiple accept=".epub"></section>
<section><h2>MUSIC</h2><p class="muted">MP3, M4A, FLAC, Ogg, Opus or WAV. Album folder (optional):
<input id="folder" type="text"></p><input type="file" id="music" multiple
accept=".mp3,.m4a,.aac,.flac,.ogg,.opus,.wav"></section>
<section><h2>CONTACTS</h2><p class="muted">A .vcf file (Google Contacts and iCloud both export one).
People already on the phone are skipped.</p><input type="file" id="contacts" accept=".vcf,text/vcard"></section>
<div id="log"></div>
<script>
const log = m => document.getElementById('log').textContent += m + '\\n';
async function send(kind, input) {
  const code = document.getElementById('code').value.trim();
  if (!/^\\d{4}$/.test(code)) { log('Type the four-digit code from the phone first.'); input.value = ''; return; }
  for (const f of input.files) {
    const q = new URLSearchParams({kind, name: f.name, folder: document.getElementById('folder').value});
    log(f.name + ' ...');
    try {
      const r = await fetch('/upload?' + q, {method: 'PUT', body: f, headers: {'X-Code': code}});
      log('  ' + (await r.text()));
      if (r.status === 403 || r.status === 410) break;
    } catch (e) { log('  the phone stopped answering (was the screen closed?)'); break; }
  }
  input.value = '';
}
for (const k of ['books', 'music', 'contacts'])
  document.getElementById(k).addEventListener('change', e => send(k, e.target));
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
