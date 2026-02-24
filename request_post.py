import asyncio
import httpx


async def call_rank_papers():
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "http://localhost:9001/rank",
            json={
                "query": "diffusion transformers",
                "category": ["cs.CV"],
                "window_days": 60,
                "top_k": 4,
            },
             headers={"Content-Type": "application/json"}
        )

        response.raise_for_status()
        data = response.json()
        print(data)
        # for paper in data:
        #     print(paper["title"], "→ score:", paper["score"])


if __name__ == "__main__":
    asyncio.run(call_rank_papers())
