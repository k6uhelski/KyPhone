"""reader_epub.py — read an EPUB for the KyPhone reader (standard library only).

An EPUB is a zip file: META-INF/container.xml names the package file (OPF), which lists
the chapters in reading order (the "spine"). This module never extracts anything to
disk; it reads the members it needs and turns each chapter's XHTML into a list of
paragraphs the layout code can wrap:

    book = load('data/books/dune.epub')
    book.title, book.author
    book.chapters[0].title
    book.chapters[0].paras        # [('h', 'CHAPTER ONE'), ('p', 'It was a dark ...'), ...]

Every string that leaves this module is drawable on the panel: printable ASCII only,
with no '|' (a wire separator). Accents are stripped, typographic punctuation is turned
into its ASCII look-alike, and anything else becomes '?'.

Limits: text only (images are skipped), no DRM, no right-to-left or non-Latin scripts.
Problems raise EpubError with a short message the UI can show on a stop alert.
"""

import posixpath
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote

MAX_MEMBER_BYTES = 8 * 1024 * 1024        # most any one file inside the book may unpack to
MAX_TOTAL_BYTES = 64 * 1024 * 1024        # most all the chapters together may unpack to
MAX_CHAPTERS = 5000

DC = 'http://purl.org/dc/elements/1.1/'
FONT_OBFUSCATION = ('http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC')
TEXT_TYPES = ('application/xhtml+xml', 'text/html', 'application/xml', 'text/xml')


class EpubError(Exception):
    """The book cannot be read; str(e) is a short reason fit for an on-screen alert."""


# ─── Text the panel can draw ──────────────────────────────────────────────────

_TRANSLIT = {
    '‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'",
    '“': '"', '”': '"', '„': '"', '‟': '"', '″': '"',
    '–': '-', '—': '--', '―': '--', '‒': '-', '−': '-', '‐': '-', '‑': '-',
    '…': '...', '•': '*', '●': '*', '◦': '*', '·': '.', '‧': '.',
    ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', ' ': ' ', '　': ' ',
    '«': '"', '»': '"', '‹': "'", '›': "'",
    'ß': 'ss', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE',
    'ø': 'o', 'Ø': 'O', 'đ': 'd', 'Đ': 'D', 'ł': 'l', 'Ł': 'L',
    'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th',
    '©': '(c)', '®': '(R)', '™': '(TM)', '°': ' deg', '×': 'x', '½': '1/2',
    '¼': '1/4', '¾': '3/4', '£': 'GBP', '€': 'EUR', '¥': 'JPY', '§': 'S.',
    '|': '/',
}
_INVISIBLE = dict.fromkeys(map(ord, '­​‌‍‎‏⁠﻿'), None)


def to_drawable(text):
    """Printable ASCII with no '|': typographic punctuation and accents are reduced to their
    plain look-alikes, other characters become '?' (a run of them becomes a single '?')."""
    out = []
    for ch in text.translate(_INVISIBLE):
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
            continue
        if ' ' <= ch <= '~':
            out.append(ch)
            continue
        if ch in '\t\n\r\f\v':
            out.append(' ')
            continue
        base = ''.join(c for c in unicodedata.normalize('NFKD', ch) if not unicodedata.combining(c))
        mapped = ''.join(_TRANSLIT.get(c, c) for c in base)
        if mapped and all(' ' <= c <= '~' for c in mapped):
            out.append(mapped)
        elif not out or out[-1] != '?':
            out.append('?')
    return ''.join(out)


def _squash(text):
    return re.sub(r' +', ' ', to_drawable(text).replace('\n', ' ')).strip()


# ─── XHTML → paragraphs ───────────────────────────────────────────────────────

