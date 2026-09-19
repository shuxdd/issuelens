import ast
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote


@dataclass(frozen=True)
class RetrievalInput:
    case_id: str
    data_version: str
    repository: str
    snapshot_commit: str
    query: str
    issue_source_url: str


@dataclass(frozen=True)
class ChunkingConfig:
    index_version: str
    max_lines: int
    overlap_lines: int
    python_parser: str = "ast-v1"
    markdown_parser: str = "headings-v1"


@dataclass(frozen=True)
class Chunk:
    source_type: str
    repository: str
    commit: str
    path: str
    start_line: int
    end_line: int
    content: str
    index_version: str


@dataclass(frozen=True)
class Corpus:
    chunks: tuple[Chunk, ...]
    chunking_config: ChunkingConfig


class Embedder(Protocol):
    identifier: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


class FastEmbedAdapter:
    def __init__(
        self,
        *,
        model_name: str,
        model_revision: str,
        dimension: int,
        cache_dir: Path,
    ) -> None:
        text_embedding: Any = import_module("fastembed").TextEmbedding
        self._model = text_embedding(
            model_name=model_name,
            cache_dir=str(cache_dir),
            threads=1,
            local_files_only=True,
            revision=model_revision,
        )
        self.identifier = f"fastembed:{model_name}@{model_revision}"
        self.dimension = dimension

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [
            tuple(float(value) for value in vector)
            for vector in self._model.passage_embed(texts)
        ]

    def embed_query(self, text: str) -> tuple[float, ...]:
        vectors = list(self._model.query_embed(text))
        if len(vectors) != 1:
            raise ValueError("Embedder must return exactly one query vector")
        return tuple(float(value) for value in vectors[0])


@dataclass(frozen=True)
class VectorIndexConfig:
    top_k: int
    score_precision: int
    generation_version: str
    data_version: str


@dataclass(frozen=True)
class RetrievalCandidate:
    rank: int
    score: str
    commit: str
    path: str
    start_line: int
    end_line: int
    source_type: str
    index_version: str
    content: str
    reference: str


@dataclass(frozen=True)
class RetrievalMetadata:
    data_version: str
    snapshot_commit: str
    index_version: str
    embedding_identifier: str
    embedding_dimension: int
    top_k: int
    generation_version: str
    chunk_count: int
    chunking: dict[str, int | str] | None = None
    similarity: str = "cosine"
    score_precision: int = 6
    tie_break: str = "path,start_line,end_line,source_type"


@dataclass(frozen=True)
class RetrievalRun:
    query: str
    candidates: tuple[RetrievalCandidate, ...]
    metadata: RetrievalMetadata


@dataclass(frozen=True)
class RecallMetric:
    hits: int
    total: int
    recall: float | None


@dataclass(frozen=True)
class RecallReport:
    top_k: int
    root_cause_files: RecallMetric
    changed_files: RecallMetric
    test_files: RecallMetric


def load_retrieval_input(case_path: Path) -> RetrievalInput:
    case: dict[str, Any] = json.loads(case_path.read_text(encoding="utf-8"))
    issue = case["issue"]
    return RetrievalInput(
        case_id=case["case_id"],
        data_version=case["schema_version"],
        repository=issue["repository"],
        snapshot_commit=case["repository_snapshot"]["commit"],
        query=issue["title"].strip(),
        issue_source_url=issue["source_url"],
    )


