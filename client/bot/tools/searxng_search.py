import json
import time
import aiohttp
from typing import List, Dict
import asyncio
import logging
import trafilatura

from bot.chroma import collection, summarize_text
from bot.constants import RELEVANT_THRESHOLD

logger = logging.getLogger(__name__)


SEARCH_URL = "https://searx.mcgillij.dev"

# Bounded so a hung searxng can't stall the RAG path for aiohttp's
# default 300s.
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _parse_results(data_text: str):
    try:
        data = json.loads(data_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("searxng returned non-JSON body (%s chars)", len(data_text or ""))
        return []
    return data.get("results", []) or []


async def search_internet(q: str, callback=None) -> List[Dict]:
    """search the internet for the top results of a query, to be used when llm is unfamiliar with a topic"""
    logger.info(f"Searching the internet for: {q}")
    url = SEARCH_URL
    params = {"q": q, "format": "json"}

    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(url, params=params, ssl=True) as response:
            if response.status == 200:
                data = await response.text()
                search_results = _parse_results(data)
                results = []
                source_num = 0

                for result in search_results:
                    title = result.get("title")
                    url = result.get("url")
                    score = result.get("score")
                    content = result.get("content")

                    if (
                        isinstance(score, (int, float))
                        and score > RELEVANT_THRESHOLD
                    ):
                        downloaded = await asyncio.to_thread(trafilatura.fetch_url, url)
                        extracted_content = await asyncio.to_thread(
                            trafilatura.extract, downloaded
                        )

                        if extracted_content:
                            summarized_content = summarize_text(
                                extracted_content, max_sentences=5
                            )
                        else:
                            summarized_content = content  # fallback

                        source_num += 1
                        discord_formatted_message = (
                            f"Researching [**{source_num}**]: [{title}](<{url}>)"
                        )
                        if callback:
                            callback(param=discord_formatted_message)

                        results.append(
                            {
                                "url": url,
                                "title": title,
                                "score": score,
                                "content": summarized_content,
                            }
                        )

                        # Store full content in ChromaDB (upsert: re-searching
                        # a stored URL must not raise DuplicateIDError and
                        # abort the whole search flow). ChromaDB runs
                        # embedding + sqlite internally — blocking; offload
                        # like the sibling chroma.py calls.
                        if extracted_content:
                            try:
                                await asyncio.to_thread(
                                    collection.upsert,
                                    documents=[extracted_content],
                                    metadatas=[
                                        {
                                            "source_url": url,
                                            "title": title,
                                            # Unix ts — the retention sweeper
                                            # (chroma.sweep_old_documents)
                                            # prunes on this.
                                            "stored_at": time.time(),
                                        }
                                    ],
                                    ids=[url],  # Using URL as a unique ID
                                )
                            except Exception:
                                logger.warning(
                                    "searxng.chroma_store_failed url=%s", url,
                                    exc_info=True,
                                )
                    else:
                        logger.info("Skipping search result score TOO LOW")

                return results
            else:
                logger.info(f"Failed to retrieve data. Status code: {response.status}")
                return []


async def search_source(source_url: str, topic: str, callback=None) -> List[Dict]:
    """Search a specific source for the current week's entries related to a topic."""
    logger.info(f"Searching {source_url} for topic: {topic}")
    url = SEARCH_URL
    params = {
        "q": f"site: {source_url} {topic}",
        "format": "json",
        "time_range": "week",
    }  # Narrow to current week

    async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
        async with session.get(url, params=params, ssl=True) as response:
            if response.status == 200:
                data = await response.text()
                search_results = _parse_results(data)
                results = []

                for result in search_results:
                    title = result.get("title")
                    url = result.get("url")
                    score = result.get("score")
                    content = result.get("content")

                    if (
                        isinstance(score, (int, float))
                        and score > RELEVANT_THRESHOLD
                    ):
                        results.append(
                            {
                                "url": url,
                                "title": title,
                                "score": score,
                                "content": content,
                            }
                        )
                        if callback:
                            callback(param=f"Found relevant result: [{title}](<{url}>)")
                return results
            else:
                logger.info(
                    f"Failed to retrieve data from {source_url}. Status code: {response.status}"
                )
                return []
