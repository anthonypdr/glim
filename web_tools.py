import re
import requests

from bs4 import BeautifulSoup
from urllib.parse import quote_plus


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(X11; Linux x86_64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120 Safari/537.36"
    )
}


def fetch_url(url, max_chars=12000):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        if (
            "text/html" not in content_type
            and "text/plain" not in content_type
        ):
            return {
                "ok": False,
                "error": (
                    f"Unsupported content type: {content_type}"
                ),
            }

        if "text/html" in content_type:
            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            for tag in soup(
                [
                    "script",
                    "style",
                    "noscript",
                    "svg",
                ]
            ):
                tag.decompose()

            text = soup.get_text(
                "\n",
                strip=True,
            )

        else:
            text = response.text

        text = re.sub(
            r"\n{3,}",
            "\n\n",
            text,
        )

        return {
            "ok": True,
            "url": response.url,
            "content": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    except Exception as error:
        return {
            "ok": False,
            "error": str(error),
        }


def web_search(query, max_results=5):
    search_url = (
        "https://html.duckduckgo.com/html/"
        f"?q={quote_plus(query)}"
    )

    try:
        response = requests.get(
            search_url,
            headers=HEADERS,
            timeout=15,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        results = []

        for result in soup.select(".result"):
            title_link = result.select_one(
                ".result__a"
            )

            snippet = result.select_one(
                ".result__snippet"
            )

            if not title_link:
                continue

            title = title_link.get_text(
                " ",
                strip=True,
            )

            url = title_link.get(
                "href"
            )

            description = (
                snippet.get_text(
                    " ",
                    strip=True,
                )
                if snippet
                else ""
            )

            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": description,
                }
            )

            if len(results) >= max_results:
                break

        return {
            "ok": True,
            "query": query,
            "results": results,
        }

    except Exception as error:
        return {
            "ok": False,
            "error": str(error),
        }
