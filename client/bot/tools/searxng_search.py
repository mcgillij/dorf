import json
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

SEARCH_URL = "https://searx.mcgillij.dev"
RELEVANT_THRESHOLD = 0.5


async def search_internet(q: str, callback=None) -> List[Dict]:
    """search the internet for the top results of a query, to be used when llm is unfamiliar with a topic"""
    logger.info(f"Searching the internet for: {q}")
    url = SEARCH_URL
    params = {"q": q, "format": "json"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params, ssl=True) as response:
            if response.status == 200:
                # Read the response content
                logger.info("awaiting the search results")
                data = await response.text()
                logger.info("converting response to text")
                # Parse the JSON data
                data = json.loads(data)
                # Print the content of the results
                results = []
                source_num = 0
                for result in data.get("results", []):

                    title = result.get("title")
                    url = result.get("url")
                    score = result.get("score")
                    content = result.get("content")

                    if score > RELEVANT_THRESHOLD:
                        source_num += 1
                        discord_formatted_message = (
                            f"Researching [**{source_num}**]: [{title}](<{url}>)"
                        )
                        callback(param=discord_formatted_message)
                        logger.info(
                            f"------------------ Entering search result for {title}"
                        )
                        results.append(
                            {
                                "url": url,
                                "title": title,
                                "score": score,
                                "content": content,
                            }
                        )
                    else:
                        logger.info("Skipping search result score TOO LOW")
                return results
            else:
                logger.info(f"Failed to retrieve data. Status code: {response.status}")
                return []
