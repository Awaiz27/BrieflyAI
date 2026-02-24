from pydantic import BaseModel, Field, conint
from typing import Optional, List
from datetime import datetime


class RankRequest(BaseModel):
    window_days: int
    category: Optional[List[str]] = None
    query: Optional[str] = None
    top_k: int = 50


from typing import List, Optional
from pydantic import BaseModel, Field, conint

class RankRequest(BaseModel):
    window_days: conint(gt=0) = Field(
        default=1, 
        description="Number of days to look back (must be > 0)"
    )
    category: Optional[List[str]] = Field(
        default=None,
        description="Optional list of categories to filter"
    )
    query: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Optional search query"
    )
    top_k: conint(gt=0, le=100) = Field(default=50,description="Number of results to return (1-100)" )


class Paper(BaseModel):
    paper_id: str = Field(..., description="Unique paper identifier")
    title: str
    summary: str
    categories: str
    submitted_at: datetime
    score: float = Field(..., ge=0)

class RankResponse(BaseModel):
    results: List[Paper]