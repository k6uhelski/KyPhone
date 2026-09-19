"""epub_fixtures.py — build small EPUB files in tests (shared by the reader's test files)."""

import zipfile

CONTAINER = ('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="%s" media-type="application/oebps-package+xml"/></rootfiles></container>')


def xhtml(body, title='t'):
    return ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title>'
            '<style>p{color:red}</style></head><body>%s</body></html>' % (title, body)).encode('utf-8')


def make_epub(path, chapters, title='A Book', author='An Author', opf_dir='OEBPS', version=3,
              toc=None, extra=None, spine_extra='', manifest_extra='', opf_override=None, skip_files=()):
    """chapters: [(file name, bytes)]. toc: {file name: title}. extra: {zip name: bytes}."""
    ids = ['c%d' % i for i in range(len(chapters))]
    manifest = ''.join('<item id="%s" href="%s" media-type="application/xhtml+xml"/>' % (i, quote_href(f))
                       for i, (f, _d) in zip(ids, chapters))
    spine = ''.join('<itemref idref="%s"/>' % i for i in ids)
    nav_item = ncx_item = ''
    files = {}
    if toc is not None and version == 3:
        nav_item = '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        links = ''.join('<li><a href="%s">%s</a></li>' % (quote_href(f), t) for f, t in toc.items())
        files['nav.xhtml'] = ('<html xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>%s</ol>'
                              '</nav></body></html>' % links).encode()
    if toc is not None and version == 2:
        ncx_item = '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        points = ''.join('<navPoint id="n%d"><navLabel><text>%s</text></navLabel><content src="%s"/></navPoint>'
                         % (k, t, quote_href(f)) for k, (f, t) in enumerate(toc.items()))
        files['toc.ncx'] = ('<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>%s</navMap></ncx>' % points).encode()
    opf = opf_override or (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="%d.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">%s%s</metadata>'
        '<manifest>%s%s%s%s</manifest><spine%s>%s%s</spine></package>'
        % (version, ('<dc:title>%s</dc:title>' % title) if title else '',
           ('<dc:creator>%s</dc:creator>' % author) if author else '',
           manifest, nav_item, ncx_item, manifest_extra, ' toc="ncx"' if ncx_item else '', spine, spine_extra)).encode()
    base = (opf_dir + '/') if opf_dir else ''
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', CONTAINER % (base + 'content.opf'))
        z.writestr(base + 'content.opf', opf)
        for f, data in chapters:
            if f not in skip_files:
                z.writestr(base + f, data)
        for f, data in files.items():
            z.writestr(base + f, data)
        for f, data in (extra or {}).items():
            z.writestr(f, data)


def quote_href(name):
    return name.replace(' ', '%20')
