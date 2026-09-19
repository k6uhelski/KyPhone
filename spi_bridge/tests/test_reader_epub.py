"""
test_reader_epub.py — the EPUB reader's parser (spi_bridge/reader_epub.py).

Pure standard library: no hardware, no pygame. Books are built in memory with zipfile.

    python3 -m pytest spi_bridge/tests/test_reader_epub.py -v
"""

import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import reader_epub  # noqa: E402
from reader_epub import EpubError  # noqa: E402

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


class TempBooks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def path(self, name='book.epub'):
        return os.path.join(self.dir, name)

    def book(self, chapters, **kw):
        p = self.path()
        make_epub(p, chapters, **kw)
        return reader_epub.load(p)

    def assertDrawable(self, book):
        for ch in book.chapters:
            for text in [ch.title] + [t for _k, t in ch.paras]:
                for c in text:
                    self.assertTrue(' ' <= c <= '~' and c != '|', repr(text))
        for text in (book.title, book.author):
            self.assertTrue(all(' ' <= c <= '~' and c != '|' for c in text), repr(text))


# ─── Text the panel can draw ──────────────────────────────────────────────────

class ToDrawable(unittest.TestCase):
    def test_typographic_punctuation_becomes_ascii(self):
        self.assertEqual(reader_epub.to_drawable('“Hi,” she said — it’s fine…'),
                         '"Hi," she said -- it\'s fine...')

    def test_accents_are_stripped_and_ligatures_expanded(self):
        self.assertEqual(reader_epub.to_drawable('café Ångström naïve ßeta æon'),
                         'cafe Angstrom naive sseta aeon')

    def test_the_wire_separator_never_survives(self):
        self.assertEqual(reader_epub.to_drawable('a|b'), 'a/b')
        self.assertNotIn('·', reader_epub.to_drawable('a·b'))          # the other separator, as a middle dot

    def test_undrawable_runs_become_a_single_question_mark(self):
        self.assertEqual(reader_epub.to_drawable('x 你好世界 y'), 'x ? y')
        self.assertEqual(reader_epub.to_drawable('ok \U0001f600\U0001f600'), 'ok ?')

    def test_invisible_characters_vanish_and_tabs_become_spaces(self):
        self.assertEqual(reader_epub.to_drawable('co\u00adop\u200bera\ttion'), 'coopera tion')

    def test_nothing_outside_printable_ascii_is_ever_returned(self):
        text = ''.join(chr(i) for i in range(0, 0x3000))
        out = reader_epub.to_drawable(text)
        self.assertTrue(all(' ' <= c <= '~' and c != '|' for c in out))


# ─── XHTML → paragraphs ───────────────────────────────────────────────────────

class HtmlToParas(unittest.TestCase):
    def paras(self, body):
        return reader_epub.html_to_paras(xhtml(body))

    def test_paragraphs_and_headings(self):
        self.assertEqual(self.paras('<h1>Chapter One</h1><p>It was dark.</p><p>Then light.</p>'),
                         [('h', 'Chapter One'), ('p', 'It was dark.'), ('p', 'Then light.')])

    def test_whitespace_collapses_and_entities_decode(self):
        self.assertEqual(self.paras('<p>  Tom &amp;\n   Jerry&#8217;s  house  </p>'), [('p', "Tom & Jerry's house")])

    def test_inline_tags_do_not_break_a_paragraph(self):
        self.assertEqual(self.paras('<p>a <em>very</em> <b>bold</b> <span>move</span></p>'), [('p', 'a very bold move')])

    def test_br_and_self_closing_tags_break_lines(self):
        self.assertEqual(self.paras('<p>one<br/>two<br>three</p>'), [('p', 'one'), ('p', 'two'), ('p', 'three')])

    def test_script_style_head_svg_and_images_are_ignored(self):
        body = '<p>keep</p><script>var x = "no";</script><svg><text>nope</text></svg><img src="a.png" alt="alt"/><p>too</p>'
        self.assertEqual(self.paras(body), [('p', 'keep'), ('p', 'too')])
        # xhtml() puts a <title> and a <style> in the head: neither may leak into the text
        self.assertEqual(self.paras('<p>only</p>'), [('p', 'only')])

    def test_lists_get_dashes_and_rules_become_scene_breaks(self):
        self.assertEqual(self.paras('<ul><li>milk</li><li>eggs</li></ul><hr/><p>later</p>'),
                         [('p', '- milk'), ('p', '- eggs'), ('p', '* * *'), ('p', 'later')])

    def test_table_cells_stay_on_one_line(self):
        self.assertEqual(self.paras('<table><tr><td>a</td><td>b</td></tr></table>'), [('p', 'a b')])

    def test_unclosed_tags_and_stray_text_do_not_lose_text(self):
        self.assertEqual(self.paras('loose text<p>one<p>two'), [('p', 'loose text'), ('p', 'one'), ('p', 'two')])

    def test_a_heading_inside_a_wrapper_is_still_a_heading(self):
        self.assertEqual(self.paras('<div><header><h2>Part <i>II</i></h2></header><p>x</p></div>'),
                         [('h', 'Part II'), ('p', 'x')])

    def test_output_is_drawable(self):
        for _k, t in self.paras('<p>“naïve” | 你好</p>'):
            self.assertTrue(all(' ' <= c <= '~' and c != '|' for c in t))

    def test_non_utf8_bytes_do_not_crash(self):
        out = reader_epub.html_to_paras(b'<p>caf\xe9 \x93quoted\x94</p>')            # windows-1252
        self.assertEqual(out, [('p', 'cafe "quoted"')])