def build_corpus(
    snapshot_root: Path,
    retrieval_input: RetrievalInput,
    config: ChunkingConfig,
) -> Corpus:
    manifest: dict[str, str] = json.loads(
        (snapshot_root / ".issuelens-snapshot.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("repository") != retrieval_input.repository
        or manifest.get("commit") != retrieval_input.snapshot_commit
    ):
        raise ValueError("Snapshot manifest does not match retrieval input")
    if config.max_lines < 1 or not 0 <= config.overlap_lines < config.max_lines:
        raise ValueError("Chunking requires 0 <= overlap_lines < max_lines")

    chunks: list[Chunk] = []

    def append_chunks(
        source_type: str,
        source_path: Path,
        lines: list[str],
        start_line: int,
        end_line: int,
    ) -> None:
        step = config.max_lines - config.overlap_lines
        window_start = start_line
        while window_start <= end_line:
            window_end = min(window_start + config.max_lines - 1, end_line)
            chunks.append(
                Chunk(
                    source_type=source_type,
                    repository=retrieval_input.repository,
                    commit=retrieval_input.snapshot_commit,
                    path=source_path.relative_to(snapshot_root).as_posix(),
                    start_line=window_start,
                    end_line=window_end,
                    content="\n".join(lines[window_start - 1 : window_end]),
                    index_version=config.index_version,
                )
            )
            if window_end == end_line:
                break
            window_start += step

    sources = [
        (source_path, source_type)
        for directory, source_type in (
            ("starlette", "python_source"),
            ("tests", "python_test"),
        )
        for source_path in sorted((snapshot_root / directory).rglob("*.py"))
    ]
    for source_path, source_type in sources:
        source = source_path.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source, filename=str(source_path))
        pending_module_nodes: list[ast.stmt] = []

        def append_chunk(
            start_line: int,
            end_line: int,
            current_source_type: str = source_type,
            current_source_path: Path = source_path,
            current_lines: list[str] = lines,
        ) -> None:
            append_chunks(
                current_source_type,
                current_source_path,
                current_lines,
                start_line,
                end_line,
            )

        def flush_module_nodes(nodes: list[ast.stmt]) -> None:
            if nodes:
                append_chunk(
                    nodes[0].lineno,
                    nodes[-1].end_lineno or nodes[-1].lineno,
                )
                nodes.clear()

        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                flush_module_nodes(pending_module_nodes)
                decorator_lines = [decorator.lineno for decorator in node.decorator_list]
                append_chunk(
                    min(decorator_lines, default=node.lineno),
                    node.end_lineno or node.lineno,
                )
            else:
                pending_module_nodes.append(node)
        flush_module_nodes(pending_module_nodes)

    for source_path in sorted((snapshot_root / "docs").rglob("*.md")):
        lines = source_path.read_text(encoding="utf-8").splitlines()
        heading_lines: list[int] = []
        in_fence = False
        for line_number, line in enumerate(lines, start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            elif not in_fence and line.startswith("#") and line.lstrip("#").startswith(" "):
                heading_lines.append(line_number)

        for index, start_line in enumerate(heading_lines):
            end_line = (
                heading_lines[index + 1] - 1
                if index + 1 < len(heading_lines)
                else len(lines)
            )
            append_chunks("markdown", source_path, lines, start_line, end_line)

    return Corpus(tuple(chunks), config)


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Embedding dimensions do not match")
    left_norm = math.sqrt(math.fsum(value * value for value in left))
    right_norm = math.sqrt(math.fsum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("Cosine similarity requires non-zero vectors")
    return math.fsum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def run_vector_retrieval(
    corpus: Corpus,
    query: str,
    embedder: Embedder,
    config: VectorIndexConfig,
) -> RetrievalRun:
    if not corpus.chunks or config.top_k < 1 or config.score_precision < 0:
        raise ValueError("Vector retrieval requires chunks, positive top_k and score precision")

    document_vectors = embedder.embed_documents(
        [f"{chunk.path}\n{chunk.content}" for chunk in corpus.chunks]
    )
    query_vector = embedder.embed_query(query)
    if len(query_vector) != embedder.dimension or len(document_vectors) != len(
        corpus.chunks
    ):
        raise ValueError("Embedder returned an unexpected vector shape")

    scored: list[tuple[str, Chunk]] = []
    for chunk, vector in zip(corpus.chunks, document_vectors, strict=True):
        if len(vector) != embedder.dimension:
            raise ValueError("Embedder returned an unexpected vector shape")
        score = _cosine_similarity(query_vector, vector)
        scored.append((f"{score:.{config.score_precision}f}", chunk))

    # ponytail: linear scan is for one case; move to pgvector when measured scale needs it.
    scored.sort(
        key=lambda item: (
            -Decimal(item[0]),
            item[1].path,
            item[1].start_line,
            item[1].end_line,
            item[1].source_type,
        )
    )
    candidates = tuple(
        RetrievalCandidate(
            rank=rank,
            score=score,
            commit=chunk.commit,
            path=chunk.path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            source_type=chunk.source_type,
            index_version=chunk.index_version,
            content=chunk.content,
            reference=(
                f"https://github.com/{chunk.repository}/blob/{chunk.commit}/"
                f"{quote(chunk.path, safe='/')}#L{chunk.start_line}-L{chunk.end_line}"
            ),
        )
        for rank, (score, chunk) in enumerate(scored[: config.top_k], start=1)
    )
    first_chunk = corpus.chunks[0]
    return RetrievalRun(
        query=query,
        candidates=candidates,
        metadata=RetrievalMetadata(
            data_version=config.data_version,
            snapshot_commit=first_chunk.commit,
            index_version=first_chunk.index_version,
            embedding_identifier=embedder.identifier,
            embedding_dimension=embedder.dimension,
            top_k=config.top_k,
            generation_version=config.generation_version,
            chunk_count=len(corpus.chunks),
            chunking={
                "max_lines": corpus.chunking_config.max_lines,
                "overlap_lines": corpus.chunking_config.overlap_lines,
                "python_parser": corpus.chunking_config.python_parser,
                "markdown_parser": corpus.chunking_config.markdown_parser,
            },
            score_precision=config.score_precision,
        ),
    )


def evaluate_recall(
    retrieval_run: RetrievalRun,
    gold_path: Path,
    *,
    top_k: int,
) -> RecallReport:
    if top_k < 1:
        raise ValueError("Recall requires positive top_k")
    gold: dict[str, list[dict[str, str]]] = json.loads(
        gold_path.read_text(encoding="utf-8")
    )
    retrieved_paths = {
        candidate.path for candidate in retrieval_run.candidates[:top_k]
    }

    def metric(category: str) -> RecallMetric:
        gold_paths = {item["path"] for item in gold[category]}
        hits = len(retrieved_paths & gold_paths)
        return RecallMetric(
            hits=hits,
            total=len(gold_paths),
            recall=hits / len(gold_paths) if gold_paths else None,
        )

    return RecallReport(
        top_k=top_k,
        root_cause_files=metric("root_cause_files"),
        changed_files=metric("changed_files"),
        test_files=metric("test_files"),
    )


def write_retrieval_result(
    output_path: Path,
    case_id: str,
    retrieval_run: RetrievalRun,
    recall: RecallReport,
) -> None:
    result = {
        "schema_version": "1.0",
        "case_id": case_id,
        "query": retrieval_run.query,
        "candidates": [asdict(candidate) for candidate in retrieval_run.candidates],
        "metadata": asdict(retrieval_run.metadata),
        "recall": asdict(recall),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
