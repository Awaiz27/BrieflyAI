# System Prompts

This folder contains all system prompts used by the research agent, separated into individual files for easy maintenance and versioning.

## Prompt Files

### Core Prompts

| File | Used By | Purpose |
|------|---------|---------|
| `writer_system.txt` | `_compose()` + `_write()` | Main system prompt for composing and writing research answers |
| `router_need_context.txt` | `_need_context()` | Determines if context/retrieval is needed for a query |
| `router_plan.txt` | `_plan_sources()` | Decides which sources to use (history, RAG, both, or none) |
| `rewrite_query.txt` | `_rewrite_query()` | Rewrites user queries for better retrieval |
| `rag_expand.txt` | `_rag_expand()` | Generates multiple query variants for improved recall |
| `rag_routing.txt` | `_rag_route()` | Routes queries to appropriate search target (papers/chunks/both) |
| `review.txt` | `_review()` | Quality review of generated answers |
| `summary.txt` | `_refresh_summary()` | Maintains rolling conversation summaries |

## Loading Prompts

All prompts are loaded using the `PromptLoader` utility:

```python
from app.prompts import PromptLoader

# Load a prompt
prompt = PromptLoader.load("writer_system")

# Prompts with parameters (e.g., rag_expand needs {n_queries})
template = PromptLoader.load("rag_expand")
prompt = template.format(n_queries=5)
```

## Editing Prompts

To modify a system prompt:

1. Edit the corresponding `.txt` file
2. No code changes needed - the agent automatically loads updated content
3. Changes take effect on the next agent restart (or when cache is cleared)

## Prompt Format

Each prompt file contains:
- Plain text (no special formatting needed)
- Clear instructions for the LLM
- Expected JSON output format (when applicable)
- Examples or guidelines

## Format Placeholders

Some prompts contain format placeholders:

| Placeholder | Prompt | Substituted With |
|------------|--------|------------------|
| `{n_queries}` | `rag_expand.txt` | Number of query variants to generate |

## Caching

Prompts are cached in memory after first load. To clear the cache:

```python
from app.prompts import PromptLoader

PromptLoader.clear_cache()
```

This is useful during development when editing prompt files.

## Adding New Prompts

To add a new system prompt:

1. Create a new `.txt` file in this directory (e.g., `my_new_prompt.txt`)
2. Add the prompt content
3. Load it in code:
   ```python
   from app.prompts import PromptLoader
   prompt = PromptLoader.load("my_new_prompt")
   ```

## Best Practices

1. **Keep it simple**: Clear, direct instructions work better
2. **Be specific**: Include examples and expected output format
3. **Use JSON**: When expecting structured output, specify exact format
4. **Version control**: Commit prompt changes with code changes
5. **Test changes**: Verify prompt behavior before deploying
6. **Document intent**: Add comments to complex prompts

## File Structure

```
app/prompts/
├── __init__.py                 # PromptLoader utility
├── README.md                   # This file
├── writer_system.txt           # Main writing prompt
├── router_need_context.txt     # Context routing
├── router_plan.txt             # Source planning
├── rewrite_query.txt           # Query rewriting
├── rag_expand.txt              # Query expansion
├── rag_routing.txt             # RAG target routing
├── review.txt                  # Quality review
└── summary.txt                 # Summarization
```

## Performance Notes

- Prompts are loaded lazily (only when first used)
- Loaded prompts are cached in memory
- Reading from disk is fast (~1ms per prompt)
- Caching avoids repeated disk reads

## Troubleshooting

### Prompt not found error
```
FileNotFoundError: Prompt file not found: .../app/prompts/my_prompt.txt
```
**Solution**: Check that the `.txt` file exists in `app/prompts/` and the name matches exactly (case-sensitive on Linux).

### Format placeholder not substituted
```python
# Wrong
prompt = PromptLoader.load("rag_expand")  # Has {n_queries} placeholder

# Right
template = PromptLoader.load("rag_expand")
prompt = template.format(n_queries=5)
```

### Prompt not updated after editing
**Solution**: Clear the cache between tests:
```python
PromptLoader.clear_cache()
```
