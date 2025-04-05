import json
import aiohttp
from typing import List, Dict
from bot.log_config import setup_logger

logger = setup_logger(__name__)
SEARCH_URL = "https://searx.mcgillij.dev"


async def search_internet(q: str) -> List[Dict]:
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
                for result in data.get("results", []):
                    results.append(
                        {
                            "url": result.get("url"),
                            "title": result.get("title"),
                            "score": result.get("score"),
                            "content": result.get("content"),
                        }
                    )
                return results
            else:
                print(f"Failed to retrieve data. Status code: {response.status}")
                return []
