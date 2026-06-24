"""60-question evaluation harness for chatbot_v2 (harder + more diverse than the 30-set).

Run (server up on :8091):
    uv run --with requests python aas_mapping/chatbot_v2/test_questions60.py
Writes /tmp/v2_eval60.json.
"""

import json
import sys
import time

import requests

URL = "http://localhost:8091/chat"

QUESTIONS = [
    # --- basics / regression ---
    ("count", "How many asset administration shells are in the repository?"),
    ("count", "How many submodels of each type are there?"),
    ("count", "How many devices are made by Endress+Hauser?"),
    ("count", "How many distinct manufacturers are there?"),
    ("count", "How many devices come from Germany?"),
    ("lookup", "What is the manufacturer of asset F22?"),
    ("lookup", "Which submodels does asset N13 have?"),
    ("search", "Which devices are made by GRUNDFOS?"),
    ("search", "List the devices that come from the USA."),
    ("structure", "What submodel types exist in the repository?"),
    # --- aggregation / superlatives (messy values) ---
    ("agg", "Which manufacturer has the most devices?"),
    ("agg", "What is the maximum medium temperature among all sensors?"),
    ("agg", "Which device has the highest maximum flow rate?"),
    ("agg", "What is the average net weight of the devices?"),
    ("agg", "What is the highest nominal voltage in the repository?"),
    ("agg", "Which device is the heaviest?"),
    ("agg", "What is the lowest minimum process pressure recorded?"),
    ("agg", "List the top 3 manufacturers by device count."),
    ("agg", "How many devices does each country of origin have?"),
    ("agg", "What is the average accuracy of the measuring instruments?"),
    # --- numeric filters / ranges ---
    ("filter", "Which devices have a maximum flow rate above 1000?"),
    ("filter", "Find devices with a nominal pressure of at least 40 bar."),
    ("filter", "Which sensors can measure above 100 degrees Celsius?"),
    ("filter", "List devices lighter than 3 kg."),
    ("filter", "Which devices have an IP rating of IP67 or higher?"),
    # --- property value lookups ---
    ("value", "What is the degree of protection of device L34?"),
    ("value", "What is the supply pressure of the pneumatic actuators?"),
    ("value", "List the accuracy values of all measuring instruments."),
    ("value", "What is the nominal voltage of asset N13?"),
    ("value", "What material is used for asset B1?"),
    # --- cross-submodel / joins ---
    ("cross", "What product designations do the Endress+Hauser devices have?"),
    ("cross", "Who is the manufacturer of the device with the highest flow rate?"),
    ("cross", "Which country do the GRUNDFOS devices come from?"),
    ("cross", "For the heaviest device, what is its manufacturer and country?"),
    # --- negation / absence ---
    ("absence", "Which assets do not have a TechnicalData submodel?"),
    ("absence", "Are there any devices without a manufacturer name?"),
    ("absence", "Which devices have no country of origin specified?"),
    # --- grouping by asset type ---
    ("group", "How many Y-type assets are there?"),
    ("group", "How many assets start with the letter N?"),
    ("group", "List all the pipe assets."),
    # --- hierarchy / structure ---
    ("hierarchy", "What is the bill of materials of pump N13?"),
    ("hierarchy", "Which components belong to asset N18?"),
    ("structure", "What fields does the Nameplate submodel contain?"),
    ("structure", "How many distinct properties does the TechnicalData submodel have?"),
    # --- connectivity (AssetInterfaces) ---
    ("connect", "Which devices expose an OPC UA interface?"),
    ("connect", "What communication interfaces are described in the repository?"),
    # --- semantic / ECLASS ---
    ("semantic", "Find elements with semanticId 0173-1#02-AAO677#004."),
    ("semantic", "What does the ECLASS property 0112/2///61987#ABA565 stand for?"),
    ("semantic", "Which submodels have a semanticId from the IDTA nameplate template?"),
    # --- comparison ---
    ("compare", "Compare the manufacturers of assets B1 and B2."),
    ("compare", "Is device N13 heavier than device N18?"),
    ("compare", "Which has a higher accuracy, asset F22 or asset L34?"),
    # --- German ---
    ("german", "Wie viele Geräte kommen aus Deutschland?"),
    ("german", "Welcher Hersteller hat die meisten Geräte?"),
    ("german", "Was ist das maximale Gewicht aller Geräte?"),
    ("german", "Zeige die Geräte von Siemens."),
    # --- fuzzy vocab ---
    ("fuzzy", "Which devices can withstand high pressure?"),
    ("fuzzy", "What is the air consumption of the pneumatic actuators?"),
    ("fuzzy", "How accurate are the level sensors?"),
    # --- meta / validation ---
    ("meta", "What can you do?"),
    ("validation", "Are there any AAS specification constraint violations in the data?"),
]


def ask(q: str) -> dict:
    t0 = time.time()
    try:
        r = requests.post(URL, json={"message": q}, timeout=220)
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
        out.append({"n": i, "cat": cat, "q": q, "answer": ans, "tools": tools,
                    "n_tools": len(tools), "error": res.get("error"),
                    "thread_id": res.get("thread_id"), "ms": res.get("ms")})
        flag = "ERR" if res.get("error") else ("EMPTY" if not ans else "ok")
        print(f"{i:2} [{cat:9}] {flag:5} {res.get('ms')}ms n={len(tools)} {tools}")
        print(f"     Q: {q}")
        print(f"     A: {ans[:160].replace(chr(10),' ')}")
        sys.stdout.flush()
    with open("/tmp/v2_eval60.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\nwrote /tmp/v2_eval60.json")


if __name__ == "__main__":
    main()
