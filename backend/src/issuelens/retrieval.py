import ast
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from statistics import median
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


class CachedDocumentEmbedder:
    def __init__(self, embedder: Embedder, cache_dir: Path) -> None:
        self.embedder = embedder
        self.cache_dir = cache_dir
        self.identifier = embedder.identifier
        self.dimension = embedder.dimension
        self.cache_hits = 0
        self.cache_misses = 0

    def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        key = hashlib.sha256(json.dumps(
            [self.identifier, self.dimension, list(texts)],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.is_file():
            try:
                vectors = [tuple(row) for row in json.loads(path.read_text(encoding="utf-8"))]
                if len(vectors) == len(texts) and all(
                    len(row) == self.dimension and all(
                        isinstance(value, (int, float)) and math.isfinite(value)
                        for value in row
                    ) for row in vectors
                ):
                    self.cache_hits += 1
                    return vectors
            except (OSError, ValueError, TypeError):
                pass
        vectors = [
            tuple(float(value) for value in row)
            for row in self.embedder.embed_documents(texts)
        ]
        if len(vectors) != len(texts) or any(len(row) != self.dimension for row in vectors):
            raise ValueError("Embedder returned an unexpected vector shape")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.cache_dir, delete=False
        ) as temporary:
            json.dump(vectors, temporary, separators=(",", ":"))
            temporary_path = Path(temporary.name)
        try:
            os.replace(temporary_path, path)
        finally:
            temporary_path.unlink(missing_ok=True)
        self.cache_misses += 1
        return vectors

    def embed_query(self, text: str) -> tuple[float, ...]:
        return tuple(self.embedder.embed_query(text))


@dataclass(frozen=True)
class VectorIndexConfig:
    top_k: int
    score_precision: int
    generation_version: str
    data_version: str


@dataclass(frozen=True)
class BM25Config:
    top_k: int
    score_precision: int
    tokenizer_version: str
    k1: float
    b: float


@dataclass(frozen=True)
class FusionConfig:
    top_k: int
    score_precision: int
    rank_constant: int
    vector_weight: float
    bm25_weight: float


@dataclass(frozen=True)
class BenchmarkConfig:
    warmup_runs: int = 0
    repetitions: int = 1


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
class HybridCandidate(RetrievalCandidate):
    vector_rank: int | None
    vector_score: str | None
    vector_contribution: str
    bm25_rank: int | None
    bm25_score: str | None
    bm25_contribution: str


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
class BM25Run:
    query: str
    candidates: tuple[RetrievalCandidate, ...]
    metadata: dict[str, int | str]


@dataclass(frozen=True)
class HybridRun:
    query: str
    candidates: tuple[HybridCandidate, ...]
    metadata: dict[str, int | str]


class Reranker(Protocol):
    identifier: str

    def score(
        self, query: str, candidates: Sequence[HybridCandidate]
    ) -> Sequence[float]: ...


class FastEmbedRerankerAdapter:
    def __init__(
        self, *, model_name: str, model_revision: str, model_path: Path
    ) -> None:
        if not model_path.is_dir():
            raise ValueError("Reranker model path does not exist")
        encoder: Any = import_module("fastembed.rerank.cross_encoder").TextCrossEncoder
        self._model = encoder(
            model_name=model_name,
            specific_model_path=str(model_path),
            local_files_only=True,
            threads=1,
            cuda=False,
        )
        self.identifier = f"fastembed:{model_name}@{model_revision}"

    def score(
        self, query: str, candidates: Sequence[HybridCandidate]
    ) -> list[float]:
        return [
            float(score)
            for score in self._model.rerank(
                query, [candidate.content for candidate in candidates], batch_size=10
            )
        ]


def verify_model_artifacts(model_path: Path, artifacts: dict[str, str]) -> int:
    if not artifacts:
        raise ValueError("Reranker artifact hashes are required")
    total_bytes = 0
    for name, expected in artifacts.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Reranker artifact path must be relative")
        path = model_path / relative
        if not path.is_file():
            raise ValueError(f"Reranker artifact missing: {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"Reranker artifact hash mismatch: {name}")
        total_bytes += path.stat().st_size
    return total_bytes


def load_local_reranker(
    config: dict[str, Any], cache_root: Path
) -> tuple[Reranker | None, int]:
    if not config["enabled"]:
        return None, 0
    model = config["model"]
    if not model.get("revision") or not model.get("artifact_files"):
        raise ValueError("A pinned local Reranker model is required")
    model_path = (
        cache_root / f"models--{model['name'].replace('/', '--')}"
        / "snapshots" / model["revision"]
    )
    size = verify_model_artifacts(model_path, model["artifact_files"])
    return FastEmbedRerankerAdapter(
        model_name=model["name"], model_revision=model["revision"], model_path=model_path
    ), size


@dataclass(frozen=True)
class RerankerConfig:
    enabled: bool
    top_k: int
    score_precision: int


@dataclass(frozen=True)
class RerankedCandidate(HybridCandidate):
    g2_rank: int
    rrf_score: str
    reranker_score: str


@dataclass(frozen=True)
class RerankedRun:
    query: str
    candidates: tuple[RerankedCandidate, ...]
    metadata: dict[str, int | str]


def rerank_hybrid_run(
    g2: HybridRun,
    reranker: Reranker | None = None,
    config: RerankerConfig | None = None,
) -> HybridRun | RerankedRun:
    if config is None:
        raise ValueError("Reranker config is required")
    if not config.enabled:
        return g2
    if reranker is None:
        raise ValueError("Enabled reranker requires a scorer")
    if config.top_k < 1 or config.score_precision < 0:
        raise ValueError("Reranker requires positive top_k and non-negative score precision")
    scores = tuple(reranker.score(g2.query, g2.candidates))
    if len(scores) != len(g2.candidates) or any(not math.isfinite(score) for score in scores):
        raise ValueError("Reranker must return one finite score per candidate")
    scored = [
        (f"{score:.{config.score_precision}f}", candidate)
        for score, candidate in zip(scores, g2.candidates, strict=True)
    ]
    scored.sort(key=lambda item: (-Decimal(item[0]), item[1].rank, item[1].path,
                                  item[1].start_line, item[1].end_line, item[1].source_type))
    candidates = tuple(
        RerankedCandidate(
            **{**asdict(candidate), "rank": rank, "score": score},
            g2_rank=candidate.rank,
            rrf_score=candidate.score,
            reranker_score=score,
        )
        for rank, (score, candidate) in enumerate(scored[: config.top_k], start=1)
    )
    return RerankedRun(
        query=g2.query,
        candidates=candidates,
        metadata={"reranker_identifier": reranker.identifier,
                  "top_k": config.top_k, "score_precision": config.score_precision},
    )


@dataclass(frozen=True)
class RetrievalComparison:
    shared_input: dict[str, int | str]
    g1_vector: RetrievalRun
    g2_hybrid: HybridRun
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RerankerComparison:
    shared_input: dict[str, int | str]
    g1_vector: RetrievalRun
    g2_hybrid: HybridRun
    g3_reranked: HybridRun | RerankedRun
    metadata: dict[str, Any]


def add_reranker_comparison(
    baseline: RetrievalComparison,
    reranker: Reranker | None,
    config: RerankerConfig,
) -> RerankerComparison:
    g3 = rerank_hybrid_run(baseline.g2_hybrid, reranker, config)
    return RerankerComparison(
        shared_input=baseline.shared_input,
        g1_vector=baseline.g1_vector,
        g2_hybrid=baseline.g2_hybrid,
        g3_reranked=g3,
        metadata={
            **baseline.metadata,
            "reranker": {
                "enabled": config.enabled,
                "identifier": reranker.identifier if config.enabled and reranker else None,
                "top_k": config.top_k,
                "score_precision": config.score_precision,
            },
        },
    )


def run_three_way_experiment(
    corpus: Corpus,
    query: str,
    embedder: Embedder,
    vector_config: VectorIndexConfig,
    bm25_config: BM25Config,
    fusion_config: FusionConfig,
    reranker: Reranker | None,
    reranker_config: RerankerConfig,
    *,
    benchmark: BenchmarkConfig,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> RerankerComparison:
    if reranker_config.top_k != fusion_config.top_k:
        raise ValueError("G2 and G3 require the same top_k")
    baseline = run_retrieval_comparison(
        corpus, query, embedder, vector_config, bm25_config, fusion_config,
        benchmark=benchmark, clock=clock,
    )
    samples: list[int] = []
    expected: HybridRun | RerankedRun | None = None
    for run_number in range(benchmark.warmup_runs + benchmark.repetitions):
        started = clock()
        current = rerank_hybrid_run(baseline.g2_hybrid, reranker, reranker_config)
        duration = clock() - started
        if duration < 0:
            raise ValueError("Benchmark clock must be monotonic")
        if run_number < benchmark.warmup_runs:
            continue
        if expected is not None and current != expected:
            raise ValueError("Reranker ranking changed between benchmark repetitions")
        expected = current
        samples.append(duration)
    comparison = add_reranker_comparison(baseline, reranker, reranker_config)
    assert expected is not None
    if comparison.g3_reranked != expected:
        raise ValueError("Reranker ranking changed after benchmark")
    comparison.metadata["latency_ns"]["g3_reranked"] = {
        "build": comparison.metadata["latency_ns"]["g2_hybrid"]["build"],
        "reranker": {
            "samples": samples,
            "median": int(median(samples)),
            "min": min(samples),
            "max": max(samples),
        },
    }
    return comparison


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


@dataclass(frozen=True)
class RetrievalMetrics:
    recall_at_5: RecallReport
    recall_at_10: RecallReport
    mrr: float
    ndcg_at_10: float


def validate_candidate_references(
    candidates: Sequence[RetrievalCandidate],
    snapshot_root: Path,
    repository: str,
    commit: str,
) -> list[str]:
    root = snapshot_root.resolve()
    errors: list[str] = []
    for candidate in candidates:
        label = f"{candidate.path}:{candidate.start_line}-{candidate.end_line}"
        source = (root / candidate.path).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            errors.append(f"{label}: path does not resolve in snapshot")
            continue
        lines = source.read_text(encoding="utf-8").splitlines()
        if not 1 <= candidate.start_line <= candidate.end_line <= len(lines):
            errors.append(f"{label}: invalid line range")
            continue
        if candidate.content != "\n".join(lines[candidate.start_line - 1:candidate.end_line]):
            errors.append(f"{label}: content does not match snapshot")
        expected = (
            f"https://github.com/{repository}/blob/{commit}/"
            f"{quote(candidate.path, safe='/')}"
            f"#L{candidate.start_line}-L{candidate.end_line}"
        )
        if candidate.commit != commit or candidate.reference != expected:
            errors.append(f"{label}: commit or reference does not match snapshot")
    return errors


def check_retrieval_thresholds(
    metrics: RetrievalMetrics, thresholds: dict[str, float]
) -> list[str]:
    values = {
        "root_cause_recall_at_5": metrics.recall_at_5.root_cause_files.recall,
        "root_cause_recall_at_10": metrics.recall_at_10.root_cause_files.recall,
        "mrr": metrics.mrr,
        "ndcg_at_10": metrics.ndcg_at_10,
    }
    failures: list[str] = []
    for name, minimum in thresholds.items():
        value = values[name]
        if value is None or value < minimum:
            failures.append(f"{name}: {value} < {minimum}")
    return failures


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


_BM25_TOKEN = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def _tokenize_bm25(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _BM25_TOKEN.finditer(text.casefold()):
        token = match.group()
        tokens.append(token)
        if "_" in token:
            tokens.extend(token.split("_"))
    return tuple(tokens)


@dataclass(frozen=True)
class _BM25Index:
    corpus: Corpus
    config: BM25Config
    documents: tuple[tuple[str, ...], ...]
    term_frequencies: tuple[Counter[str], ...]
    document_frequency: Counter[str]
    average_length: float
    index_size: dict[str, int]


def _build_bm25_index(
    corpus: Corpus,
    config: BM25Config,
) -> _BM25Index:
    if (
        not corpus.chunks
        or config.top_k < 1
        or config.score_precision < 0
        or config.k1 <= 0
        or not 0 <= config.b <= 1
    ):
        raise ValueError("BM25 requires chunks, positive top_k/k1 and 0 <= b <= 1")

    documents = tuple(
        _tokenize_bm25(f"{chunk.path}\n{chunk.content}") for chunk in corpus.chunks
    )
    term_frequencies = tuple(Counter(document) for document in documents)
    document_frequency = Counter(
        token for document in documents for token in set(document)
    )
    average_length = math.fsum(len(document) for document in documents) / len(documents)
    postings: dict[str, list[list[int]]] = {}
    for document_id, frequencies in enumerate(term_frequencies):
        for token, frequency in frequencies.items():
            postings.setdefault(token, []).append([document_id, frequency])
    serialized = json.dumps(
        {
            "document_lengths": [len(document) for document in documents],
            "postings": postings,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _BM25Index(
        corpus=corpus,
        config=config,
        documents=documents,
        term_frequencies=term_frequencies,
        document_frequency=document_frequency,
        average_length=average_length,
        index_size={
            "document_count": len(documents),
            "term_count": len(document_frequency),
            "posting_count": sum(document_frequency.values()),
            "token_count": sum(len(document) for document in documents),
            "serialized_bytes": len(serialized),
        },
    )


def _query_bm25_index(index: _BM25Index, query: str) -> BM25Run:
    config = index.config
    query_tokens = _tokenize_bm25(query)
    document_count = len(index.documents)
    scored: list[tuple[str, Chunk]] = []
    for chunk, document, term_frequency in zip(
        index.corpus.chunks,
        index.documents,
        index.term_frequencies,
        strict=True,
    ):
        length_normalization = config.k1 * (
            1 - config.b + config.b * len(document) / index.average_length
        )
        score = math.fsum(
            math.log(
                1
                + (document_count - index.document_frequency[token] + 0.5)
                / (index.document_frequency[token] + 0.5)
            )
            * term_frequency[token]
            * (config.k1 + 1)
            / (term_frequency[token] + length_normalization)
            for token in query_tokens
            if term_frequency[token]
        )
        if score > 0:
            scored.append((f"{score:.{config.score_precision}f}", chunk))

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
    return BM25Run(
        query=query,
        candidates=candidates,
        metadata={
            "document_count": document_count,
            "average_document_length": (
                f"{index.average_length:.{config.score_precision}f}"
            ),
            "tokenizer_version": config.tokenizer_version,
            "k1": f"{config.k1:.{config.score_precision}f}",
            "b": f"{config.b:.{config.score_precision}f}",
            "score_precision": config.score_precision,
            "tie_break": "path,start_line,end_line,source_type",
        },
    )


def run_bm25_retrieval(
    corpus: Corpus,
    query: str,
    config: BM25Config,
) -> BM25Run:
    return _query_bm25_index(_build_bm25_index(corpus, config), query)


def fuse_retrieval_runs(
    vector: RetrievalRun,
    bm25: BM25Run,
    config: FusionConfig,
) -> HybridRun:
    if vector.query != bm25.query:
        raise ValueError("Fusion requires the same query")
    if (
        config.top_k < 1
        or config.score_precision < 0
        or config.rank_constant < 0
        or config.vector_weight < 0
        or config.bm25_weight < 0
        or config.vector_weight + config.bm25_weight == 0
    ):
        raise ValueError("Fusion requires positive output and non-negative RRF weights")

    def key(candidate: RetrievalCandidate) -> tuple[str, str, int, int, str, str]:
        return (
            candidate.commit,
            candidate.path,
            candidate.start_line,
            candidate.end_line,
            candidate.source_type,
            candidate.index_version,
        )

    vector_by_key = {key(candidate): candidate for candidate in vector.candidates}
    bm25_by_key = {key(candidate): candidate for candidate in bm25.candidates}
    scored: list[
        tuple[
            str,
            RetrievalCandidate,
            RetrievalCandidate | None,
            RetrievalCandidate | None,
            str,
            str,
        ]
    ] = []
    for candidate_key in vector_by_key.keys() | bm25_by_key.keys():
        vector_candidate = vector_by_key.get(candidate_key)
        bm25_candidate = bm25_by_key.get(candidate_key)
        vector_contribution = (
            config.vector_weight
            / (config.rank_constant + vector_candidate.rank)
            if vector_candidate is not None
            else 0.0
        )
        bm25_contribution = (
            config.bm25_weight / (config.rank_constant + bm25_candidate.rank)
            if bm25_candidate is not None
            else 0.0
        )
        source = vector_candidate or bm25_candidate
        assert source is not None
        scored.append(
            (
                f"{vector_contribution + bm25_contribution:.{config.score_precision}f}",
                source,
                vector_candidate,
                bm25_candidate,
                f"{vector_contribution:.{config.score_precision}f}",
                f"{bm25_contribution:.{config.score_precision}f}",
            )
        )

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
        HybridCandidate(
            rank=rank,
            score=score,
            commit=candidate.commit,
            path=candidate.path,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            source_type=candidate.source_type,
            index_version=candidate.index_version,
            content=candidate.content,
            reference=candidate.reference,
            vector_rank=vector_candidate.rank if vector_candidate else None,
            vector_score=vector_candidate.score if vector_candidate else None,
            vector_contribution=vector_contribution,
            bm25_rank=bm25_candidate.rank if bm25_candidate else None,
            bm25_score=bm25_candidate.score if bm25_candidate else None,
            bm25_contribution=bm25_contribution,
        )
        for rank, (
            score,
            candidate,
            vector_candidate,
            bm25_candidate,
            vector_contribution,
            bm25_contribution,
        ) in enumerate(scored[: config.top_k], start=1)
    )
    return HybridRun(
        query=vector.query,
        candidates=candidates,
        metadata={
            "strategy": "rrf-v1",
            "rank_constant": config.rank_constant,
            "vector_weight": f"{config.vector_weight:.{config.score_precision}f}",
            "bm25_weight": f"{config.bm25_weight:.{config.score_precision}f}",
            "score_precision": config.score_precision,
            "top_k": config.top_k,
            "tie_break": "path,start_line,end_line,source_type",
        },
    )


def run_retrieval_comparison(
    corpus: Corpus,
    query: str,
    embedder: Embedder,
    vector_config: VectorIndexConfig,
    bm25_config: BM25Config,
    fusion_config: FusionConfig,
    *,
    benchmark: BenchmarkConfig | None = None,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> RetrievalComparison:
    benchmark = benchmark or BenchmarkConfig()
    if not (
        vector_config.top_k == bm25_config.top_k == fusion_config.top_k
    ):
        raise ValueError("G1 and G2 require the same top_k")
    if benchmark.warmup_runs < 0 or benchmark.repetitions < 1:
        raise ValueError("Benchmark requires non-negative warmups and repetitions")

    samples: dict[str, list[int]] = {
        "vector_build": [],
        "vector_query": [],
        "bm25_build": [],
        "bm25_query": [],
        "fusion": [],
    }
    vector: RetrievalRun | None = None
    hybrid: HybridRun | None = None
    vector_index: _VectorIndex | None = None
    bm25_index: _BM25Index | None = None
    for run_number in range(benchmark.warmup_runs + benchmark.repetitions):
        started = clock()
        current_vector_index = _build_vector_index(corpus, embedder, vector_config)
        vector_built = clock()
        current_vector = _query_vector_index(current_vector_index, query)
        vector_queried = clock()
        current_bm25_index = _build_bm25_index(corpus, bm25_config)
        bm25_built = clock()
        current_bm25 = _query_bm25_index(current_bm25_index, query)
        bm25_queried = clock()
        current_hybrid = fuse_retrieval_runs(
            current_vector, current_bm25, fusion_config
        )
        fused = clock()
        durations = (
            vector_built - started,
            vector_queried - vector_built,
            bm25_built - vector_queried,
            bm25_queried - bm25_built,
            fused - bm25_queried,
        )
        if any(duration < 0 for duration in durations):
            raise ValueError("Benchmark clock must be monotonic")
        if run_number < benchmark.warmup_runs:
            continue
        for name, duration in zip(samples, durations, strict=True):
            samples[name].append(duration)
        if vector is not None and (
            current_vector != vector or current_hybrid != hybrid
        ):
            raise ValueError("Retrieval ranking changed between benchmark repetitions")
        vector = current_vector
        hybrid = current_hybrid
        vector_index = current_vector_index
        bm25_index = current_bm25_index

    assert vector is not None and hybrid is not None
    assert vector_index is not None and bm25_index is not None

    def summary(values: list[int]) -> dict[str, int | list[int]]:
        return {
            "samples": values,
            "median": int(median(values)),
            "min": min(values),
            "max": max(values),
        }

    vector_bytes = len(corpus.chunks) * embedder.dimension * 4
    vector_size = {
        "document_count": len(corpus.chunks),
        "dimension": embedder.dimension,
        "value_count": len(corpus.chunks) * embedder.dimension,
        "payload_bytes": vector_bytes,
        "element_encoding": "float32",
    }
    bm25_size = bm25_index.index_size
    return RetrievalComparison(
        shared_input={
            "query": query,
            "snapshot_commit": vector.metadata.snapshot_commit,
            "index_version": vector.metadata.index_version,
            "chunk_count": len(corpus.chunks),
            "embedding_identifier": embedder.identifier,
            "embedding_dimension": embedder.dimension,
            "top_k": vector_config.top_k,
        },
        g1_vector=vector,
        g2_hybrid=hybrid,
        metadata={
            "data_version": vector_config.data_version,
            "snapshot_commit": vector.metadata.snapshot_commit,
            "chunking": {
                "index_version": corpus.chunking_config.index_version,
                "max_lines": corpus.chunking_config.max_lines,
                "overlap_lines": corpus.chunking_config.overlap_lines,
                "python_parser": corpus.chunking_config.python_parser,
                "markdown_parser": corpus.chunking_config.markdown_parser,
            },
            "embedding": {
                "identifier": embedder.identifier,
                "dimension": embedder.dimension,
            },
            "bm25": {
                "tokenizer_version": bm25_config.tokenizer_version,
                "k1": f"{bm25_config.k1:.{bm25_config.score_precision}f}",
                "b": f"{bm25_config.b:.{bm25_config.score_precision}f}",
                "score_precision": bm25_config.score_precision,
            },
            "fusion": {
                "strategy": "rrf-v1",
                "rank_constant": fusion_config.rank_constant,
                "vector_weight": (
                    f"{fusion_config.vector_weight:.{fusion_config.score_precision}f}"
                ),
                "bm25_weight": (
                    f"{fusion_config.bm25_weight:.{fusion_config.score_precision}f}"
                ),
                "score_precision": fusion_config.score_precision,
            },
            "top_k": vector_config.top_k,
            "benchmark": {
                "clock": "perf_counter_ns",
                "warmup_runs": benchmark.warmup_runs,
                "repetitions": benchmark.repetitions,
            },
            "latency_ns": {
                "g1_vector": {
                    "build": summary(samples["vector_build"]),
                    "query": summary(samples["vector_query"]),
                },
                "g2_hybrid": {
                    "build": summary(
                        [
                            vector_build + bm25_build
                            for vector_build, bm25_build in zip(
                                samples["vector_build"],
                                samples["bm25_build"],
                                strict=True,
                            )
                        ]
                    ),
                    "query": summary(
                        [
                            vector_query + bm25_query + fusion
                            for vector_query, bm25_query, fusion in zip(
                                samples["vector_query"],
                                samples["bm25_query"],
                                samples["fusion"],
                                strict=True,
                            )
                        ]
                    ),
                },
            },
            "index_size": {
                "vector": vector_size,
                "bm25": bm25_size,
                "g1_total_bytes": vector_bytes,
                "g2_total_bytes": vector_bytes + bm25_size["serialized_bytes"],
            },
        },
    )


@dataclass(frozen=True)
class _VectorIndex:
    corpus: Corpus
    embedder: Embedder
    config: VectorIndexConfig
    vectors: tuple[tuple[float, ...], ...]


def _build_vector_index(
    corpus: Corpus,
    embedder: Embedder,
    config: VectorIndexConfig,
) -> _VectorIndex:
    if not corpus.chunks or config.top_k < 1 or config.score_precision < 0:
        raise ValueError("Vector retrieval requires chunks, positive top_k and score precision")

    document_vectors = tuple(
        tuple(float(value) for value in vector)
        for vector in embedder.embed_documents(
        [f"{chunk.path}\n{chunk.content}" for chunk in corpus.chunks]
        )
    )
    if len(document_vectors) != len(corpus.chunks):
        raise ValueError("Embedder returned an unexpected vector shape")
    if any(len(vector) != embedder.dimension for vector in document_vectors):
        raise ValueError("Embedder returned an unexpected vector shape")
    return _VectorIndex(corpus, embedder, config, document_vectors)


def _query_vector_index(index: _VectorIndex, query: str) -> RetrievalRun:
    corpus = index.corpus
    embedder = index.embedder
    config = index.config
    query_vector = embedder.embed_query(query)
    if len(query_vector) != embedder.dimension:
        raise ValueError("Embedder returned an unexpected vector shape")

    scored: list[tuple[str, Chunk]] = []
    for chunk, vector in zip(corpus.chunks, index.vectors, strict=True):
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


def run_vector_retrieval(
    corpus: Corpus,
    query: str,
    embedder: Embedder,
    config: VectorIndexConfig,
) -> RetrievalRun:
    return _query_vector_index(_build_vector_index(corpus, embedder, config), query)


def evaluate_recall(
    retrieval_run: RetrievalRun | HybridRun | RerankedRun,
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


def evaluate_retrieval_metrics(
    retrieval_run: RetrievalRun | HybridRun | RerankedRun,
    gold_path: Path,
) -> RetrievalMetrics:
    gold: dict[str, list[dict[str, Any]]] = json.loads(
        gold_path.read_text(encoding="utf-8")
    )
    relevance = {
        str(item["path"]): int(item["relevance"])
        for item in gold["root_cause_files"]
    }
    first_relevant_rank = next(
        (
            candidate.rank
            for candidate in retrieval_run.candidates
            if relevance.get(candidate.path, 0) > 0
        ),
        None,
    )

    seen_paths: set[str] = set()
    dcg = 0.0
    for candidate in retrieval_run.candidates[:10]:
        grade = 0 if candidate.path in seen_paths else relevance.get(candidate.path, 0)
        seen_paths.add(candidate.path)
        dcg += (2**grade - 1) / math.log2(candidate.rank + 1)
    ideal_grades = sorted(relevance.values(), reverse=True)[:10]
    ideal_dcg = math.fsum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, start=1)
    )

    return RetrievalMetrics(
        recall_at_5=evaluate_recall(retrieval_run, gold_path, top_k=5),
        recall_at_10=evaluate_recall(retrieval_run, gold_path, top_k=10),
        mrr=round(1 / first_relevant_rank, 6) if first_relevant_rank else 0.0,
        ndcg_at_10=round(dcg / ideal_dcg, 6) if ideal_dcg else 0.0,
    )


def run_regression_suite(manifest_path: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = RerankerConfig(**manifest["reranker"])
    thresholds: dict[str, float] = manifest["thresholds"]

    class RankPreservingReranker:
        identifier = "g2-rank-preserving-ci-v1"

        def score(
            self, query: str, candidates: Sequence[HybridCandidate]
        ) -> list[float]:
            return [-float(candidate.rank) for candidate in candidates]

    entries: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        source: dict[str, Any] = json.loads(
            (manifest_path.parent / entry["source"]).read_text(encoding="utf-8")
        )
        if entry["kind"] == "real_snapshot":
            query = source["shared_input"]["query"]
            candidates = source["groups"]["g2_hybrid"]["candidates"]
            g2 = HybridRun(
                query=query,
                candidates=tuple(HybridCandidate(**candidate) for candidate in candidates),
                metadata={"strategy": "rrf-v1"},
            )
            pipeline = "frozen-g2+reranker"
        else:
            query = source["query"]
            corpus = Corpus(
                chunks=tuple(Chunk(**chunk) for chunk in source["chunks"]),
                chunking_config=ChunkingConfig(**source["chunking"]),
            )
            class FixedEmbedder:
                def __init__(self, fixed_corpus: Corpus, fixed_query: str,
                             settings: dict[str, Any]) -> None:
                    self.identifier = str(settings["identifier"])
                    self.dimension = int(settings["dimension"])
                    self.expected_texts = [
                        f"{chunk.path}\n{chunk.content}" for chunk in fixed_corpus.chunks
                    ]
                    self.query = fixed_query
                    self.document_vectors = [
                        tuple(float(value) for value in vector)
                        for vector in settings["document_vectors"]
                    ]
                    self.query_vector = tuple(float(value) for value in settings["query_vector"])

                def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
                    if list(texts) != self.expected_texts:
                        raise ValueError("Hand-fixed corpus changed")
                    return self.document_vectors

                def embed_query(self, text: str) -> Sequence[float]:
                    if text != self.query:
                        raise ValueError("Hand-fixed query changed")
                    return self.query_vector

            g2 = run_retrieval_comparison(
                corpus, query, FixedEmbedder(corpus, query, source["embedding"]),
                VectorIndexConfig(**source["vector"]),
                BM25Config(**source["bm25"]),
                FusionConfig(**source["fusion"]),
            ).g2_hybrid
            pipeline = "vector+bm25+rrf+reranker"
        g3 = rerank_hybrid_run(g2, RankPreservingReranker(), config)
        metrics = evaluate_retrieval_metrics(g3, manifest_path.parent / entry["gold"])
        failures = check_retrieval_thresholds(metrics, thresholds)
        entries.append(
            {"id": entry["id"], "kind": entry["kind"], "pipeline": pipeline,
             "metrics": asdict(metrics), "failures": failures}
        )
    return {
        "status": "failed" if any(entry["failures"] for entry in entries) else "passed",
        "scorer": RankPreservingReranker.identifier,
        "thresholds": thresholds,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "entries": entries,
    }


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


def write_retrieval_comparison(
    output_path: Path,
    case_id: str,
    comparison: RetrievalComparison,
    g1_metrics: RetrievalMetrics,
    g2_metrics: RetrievalMetrics,
) -> None:
    result = {
        "schema_version": "1.0",
        "case_id": case_id,
        "shared_input": comparison.shared_input,
        "groups": {
            "g1_vector": {
                "candidates": [
                    asdict(candidate) for candidate in comparison.g1_vector.candidates
                ],
                "metrics": asdict(g1_metrics),
            },
            "g2_hybrid": {
                "candidates": [
                    asdict(candidate) for candidate in comparison.g2_hybrid.candidates
                ],
                "metrics": asdict(g2_metrics),
            },
        },
        "metadata": comparison.metadata,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def write_reranker_comparison(
    output_path: Path,
    case_id: str,
    comparison: RerankerComparison,
    gold_path: Path,
) -> None:
    groups: dict[str, RetrievalRun | HybridRun | RerankedRun] = {
        "g1_vector": comparison.g1_vector,
        "g2_hybrid": comparison.g2_hybrid,
        "g3_reranked": comparison.g3_reranked,
    }
    result = {
        "schema_version": "1.0",
        "case_id": case_id,
        "shared_input": comparison.shared_input,
        "groups": {
            name: {
                "candidates": [asdict(candidate) for candidate in run.candidates],
                "metrics": asdict(evaluate_retrieval_metrics(run, gold_path)),
            }
            for name, run in groups.items()
        },
        "metadata": comparison.metadata,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
