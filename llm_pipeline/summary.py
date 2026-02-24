from db.research_paper_data import fetch_paper_abstract_chunk_batch, update_abstract_chunk_summaries
from llm_pipeline.OllamaClient import OllamaLLMClient
from configs.constants import MODEL_BASE_URL, LLM_MODEL_NAME
import logging

logger = logging.getLogger(__name__)

system_prompt = """
You are a scholarly reviewer. Your task is to produce a concise yet comprehensive summary of a research paper’s abstract. 
The summary must clearly capture the study’s objectives, methodology, key arguments, major findings, and conclusions. 
Ensure that all critical points and contributions of the paper are accurately represented, while rewriting the content in original language that preserves the intended meaning and academic tone.
"""

class LLMSummarizer:
    def __init__(self, 
                 batch_size: int = 50, 
                 timeout : int = 60, 
                 system_prompt : str = "You are an AI Assitant",
                 ):
        self.batch_size = batch_size

        self.summary_model = OllamaLLMClient(
            base_url=MODEL_BASE_URL,
            model=LLM_MODEL_NAME,
            max_concurrency=4,
            system_prompt=system_prompt,
            timeout=timeout,
        )

    async def run(self):
        logger.info("Starting LLM summarization job")

        async with self.summary_model:
            while True:
                rows = await fetch_paper_abstract_chunk_batch(
                    limit=self.batch_size
                )

                if not rows:
                    logger.info("All records processed")
                    break

                prompts = [row["text"] for row in rows]

                summaries = await self.summary_model.generate(
                    prompts=prompts
                )

                update_rows = [
                    {
                        "id": row["id"],
                        "llm_summary": summary,
                    }
                    for row, summary in zip(rows, summaries)
                ]

                await update_abstract_chunk_summaries(update_rows)

                logger.info(
                    "Processed %d records",
                    len(update_rows),
                )

        logger.info("LLM summarization completed")