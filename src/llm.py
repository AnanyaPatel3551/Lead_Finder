"""The only way to call the LLM: sends messages, waits between calls, retries on 429, counts usage."""

import json
import time
from dataclasses import dataclass

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from src import config


class RequestTooLargeError(Exception):
    """Raised before sending when a request could never fit in one minute's token budget."""


@dataclass
class Usage:
    """Token and request counts for one call, or summed over a whole run."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0  # we saw no cache hits on Groq, but record it in case that changes

    def add(self, other: "Usage") -> None:
        """Add another Usage's counts into this one (used to total a company or a run)."""
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens


def trim_old_tool_results(messages: list[dict]) -> list[dict]:
    """Return a copy of messages where every tool result except the newest is cut short.

    In: the full chat history. Out: a new list; the caller's list is never changed,
    so logs and the agent still keep the full text.
    """
    tool_positions = [i for i, message in enumerate(messages) if message.get("role") == "tool"]
    if len(tool_positions) <= 1:
        return list(messages)

    newest = tool_positions[-1]
    limit = config.TRIMMED_TOOL_RESULT_CHARS
    trimmed: list[dict] = []
    for i, message in enumerate(messages):
        content = message.get("content") or ""
        if message.get("role") == "tool" and i != newest and len(content) > limit:
            # The marker tells the model text is missing, so it never reads a cut as "not on the page"
            message = {**message, "content": content[:limit] + " [trimmed]"}
        trimmed.append(message)
    return trimmed


def estimate_tokens(messages: list[dict], tools: list[dict] | None = None,
                    schema: dict | None = None) -> int:
    """Roughly estimate a request's size in tokens: characters sent / CHARS_PER_TOKEN + output reserve.

    Counting the JSON text (keys, brackets) slightly overestimates, which is the safe direction.
    """
    characters = len(json.dumps(messages, default=str))
    if tools:
        characters += len(json.dumps(tools))
    if schema:
        characters += len(json.dumps(schema))
    return characters // config.CHARS_PER_TOKEN + config.MAX_OUTPUT_TOKENS


def check_request_size(estimated_tokens: int) -> None:
    """Raise RequestTooLargeError if one request could not fit in the per-minute token limit."""
    if estimated_tokens > config.MAX_TOKENS_PER_REQUEST:
        raise RequestTooLargeError(
            f"Request estimated at {estimated_tokens} tokens, over the {config.MAX_TOKENS_PER_REQUEST}"
            f"-token cap ({config.TPM_LIMIT} TPM x {config.TOKEN_SAFETY_MARGIN} margin). "
            "Waiting or retrying will not help: shorten the history or the tool results."
        )


@dataclass
class LLMResponse:
    """What call_llm returns: the raw reply, the validated object (if a schema was given), and usage."""

    message: object                   # the assistant message: .content (text) and .tool_calls
    parsed: BaseModel | None          # filled only when a schema was given and the reply matched it
    validation_error: str | None      # why the reply didn't match the schema, for the caller's retry
    finish_reason: str | None         # "stop" = finished; "length" = cut off at MAX_OUTPUT_TOKENS
    usage: Usage


_client: OpenAI | None = None
_last_call_time: float = 0.0


def _get_client() -> OpenAI:
    """Create the OpenAI-compatible client once, failing clearly if .env is incomplete."""
    global _client
    if _client is None:
        if not config.LLM_MODEL or not config.LLM_API_KEY:
            raise RuntimeError("LLM_MODEL or GROQ_API_KEY is empty: fill them in .env (see .env.example).")
        # max_retries=0: otherwise the SDK silently retries 429s itself, hidden from our counts and logs
        _client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL,
                         max_retries=0, timeout=config.LLM_TIMEOUT_SECONDS)
    return _client


def _wait_for_pace() -> None:
    """Sleep so that consecutive LLM calls start at least SECONDS_BETWEEN_CALLS apart."""
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < config.SECONDS_BETWEEN_CALLS:
        time.sleep(config.SECONDS_BETWEEN_CALLS - elapsed)
    _last_call_time = time.monotonic()


def _retry_wait_seconds(error: openai.RateLimitError, attempt: int) -> float:
    """Seconds to wait after a 429: Groq's retry-after header if given, else 15, 30, 45 s."""
    retry_after = error.response.headers.get("retry-after")
    if retry_after is not None:
        return float(retry_after)
    return 15.0 * attempt


def _build_response(response: object, schema: type[BaseModel] | None) -> LLMResponse:
    """Turn the raw API response into an LLMResponse, validating against schema if one was given."""
    message = response.choices[0].message
    details = response.usage.prompt_tokens_details
    usage = Usage(requests=1,
                  input_tokens=response.usage.prompt_tokens,
                  output_tokens=response.usage.completion_tokens,
                  cached_tokens=(details.cached_tokens or 0) if details else 0)
    parsed = None
    validation_error = None
    if schema is not None:
        try:
            parsed = schema.model_validate_json(message.content or "")
        except ValidationError as error:
            validation_error = str(error)  # returned, not raised, so this call's tokens are still counted
    return LLMResponse(message, parsed, validation_error, response.choices[0].finish_reason, usage)