# ─── Whole books ──────────────────────────────────────────────────────────────

class LoadBooks(TempBooks):
    CH = [('one.xhtml', xhtml('<h1>Dawn</h1><p>The first chapter.</p>')),
          ('two.xhtml', xhtml('<h1>Dusk</h1><p>The second chapter.</p>'))]

    def test_metadata_and_reading_order(self):
        book = self.book(self.CH, title='The Book', author='A. Writer')
        self.assertEqual((book.title, book.author), ('The Book', 'A. Writer'))
        self.assertEqual([c.paras[1][1] for c in book.chapters], ['The first chapter.', 'The second chapter.'])
        self.assertGreater(book.char_count(), 20)
        self.assertDrawable(book)

    def test_epub3_navigation_document_names_the_chapters(self):
        book = self.book(self.CH, toc={'one.xhtml': 'I. Morning', 'two.xhtml': 'II. Evening'})
        self.assertEqual([c.title for c in book.chapters], ['I. Morning', 'II. Evening'])

    def test_epub2_ncx_names_the_chapters(self):
        book = self.book(self.CH, version=2, toc={'one.xhtml#top': 'Morning', 'two.xhtml': 'Evening'})
        self.assertEqual([c.title for c in book.chapters], ['Morning', 'Evening'])

    def test_without_a_toc_the_heading_then_a_part_number_name_the_chapter(self):
        book = self.book(self.CH + [('three.xhtml', xhtml('<p>No heading here.</p>'))])
        self.assertEqual([c.title for c in book.chapters], ['Dawn', 'Dusk', 'PART 3'])

    def test_non_linear_items_and_non_text_media_are_skipped(self):
        book = self.book(self.CH, manifest_extra='<item id="pic" href="a.jpg" media-type="image/jpeg"/>'
                                                 '<item id="note" href="one.xhtml" media-type="application/xhtml+xml"/>',
                         spine_extra='<itemref idref="pic"/><itemref idref="note" linear="no"/>')
        self.assertEqual(len(book.chapters), 2)

    def test_empty_chapters_and_missing_files_are_dropped(self):
        book = self.book([('cover.xhtml', xhtml('<img src="c.jpg"/>'))] + self.CH + [('gone.xhtml', xhtml('<p>x</p>'))],
                         skip_files=('gone.xhtml',))
        self.assertEqual([c.title for c in book.chapters], ['Dawn', 'Dusk'])

    def test_encoded_hrefs_nested_folders_and_the_opf_at_the_root(self):
        book = self.book([('text/chap one.xhtml', xhtml('<p>spaced</p>'))])
        self.assertEqual(book.chapters[0].paras, [('p', 'spaced')])
        book = self.book([('x.xhtml', xhtml('<p>root</p>'))], opf_dir='')
        self.assertEqual(book.chapters[0].paras, [('p', 'root')])

    def test_member_names_match_regardless_of_case(self):
        p = self.path()
        make_epub(p, [('One.xhtml', xhtml('<p>case</p>'))])
        with zipfile.ZipFile(p) as z:
            data = {n: z.read(n) for n in z.namelist()}
        data['OEBPS/one.xhtml'] = data.pop('OEBPS/One.xhtml')
        with zipfile.ZipFile(p, 'w') as z:
            for n, d in data.items():
                z.writestr(n, d)
        self.assertEqual(reader_epub.load(p).chapters[0].paras, [('p', 'case')])

    def test_a_missing_title_falls_back_to_the_file_name(self):
        book = self.book(self.CH, title='')
        self.assertEqual(book.title, 'book')

    def test_titles_and_authors_are_drawable(self):
        book = self.book(self.CH, title='Café | Nights — 你', author='Zoë')
        self.assertDrawable(book)
        self.assertEqual(book.title, 'Cafe / Nights -- ?')

    def test_read_info_needs_no_chapters(self):
        p = self.path()
        make_epub(p, self.CH, title='Only Info', author='Me', skip_files=('one.xhtml', 'two.xhtml'))
        self.assertEqual(reader_epub.read_info(p), ('Only Info', 'Me'))

    def test_font_obfuscation_is_not_drm(self):
        enc = ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData>'
               '<EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/></EncryptedData></encryption>')
        book = self.book(self.CH, extra={'META-INF/encryption.xml': enc.encode()})
        self.assertEqual(len(book.chapters), 2)


