# EPUB chapter extraction

Run from this directory:

```sh
uv run --no-project epub_to_chapters.py "Another Book.epub"
```

The default output directory is the EPUB's full path with `.epub` removed.
The source EPUB is unchanged. Existing files are never overwritten: a nonempty
output directory is refused. Choose another directory with `--output`.

```sh
uv run --no-project epub_to_chapters.py "Another Book.epub" --dry-run
uv run --no-project epub_to_chapters.py "Another Book.epub" --output "Extracted Book"
```

If uv's usual cache directory is unavailable, prefix the command with
`UV_CACHE_DIR=/tmp/sh-uv-cache`.

## Files

- `Chapter00.txt`: all readable material before the first chapter, if present.
- `Chapter01.txt`, `Chapter02.txt`, etc.: chapters in reading order, numbered
  sequentially, using UTF-8 and preserving paragraphs and punctuation.
- `ChapterXX.txt`: material after the final chapter, if identified and nonempty.

The script reads EPUB 2 NCX or EPUB 3 navigation contents, then follows the EPUB's
spine (reading order). Chapters may span several documents or share a document
using anchor links. Separate part headings between chapters are attached to the
following chapter; everything before Chapter 1 stays in `Chapter00.txt`.
It checks that splitting loses or duplicates no extracted text before writing.
Images contribute their alt text where supplied; image-only text requires OCR
and cannot be recovered by this script. Formatting such as italics is removed.

## Chapter detection

Explicit chapter entries and numbered chapter labels are preferred. Otherwise,
the script tries chapter document filenames, substantial named contents entries
(excluding common front/back matter and part headings), then chapter headings.
Recognised back matter
(such as acknowledgements or an epilogue) goes in `ChapterXX.txt`.

EPUB structures vary. Inspect the dry run when reusing the script. If a book has
ambiguous or unusual chapter names, select the appropriate contents labels with
a regular expression:

```sh
uv run --no-project epub_to_chapters.py "Another Book.epub" --chapter-pattern '^Chapter\s+\d+'
```

This option selects labels from the contents; output numbering remains sequential.
For books whose contents list only parts, but whose chapters have separate HTML
documents, select those documents instead:

```sh
uv run --no-project epub_to_chapters.py "Another Book.epub" --chapter-files 'chapter\d+\.x?html$'
```

`--chapter-files` matches paths inside the EPUB ZIP archive. Each matching
document starts a chapter; subsequent documents continue it until the next
chapter, part heading, or back matter boundary. You can inspect the archive's
filenames with `unzip -l "Another Book.epub"` to choose a suitable pattern.

If the contents list only parts and inspection confirms that each chapter is a
separate document in reading order, use:

```sh
uv run --no-project epub_to_chapters.py "Another Book.epub" --spine-chapters
```

This starts chapters at each nonempty document after the first part heading,
excluding separate part pages and identified back matter. It also preserves
intermissions as separate sections. Use this option only after checking the
structure: some EPUBs split a single chapter across multiple documents.

Books without reliable chapter boundaries or identifiable back matter may need
book-specific adjustments. The script uses only Python's standard library.