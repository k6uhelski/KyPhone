"""music_library.py — find the music on the phone and read what each file says about itself (standard library only).

    lib = scan('data/music', 'data/music_index.json')
    for album in lib.albums:                 # A to Z
        album.name, album.artist, album.tracks     # tracks in disc / track order
        track.title, track.artist, track.seconds, track.path

Music is any audio file below the music folder, however deeply it is filed. Tags are read straight from the files
(MP3 ID3v2/ID3v1, FLAC and Ogg/Opus Vorbis comments, M4A/AAC iTunes atoms); a file with no tags is described by its
name and folders ("Artist/Album/03 - Song.mp3"). Only the few bytes that matter are read, so a tag holding cover art
does not slow a scan. Everything that leaves this module is drawable on the panel (printable ASCII, no '|' or the
separator), via reader_epub.to_drawable.

The result of reading each file is cached in a small JSON index keyed by (path, size, modified time), so opening
LISTEN on a big library only reads files that are new or changed.
"""

import json
import os
import re
import struct
import unicodedata
import wave

import reader_epub

AUDIO_EXTS = ('.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav')
MAX_FILES = 20000
MAX_DEPTH = 8
UNKNOWN_ARTIST = 'Unknown artist'
UNKNOWN_ALBUM = 'Unknown album'
LOOSE_ALBUM = 'Loose tracks'                  # files sitting directly in the music folder


# ─── Text and time ────────────────────────────────────────────────────────────

def clean(text):
    """Drawable, single-spaced, trimmed ('' if nothing is left)."""
    if not text:
        return ''
    text = ''.join(c for c in str(text) if c in '\t\n\r' or unicodedata.category(c) != 'Cc')      # tags sometimes hold control junk
    return re.sub(r'\s+', ' ', reader_epub.to_drawable(text)).strip()


def format_time(seconds):
    """3:42, or 1:02:03 for an hour or more; '' if unknown."""
    if seconds is None or seconds < 0:
        return ''
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return '%d:%02d:%02d' % (h, m, s) if h else '%d:%02d' % (m, s)


def _number(text):
    """'3/12' -> 3, '7' -> 7, anything else -> None."""
    m = re.match(r'\s*(\d+)', str(text or ''))
    return int(m.group(1)) if m else None


# ─── ID3 (MP3) ────────────────────────────────────────────────────────────────

_ID3V22 = {b'TT2': 'title', b'TP1': 'artist', b'TAL': 'album', b'TP2': 'album_artist', b'TRK': 'number', b'TPA': 'disc'}
_ID3V23 = {b'TIT2': 'title', b'TPE1': 'artist', b'TALB': 'album', b'TPE2': 'album_artist', b'TRCK': 'number',
           b'TPOS': 'disc'}
_ID3_TEXT_LIMIT = 4096


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3_text(data):
    """A text frame's payload -> str (first string only)."""
    if not data:
        return ''
    enc, body = data[0], data[1:]
    try:
        if enc == 1:                                       # UTF-16 with a byte-order mark
            text = body.decode('utf-16', 'replace')
        elif enc == 2:
            text = body.decode('utf-16-be', 'replace')
        elif enc == 3:
            text = body.decode('utf-8', 'replace')
        else:
            text = body.decode('latin-1')
    except Exception:
        return ''
    return text.split('\x00')[0]


