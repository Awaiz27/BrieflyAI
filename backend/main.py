"""Entry point — uvicorn app.main:app or python main.py"""

from app.main import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    from app.settings import get_settings

    s = get_settings()
    uvicorn.run("app.main:app", host=s.app_host, port=s.app_port, reload=s.debug)
