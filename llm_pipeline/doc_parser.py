from dataclasses import dataclass
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

from docling.chunking import HybridChunker
from docling.datamodel.base_models import ConversionStatus
from docling.datamodel.accelerator_options import AcceleratorDevice
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
from docling.pipeline.threaded_standard_pdf_pipeline import ThreadedStandardPdfPipeline
from docling.datamodel.base_models import InputFormat

from llm_pipeline.OllamaClient import OllamaEmbeddingClient
from db.research_paper_data import fetch_unprocessed_paper, insert_paper_chunk_data, insert_abstract_chunk_data
from configs.constants import IO_CONCURRENCY , CPU_WORKERS , MODEL_BASE_URL, EMBEDDER_MODEL_NAME

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class DocumentIngestConfig:
    # Accelerator
    device: AcceleratorDevice = AcceleratorDevice.CUDA

    # Pipeline batching
    ocr_batch_size: int = 4
    layout_batch_size: int = 64
    table_batch_size: int = 4

    # Chunking
    chunk_max_tokens: int = 800
    chunk_min_tokens: int = 200

    #embedding
    embedder_base_url: str = MODEL_BASE_URL
    embedder_model_name: str = EMBEDDER_MODEL_NAME


def _build_docling_converter(cfg: DocumentIngestConfig) -> DocumentConverter:
    pipeline_options = ThreadedPdfPipelineOptions(
        accelerator_options={
            "device": cfg.device
        },
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
    """
    Stateless, memory-efficient, ingestion service.
    """

    def __init__(self, config: DocumentIngestConfig):
        self._config = config
        self._converter = _build_docling_converter(config)
        self._converter.initialize_pipeline(InputFormat.PDF)
        self._simple_converter =  DocumentConverter()

        self._chunker = HybridChunker(
            tokenizer = "Qwen/Qwen3-Embedding-0.6B",
            max_tokens=config.chunk_max_tokens,
            min_tokens=config.chunk_min_tokens,
        )  

        self._embedder = OllamaEmbeddingClient(base_url=self._config.embedder_base_url, model=self._config.embedder_model_name)

        self.io_semaphore = asyncio.Semaphore(IO_CONCURRENCY)
        self.process_pool = ThreadPoolExecutor(max_workers=CPU_WORKERS)

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        return await self._embedder.embed(texts)
    
    def _extract_page_range(self,chunk):
        meta = getattr(chunk, "meta", None)
        if not meta or not hasattr(meta, "doc_items"):
            return None, None

        pages = []
        for item in meta.doc_items:
            for prov in item.prov or []:
                if prov.page_no is not None:
                    pages.append(prov.page_no)

        if not pages:
            return None, None

        return min(pages), max(pages)
    
    async def _embed_chunks(
        self,
        chunks,
        base_meta: dict,
    ) -> List[Dict]:
        texts, metas = [], []

        for idx, chunk in enumerate(chunks):
            chunk_text = getattr(chunk, "text", "").strip()
            if not chunk_text:
                continue

            meta = dict(base_meta)
            meta.update({
                "chunk_index": idx,
                "text": chunk_text,
            })

            if not chunk_text:
                continue

            chunk_meta = getattr(chunk, "meta", None)
            if chunk_meta and getattr(chunk_meta, "headings", None):
                #meta["section"] = chunk_meta.headings
                if isinstance(chunk_meta.headings, str):
                    meta["section"] = chunk_meta.headings
                elif isinstance(chunk_meta.headings, List):
                    meta["section"] = ", ".join(chunk_meta.headings)
                else:
                    RuntimeError(f"The section parsing type is not supported : {type(chunk_meta.headings)}")

            page_range = self._extract_page_range(chunk)
            if page_range:
                meta["page_start"], meta["page_end"] = page_range

            texts.append(chunk_text)
            metas.append(meta)

        if not texts:
            return []

        embeddings = await self._embed(texts)

        for meta, vector in zip(metas, embeddings):
            meta["embedding"] = vector

        return metas
    
    async def _abstract_ingest(
        self,
        doc_id: str,
        pdf_url: str,
        text: str,
    ) -> List[Dict]:
        
        result = self._simple_converter.convert_string(text, format=InputFormat.MD)
        doc = result.document

        abstract_chunks = list(self._chunker.chunk(doc))

        base_meta = {
            "rp_abstract_id": doc_id,
            "pdf_url": pdf_url,
        }

        return await self._embed_chunks(
            chunks=abstract_chunks,
            base_meta=base_meta,
        )

    async def _ingest(self, data: dict) -> tuple[list[dict], list[dict]]:

        doc_id = data["id"]
        pdf_url = data["pdf_url"]

        result = self._converter.convert(pdf_url)
        if result.status != ConversionStatus.SUCCESS:
            raise RuntimeError("Docling conversion failed")

        document = getattr(result, "document", result)

        doc_chunks = list(self._chunker.chunk(document))

        base_meta = {
            "rp_abstract_id": doc_id,
            "doc_id": pdf_url,
            # "pdf_url": pdf_url,
            "type": "body",
        }

        abstract_text = f'{data["title"]}\n\n{data["summary"]}'
        abstract_records = await self._abstract_ingest(
            doc_id=doc_id,
            pdf_url=pdf_url,
            text=abstract_text,
        )

        body_records = await self._embed_chunks(
            chunks=doc_chunks,
            base_meta=base_meta,
        )

       

        return body_records, abstract_records
    
    async def _process_single_paper(self, temp_data: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
            body_records, abstract_records = await loop.run_in_executor(
                self.process_pool,
                self._ingest,
                temp_data
                )

            async with self.io_semaphore:
                await insert_abstract_chunk_data(abstract_records)
                await insert_paper_chunk_data(body_records)
        except Exception as e:
            logger.exception(
                f"Failed processing paper id={temp_data.get('id')}: {e}"
            )
    
    async def run(self):

        unprocessed_data = await fetch_unprocessed_paper()
        total = len(unprocessed_data)

        if total == 0:
            logger.info("There are no new Paper to process")
            return

        logger.info(
            f"There are total {total} new paper that will be chunked and embedded."
        )

        tasks = [
        asyncio.create_task(self._process_single_paper(temp_data))
        for temp_data in unprocessed_data
    ]

        # Run concurrently on the SAME event loop
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            f"Record of overall {total} paper has been added to Chunk Table"
        )