def _read_id3v2(f):
    """-> (tags dict, offset where the audio starts). Leaves the tag alone if there is not one."""
    f.seek(0)
    head = f.read(10)
    if len(head) < 10 or head[:3] != b'ID3' or head[3] not in (2, 3, 4):
        return {}, 0
    version, flags = head[3], head[5]
    size = _syncsafe(head[6:10])
    end = 10 + size
    pos = 10
    if version >= 3 and flags & 0x40:                      # an extended header sits before the frames
        f.seek(10)
        ext = f.read(4)
        if len(ext) == 4:
            skip = _syncsafe(ext) if version == 4 else struct.unpack('>I', ext)[0] + 4
            pos = 10 + skip
    names = _ID3V22 if version == 2 else _ID3V23
    idlen = 3 if version == 2 else 4
    hlen = 6 if version == 2 else 10
    tags = {}
    while pos + hlen <= end:
        f.seek(pos)
        h = f.read(hlen)
        if len(h) < hlen or h[0] == 0:                     # padding: the frames are over
            break
        fid = h[:idlen]
        if version == 2:
            fsize = (h[3] << 16) | (h[4] << 8) | h[5]
        elif version == 4:
            fsize = _syncsafe(h[4:8])
        else:
            fsize = struct.unpack('>I', h[4:8])[0]
        if fsize <= 0 or pos + hlen + fsize > end:
            break
        name = names.get(fid)
        if name and fsize <= _ID3_TEXT_LIMIT and name not in tags:
            tags[name] = _id3_text(f.read(fsize))
        pos += hlen + fsize
    return tags, end


def _read_id3v1(f, size):
    if size < 128:
        return {}
    f.seek(size - 128)
    tag = f.read(128)
    if len(tag) < 128 or tag[:3] != b'TAG':
        return {}
    def field(a, b):
        return tag[a:b].split(b'\x00')[0].decode('latin-1').strip()
    out = {'title': field(3, 33), 'artist': field(33, 63), 'album': field(63, 93)}
    if tag[125] == 0 and tag[126]:                         # ID3v1.1 track number
        out['number'] = str(tag[126])
    return out


_MP3_BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],      # MPEG 1, layer III
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],          # MPEG 2 and 2.5, layer III
}
_MP3_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _mp3_seconds(f, audio_start, size, v1_bytes):
    """Length of an MP3: exact from a Xing/Info header, else estimated from the first frame's bitrate."""
    f.seek(audio_start)
    chunk = f.read(8192)
    for i in range(len(chunk) - 4):
        if chunk[i] != 0xFF or (chunk[i + 1] & 0xE0) != 0xE0:
            continue
        b1, b2, b3 = chunk[i + 1], chunk[i + 2], chunk[i + 3]
        version, layer = (b1 >> 3) & 3, (b1 >> 1) & 3
        if version == 1 or layer != 1:                     # reserved version, or not layer III
            continue
        bit_i, rate_i = (b2 >> 4) & 15, (b2 >> 2) & 3
        if bit_i in (0, 15) or rate_i == 3:
            continue
        bitrate = _MP3_BITRATES[1 if version == 3 else 2][bit_i] * 1000
        rate = _MP3_RATES[version][rate_i]
        per_frame = 1152 if version == 3 else 576
        mono = ((b3 >> 6) & 3) == 3
        xing = i + 4 + (17 if mono else 32) if version == 3 else i + 4 + (9 if mono else 17)
        if chunk[xing:xing + 4] in (b'Xing', b'Info') and len(chunk) >= xing + 12:
            if chunk[xing + 7] & 1:                        # a frame count is present
                frames = struct.unpack('>I', chunk[xing + 8:xing + 12])[0]
                return frames * per_frame / float(rate)
        payload = size - audio_start - v1_bytes - i
        return payload * 8.0 / bitrate if payload > 0 else None
    return None


def _read_mp3(path):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        tags, start = _read_id3v2(f)
        v1 = _read_id3v1(f, size)
        for k, v in v1.items():
            if v and not tags.get(k):                          # ID3v1 only fills what ID3v2 left empty
                tags[k] = v
        seconds = _mp3_seconds(f, start, size, 128 if v1 else 0)
    if seconds:
        tags['seconds'] = seconds
    return tags


# ─── FLAC and Vorbis comments ─────────────────────────────────────────────────

_VORBIS_KEYS = {'title': 'title', 'artist': 'artist', 'album': 'album', 'albumartist': 'album_artist',
                'tracknumber': 'number', 'discnumber': 'disc'}


