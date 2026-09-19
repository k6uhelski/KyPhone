"""audio_fixtures.py — build small audio files with known tags and lengths (shared by the music tests, and handy for
checking the player on the phone: a real tone that plays for a known number of seconds).

None of these are music: the MPEG and FLAC "audio" is zeros. They are shaped exactly enough for the tag and length
readers in music_library.py, which never decode audio.
"""

import io
import math
import struct
import wave


# ─── WAV (real, playable) ─────────────────────────────────────────────────────

def wav_bytes(seconds=1.0, rate=8000, freq=440.0, volume=0.2):
    """A mono 16-bit sine tone: a real WAV that any player can play."""
    n = int(seconds * rate)
    frames = b''.join(struct.pack('<h', int(volume * 32767 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return out.getvalue()


# ─── MP3 ──────────────────────────────────────────────────────────────────────

def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v2(version=3, encoding=0, padding=0, extended_header=False, art=0, order=None, **fields):
    """An ID3v2 tag. fields: title, artist, album, album_artist, number, disc (str). `art` adds a picture frame of
    that many bytes BEFORE the text frames (to prove it is skipped, not read)."""
    ids4 = {'title': b'TIT2', 'artist': b'TPE1', 'album': b'TALB', 'album_artist': b'TPE2', 'number': b'TRCK', 'disc': b'TPOS'}
    ids3 = {'title': b'TT2', 'artist': b'TP1', 'album': b'TAL', 'album_artist': b'TP2', 'number': b'TRK', 'disc': b'TPA'}

    def payload(text):
        if encoding == 1:
            return b'\x01' + b'\xff\xfe' + text.encode('utf-16-le')
        if encoding == 2:
            return b'\x02' + text.encode('utf-16-be')
        if encoding == 3:
            return b'\x03' + text.encode('utf-8')
        return b'\x00' + text.encode('latin-1', 'replace')

    def frame(fid, data):
        if version == 2:
            return fid + struct.pack('>I', len(data))[1:] + data
        size = _syncsafe(len(data)) if version == 4 else struct.pack('>I', len(data))
        return fid + size + b'\x00\x00' + data

    body = b''
    if art:
        body += frame(b'APIC' if version != 2 else b'PIC', b'\x00image/jpeg\x00\x03\x00' + b'\xab' * art)
    keys = order or list(fields)
    for key in keys:
        if key in fields:
            body += frame((ids3 if version == 2 else ids4)[key], payload(fields[key]))
    body += b'\x00' * padding
    ext = b''
    flags = 0
    if extended_header and version >= 3:
        flags = 0x40
        ext = (_syncsafe(6) + b'\x01\x00') if version == 4 else (struct.pack('>I', 6) + b'\x00\x00\x00\x00\x00\x00')
    tag_body = ext + body
    return b'ID3' + bytes([version, 0, flags]) + _syncsafe(len(tag_body)) + tag_body


def id3v1(title='', artist='', album='', track=0):
    def pad(text, n):
        return text.encode('latin-1', 'replace')[:n].ljust(n, b'\x00')
    comment = b'\x00' * 28 + b'\x00' + bytes([track])
    return b'TAG' + pad(title, 30) + pad(artist, 30) + pad(album, 30) + b'2001' + comment + b'\x00'


def mp3_bytes(tag=b'', frames=100, xing_frames=None, v1=b'', junk_before=0):
    """An MPEG-1 layer III, 128 kbps, 44.1 kHz stereo file of `frames` frames of silence (about 26 ms each). With
    `xing_frames` the first frame carries a Xing header claiming that many frames."""
    header = b'\xff\xfb\x90\x00'
    body = b''
    for i in range(frames):
        frame = bytearray(header + b'\x00' * (417 - 4))
        if i == 0 and xing_frames is not None:
            frame[36:40] = b'Xing'
            frame[40:44] = struct.pack('>I', 1)                     # flags: a frame count follows
            frame[44:48] = struct.pack('>I', xing_frames)
        body += bytes(frame)
    return tag + b'\x00' * junk_before + body + v1


# ─── FLAC, Ogg ────────────────────────────────────────────────────────────────

def vorbis_comments(comments, vendor='fixture'):
    """[('ARTIST', 'x'), ...] -> the payload of a Vorbis comment block."""
    out = struct.pack('<I', len(vendor)) + vendor.encode() + struct.pack('<I', len(comments))
    for key, value in comments:
        item = ('%s=%s' % (key, value)).encode('utf-8')
        out += struct.pack('<I', len(item)) + item
    return out


def flac_bytes(comments=(), rate=44100, seconds=10, picture=0):
    info = bytearray(34)
    info[0:2] = info[2:4] = struct.pack('>H', 4096)
    packed = (rate << 44) | (1 << 41) | (15 << 36) | (rate * seconds)         # rate, 2 channels, 16 bits, total samples
    info[10:18] = struct.pack('>Q', packed)
    blocks = [(0, bytes(info))]
    if picture:
        blocks.append((6, b'\xcd' * picture))
    blocks.append((4, vorbis_comments(list(comments))))
    out = b'fLaC'
    for i, (kind, data) in enumerate(blocks):
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes([last | kind]) + struct.pack('>I', len(data))[1:] + data
    return out + b'\x00' * 64


def _ogg_page(packets, granule, seq, header_type=0, serial=1):
    table, body = b'', b''
    for pkt in packets:
        n = len(pkt)
        while n >= 255:
            table += b'\xff'
            n -= 255
        table += bytes([n])
        body += pkt
    return b'OggS' + bytes([0, header_type]) + struct.pack('<qIII', granule, serial, seq, 0) + bytes([len(table)]) + table + body


def ogg_bytes(kind='opus', comments=(), seconds=10, pre_skip=312, rate=44100):
    """An Ogg Opus or Ogg Vorbis file: the two header packets, then a last page whose granule position gives the length."""
    if kind == 'opus':
        head = b'OpusHead' + bytes([1, 2]) + struct.pack('<H', pre_skip) + struct.pack('<I', 48000) + b'\x00\x00\x00'
        tags = b'OpusTags' + vorbis_comments(list(comments))
        granule = seconds * 48000 + pre_skip
    else:
        head = b'\x01vorbis' + struct.pack('<I', 0) + bytes([2]) + struct.pack('<I', rate) + b'\x00' * 14
        tags = b'\x03vorbis' + vorbis_comments(list(comments)) + b'\x01'
        granule = seconds * rate
    return (_ogg_page([head], 0, 0, header_type=2) + _ogg_page([tags], 0, 1)
            + _ogg_page([b'\x00' * 40], granule, 2, header_type=4))


# ─── M4A ──────────────────────────────────────────────────────────────────────

def atom(kind, payload=b''):
    return struct.pack('>I', 8 + len(payload)) + kind + payload


def m4a_bytes(title=None, artist=None, album=None, album_artist=None, track=None, disc=None, seconds=10, timescale=1000,
              moov_first=False, art=0):
    def text_item(kind, text):
        return atom(kind, atom(b'data', struct.pack('>II', 1, 0) + text.encode('utf-8')))

    def number_item(kind, n):
        return atom(kind, atom(b'data', struct.pack('>II', 0, 0) + b'\x00\x00' + struct.pack('>HH', n, 0) + b'\x00\x00'))

    items = b''
    for kind, val in ((b'\xa9nam', title), (b'\xa9ART', artist), (b'\xa9alb', album), (b'aART', album_artist)):
        if val is not None:
            items += text_item(kind, val)
    if track is not None:
        items += number_item(b'trkn', track)
    if disc is not None:
        items += number_item(b'disk', disc)
    if art:
        items += atom(b'covr', atom(b'data', struct.pack('>II', 13, 0) + b'\xee' * art))
    mvhd = atom(b'mvhd', b'\x00\x00\x00\x00' + struct.pack('>IIII', 0, 0, timescale, int(seconds * timescale)) + b'\x00' * 80)
    udta = atom(b'udta', atom(b'meta', b'\x00\x00\x00\x00' + atom(b'ilst', items)))
    moov = atom(b'moov', mvhd + udta)
    ftyp = atom(b'ftyp', b'M4A \x00\x00\x00\x00M4A mp42isom')
    mdat = atom(b'mdat', b'\x00' * 2000)
    return ftyp + moov + mdat if moov_first else ftyp + mdat + moov
