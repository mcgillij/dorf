import lmstudio as lms
import asyncio
from typing import List, Dict

from bot.tools.searxng_search import search_internet

from bot.utilities import setup_logger

logger = setup_logger(__name__)


# Tool defs
def search_tool(query: str) -> List[Dict]:
    """Searches the internet for a given query"""
    logger.info("Searching the internet for: %s", query)
    return asyncio.run(search_internet(query))


def multiply(a: float, b: float) -> float:
    """Given two numbers a and b. Returns the product of them."""
    logger.info(f"Multiplying {a} by {b}")
    return a * b


async def search_with_tool(query: str) -> List[Dict]:
    """Searches the internet for a given query"""
    logger.info("Searching the internet for: %s", query)
    # model = lms.llm("qwen2.5-14b-instruct")
    model = lms.llm("qwen2.5-7b-instruct")
    model.act(
        query
        + " you can search the internet with the search_tool, Show your sources 'url/title' in the results of the search.",
        [multiply, search_tool],
        on_message=print,
    )
    return await search_internet(query)


if __name__ == "__main__":
    # Example usage
    query = "What is the newest assassins creed game?"
    results = search_tool(query)
    print("Search results:", results)

    # Example usage with async
    asyncio.run(search_with_tool(query))
