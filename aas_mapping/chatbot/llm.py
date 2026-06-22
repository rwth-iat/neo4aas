"""LLM helpers: a single chat call + a tolerant JSON extractor."""

import json
import re
import time

from config import llm_client


def llm_call(model: str, messages: list, max_tokens: int = 1024, retries: int = 2) -> tuple[str, int]:
    """Single LLM call. Returns (content, elapsed_ms).

    The KIConnect provider intermittently returns ``finish_reason=stop`` with a null
    content for the same request; retry a few times until we get non-empty content.
    """
    t0 = time.perf_counter()
    content = ""
    for _ in range(retries + 1):
        response = llm_client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,  # deterministic tool decisions and AASQL generation
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        if content.strip():
            break
    return content, int((time.perf_counter() - t0) * 1000)


def extract_json(text: str) -> dict:
    """Balanced-brace JSON extractor — tolerant to prose, code fences, multiple blocks."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : j + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            j += 1
        i += 1
    raise ValueError(f"No JSON object found in response: {text[:200]}")
