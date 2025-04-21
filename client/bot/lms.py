import lmstudio as lms
import asyncio
from typing import List, Dict

from bot.tools.searxng_search import search_internet

from bot.log_config import setup_logger

logger = setup_logger(__name__)
MAX_PREDICTION_ROUNDS = 10


async def wrap_model_act(model, query, tools, on_message=None, callback=None) -> str:
    """Wrap a synchronous call in an async context if necessary."""
    logger.info("in the wrapper")
    loop = asyncio.get_event_loop()

    search_results = []

    def append_search(param):
        logger.info(f"-----------------------> Appending: {param=}")
        # callback(param=param)
        search_results.append(param)

    await loop.run_in_executor(
        None,
        lambda: model.act(
            query,
            tools,
            on_message=append_search,
            # max_prediction_rounds=MAX_PREDICTION_ROUNDS,
            # on_round_start=callback,
            # on_round_end=callback,
            # on_prediction_completed=callback,
        ),
    )
    logger.info(f"********************** {search_results=}")
    parsed_result = parse_all_texts(search_results)
    # parsed_result = parse_search_result(search_results)

    logger.info(f"********************** {parsed_result=}")
    return parsed_result


async def search_with_tool(query: str, callback) -> str:
    """Searches the internet for a given query"""

    # Tool defs
    def search_tool(query: str) -> List[Dict]:
        """Searches the internet for a given query"""
        logger.info("Searching the internet for: %s", query)
        return asyncio.run(search_internet(query, callback=callback))

    logger.info("Searching the internet for: %s", query)
    model = (
        lms.llm()
    )  # load the default model, just make sure the default model always has tools training
    logger.info("model loaded")
    response = await wrap_model_act(
        model,
        query
        + " always use your 'search_tool' and add the sources as links at the end, give your response in text, never JSON",
        [search_tool],
        callback=callback,
    )
    return response


def parse_all_texts(search_results: list) -> str:
    results = []
    for msg in search_results:
        content = getattr(msg, "content", None)
        if not content:
            continue

        for part in content:
            text = getattr(part, "text", None)
            if (
                text
                and "json" not in text.lower()
                and "function call" not in text.lower()
            ):
                results.append(text.strip())

    return "\n\n".join(results) if results else "No valid response found."
