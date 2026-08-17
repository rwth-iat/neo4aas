"""Evaluation harness: fire 30 questions at the running chatbot_v2 ``/chat`` endpoint for the
**Lieferanten** (supplier) repo, capture answer + tool trace + latency, then replay the same 30
against **Pumpwerk** to compare tool behaviour, correctness and speed.

The 30 questions are grounded in the live Lieferanten graph (8966 AAS; manufacturers SICK AG,
Bürkert, Phoenix Contact, R. STAHL, ABB; submodel types HandoverDocumentation / TechnicalData /
Nameplate / ContactInformations / DigitalNameplate / CarbonFootprint; countries via NationalCode
DE/NO/CN/FI/…). Ground-truth notes live in GT, used only for the printed scorecard, never sent.

Run (server must be up on :8091, both repos reachable):
    uv run --with requests python aas_mapping/chatbot_v2/test_questions_lieferanten.py
"""

import json
import sys
import time

import requests

URL = "http://localhost:8091/chat"

# (category, question, ground_truth_hint). The hint is for the human reading the scorecard.
QUESTIONS = [
    ("count",     "How many asset administration shells are in the repository?", "8966"),
    ("count",     "How many submodels of each type are there?", "HandoverDoc 6964, TechData 6950, Nameplate 4966, ContactInfo 2966, DigitalNameplate 2000, CarbonFootprint 566"),
    ("aggregate", "How many devices are made by SICK AG?", "~4000"),
    ("aggregate", "How many distinct manufacturers are in the repository?", "handful (SICK, Bürkert, Phoenix Contact, R. STAHL, ABB) — names vary"),
    ("aggregate", "How many devices come from Germany?", "NationalCode DE ~8899"),
    ("compare",   "Which manufacturer has the most devices?", "SICK AG (4000) ~ Bürkert (3998)"),
    ("aggregate", "How many devices come from each country?", "count_by_value on NationalCode"),
    ("lookup",    "Show the details of asset ABB_Actuators_310320262157.", "exists; ABB"),
    ("lookup",    "What is the manufacturer of asset ABB_Positioners_310320262157?", "ABB"),
    ("lookup",    "Which submodels does asset ABB_Rotary_Actuators_310320262157 have?", "Nameplate/TechnicalData/etc"),
    ("search",    "Which devices are made by Bürkert?", "~3998 — should not enumerate all, list/count"),
    ("search",    "Find all devices manufactured by Phoenix Contact.", "~3994"),
    ("search",    "List devices that come from Norway.", "NationalCode NO ~5932"),
    ("search",    "Find devices that have a CarbonFootprint submodel.", "566 devices"),
    ("structure", "What submodel types exist in the repository?", "6 types"),
    ("structure", "What fields does the Nameplate submodel contain?", "ManufacturerName, etc"),
    ("structure", "What fields does the CarbonFootprint submodel contain?", "PcfCO2eq, Country, PcfCalculationMethod, …"),
    ("semantic",  "What does the ECLASS property AAO677 stand for?", "Manufacturer name (0173-1#02-AAO677)"),
    ("semantic",  "Find elements with semanticId 0173-1#02-AAO677#004.", "Manufacturer_name elements"),
    ("value",     "What is the highest product carbon footprint (PcfCO2eq) value?", "aggregate max over PcfCO2eq"),
    ("value",     "List the carbon footprint (PcfCO2eq) values of the devices.", "property_values PcfCO2eq"),
    ("value",     "What is the GTIN or EAN code of asset ABB_Actuators_310320262157?", "AAO663 GTIN__EAN__code"),
    ("compare",   "Compare the manufacturers of assets ABB_Actuators_310320262157 and ABB_Positioners_310320262157.", "both ABB"),
    ("missing",   "Which devices lack a CarbonFootprint submodel?", "assets_missing — most lack it"),
    ("missing",   "Which devices have no TechnicalData submodel?", "assets_missing TechnicalData"),
    ("german",    "Wie viele Geräte stammen von SICK AG?", "~4000"),
    ("german",    "Welcher Hersteller hat die meisten Geräte?", "SICK AG"),
    ("fuzzy",     "Which devices report their CO2 footprint?", "RAG -> PcfCO2eq / CarbonFootprint"),
    ("hierarchy", "What is the bill of materials of asset ABB_Actuators_310320262157?", "likely none (no HierarchicalStructures) — graceful empty"),
    ("validation","Are there any AAS specification constraint violations in the data?", "validate_constraints"),
]