def call_llm(messages: list[dict], tools: list[dict] | None = None,
             schema: type[BaseModel] | None = None,
             reasoning_effort: str = config.REASONING_EFFORT,
             temperature: float | None = None) -> LLMResponse:
    """Send one chat request: trims history, checks size, paces, retries on 429, counts usage.

    In: messages as dicts; optionally tools (for a tool loop) OR a Pydantic schema (for JSON output).
    Out: LLMResponse. Raises RequestTooLargeError, or the last 429 if retries run out.
    """
    if tools and schema:
        raise ValueError("Groq can't combine tools and a JSON schema in one request; pass only one.")

    to_send = trim_old_tool_results(messages)
    json_schema = None
    if schema is not None:
        # Builds a strict schema (all fields required, no extra keys), the form Groq's strict mode needs
        json_schema = openai.pydantic_function_tool(schema)["function"]["parameters"]
    check_request_size(estimate_tokens(to_send, tools, json_schema))

    request: dict = {"model": config.LLM_MODEL, "messages": to_send,
                     "max_completion_tokens": config.MAX_OUTPUT_TOKENS,
                     "reasoning_effort": reasoning_effort}
    if temperature is not None:
        # None keeps the provider's default; only callers that need repeatable answers set it
        request["temperature"] = temperature
    if tools:
        request["tools"] = tools
    if json_schema is not None:
        request["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": json_schema}}

    for attempt in range(1, config.MAX_RETRIES + 2):  # 1 first try + MAX_RETRIES retries
        _wait_for_pace()
        try:
            response = _get_client().chat.completions.create(**request)
            return _build_response(response, schema)
        except openai.RateLimitError as error:
            wait = _retry_wait_seconds(error, attempt)
            if attempt > config.MAX_RETRIES or wait > config.MAX_RETRY_WAIT_SECONDS:
                raise  # out of retries, or the daily limit is used up: Groq's message says which
            time.sleep(wait)
        except openai.BadRequestError as error:
            if error.code not in ("json_validate_failed", "tool_use_failed"):
                raise  # any other 400 is a bug in our request, not a bad reply
            # Groq rejects some bad replies instead of returning them: broken JSON in strict mode
            # (often cut off at the output limit), or a tool call when no tools were offered.
            # Treat them as failed replies so the caller can retry, not crash.
            # Groq sends no token counts with these errors, so only the request is counted.
            return LLMResponse(None, None, f"Groq rejected the reply ({error.code}): {error.message}",
                               error.code, Usage(requests=1))
    raise RuntimeError("unreachable: the loop always returns or raises")


# First-attempt failures inside call_llm_validated. llm.py has no run log, so they wait here and
# main.py writes them to log.jsonl after each company, the same way for both architectures.
_first_attempt_errors: list[dict] = []


def take_first_attempt_errors() -> list[dict]:
    """Return the first-attempt validation errors recorded since the last call, and clear them."""
    errors = list(_first_attempt_errors)
    _first_attempt_errors.clear()
    return errors


def call_llm_validated(messages: list[dict], schema: type[BaseModel],
                       reasoning_effort: str = config.REASONING_EFFORT,
                       temperature: float | None = None) -> LLMResponse:
    """Ask for JSON matching schema; if the reply fails validation, retry ONCE showing the model its error.

    In: the conversation and a Pydantic schema. The caller's list is never changed.
    Out: the last LLMResponse, with usage summed over both attempts. If validation_error is
    still set, both attempts failed: the caller should log it and skip, never crash.
    """
    first = call_llm(messages, schema=schema, reasoning_effort=reasoning_effort, temperature=temperature)
    if first.validation_error is None:
        return first

    # The model sees its own bad reply and exactly what was wrong with it
    error_text = first.validation_error[:1500]  # Pydantic repeats the input; keep the retry small
    # Recorded only, never acted on: says why the retry happened (schema name = which role)
    _first_attempt_errors.append({"schema": schema.__name__, "finish_reason": first.finish_reason,
                                  "error": error_text, "usage": vars(first.usage)})
    if first.finish_reason == "tool_use_failed":
        # The model tried to call a tool, but this call offers none
        correction = ("You cannot call tools now: research is over. Reply with the final JSON only, "
                      "using what you already found; anything still missing is 'unknown'.")
    elif first.finish_reason in ("length", "json_validate_failed"):
        # Cut off at the output limit: the same reply would be cut off again, so ask for less
        correction = ("Your reply was cut off or was not valid JSON. Reply again with corrected JSON only, "
                      "and keep every quote and why_it_fits short.")
    else:
        correction = ("Your reply did not match the required format:\n"
                      f"{error_text}\nReply again with corrected JSON only.")
    # message is None when Groq rejected the reply outright, so there is no text to show back
    bad_reply = (first.message.content if first.message else None) or "(empty reply)"
    retry_messages = messages + [
        {"role": "assistant", "content": bad_reply},
        {"role": "user", "content": correction},
    ]
    second = call_llm(retry_messages, schema=schema, reasoning_effort=reasoning_effort, temperature=temperature)
    second.usage.add(first.usage)  # both attempts cost tokens, so both are counted
    return second
