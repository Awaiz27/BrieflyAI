import requests
from configs.constants import MAX_PDF_SIZE_MB, DOWNLOAD_TIMEOUT, USER_AGENT


class PDFDownloadError(Exception):
    pass


def download_pdf(url: str) -> bytes:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=DOWNLOAD_TIMEOUT,
        stream=True,
    )
    response.raise_for_status()

    content = b""
    max_bytes = MAX_PDF_SIZE_MB * 1024 * 1024

    for chunk in response.iter_content(chunk_size=8192):
        content += chunk
        if len(content) > max_bytes:
            raise PDFDownloadError("PDF exceeds size limit")

    if not content.startswith(b"%PDF"):
        raise PDFDownloadError("Downloaded file is not a valid PDF")

    return content
