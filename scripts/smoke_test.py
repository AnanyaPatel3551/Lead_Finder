"""One tiny LLM call returning JSON validated by Pydantic; prints the result and usage."""

import sys

from pydantic import BaseModel

from src import config
from src.llm import call_llm


class SmokeReply(BaseModel):
    """The tiny object we ask for: easy to check by eye, and uses a str, an int and a bool."""

    greeting: str
    two_plus_two: int
    json_is_easy: bool


def main() -> int:
    """Run one structured call. Out: exit code 0 if the reply validated, 1 if not."""
    print(f"Model: {config.LLM_MODEL} at {config.LLM_BASE_URL}")
    messages = [
        {"role": "system", "content": "You reply only with JSON matching the given schema."},
        {"role": "user", "content": "Say hello, add 2 + 2, and say whether JSON is easy."},
    ]
    response = call_llm(messages, schema=SmokeReply)

    print(f"Usage: {response.usage}")
    if response.validation_error:
        # Show the raw text too: when validation fails, the raw reply shows why
        print(f"FAILED validation:\n{response.validation_error}\nRaw reply: {response.message.content}")
        return 1
    print(f"Parsed: {response.parsed!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
