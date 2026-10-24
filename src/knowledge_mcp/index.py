"""Incremental Markdown chunk index with bilingual tokenisation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {".md", ".txt", ".rst"}
LATIN_RE = re.compile(r"[A-Za-z0-9_+-]+")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def tokenize(text: str) -> list[str]:
    """Return Latin words plus Chinese unigrams/bigrams.

    Bigrams keep the implementation dependency-free while fixing the common failure where a
    whole Chinese sentence was previously treated as one indivisible token.
    """

    tokens = [item.casefold() for item in LATIN_RE.findall(text)]
    for run in CHINESE_RE.findall(text):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


@dataclass(frozen=True, slots=True)
class NoteChunk:
    chunk_id: str
    path: str
    heading: str
    start_line: int
    end_line: int
    content: str
    file_hash: str


def markdown_chunks(path: str, text: str, file_hash: str) -> list[NoteChunk]:
    lines = text.splitlines()
    if not lines:
        return []
    starts = [index for index, line in enumerate(lines) if HEADING_RE.match(line)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    chunks: list[NoteChunk] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] - 1 if position + 1 < len(starts) else len(lines) - 1
        content = "\n".join(lines[start : end + 1]).strip()
        if not content:
            continue
        match = HEADING_RE.match(lines[start])
        heading = match.group(2).strip() if match else "(开头)"
        chunk_key = f"{path}:{start + 1}:{end + 1}:{file_hash}"
        chunks.append(
            NoteChunk(
                hashlib.sha256(chunk_key.encode()).hexdigest()[:20],
                path,
                heading,
                start + 1,
                end + 1,
                content,
                file_hash,
            )
        )
    return chunks


class NoteIndex:
    def __init__(self, notes_root: Path, database: Path, max_file_bytes: int) -> None:
        self.notes_root = notes_root
        self.database = database
        self.max_file_bytes = max_file_bytes
        database.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS note_files (
                    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL, indexed_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS note_chunks (
                    chunk_id TEXT PRIMARY KEY, path TEXT NOT NULL, heading TEXT NOT NULL,
                    start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
                    content TEXT NOT NULL, tokens TEXT NOT NULL, file_hash TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_note_chunks_path ON note_chunks(path);
                """
            )

    def sync(self) -> dict[str, int]:
        seen: set[str] = set()
        changed = 0
        skipped = 0
        with self._db() as db:
            current = {
                row["path"]: (row["mtime_ns"], row["size"])
                for row in db.execute("SELECT path, mtime_ns, size FROM note_files")
            }
            for file in sorted(self.notes_root.rglob("*")):
                if not file.is_file() or file.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                relative = file.relative_to(self.notes_root).as_posix()
                seen.add(relative)
                stat = file.stat()
                if stat.st_size > self.max_file_bytes:
                    skipped += 1
                    continue
                if current.get(relative) == (stat.st_mtime_ns, stat.st_size):
                    continue
                try:
                    raw = file.read_bytes()
                    text = raw.decode("utf-8")
                except (OSError, UnicodeError):
                    skipped += 1
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                db.execute("DELETE FROM note_chunks WHERE path = ?", (relative,))
                for chunk in markdown_chunks(relative, text, digest):
                    db.execute(
                        "INSERT INTO note_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            chunk.chunk_id,
                            chunk.path,
                            chunk.heading,
                            chunk.start_line,
                            chunk.end_line,
                            chunk.content,
                            json.dumps(tokenize(chunk.content), ensure_ascii=False),
                            chunk.file_hash,
                        ),
                    )
                db.execute(
                    """INSERT INTO note_files(path, sha256, mtime_ns, size)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,
                         mtime_ns=excluded.mtime_ns, size=excluded.size,
                         indexed_at=CURRENT_TIMESTAMP""",
                    (relative, digest, stat.st_mtime_ns, stat.st_size),
                )
                changed += 1
            removed = set(current) - seen
            for relative in removed:
                db.execute("DELETE FROM note_chunks WHERE path = ?", (relative,))
                db.execute("DELETE FROM note_files WHERE path = ?", (relative,))
        return {"changed_files": changed, "removed_files": len(removed), "skipped_files": skipped}

    def status(self) -> dict[str, Any]:
        with self._db() as db:
            files = db.execute("SELECT COUNT(*) FROM note_files").fetchone()[0]
            chunks = db.execute("SELECT COUNT(*) FROM note_chunks").fetchone()[0]
            last = db.execute("SELECT MAX(indexed_at) FROM note_files").fetchone()[0]
        return {"files": files, "chunks": chunks, "last_indexed_at": last}

    def search(self, query: str, top_k: int) -> tuple[list[dict[str, Any]], str | None]:
        self.sync()
        query_tokens = set(tokenize(query))
        with self._db() as db:
            rows = list(db.execute("SELECT * FROM note_chunks"))
        document_frequency: dict[str, int] = {}
        decoded: list[tuple[sqlite3.Row, set[str]]] = []
        for row in rows:
            tokens = set(json.loads(row["tokens"]))
            decoded.append((row, tokens))
            for token in tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1

        scored: list[tuple[float, sqlite3.Row]] = []
        total = max(1, len(rows))
        for row, tokens in decoded:
            overlap = query_tokens & tokens
            if not overlap:
                continue
            score = sum(math.log((total + 1) / (document_frequency[t] + 0.5)) + 1 for t in overlap)
            folded = row["content"].casefold()
            if query.casefold() in folded:
                score += 5
            score += sum(1.5 for token in overlap if token in row["heading"].casefold())
            scored.append((score, row))

        fallback: str | None = None
        if not scored:
            fallback = "filename"
            folded_query = query.casefold()
            words = set(LATIN_RE.findall(folded_query))
            for row, _ in decoded:
                filename = Path(row["path"]).stem.casefold()
                score = sum(2 for word in words if word in filename)
                if score or filename in folded_query:
                    scored.append((float(score or 1), row))

        scored.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"]))
        matches = []
        for score, row in scored[:top_k]:
            chunk_lines = row["content"].splitlines()
            best_index, best_line = max(
                enumerate(chunk_lines),
                key=lambda item: (
                    len(query_tokens & set(tokenize(item[1]))),
                    int(not item[1].lstrip().startswith("#")),
                ),
            )
            matches.append(
                {
                    "path": row["path"],
                    "line": row["start_line"] + best_index,
                    "score": round(score, 4),
                    "snippet": best_line.strip()[:320],
                    "heading": row["heading"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "chunk_id": row["chunk_id"],
                    "content": row["content"],
                }
            )
        return matches, fallback