_BLOCKS = {'p', 'div', 'section', 'article', 'blockquote', 'li', 'ul', 'ol', 'tr', 'table', 'pre', 'dd', 'dt',
           'dl', 'figure', 'figcaption', 'header', 'footer', 'aside', 'main', 'address', 'caption', 'body',
           'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'hr', 'nav', 'details', 'summary'}
_HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
_SKIPPED = {'script', 'style', 'head', 'svg', 'math', 'title', 'noscript', 'template', 'object', 'iframe'}
_CELLS = {'td', 'th'}


class _TextExtractor(HTMLParser):
    """Collects paragraphs as ('p', text) or ('h', text). Block tags end a paragraph."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras = []
        self._buf = []
        self._skip = 0
        self._heading = 0

    def _flush(self, kind=None):
        text = _squash(''.join(self._buf))
        self._buf = []
        if text:
            self.paras.append((kind or ('h' if self._heading else 'p'), text))

    def handle_starttag(self, tag, attrs):
        if tag in _SKIPPED:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _BLOCKS:
            self._flush()
            if tag in _HEADINGS:
                self._heading += 1
            elif tag == 'hr':
                self.paras.append(('p', '* * *'))
            elif tag == 'li':
                self._buf.append('- ')
        elif tag in _CELLS:
            self._buf.append(' ')

    def handle_startendtag(self, tag, attrs):
        # <br/> and <hr/> arrive as start+end; only a "void" tag has no matching end tag to balance
        if tag in ('br', 'hr') or tag in _BLOCKS:
            self.handle_starttag(tag, attrs)
            if tag in _HEADINGS:
                self._heading = max(0, self._heading - 1)
        elif tag in _SKIPPED:
            return
        else:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _SKIPPED:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in _BLOCKS:
            self._flush()
            if tag in _HEADINGS:
                self._heading = max(0, self._heading - 1)

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _decode(data):
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', 'replace')


def html_to_paras(data):
    """XHTML/HTML bytes -> list of ('p'|'h', drawable text)."""
    parser = _TextExtractor()
    parser.feed(_decode(data))
    parser.close()
    return parser.paras


# ─── The zip and its package file ─────────────────────────────────────────────

def _local(tag):
    return tag.rsplit('}', 1)[-1] if isinstance(tag, str) else ''


def _xml(data):
    if b'<!ENTITY' in data:                       # no entity tricks ("billion laughs"); no real book needs them
        raise EpubError('NOT A VALID EPUB FILE')
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise EpubError('COULD NOT READ THE BOOK')


class _Package:
    """An opened EPUB: zip + parsed OPF. Reads members on request with a size cap."""

    def __init__(self, path):
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            raise EpubError('NOT A VALID EPUB FILE')
        try:
            self._names = {n: n for n in self.zip.namelist()}
            self._lower = {n.lower(): n for n in self._names}
            self._check_drm()
            self.opf_path = self._find_opf()
            root = _xml(self.read(self.opf_path))
        except Exception:
            self.zip.close()
            raise
        self.base = posixpath.dirname(self.opf_path)
        self.manifest = {}                        # id -> (href, media_type, properties)
        self.spine = []                           # [(id, linear)]
        self.spine_toc = None
        self.title, self.author = '', ''
        for el in root:
            name = _local(el.tag)
            if name == 'metadata':
                for md in el:
                    md_name = _local(md.tag)
                    text = ' '.join((md.text or '').split())
                    if md_name == 'title' and text and not self.title:
                        self.title = text
                    elif md_name == 'creator' and text and not self.author:
                        self.author = text
            elif name == 'manifest':
                for item in el:
                    if _local(item.tag) == 'item' and item.get('id') and item.get('href'):
                        self.manifest[item.get('id')] = (item.get('href'), item.get('media-type', ''),
                                                         (item.get('properties') or '').split())
            elif name == 'spine':
                self.spine_toc = el.get('toc')
                for ref in el:
                    if _local(ref.tag) == 'itemref' and ref.get('idref'):
                        self.spine.append((ref.get('idref'), ref.get('linear', 'yes') != 'no'))

    def close(self):
        self.zip.close()

    def _member(self, name):
        if name in self._names:
            return name
        return self._lower.get(name.lower())

    def read(self, name):
        member = self._member(name)
        if member is None:
            raise EpubError('COULD NOT READ THE BOOK')
        info = self.zip.getinfo(member)
        if info.file_size > MAX_MEMBER_BYTES:
            raise EpubError('THE BOOK IS TOO LARGE')
        try:
            with self.zip.open(member) as f:
                data = f.read(MAX_MEMBER_BYTES + 1)
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError):
            raise EpubError('COULD NOT READ THE BOOK')
        if len(data) > MAX_MEMBER_BYTES:
            raise EpubError('THE BOOK IS TOO LARGE')
        return data

    def resolve(self, href):
        """An href from the OPF (relative, possibly %-encoded, possibly with #fragment) -> zip member name."""
        href = unquote(href.split('#', 1)[0])
        return posixpath.normpath(posixpath.join(self.base, href)) if href else ''

    def _find_opf(self):
        member = self._member('META-INF/container.xml')
        if member is None:
            raise EpubError('NOT A VALID EPUB FILE')
        root = _xml(self.read(member))
        for el in root.iter():
            if _local(el.tag) == 'rootfile' and el.get('full-path'):
                return unquote(el.get('full-path'))
        raise EpubError('NOT A VALID EPUB FILE')

    def _check_drm(self):
        member = self._member('META-INF/encryption.xml')
        if member is None:
            return
        try:
            root = _xml(self.zip.read(member))
        except EpubError:
            raise EpubError('THIS BOOK IS COPY PROTECTED')
        for el in root.iter():
            if _local(el.tag) == 'EncryptionMethod' and el.get('Algorithm') not in FONT_OBFUSCATION:
                raise EpubError('THIS BOOK IS COPY PROTECTED')

    def reading_order(self):
        """[(zip member name, manifest id)] of the readable chapters, in order."""
        out = []
        for idref, linear in self.spine:
            entry = self.manifest.get(idref)
            if not linear or entry is None:
                continue
            href, media, _props = entry
            if media and media not in TEXT_TYPES:
                continue
            name = self.resolve(href)
            if self._member(name) is not None:
                out.append((self._member(name), name))
        return out

    def toc_titles(self):
        """{zip member name: chapter title} from the EPUB 3 nav document or the EPUB 2 NCX, if any."""
        titles = {}

        def add(href, text):
            name = self.resolve(href)
            text = _squash(text)
            if name and text and name not in titles:
                titles[name] = text

        for _id, (href, _media, props) in self.manifest.items():
            if 'nav' in props:
                try:
                    _NavCollector(self, add).run(self.read(self.resolve(href)))
                except EpubError:
                    pass
        if not titles and self.spine_toc in self.manifest:
            try:
                ncx = _xml(self.read(self.resolve(self.manifest[self.spine_toc][0])))
            except EpubError:
                ncx = None
            if ncx is not None:
                for point in ncx.iter():
                    if _local(point.tag) != 'navPoint':
                        continue
                    label = src = None
                    for child in point:
                        if _local(child.tag) == 'navLabel':
                            label = ''.join(t.text or '' for t in child.iter() if _local(t.tag) == 'text')
                        elif _local(child.tag) == 'content':
                            src = child.get('src')
                    if label and src:
                        add(src, label)
        return titles


class _NavCollector(HTMLParser):
    """Pulls (href, text) out of the <nav epub:type="toc"> list of an EPUB 3 navigation document."""

    def __init__(self, package, add):
        super().__init__(convert_charrefs=True)
        self.package, self.add = package, add
        self._in_toc = 0
        self._href = None
        self._text = []

    def run(self, data):
        self.feed(_decode(data))
        self.close()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'nav' and 'toc' in (a.get('epub:type') or a.get('type') or ''):
            self._in_toc = 1
        elif self._in_toc and tag == 'a' and a.get('href'):
            self._href, self._text = a['href'], []

    def handle_endtag(self, tag):
        if tag == 'nav':
            self._in_toc = 0
        elif tag == 'a' and self._href is not None:
            self.add(self._href, ''.join(self._text))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


# ─── The public API ───────────────────────────────────────────────────────────

class Chapter:
    def __init__(self, title, paras):
        self.title = title
        self.paras = paras                        # [('p'|'h', text)]; empty for a cover page or a blank divider

    def char_count(self):
        return sum(len(t) for _k, t in self.paras)


class Book:
    """An opened book. Chapters are parsed only when asked for (a large novel is millions of characters and the
    phone's processor is slow), so opening is instant; keep the Book while reading and close() it afterwards.

        book = load(path)
        book.title, book.author, len(book)          # len = number of chapters in reading order
        book.chapter(i).paras                       # parsed on first use; the last few stay cached
        book.next_with_text(i, +1)                  # the next chapter that has any text, or None
        book.progress(i, fraction)                  # 0..1 through the whole book (by size, so approximate)
    """
    CACHE = 4

    def __init__(self, pkg, path, title, author, order, titles):
        self._pkg = pkg
        self.path = path
        self.title = title
        self.author = author
        self._order = order                       # [(zip member, resolved name)]
        self._titles = titles                     # {resolved name: title}
        self._cache = {}
        self._used = []
        self.weights = [max(1, pkg.zip.getinfo(member).file_size) for member, _n in order]
        self._total = float(sum(self.weights))

    def __len__(self):
        return len(self._order)

    def close(self):
        self._pkg.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def chapter(self, i):
        if not 0 <= i < len(self._order):
            raise IndexError('chapter %d of %d' % (i, len(self._order)))
        if i in self._cache:
            self._used.remove(i)
            self._used.append(i)
            return self._cache[i]
        member, name = self._order[i]
        paras = html_to_paras(self._pkg.read(member))
        title = self._titles.get(name)
        if not title:
            heading = next((t for k, t in paras[:3] if k == 'h'), None)
            title = heading or 'PART %d' % (i + 1)
        chapter = self._cache[i] = Chapter(title, paras)
        self._used.append(i)
        while len(self._used) > self.CACHE:
            del self._cache[self._used.pop(0)]
        return chapter

    def next_with_text(self, i, step):
        """The nearest chapter after (step=+1) or before (step=-1) chapter i that has text; None at the ends."""
        j = i + step
        while 0 <= j < len(self._order):
            if self.chapter(j).paras:
                return j
            j += step
        return None

    def first_with_text(self):
        """Index of the first chapter with any text. Raises EpubError if the book has none."""
        if self.chapter(0).paras:
            return 0
        j = self.next_with_text(0, +1)
        if j is None:
            raise EpubError('THE BOOK HAS NO READABLE TEXT')
        return j

    def progress(self, i, fraction):
        """How far through the book (0..1) a position `fraction` of the way through chapter i is, by file size."""
        before = float(sum(self.weights[:i]))
        return min(1.0, max(0.0, (before + self.weights[i] * min(1.0, max(0.0, fraction))) / self._total))


def _title_of(pkg, path):
    title = _squash(pkg.title)
    if not title:
        base = posixpath.basename(str(path).replace('\\', '/'))
        title = _squash(re.sub(r'\.epub$', '', base, flags=re.I).replace('_', ' '))
    return title or 'UNTITLED'


def read_info(path):
    """(title, author) without reading any chapter — for the library list. Raises EpubError."""
    pkg = _Package(path)
    try:
        return _title_of(pkg, path), _squash(pkg.author)
    finally:
        pkg.close()


def load(path):
    """Open a book. Raises EpubError (message fit for an alert) if it cannot be read. Close it when done."""
    pkg = _Package(path)
    try:
        order = pkg.reading_order()[:MAX_CHAPTERS]
        if not order:
            raise EpubError('THE BOOK HAS NO READABLE TEXT')
        if sum(pkg.zip.getinfo(member).file_size for member, _n in order) > MAX_TOTAL_BYTES:
            raise EpubError('THE BOOK IS TOO LARGE')
        book = Book(pkg, path, _title_of(pkg, path), _squash(pkg.author), order, pkg.toc_titles())
        book.first_with_text()                    # a book of nothing but pictures is refused now, not at page one
        return book
    except Exception:
        pkg.close()
        raise
