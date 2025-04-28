import json
import aiohttp
from typing import List, Dict
import asyncio
import logging
import trafilatura

from bot.chroma import collection, summarize_text
from bot.constants import RELEVANT_THRESHOLD

logger = logging.getLogger(__name__)


SEARCH_URL = "https://searx.mcgillij.dev"


async def search_internet(q: str, callback=None) -> List[Dict]:
    """search the internet for the top results of a query, to be used when llm is unfamiliar with a topic"""
    logger.info(f"Searching the internet for: {q}")
    url = SEARCH_URL
    params = {"q": q, "format": "json"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, ssl=True) as response:
            if response.status == 200:
                data = await response.text()
                data = json.loads(data)
                results = []
                source_num = 0

                for result in data.get("results", []):
                    title = result.get("title")
                    url = result.get("url")
                    score = result.get("score")
                    content = result.get("content")

                    if score > RELEVANT_THRESHOLD:
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

                        # Store full content in ChromaDB
                        if extracted_content:
                            collection.add(
                                documents=[extracted_content],
                                metadatas=[{"source_url": url, "title": title}],
                                ids=[url],  # Using URL as a unique ID
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

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, ssl=True) as response:
            if response.status == 200:
                data = await response.text()
                data = json.loads(data)
                results = []

                for result in data.get("results", []):
                    title = result.get("title")
                    url = result.get("url")
                    score = result.get("score")
                    content = result.get("content")

                    if score > RELEVANT_THRESHOLD:
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