# ─── Books that cannot be read ────────────────────────────────────────────────

class Unreadable(TempBooks):
    def fails(self, expect, path=None, fn=None):
        with self.assertRaises(EpubError) as cm:
            (fn or reader_epub.load)(path or self.path())
        self.assertIn(expect, str(cm.exception))
        self.assertEqual(str(cm.exception), str(cm.exception).upper())     # alert text is upper-case, like the UI's

    def test_a_file_that_is_not_a_zip(self):
        with open(self.path(), 'wb') as f:
            f.write(b'not a zip at all')
        self.fails('NOT A VALID EPUB')

    def test_an_empty_file_and_a_missing_file(self):
        open(self.path(), 'wb').close()
        self.fails('NOT A VALID EPUB')
        self.fails('NOT A VALID EPUB', path=self.path('missing.epub'))

    def test_a_zip_with_no_container(self):
        with zipfile.ZipFile(self.path(), 'w') as z:
            z.writestr('hello.txt', 'hi')
        self.fails('NOT A VALID EPUB')

    def test_a_container_that_points_nowhere(self):
        with zipfile.ZipFile(self.path(), 'w') as z:
            z.writestr('META-INF/container.xml', CONTAINER % 'missing.opf')
        self.fails('COULD NOT READ')

    def test_a_malformed_package_file(self):
        make_epub(self.path(), [], opf_override=b'<package><metadata>')
        self.fails('COULD NOT READ')

    def test_a_book_with_no_chapters(self):
        make_epub(self.path(), [])
        self.fails('NO READABLE TEXT')

    def test_a_book_whose_chapters_are_all_empty(self):
        make_epub(self.path(), [('a.xhtml', xhtml('<img src="x.png"/>'))])
        self.fails('NO READABLE TEXT')

    def test_drm_is_refused(self):
        enc = ('<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><EncryptedData>'
               '<EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/></EncryptedData></encryption>')
        make_epub(self.path(), [('a.xhtml', xhtml('<p>x</p>'))], extra={'META-INF/encryption.xml': enc.encode()})
        self.fails('COPY PROTECTED')
        self.fails('COPY PROTECTED', fn=reader_epub.read_info)

    def test_entity_declarations_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE p [<!ENTITY a "aaaa">]><package/>'
        make_epub(self.path(), [('a.xhtml', xhtml('<p>x</p>'))], opf_override=bomb)
        self.fails('NOT A VALID EPUB')

    def test_an_oversize_chapter_is_refused(self):
        make_epub(self.path(), [('a.xhtml', xhtml('<p>%s</p>' % ('word ' * 2000)))])
        old = reader_epub.MAX_MEMBER_BYTES
        reader_epub.MAX_MEMBER_BYTES = 1000
        try:
            self.fails('TOO LARGE')
        finally:
            reader_epub.MAX_MEMBER_BYTES = old

    def test_a_book_over_the_total_limit_is_refused(self):
        make_epub(self.path(), [('a.xhtml', xhtml('<p>%s</p>' % ('word ' * 400))), ('b.xhtml', xhtml('<p>%s</p>' % ('word ' * 400)))])
        old = reader_epub.MAX_TOTAL_BYTES
        reader_epub.MAX_TOTAL_BYTES = 3000
        try:
            self.fails('TOO LARGE')
        finally:
            reader_epub.MAX_TOTAL_BYTES = old

    def test_a_corrupt_chapter_is_reported_not_raised_raw(self):
        p = self.path()
        make_epub(p, [('a.xhtml', xhtml('<p>%s</p>' % ('hello ' * 500)))])
        raw = bytearray(open(p, 'rb').read())
        marker = raw.find(b'hello')                 # the zip is stored, not deflated: this is the chapter's own bytes
        self.assertGreater(marker, 0)
        raw[marker] ^= 0xFF                         # now the chapter fails its checksum
        open(p, 'wb').write(bytes(raw))
        self.fails('COULD NOT READ')


if __name__ == '__main__':
    unittest.main()
