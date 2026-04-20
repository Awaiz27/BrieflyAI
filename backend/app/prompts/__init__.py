"""Prompt loader utility for managing system prompts from external files."""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Prompts directory
PROMPTS_DIR = Path(__file__).parent
PROMPTS_DIR = Path(__file__).parent


class PromptLoader:
    """Load system prompts from external files with caching."""
    
    _cache: dict[str, str] = {}
    
    @classmethod
    def load(cls, prompt_name: str) -> str:
        """Load a prompt by name (without .txt extension).
        
        Args:
            prompt_name: Name of the prompt file (e.g., 'writer_system')
        
        Returns:
            Prompt content as string
        
        Raises:
            FileNotFoundError: If prompt file doesn't exist
        """
        if prompt_name in cls._cache:
            return cls._cache[prompt_name]
        
        prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
        
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        
        try:
            content = prompt_path.read_text(encoding="utf-8").strip()
            cls._cache[prompt_name] = content
            logger.debug(f"[PromptLoader] Loaded prompt: {prompt_name}")
            return content
        except Exception as e:
            logger.error(f"[PromptLoader] Failed to load {prompt_name}: {e}")
            raise
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the prompt cache (useful for testing)."""
        cls._cache.clear()
        logger.debug("[PromptLoader] Cache cleared")