def ask(q: str, repo: str) -> dict:
    t0 = time.time()
    try:
        r = requests.post(URL, json={"message": q, "repo": repo}, timeout=240)
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"request failed: {exc}", "ms": int((time.time() - t0) * 1000)}
    data["ms"] = int((time.time() - t0) * 1000)
    return data


def run_repo(repo: str) -> list[dict]:
    print(f"\n{'='*78}\n  REPO = {repo}\n{'='*78}")
    out = []
    for i, (cat, q, gt) in enumerate(QUESTIONS, 1):
        res = ask(q, repo)
        tools = [t.get("action") for t in res.get("tool_trace", [])]
        ans = (res.get("answer") or "").strip()
        rec = {"n": i, "cat": cat, "q": q, "gt": gt, "answer": ans,
               "tools": tools, "n_tools": len(tools),
               "error": res.get("error"), "ms": res.get("ms")}
        out.append(rec)
        flag = "ERR" if res.get("error") else ("EMPTY" if not ans else "ok")
        print(f"{i:2} [{cat:9}] {flag:5} {res.get('ms'):>6}ms tools={tools}")
        print(f"     Q: {q}")
        print(f"     GT: {gt}")
        print(f"     A: {ans[:240].replace(chr(10),' ')}")
        sys.stdout.flush()
    return out


def summary(tag: str, recs: list[dict]) -> dict:
    ok = sum(1 for r in recs if not r["error"] and r["answer"])
    err = sum(1 for r in recs if r["error"])
    empty = sum(1 for r in recs if not r["error"] and not r["answer"])
    times = [r["ms"] for r in recs if r.get("ms")]
    times_sorted = sorted(times)
    med = times_sorted[len(times_sorted) // 2] if times_sorted else 0
    return {"tag": tag, "ok": ok, "err": err, "empty": empty,
            "total_ms": sum(times), "median_ms": med,
            "max_ms": max(times) if times else 0}


def main():
    lief = run_repo("lieferanten")
    pump = run_repo("pumpwerk")
    with open("/tmp/v2_eval_lieferanten.json", "w", encoding="utf-8") as fh:
        json.dump(lief, fh, ensure_ascii=False, indent=2)
    with open("/tmp/v2_eval_pumpwerk.json", "w", encoding="utf-8") as fh:
        json.dump(pump, fh, ensure_ascii=False, indent=2)

    sl, sp = summary("lieferanten", lief), summary("pumpwerk", pump)
    print(f"\n{'='*78}\n  COMPARISON (30 questions each)\n{'='*78}")
    hdr = f"{'metric':<14}{'lieferanten':>16}{'pumpwerk':>16}"
    print(hdr)
    for k, label in [("ok", "answered"), ("err", "errors"), ("empty", "empty"),
                     ("median_ms", "median ms"), ("max_ms", "max ms"), ("total_ms", "total ms")]:
        print(f"{label:<14}{sl[k]:>16}{sp[k]:>16}")
    print("\nper-question latency (ms)  lief / pump")
    for l, p in zip(lief, pump):
        print(f"{l['n']:2} [{l['cat']:9}] {l['ms']:>7} / {p['ms']:>7}   {l['q'][:48]}")
    print("\nwrote /tmp/v2_eval_lieferanten.json and /tmp/v2_eval_pumpwerk.json")


if __name__ == "__main__":
    main()
