import lmstudio as lms
import asyncio
from typing import List, Dict
import logging

from bot.tools.searxng_search import search_internet

# from bot.constants import MAX_PREDICTION_ROUNDS
from bot.chroma import RAGContextBuilder, collection

rag_builder = RAGContextBuilder(collection, search_internet, similarity_threshold=0.5)
logger = logging.getLogger(__name__)


async def wrap_model_translate(model, query, on_message=None, callback=None) -> str:
    """Wrap a synchronous call in an async context if necessary."""
    logger.info("in the wrapper")
    loop = asyncio.get_event_loop()
    chat = lms.Chat(
        "translate the text from the indian marathi language to english. no preamble / notes",
    )
    chat.add_user_message(query)
    result = await loop.run_in_executor(
        None,
        lambda: model.respond(
            chat,
            on_message=chat.append,
        ),
    )

    return result  # assuming this is text


async def wrap_model(model, query, on_message=None, callback=None) -> str:
    """Wrap a synchronous call in an async context if necessary."""
    logger.info("in the wrapper")
    loop = asyncio.get_event_loop()
    chat = lms.Chat(
        "You summarize the most relevant context information based on the query, bullet points are preferred. no preamble / notes",
    )
    chat.add_user_message(query)
    result = await loop.run_in_executor(
        None,
        lambda: model.respond(
            chat,
            on_message=chat.append,
        ),
    )

    return result  # assuming this is text


async def wrap_model_act(model, query, tools, on_message=None, callback=None) -> str:
    """Wrap a synchronous call in an async context if necessary."""
    logger.info("in the wrapper")
    loop = asyncio.get_event_loop()

    search_results = []

    def append_search(param):
        logger.info(f"-----------------------> Appending: {param=}")
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


async def translate(query: str, callback=None) -> str:
    """translate"""
    model = lms.llm()  # load model
    logger.info("model loaded")

    response = await wrap_model(
        model,
        query,
        # callback=callback,
    )
    content = extract_content(response)
    # logger.info(f"********************************  Content: {content}")
    # logger.info(f"********************************  Response: {response}")
    return content


async def summarize(query: str, callback) -> str:
    """summarize"""
    model = lms.llm()  # load model
    logger.info("model loaded")

    response = await wrap_model(
        model,
        query,
        # callback=callback,
    )
    content = extract_content(response)
    # logger.info(f"********************************  Content: {content}")
    # logger.info(f"********************************  Response: {response}")
    return content


async def search_with_tool(query: str, callback) -> str:
    """Searches using RAG + live search if needed."""

    def search_tool(query: str) -> List[Dict]:
        """Searches the internet for a given query"""
        logger.info("Searching the internet for: %s", query)
        return asyncio.run(search_internet(query, callback=callback))

    logger.info(f"Searching using RAG flow for: {query}")

    model = lms.llm()  # load model
    logger.info("model loaded")

    # Use RAG builder
    docs = await rag_builder.retrieve(query, callback=callback)
    context_text = rag_builder.build_context_text(docs)

    if context_text.strip():
        logger.info(f"Successfully built RAG context with {len(docs)} documents.")
        final_query = f"""Use the following knowledge to answer the query. Cite sources where relevant.

Context:
{context_text}

Query:
{query}
"""
        logger.info(f"Final query to model: {final_query}")

        # Send it to the model
        response = await wrap_model(
            model,
            final_query,
            callback=callback,
        )
        content = extract_content(response)
        logger.info(f"********************************  Content: {content}")
        logger.info(f"********************************  Response: {response}")
        return content
    else:
        logger.info(f"No good RAG matches, falling back to live search.")

        final_query = (
            query
            + " Use your 'search_tool' *only* if you cannot answer from the provided Context. Give your response in text, never JSON"
        )

    logger.info(f"Final query to model: {final_query}")
    tools = [search_tool]

    # Send it to the model
    response = await wrap_model_act(
        model,
        final_query,
        tools,
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


def extract_content(prediction_result):
    """
    Extract the content from an OpenAI PredictionResult object.

    Args:
        prediction_result: The PredictionResult object returned from OpenAI API

    Returns:
        str: The extracted content text
    """
    # If it's already an object with content attribute
    if hasattr(prediction_result, "content"):
        return prediction_result.content

    # Alternative access methods if it's a dictionary-like structure
    elif isinstance(prediction_result, dict) and "content" in prediction_result:
        return prediction_result["content"]

    # If it's a more complex structure like OpenAI's completion response
    elif hasattr(prediction_result, "choices") and len(prediction_result.choices) > 0:
        # For OpenAI completions/chat completions
        if hasattr(prediction_result.choices[0], "message"):
            return prediction_result.choices[0].message.content
        elif hasattr(prediction_result.choices[0], "text"):
            return prediction_result.choices[0].text

    # If none of the above methods work
    return "Could not extract content from the prediction result"
