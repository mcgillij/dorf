import lmstudio as lms
import asyncio
from typing import List, Dict

from bot.tools.searxng_search import search_internet

from bot.log_config import setup_logger

logger = setup_logger(__name__)
MAX_PREDICTION_ROUNDS = 5


# Tool defs
def search_tool(query: str) -> List[Dict]:
    """Searches the internet for a given query"""
    logger.info("Searching the internet for: %s", query)
    return asyncio.run(search_internet(query))


async def wrap_model_act(model, query, tools, on_message=None) -> str:
    """Wrap a synchronous call in an async context if necessary."""
    logger.info("in the wrapper")
    loop = asyncio.get_event_loop()

    search_results = []

    def append_search(param):
        print(f"Appending: {param=}")
        search_results.append(param)

    # result = await loop.run_in_executor(None, lambda: model.respond(query, tools, on_message=on_message))
    result = await loop.run_in_executor(
        None,
        lambda: model.act(
            query,
            tools,
            on_message=append_search,
            max_prediction_rounds=MAX_PREDICTION_ROUNDS,
            on_round_start=print,
            on_round_end=print,
            on_prediction_completed=print,
        ),
    )
    logger.info(f"{search_results=}")
    parsed_result = parse_search_result(search_results)

    return parsed_result


async def search_with_tool(query: str) -> str:
    """Searches the internet for a given query"""
    logger.info("Searching the internet for: %s", query)
    model = (
        lms.llm()
    )  # load the default model, just make sure the default model always has tools training
    logger.info("model loaded")
    response = await wrap_model_act(
        model,
        query
        + " Show the sources they are provided as 'url/title' in the results, format the message as discord message markdown",
        [search_tool],
    )
    return response


def parse_search_result(search_results: List) -> str:
    for item in search_results:
        # Convert the object to a dictionary if it is not already one
        if hasattr(item, "to_dict"):
            item = item.to_dict()

        if "content" in item:
            for content_item in item["content"]:
                if "text" in content_item:
                    text_value = content_item["text"]
                    if text_value != "":
                        return text_value


if __name__ == "__main__":
    # Example usage
    query = "What is the newest assassins creed game?"
    results = search_tool(query)
    print("Search results:", results)

    # Example usage with async
    asyncio.run(search_with_tool(query))
