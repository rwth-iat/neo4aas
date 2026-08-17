"""Ad-hoc evaluation harness: fire a set of diverse questions at the running chatbot_v2
`/chat` endpoint, capture answer + tool trace + error, and dump JSON for analysis.

Run (server must be up on :8091):
    uv run --with requests python scripts/eval/eval_questions.py
"""

import json
import sys
import time

import requests

URL = "http://localhost:8091/chat"

# (category, question). Ground-truth notes are in the analysis, not sent to the bot.
QUESTIONS = [
    ("count", "How many asset administration shells are in the repository?"),
    ("count", "How many submodels of each type are there?"),
    ("count", "How many devices are made by Endress+Hauser?"),
    ("count", "How many distinct manufacturers are in the repository?"),
    ("count", "How many devices come from Germany?"),
    ("lookup", "Show the details of asset B1."),
    ("lookup", "What is the manufacturer of asset F22?"),
    ("lookup", "Which submodels does asset N13 have?"),
    ("search", "Find all radar level sensors."),
    ("search", "Which devices are made by GRUNDFOS?"),
    ("search", "List the devices that come from the USA."),
    ("search", "Find the pumps in the repository."),
    ("value", "What is the maximum medium temperature among all the sensors?"),
    ("value", "Which device has the highest ambient temperature rating?"),
    ("value", "What is the degree of protection (IP rating) of device L34?"),
    ("value", "List the accuracy values of the measuring instruments."),
    ("compare", "Compare the manufacturers of assets B1 and B2."),
    ("compare", "Which manufacturer has the most devices?"),
    ("cross", "What product designations do the Endress+Hauser devices have?"),
    ("structure", "What fields does the Nameplate submodel contain?"),
    ("structure", "What submodel types exist in the repository?"),
    ("semantic", "Find elements with semanticId 0173-1#02-AAO677#004."),
    ("semantic", "What does the ECLASS property 0112/2///61987#ABA565 stand for?"),
    ("german", "Wie viele Geräte kommen aus Deutschland?"),
    ("german", "Welcher Hersteller hat die meisten Geräte?"),
    ("fuzzy", "Which devices can withstand high pressure?"),
    ("fuzzy", "What is the air consumption of the pneumatic actuators?"),
    ("hierarchy", "What is the bill of materials of pump N13?"),
    ("hierarchy", "Which components belong to asset N18?"),
    ("validation", "Are there any AAS specification constraint violations in the data?"),
]


def ask(q: str) -> dict:
    t0 = time.time()
    try:
        r = requests.post(URL, json={"message": q}, timeout=200)
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"request failed: {exc}", "ms": int((time.time() - t0) * 1000)}
    data["ms"] = int((time.time() - t0) * 1000)
    return data


def main():
    out = []
    for i, (cat, q) in enumerate(QUESTIONS, 1):
        res = ask(q)
        tools = [t.get("action") for t in res.get("tool_trace", [])]
        ans = (res.get("answer") or "").strip()
        rec = {"n": i, "cat": cat, "q": q, "answer": ans,
               "tools": tools, "n_tools": len(tools),
               "error": res.get("error"), "thread_id": res.get("thread_id"),
               "ms": res.get("ms")}
        out.append(rec)
        flag = "ERR" if res.get("error") else ("EMPTY" if not ans else "ok")
        print(f"{i:2} [{cat:9}] {flag:5} {res.get('ms')}ms tools={tools}")
        print(f"     Q: {q}")
        print(f"     A: {ans[:200].replace(chr(10),' ')}")
        sys.stdout.flush()
    with open("/tmp/v2_eval.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\nwrote /tmp/v2_eval.json")


if __name__ == "__main__":
    main()
