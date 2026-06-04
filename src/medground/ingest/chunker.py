"""Section-aware chunking.

PubMed abstracts often arrive as labeled sections (BACKGROUND, METHODS, RESULTS, CONCLUSIONS).
We honor those: each labeled section is its own chunk, then long sections are split with a small
character overlap so token-bounded embedders never see truncation.

We always emit the title as its own chunk — short, high-signal, used heavily by lexical search.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from medground.config import CONFIG
from medground.models import Chunk, ChunkSection, Paper

_SECTION_MAP = {
    "BACKGROUND": ChunkSection.BACKGROUND,
    "INTRODUCTION": ChunkSection.BACKGROUND,
    "OBJECTIVE": ChunkSection.BACKGROUND,
    "OBJECTIVES": ChunkSection.BACKGROUND,
    "PURPOSE": ChunkSection.BACKGROUND,
    "METHODS": ChunkSection.METHODS,
    "METHOD": ChunkSection.METHODS,
    "DESIGN": ChunkSection.METHODS,
    "PATIENTS AND METHODS": ChunkSection.METHODS,
    "MATERIALS AND METHODS": ChunkSection.METHODS,
    "RESULTS": ChunkSection.RESULTS,
    "FINDINGS": ChunkSection.RESULTS,
    "CONCLUSIONS": ChunkSection.CONCLUSIONS,
    "CONCLUSION": ChunkSection.CONCLUSIONS,
    "INTERPRETATION": ChunkSection.CONCLUSIONS,
}

_LABEL_RE = re.compile(r"^([A-Z][A-Z /&\-]{2,40}):\s+", re.MULTILINE)


def _split_labeled(abstract: str) -> list[tuple[ChunkSection, str]]:
    """Return [(section, text), ...]. If no labels found, returns one ABSTRACT chunk."""
    matches = list(_LABEL_RE.finditer(abstract))
    if not matches:
        return [(ChunkSection.ABSTRACT, abstract.strip())]
    parts: list[tuple[ChunkSection, str]] = []
    for i, m in enumerate(matches):
        label = m.group(1).strip().upper()
        section = _SECTION_MAP.get(label, ChunkSection.ABSTRACT)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(abstract)
        text = abstract[start:end].strip()
        if text:
            parts.append((section, text))
    return parts or [(ChunkSection.ABSTRACT, abstract.strip())]


def _windowed(text: str, size: int, overlap: int) -> Iterator[tuple[int, int, str]]:
    """Yield (char_start, char_end, slice) windows. Overlap measured in characters."""
    if len(text) <= size:
        yield 0, len(text), text
        return
    i = 0
    n = len(text)
    while i < n:
        j = min(n, i + size)
        # Try to break on a sentence boundary near j to avoid mid-sentence splits.
        if j < n:
            window = text[i:j]
            cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
            if cut > size // 2:
                j = i + cut + 2
        yield i, j, text[i:j]
        if j >= n:
            break
        i = j - overlap


def chunk_paper(
    paper: Paper, *, chunk_chars: int | None = None, overlap: int | None = None
) -> list[Chunk]:
    chunk_chars = chunk_chars or CONFIG.chunk_chars
    overlap = overlap or CONFIG.chunk_overlap
    chunks: list[Chunk] = []
    index = 0

    if paper.title:
        chunks.append(
            Chunk(
                id=f"{paper.id}#{index}",
                paper_id=paper.id,
                index=index,
                section=ChunkSection.TITLE,
                text=paper.title.strip(),
                char_start=0,
                char_end=len(paper.title),
            )
        )
        index += 1

    if paper.abstract:
        for section, body in _split_labeled(paper.abstract):
            for cs, ce, sub in _windowed(body, chunk_chars, overlap):
                chunks.append(
                    Chunk(
                        id=f"{paper.id}#{index}",
                        paper_id=paper.id,
                        index=index,
                        section=section,
                        text=sub,
                        char_start=cs,
                        char_end=ce,
                    )
                )
                index += 1

    return chunks
