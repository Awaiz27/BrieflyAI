from http import HTTPStatus
from fastapi import APIRouter, HTTPException
from api.schemas import RankRequest, RankResponse, Paper
from services.retrieval import get_intent_vector
from services.ranker import rank_papers

router = APIRouter() #prefix="/"


@router.post("/rank", response_model=RankResponse)
async def rank_endpoint(req: RankRequest) -> RankResponse:
    try:
        intent_vec = await get_intent_vector(req.query, req.category)
        # print(len(intent_vec))
        # print(req.category)
        rows = await rank_papers(
            intent_vectors=intent_vec,
            window_days=req.window_days,
            categories=req.category,
            top_k=req.top_k,
        )
        # print(rows)
        return RankResponse(
            results=[
                Paper(
                    paper_id=str(r["id"]),
                    title=r["title"],
                    summary=r["summary"],
                    categories=r["primary_category"],
                    submitted_at=r["created_at"],
                    score=float(r["score"]),
                )
                for r in rows
            ]
        )
    except ValueError as e:
        print(e)
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(e))
