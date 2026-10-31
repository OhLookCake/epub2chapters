#!/usr/bin/env python3
"""Split an EPUB into chapter text files using its contents and reading order.

Usage: uv run --no-project epub_to_chapters.py "Book.epub"
Uses only the Python standard library; no packages need to be installed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import posixpath
import re
import sys
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def clean(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u00ad", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def target(base: str, href: str) -> tuple[str, str]:
    url = urlsplit(href)
    if url.scheme or url.netloc:
        raise ValueError(f"External contents link is unsupported: {href}")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(url.path))) if url.path else base
    return path, unquote(url.fragment)


@dataclass
class Entry:
    path: str
    fragment: str
    label: str
    kind: str = ""


class TextParser(HTMLParser):
    """Keep paragraph breaks, inline word joins, anchors, and heading offsets."""

    BLOCKS = {"address", "article", "aside", "blockquote", "div", "dl", "dt", "dd",
              "figcaption", "figure", "footer", "header", "li", "main", "nav", "ol",
              "p", "pre", "section", "table", "tr", "ul", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self, data: bytes):
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.length = 0
        self.anchors: dict[str, int] = {}
        self.headings: list[tuple[int, str, str]] = []
        self.heading: tuple[int, str, str] | None = None
        self.in_body = False
        self.skip = 0
        encoding = re.search(br"encoding\s*=\s*['\"]([^'\"]+)", data[:200])
        self.feed(data.decode(encoding.group(1).decode() if encoding else "utf-8-sig"))
        self.close()
        self.text = "".join(self.chunks)

    def add(self, value: str) -> None:
        self.chunks.append(value)
        self.length += len(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == "body":
            self.in_body = True
        if not self.in_body:
            return
        if tag in {"script", "style"}:
            self.skip += 1
        if self.skip:
            return
        if tag in self.BLOCKS:
            self.add("\n\n")
        for key in ("id", "name"):
            if attrs.get(key):
                self.anchors.setdefault(attrs[key], self.length)
        if re.fullmatch(r"h[1-6]", tag):
            self.heading = (self.length, tag, attrs.get("class", "") or "")
        if tag == "br":
            self.add("\n")
        elif tag == "hr":
            self.add("\n\n* * *\n\n")
        elif tag in {"td", "th"}:
            self.add(" ")
        elif tag == "img" and attrs.get("alt"):
            self.add(attrs["alt"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_body:
            return
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if self.heading and tag == self.heading[1]:
            start, _, classes = self.heading
            self.headings.append((start, clean("".join(self.chunks)[start:]), classes))
            self.heading = None
        if tag in self.BLOCKS:
            self.add("\n\n")
        if tag == "body":
            self.in_body = False

    def handle_data(self, data: str) -> None:
        if self.in_body and not self.skip:
            # HTML source line wrapping is not a paragraph or line break.
            self.add(re.sub(r"\s+", " ", data))


NUMBER = r"(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty(?:[ -]\w+)?)"
CHAPTER = re.compile(rf"^(?:(?:chapter|chap\.?|ch\.)\s*{NUMBER}\b|{NUMBER}\s*[.:]?\s*$)", re.I)
FRONT = re.compile(r"^(?:cover|title(?: page)?|copyright|dedication|contents|table of contents|also by|praise|epigraph|prologue|preface|foreword|introduction)\b", re.I)
BACK = re.compile(r"^(?:epilogue|afterword|acknowledg\w*|appendi\w*|notes|endnotes|footnotes|bibliography|index|glossary|about (?:the )?author|also (?:by|available)|other books|a note|reading group|book club|discussion|extras|excerpt|preview|continue reading|credits|copyright|colophon)\b", re.I)
PART = re.compile(r"^(?:(?:part|book|volume|act)\s+|intermission\b)", re.I)
CHAPTER_FILE = re.compile(r"(?:^|[/_-])(?:chapter|ch|c)[_-]?\d+(?:[_-]|\.x?html?$)", re.I)
BACK_FILE = re.compile(r"(?:^|/)(?:acknow\w*|afterword|epilogue|appendix|endnotes|bibliography|colophon)\.x?html?$", re.I)


def contents(z: ZipFile, manifest: dict[str, ET.Element], package: str, spine: ET.Element) -> list[Entry]:
    nav = next((item for item in manifest.values() if "nav" in item.get("properties", "").split()), None)
    if nav is not None:
        path, _ = target(package, nav.attrib["href"])
        root = ET.fromstring(z.read(path))
        for node in root.iter():
            types = node.get("{http://www.idpf.org/2007/ops}type", "").split()
            if local(node.tag) == "nav" and ("toc" in types or node.get("role") == "doc-toc"):
                entries = []
                for a in node.iter():
                    if local(a.tag) == "a" and a.get("href"):
                        p, fragment = target(path, a.attrib["href"])
                        entries.append(Entry(p, fragment, " ".join("".join(a.itertext()).split()), a.get("{http://www.idpf.org/2007/ops}type", "")))
                if entries:
                    return entries
    ncx = manifest.get(spine.get("toc", ""))
    if ncx is None:
        ncx = next((item for item in manifest.values() if item.get("media-type") == "application/x-dtbncx+xml"), None)
    if ncx is None:
        return []
    path, _ = target(package, ncx.attrib["href"])
    entries = []
    for point in ET.fromstring(z.read(path)).iter():
        if local(point.tag) != "navPoint":
            continue
        label = next((e for e in point if local(e.tag) == "navLabel"), None)
        link = next((e for e in point if local(e.tag) == "content"), None)
        if label is not None and link is not None:
            p, fragment = target(path, link.attrib["src"])
            entries.append(Entry(p, fragment, " ".join("".join(label.itertext()).split()), point.get("class", "")))
    return entries


def split_epub(source: Path, chapter_pattern: str | None = None, chapter_files: str | None = None,
               spine_chapters: bool = False) -> tuple[dict[str, str], str]:
    with ZipFile(source) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        package = next(e.attrib["full-path"] for e in container.iter() if local(e.tag) == "rootfile")
        root = ET.fromstring(z.read(package))
        manifest_node = next(e for e in root if local(e.tag) == "manifest")
        manifest = {e.attrib["id"]: e for e in manifest_node}
        spine = next(e for e in root if local(e.tag) == "spine")
        documents = {}
        starts = {}
        chunks = []
        length = 0
        for ref in spine:
            item = manifest[ref.attrib["idref"]]
            if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            path, _ = target(package, item.attrib["href"])
            if path in documents:
                raise ValueError(f"Repeated document in reading order: {path}")
            doc = TextParser(z.read(path))
            documents[path] = doc
            starts[path] = length
            chunks.append(doc.text + "\n\n")
            length += len(chunks[-1])
        text = "".join(chunks)
        toc = contents(z, manifest, package, spine)

    def position(entry: Entry) -> int | None:
        if entry.path not in starts:
            return None
        offset = 0
        if entry.fragment:
            if entry.fragment not in documents[entry.path].anchors:
                raise ValueError(f"Contents anchor not found: {entry.path}#{entry.fragment}")
            offset = documents[entry.path].anchors[entry.fragment]
        return starts[entry.path] + offset

    pattern = re.compile(chapter_pattern, re.I) if chapter_pattern else None

    def is_chapter(entry: Entry) -> bool:
        if pattern:
            return bool(pattern.search(entry.label))
        if FRONT.search(entry.label) or BACK.search(entry.label) or PART.search(entry.label):
            return False
        return "chapter" in entry.kind.split() or bool(CHAPTER.search(entry.label))

    # Do not mistake chapter labels in an appended sample of another book for
    # chapters in this book. Copyright/front matter is not an ending marker.
    back_positions = [position(e) for e in toc if e.path in starts and BACK.search(e.label) and not FRONT.search(e.label)]
    back_positions += [starts[path] for path in documents if BACK_FILE.search(path)]
    back_paths = {e.path for e in toc if e.path in starts and BACK.search(e.label) and not FRONT.search(e.label)}
    back_paths.update(path for path in documents if BACK_FILE.search(path))
    cutoff = min((p for p in back_positions if p is not None), default=len(text))
    part_entries = [e for e in toc if e.path in starts and (PART.search(e.label) or "part" in e.kind.split())]
    part_positions = [position(e) for e in part_entries]
    first_part = min((p for p in part_positions if p is not None and p < cutoff), default=None)
    eligible = [e for e in toc if e.path in starts and (pattern or position(e) < cutoff)]
    chapters = [e for e in eligible if is_chapter(e)]
    method = "table of contents"
    if spine_chapters:
        if first_part is None:
            raise ValueError("--spine-chapters requires a part heading in the table of contents")
        part_paths = {e.path for e in part_entries}
        chapters = [Entry(path, "", posixpath.basename(path)) for path, doc in documents.items()
                    if first_part < starts[path] < cutoff and path not in part_paths and path not in back_paths and clean(doc.text)]
        method = "reading-order documents after the first part heading (--spine-chapters)"
    elif chapter_files or (not chapters and not pattern):
        file_pattern = re.compile(chapter_files, re.I) if chapter_files else CHAPTER_FILE
        chapters = [Entry(path, "", posixpath.basename(path)) for path in documents
                    if file_pattern.search(path) and starts[path] < cutoff and path not in back_paths]
        method = "chapter document filenames"
    if not chapters and not pattern and not chapter_files and not spine_chapters:
        # Named chapters: use contents entries that are not recognisable book matter.
        chapters = [e for e in eligible if not (FRONT.search(e.label) or BACK.search(e.label) or PART.search(e.label))
                    and len(clean(documents[e.path].text)) > 200
                    and (first_part is None or position(e) > first_part)]
        method = "named entries in the table of contents"
    boundaries = [(position(e), e.label) for e in chapters]
    if not boundaries and not pattern and not chapter_files and not spine_chapters:
        method = "chapter headings"
        for path, doc in documents.items():
            for offset, label, classes in doc.headings:
                if starts[path] + offset < cutoff and (CHAPTER.search(label) or "chapter" in classes.split()):
                    boundaries.append((starts[path] + offset, label))
    if not boundaries:
        raise ValueError("No chapters identified. Try --chapter-pattern for contents labels or --chapter-files for spine document paths.")
    boundaries = sorted(dict(boundaries).items())
    first, last = boundaries[0][0], boundaries[-1][0]
    # Recognised back matter ends the last chapter, including fragment boundaries.
    ends = [position(e) for e in toc if e.path in starts and BACK.search(e.label) and not is_chapter(e)]
    ends += [starts[path] for path in documents if BACK_FILE.search(path)]
    ends += [starts[p] + offset for p, doc in documents.items() for offset, label, _ in doc.headings if BACK.search(label)]
    end = min((p for p in ends if p is not None and p > last), default=len(text))
    # A separate part-title page belongs with the following chapter. Everything
    # before the first chapter remains in Chapter00, as requested.
    for i in range(1, len(boundaries)):
        previous, current = boundaries[i - 1][0], boundaries[i][0]
        parts = [position(e) for e in toc if e.path in starts and (PART.search(e.label) or "part" in e.kind.split())]
        candidates = [p for p in parts if p is not None and previous < p < current]
        if candidates:
            boundaries[i] = (min(candidates), boundaries[i][1])
    files = {}
    if front := clean(text[:first]):
        files["Chapter00.txt"] = front + "\n"
    for i, (start, _) in enumerate(boundaries):
        stop = boundaries[i + 1][0] if i + 1 < len(boundaries) else end
        body = clean(text[start:stop])
        if not body:
            raise ValueError(f"Chapter {i + 1} has no readable text")
        files[f"Chapter{i + 1:02d}.txt"] = body + "\n"
    if back := clean(text[end:]):
        files["ChapterXX.txt"] = back + "\n"
    # Check that segmentation neither loses nor duplicates any readable text.
    compact = lambda s: re.sub(r"\s+", "", clean(s))
    if compact("".join(files.values())) != compact(text):
        raise ValueError("Text preservation check failed")
    return files, f"{len(boundaries)} chapters identified from {method}; text preservation check passed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path)
    parser.add_argument("-o", "--output", type=Path, help="Output directory (default: EPUB path without .epub)")
    parser.add_argument("--chapter-pattern", help="Regex selecting chapter labels in the table of contents")
    parser.add_argument("--chapter-files", help="Regex selecting chapter-start document paths in reading order")
    parser.add_argument("--spine-chapters", action="store_true", help="Treat each nonempty reading-order document after the first part heading and before back matter as a chapter; inspect the EPUB first")
    parser.add_argument("--dry-run", action="store_true", help="List planned files without writing")
    args = parser.parse_args()
    try:
        if sum(bool(value) for value in (args.chapter_pattern, args.chapter_files, args.spine_chapters)) > 1:
            raise ValueError("Use only one of --chapter-pattern, --chapter-files, or --spine-chapters")
        files, summary = split_epub(args.epub, args.chapter_pattern, args.chapter_files, args.spine_chapters)
        output = args.output or args.epub.with_suffix("")
        if output.exists() and not output.is_dir():
            raise ValueError(f"Output path is not a directory: {output}")
        if not args.dry_run and output.exists() and any(output.iterdir()):
            raise ValueError(f"Output directory is not empty: {output}. Choose a new directory with --output.")
        print(summary)
        print(f"Output: {output}")
        if not args.dry_run:
            output.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            print(f"  {name}: {len(text.split()):,} words")
            if not args.dry_run:
                with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
        return 0
    except (OSError, ValueError, KeyError, LookupError, StopIteration, ET.ParseError, BadZipFile, re.error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