def _vorbis_comments(data):
    """The payload of a Vorbis comment block (vendor, then KEY=value strings) -> tags dict."""
    tags = {}
    try:
        n = struct.unpack('<I', data[:4])[0]
        pos = 4 + n
        count = struct.unpack('<I', data[pos:pos + 4])[0]
        pos += 4
        for _ in range(min(count, 512)):
            n = struct.unpack('<I', data[pos:pos + 4])[0]
            pos += 4
            item = data[pos:pos + n].decode('utf-8', 'replace')
            pos += n
            key, _eq, value = item.partition('=')
            name = _VORBIS_KEYS.get(key.lower())
            if name and name not in tags:
                tags[name] = value
    except (struct.error, IndexError):
        pass
    return tags


def _read_flac(path):
    tags = {}
    with open(path, 'rb') as f:
        if f.read(4) != b'fLaC':
            return {}
        while True:
            h = f.read(4)
            if len(h) < 4:
                break
            last, kind, length = h[0] & 0x80, h[0] & 0x7F, (h[1] << 16) | (h[2] << 8) | h[3]
            if kind == 0 and length >= 18:                 # STREAMINFO: sample rate and total samples
                info = f.read(length)
                rate = (info[10] << 12) | (info[11] << 4) | (info[12] >> 4)
                total = ((info[13] & 0x0F) << 32) | struct.unpack('>I', info[14:18])[0]
                if rate and total:
                    tags['seconds'] = total / float(rate)
            elif kind == 4 and length <= 1 << 20:          # VORBIS_COMMENT
                tags.update(_vorbis_comments(f.read(length)))
            else:
                f.seek(length, 1)
            if last:
                break
    return tags


# ─── Ogg (Vorbis, Opus) ───────────────────────────────────────────────────────

def _ogg_packets(data, want=2):
    """The first `want` packets of an Ogg stream in `data` (or fewer if it runs out)."""
    packets, cur, pos = [], b'', 0
    while pos + 27 <= len(data) and len(packets) < want:
        if data[pos:pos + 4] != b'OggS':
            break
        nseg = data[pos + 26]
        table = data[pos + 27:pos + 27 + nseg]
        body = pos + 27 + nseg
        for lace in table:
            cur += data[body:body + lace]
            body += lace
            if lace < 255:
                packets.append(cur)
                cur = b''
                if len(packets) >= want:
                    break
        pos = body
    return packets


def _read_ogg(path):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(65536)
        tail = b''
        if size > 65536:
            f.seek(max(0, size - 65536))
            tail = f.read(65536)
        else:
            tail = head
    packets = _ogg_packets(head)
    tags, rate, skip = {}, None, 0
    if packets and packets[0][:8] == b'OpusHead' and len(packets[0]) >= 12:
        rate, skip = 48000, struct.unpack('<H', packets[0][10:12])[0]
        if len(packets) > 1 and packets[1][:8] == b'OpusTags':
            tags = _vorbis_comments(packets[1][8:])
    elif packets and packets[0][:7] == b'\x01vorbis' and len(packets[0]) >= 16:
        rate = struct.unpack('<I', packets[0][12:16])[0]
        if len(packets) > 1 and packets[1][:7] == b'\x03vorbis':
            tags = _vorbis_comments(packets[1][7:])
    last = tail.rfind(b'OggS')
    if rate and last >= 0 and last + 14 <= len(tail):
        granule = struct.unpack('<q', tail[last + 6:last + 14])[0]
        if granule > skip:
            tags['seconds'] = (granule - skip) / float(rate)
    return tags


# ─── M4A / AAC ────────────────────────────────────────────────────────────────

_M4A_KEYS = {b'\xa9nam': 'title', b'\xa9ART': 'artist', b'\xa9alb': 'album', b'aART': 'album_artist'}


def _atoms(data, start=0, end=None):
    """Yield (type, payload_start, payload_end) for the atoms in data[start:end]."""
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack('>I4s', data[pos:pos + 8])
        header = 8
        if size == 1 and pos + 16 <= end:
            size, header = struct.unpack('>Q', data[pos + 8:pos + 16])[0], 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            break
        yield kind, pos + header, pos + size
        pos += size


