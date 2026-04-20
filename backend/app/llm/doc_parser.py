"""Document ingestion pipeline: PDF parsing, chunking, embedding."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from docling.chunking import HybridChunker
from docling.datamodel.accelerator_options import AcceleratorDevice
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.pipeline.threaded_standard_pdf_pipeline import ThreadedStandardPdfPipeline

from app.db.engine import get_session
from app.db.repositories.papers import (
    fetch_unprocessed_papers,
    insert_abstract_chunks,
    insert_chunks,
)
from app.llm.clients import OllamaEmbeddingClient
from app.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestConfig:
    device: AcceleratorDevice = AcceleratorDevice.CPU
    ocr_batch_size: int = 2
    layout_batch_size: int = 16
    table_batch_size: int = 2
    chunk_max_tokens: int = 5000
    chunk_min_tokens: int = 500


def _build_converter(cfg: IngestConfig) -> DocumentConverter:
    pipeline_options = ThreadedPdfPipelineOptions(
        accelerator_options={"device": cfg.device},
        ocr_batch_size=cfg.ocr_batch_size,
        layout_batch_size=cfg.layout_batch_size,
        table_batch_size=cfg.table_batch_size,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=ThreadedStandardPdfPipeline,
                pipeline_options=pipeline_options,
            )
        }
    )


class DocumentIngestionPipeline:
    def __init__(self, config: IngestConfig | None = None):
        s = get_settings()
        self._cfg = config or IngestConfig(
            ocr_batch_size=s.ocr_batch_size,
            layout_batch_size=s.layout_batch_size,
            table_batch_size=s.table_batch_size,
            chunk_max_tokens=s.chunk_max_tokens,
            chunk_min_tokens=s.chunk_min_tokens,
        )
        self._converter = _build_converter(self._cfg)
        self._converter.initialize_pipeline(InputFormat.PDF)
        self._simple_converter = DocumentConverter()
        self._chunker = HybridChunker(
            tokenizer="Qwen/Qwen3-Embedding-0.6B",
            max_tokens=self._cfg.chunk_max_tokens,
            min_tokens=self._cfg.chunk_min_tokens,
        )
        self._embedder = OllamaEmbeddingClient(
            base_url=s.model_base_url,
            model=s.embedder_model_name,
        )
        self._io_sem = asyncio.Semaphore(s.io_concurrency)
        self._paper_sem = asyncio.Semaphore(max(1, int(s.ingest_max_parallel_papers)))
        self._pool = ThreadPoolExecutor(max_workers=s.cpu_workers)

    def _extract_page_range(self, chunk: Any) -> tuple[int | None, int | None]:
        meta = getattr(chunk, "meta", None)
        if not meta or not hasattr(meta, "doc_items"):
            return None, None
        pages = []
        for item in meta.doc_items:
            for prov in item.prov or []:
                if prov.page_no is not None:
                    pages.append(prov.page_no)
        return (min(pages), max(pages)) if pages else (None, None)

    async def _embed_chunks(self, chunks: Any, base_meta: dict) -> list[dict]:
        texts, metas = [], []
        for idx, chunk in enumerate(chunks):
            chunk_text = getattr(chunk, "text", "").strip()
            if not chunk_text:
                continue
            meta = {**base_meta, "chunk_index": idx, "text": chunk_text}
            chunk_meta = getattr(chunk, "meta", None)
            if chunk_meta and getattr(chunk_meta, "headings", None):
                headings = chunk_meta.headings
                meta["section"] = ", ".join(headings) if isinstance(headings, list) else str(headings)
            ps, pe = self._extract_page_range(chunk)
            if ps is not None:
                meta["page_start"], meta["page_end"] = ps, pe
            texts.append(chunk_text)
            metas.append(meta)

        if not texts:
            return []
        embeddings = await self._embedder.embed(texts)
        for m, v in zip(metas, embeddings):
            m["embedding"] = v
        return metas

    async def _abstract_ingest(self, doc_id: str, pdf_url: str, text: str) -> list[dict]:
        result = self._simple_converter.convert_string(text, format=InputFormat.MD)
        abstract_chunks = list(self._chunker.chunk(result.document))
        return await self._embed_chunks(abstract_chunks, {"rp_abstract_id": doc_id, "pdf_url": pdf_url})

    def _ingest_sync(self, data: dict) -> tuple[str, str, list, str]:
        doc_id = data["id"]
        pdf_url = data["pdf_url"]
        result = self._converter.convert(pdf_url)
        if result.status != ConversionStatus.SUCCESS:
            raise RuntimeError(f"Docling conversion failed for {pdf_url}")
        doc_chunks = list(self._chunker.chunk(getattr(result, "document", result)))
        abstract_text = f'{data["title"]}\n\n{data["summary"]}'
        return doc_id, pdf_url, doc_chunks, abstract_text

    async def _process_paper(self, paper: dict) -> None:
        async with self._paper_sem:
            try:
                loop = asyncio.get_running_loop()
                doc_id, pdf_url, doc_chunks, abstract_text = await loop.run_in_executor(
                    self._pool, self._ingest_sync, paper
                )
                abstract_records = await self._abstract_ingest(doc_id, pdf_url, abstract_text)
                body_records = await self._embed_chunks(
                    doc_chunks, {"rp_abstract_id": doc_id, "doc_id": pdf_url, "type": "body"}
                )
                async with self._io_sem:
                    async with get_session() as session:
                        async with session.begin():
                            await insert_abstract_chunks(session, abstract_records)
                            await insert_chunks(session, body_records)
            except Exception:
                logger.exception("Failed processing paper id=%s", paper.get("id"))

    async def run(self) -> None:
        async with get_session() as session:
            async with session.begin():
                papers = await fetch_unprocessed_papers(session)

        total = len(papers)
        if total == 0:
            logger.info("No new papers to process")
            return

        logger.info("Processing %d unprocessed papers", total)
        tasks = [asyncio.create_task(self._process_paper(p)) for p in papers]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Finished processing %d papers", total)
