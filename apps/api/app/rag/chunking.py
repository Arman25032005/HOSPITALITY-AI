import re
from dataclasses import dataclass

MAX_CHARS = 900
OVERLAP_CHARS = 120
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str  # includes the heading trail so a chunk is understandable on its own
    heading: str


def _split_long(paragraph: str) -> list[str]:
    if len(paragraph) <= MAX_CHARS:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    out, cur = [], ""
    for s in sentences:
        while len(s) > MAX_CHARS:  # pathological: no sentence breaks
            if cur:
                out.append(cur)
                cur = ""
            out.append(s[:MAX_CHARS])
            s = s[MAX_CHARS - OVERLAP_CHARS:]
        if cur and len(cur) + len(s) + 1 > MAX_CHARS:
            out.append(cur)
            cur = cur[-OVERLAP_CHARS:].split(" ", 1)[-1] + " " + s if OVERLAP_CHARS else s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, title: str = "") -> list[Chunk]:
    """Heading-aware chunking: paragraphs are packed up to MAX_CHARS within a section."""
    trail: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []
    paras: list[str] = []

    def heading_text() -> str:
        parts = ([title] if title else []) + [h for _, h in trail]
        deduped = [p for i, p in enumerate(parts) if i == 0 or p.lower() != parts[i - 1].lower()]
        return " > ".join(deduped)

    def flush():
        nonlocal paras
        if paras:
            sections.append((heading_text(), paras))
            paras = []

    for block in re.split(r"\n\s*\n", text):
        lines = block.split("\n")
        body: list[str] = []
        for line in lines:
            m = _HEADING.match(line.strip())
            if m:
                if body:
                    paras.append("\n".join(body))
                    body = []
                flush()
                level = len(m.group(1))
                while trail and trail[-1][0] >= level:
                    trail.pop()
                trail.append((level, m.group(2).strip()))
            else:
                body.append(line)
        if body and "\n".join(body).strip():
            paras.append("\n".join(body).strip())
    flush()

    chunks: list[Chunk] = []
    for heading, ps in sections:
        cur = ""
        for p in ps:
            for piece in _split_long(p):
                if cur and len(cur) + len(piece) + 2 > MAX_CHARS:
                    chunks.append(Chunk(len(chunks), _compose(heading, cur), heading))
                    cur = piece
                else:
                    cur = f"{cur}\n\n{piece}" if cur else piece
        if cur:
            chunks.append(Chunk(len(chunks), _compose(heading, cur), heading))
    return chunks


def _compose(heading: str, body: str) -> str:
    return f"{heading}\n{body}" if heading else body
