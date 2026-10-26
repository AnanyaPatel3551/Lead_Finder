"""The tools the LLM sees and the one function that runs them: shared by the single agent and the researcher."""

import json

from src.tools import fetch, jobs, search

# Exactly what both architectures pass to call_llm(tools=...): same names, same descriptions
TOOL_DEFINITIONS: list[dict] = [search.TOOL_DEFINITION, fetch.TOOL_DEFINITION, jobs.TOOL_DEFINITION]


def run_tool(name: str, arguments: str | dict) -> str:
    """Run one tool call from the LLM and return its result as text for a 'tool' message.

    In: the tool name and its arguments (the JSON string from the LLM, or a dict).
    Out: text. Bad calls (unknown tool, bad JSON, missing argument) come back as an error
    message the LLM can read and correct, never as an exception that stops the run.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError as error:
            return f"error: arguments are not valid JSON ({error}). Call {name} again with valid JSON."

    try:
        if name == "web_search":
            return search.format_for_llm(search.web_search(arguments["query"]))
        if name == "fetch_page":
            return fetch.format_for_llm(fetch.fetch_page(arguments["url"]))
        if name == "find_jobs":
            result = jobs.find_jobs(arguments["company"], arguments.get("website"), arguments.get("query"))
            return jobs.format_for_llm(result)
    except KeyError as missing:
        return f"error: {name} needs the argument {missing}."
    return f"error: there is no tool called '{name}'. Available: web_search, fetch_page, find_jobs."