def _read_m4a(path):
    tags = {}
    with open(path, 'rb') as f:
        moov = None
        while True:                                        # top-level atoms: seek past mdat, read only moov
            h = f.read(8)
            if len(h) < 8:
                break
            size, kind = struct.unpack('>I4s', h)
            header = 8
            if size == 1:
                big = f.read(8)
                if len(big) < 8:
                    break
                size, header = struct.unpack('>Q', big)[0], 16
            if kind == b'moov':
                if size - header > 16 << 20:
                    return {}
                moov = f.read(size - header)
                break
            if size < header:
                break
            f.seek(size - header, 1)
    if moov is None:
        return {}
    for kind, a, b in _atoms(moov):
        if kind == b'mvhd' and b - a >= 20:
            version = moov[a]
            if version == 0:
                scale, dur = struct.unpack('>II', moov[a + 12:a + 20])
            elif b - a >= 32:
                scale, dur = struct.unpack('>IQ', moov[a + 20:a + 32])
            else:
                continue
            if scale and dur:
                tags['seconds'] = dur / float(scale)
        elif kind == b'udta':
            for k2, a2, b2 in _atoms(moov, a, b):
                if k2 != b'meta':
                    continue
                for k3, a3, b3 in _atoms(moov, a2 + 4, b2):            # meta has 4 bytes of version/flags first
                    if k3 != b'ilst':
                        continue
                    for item, a4, b4 in _atoms(moov, a3, b3):
                        for k5, a5, b5 in _atoms(moov, a4, b4):
                            if k5 != b'data' or b5 - a5 < 8:
                                continue
                            payload = moov[a5 + 8:b5]
                            if item in _M4A_KEYS:
                                tags.setdefault(_M4A_KEYS[item], payload.decode('utf-8', 'replace'))
                            elif item == b'trkn' and len(payload) >= 4:
                                tags.setdefault('number', str(struct.unpack('>H', payload[2:4])[0]))
                            elif item == b'disk' and len(payload) >= 4:
                                tags.setdefault('disc', str(struct.unpack('>H', payload[2:4])[0]))
    return tags


# ─── WAV ──────────────────────────────────────────────────────────────────────

def _read_wav(path):
    try:
        with wave.open(path, 'rb') as w:
            if w.getframerate():
                return {'seconds': w.getnframes() / float(w.getframerate())}
    except (wave.Error, EOFError, OSError):
        pass
    return {}


_READERS = {'.mp3': _read_mp3, '.flac': _read_flac, '.ogg': _read_ogg, '.opus': _read_ogg, '.m4a': _read_m4a,
            '.aac': _read_m4a, '.wav': _read_wav}


def read_tags(path):
    """The raw tags of one file as {title, artist, album, album_artist, number, disc, seconds}: whatever it says,
    nothing invented. A damaged or unknown file gives {} rather than an error."""
    reader = _READERS.get(os.path.splitext(path)[1].lower())
    if reader is None:
        return {}
    try:
        return reader(path)
    except Exception:                                      # a truncated or odd file must never stop a scan
        return {}


# ─── Tracks, albums, the library ──────────────────────────────────────────────

class Track:
    def __init__(self, path, title, artist, album, album_artist, number, disc, seconds):
        self.path, self.title, self.artist, self.album = path, title, artist, album
        self.album_artist, self.number, self.disc, self.seconds = album_artist, number, disc, seconds

    def time(self):
        return format_time(self.seconds)


class Album:
    def __init__(self, name, artist, tracks):
        self.name, self.artist, self.tracks = name, artist, tracks


class Library:
    def __init__(self, albums):
        self.albums = albums

    def __len__(self):
        return sum(len(a.tracks) for a in self.albums)


_FILE_NUMBER = re.compile(r'^\s*(\d{1,3})(?:\s*[-._)]+\s*|\s+)(\S.*)$')      # "03 - Song", "03. Song", "3 Song"


