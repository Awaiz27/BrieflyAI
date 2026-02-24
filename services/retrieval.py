from typing import List, Optional
from db.update_intent_vectors import fetch_intent_from_db
from services.embeddings import embed_query, blend_vectors

async def get_intent_vector(query: Optional[str], category: Optional[List[str]]) -> List[float]:
    """
    Unified logic:
    - no query, no category => global centroid
    - category only => category centroid
    - query only => query embedding
    - query + category => blend(query, category centroid)
    """
    if query and category:
       
        qv = embed_query(query)
        cv = await fetch_intent_from_db(category)
        return blend_vectors(qv, cv, alpha=0.7)

    if query:
        return embed_query(query)

    if category:
        return await fetch_intent_from_db(category)

    return await fetch_intent_from_db(["global"])