def _from_path(rel):
    """What the folders and file name say: (artist, album, title, track number), each possibly None."""
    parts = rel.replace('\\', '/').split('/')
    stem = os.path.splitext(parts[-1])[0].replace('_', ' ')
    number = None
    m = _FILE_NUMBER.match(stem)
    if m:
        number, stem = int(m.group(1)), m.group(2)
    album = parts[-2] if len(parts) >= 2 else None
    artist = parts[-3] if len(parts) >= 3 else None
    return artist, album, stem.strip(), number


def _track(root, path, tags):
    rel = os.path.relpath(path, root)
    p_artist, p_album, p_title, p_number = _from_path(rel)
    title = clean(tags.get('title')) or clean(p_title) or 'Untitled'
    artist = clean(tags.get('artist')) or clean(p_artist) or UNKNOWN_ARTIST
    album = clean(tags.get('album')) or clean(p_album) or (UNKNOWN_ALBUM if p_album is None else LOOSE_ALBUM)
    if p_album is None and not clean(tags.get('album')):
        album = LOOSE_ALBUM
    seconds = tags.get('seconds')
    return Track(path, title, artist, album, clean(tags.get('album_artist')),
                 _number(tags.get('number')) or p_number, _number(tags.get('disc')) or 1,
                 int(round(seconds)) if isinstance(seconds, (int, float)) and seconds > 0 else None)


_DISC_FOLDER = re.compile(r'^(?:cd|disc|disk)\s*\d+\b', re.I)


def _album_home(path):
    """The folder an album lives in: the track's own, or its parent for "Disc 1" / "CD 2" subfolders."""
    folder = os.path.dirname(path)
    if _DISC_FOLDER.match(os.path.basename(folder)):
        folder = os.path.dirname(folder)
    return folder


def _albums(tracks):
    """Tracks with the same album name belong together when they share an album-artist tag, or, with no such tag,
    a folder: two artists can each have a "Greatest Hits", and a compilation sits in one folder."""
    groups, order = {}, []
    for t in tracks:
        key = (t.album.lower(), t.album_artist.lower() or _album_home(t.path))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(t)
    albums = []
    for key in order:
        items = sorted(groups[key], key=lambda t: (t.disc, t.number is None, t.number or 0, t.title.lower(), t.path))
        artists = {t.artist for t in items}
        artist = items[0].album_artist or (items[0].artist if len(artists) == 1 else 'Various artists')
        albums.append(Album(items[0].album, artist, items))
    albums.sort(key=lambda a: (a.name.lower(), a.artist.lower()))
    return albums


# ─── Scanning, with a cache ───────────────────────────────────────────────────

def _load_index(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def scan(music_dir, index_path=None):
    """Read the music folder into a Library. Never raises: a missing folder is an empty library, and a file that
    cannot be read is still listed, by its name."""
    root = os.path.abspath(music_dir)
    files = []
    if os.path.isdir(root):
        base_depth = root.count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            for name in sorted(filenames):
                if name.startswith('.') or os.path.splitext(name)[1].lower() not in AUDIO_EXTS:
                    continue
                files.append(os.path.join(dirpath, name))
                if len(files) >= MAX_FILES:
                    break
            if len(files) >= MAX_FILES:
                break
    cache = _load_index(index_path) if index_path else {}
    fresh, tracks = {}, []
    for path in files:
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = os.path.relpath(path, root)
        entry = cache.get(key)
        if (isinstance(entry, dict) and entry.get('size') == st.st_size and entry.get('mtime') == st.st_mtime_ns
                and isinstance(entry.get('tags'), dict)):
            tags = entry['tags']
        else:
            tags = read_tags(path)
        fresh[key] = {'size': st.st_size, 'mtime': st.st_mtime_ns, 'tags': tags}
        tracks.append(_track(root, path, tags))
    if index_path and fresh != cache:
        _save_index(index_path, fresh)
    return Library(_albums(tracks))
