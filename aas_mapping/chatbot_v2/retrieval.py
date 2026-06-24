"""Semantic field discovery (RAG) for chatbot_v2.

Bridges the user's vocabulary to the repository's real field names. Builds an in-memory
FAISS index over the distinct SubmodelElement fields actually present in the graph
(idShort + the Submodel type it lives in + its semanticId/IRDI), embedded with the
KIConnect ``qwen3-embedding-8b`` model. ``find_relevant_fields(question)`` returns the
closest field paths + semanticIds, which the agent feeds to ``aasql_query`` instead of
guessing — replacing the hardcoded field lists in the system prompt.

The corpus is small (distinct fields, not instances), built lazily once and cached.
"""

import json
from functools import lru_cache

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from config import get_aas_client, log, HYDE
from llm import embeddings, util_model

# Distinct (submodel type, field idShort, semanticId, ECLASS concept) in the graph.
# `concept` is the version-agnostic IRDI base (target_id_base) — the ECLASS concept the
# field is tagged with, used to group differently-named fields that mean the same thing.
_FIELDS_CYPHER = (
    "MATCH (sm:Submodel) "
    "MATCH (sm)-[:submodelElements|value|statements*1..]->(e:Referable) "
    "OPTIONAL MATCH (e)-[:semanticId]->(er:Reference) "
    "WITH sm.idShort AS submodel_type, e.idShort AS field, er.keys_value[0] AS semanticId, "
    "er.target_id_base AS concept "
    "WHERE field IS NOT NULL "
    "RETURN DISTINCT submodel_type, field, semanticId, concept "
    "LIMIT 5000"
)


def _corpus() -> list[Document]:
    client = get_aas_client()
    if client is None:
        return []
    rows = client.execute_clause(_FIELDS_CYPHER) or []
    docs = []
    for r in rows:
        field, smt, sem = r["field"], r.get("submodel_type"), r.get("semanticId")
        # Embed a short natural phrase so user wording matches even when idShort differs.
        text = f"{field} — field in the {smt} submodel" + (f" (semanticId {sem})" if sem else "")
        docs.append(Document(page_content=text,
                             metadata={"field": field, "submodel_type": smt,
                                       "semanticId": sem, "concept": r.get("concept")}))
    return docs


def _concept_names(concepts: list[str]) -> dict:
    """Map ECLASS concept IRDIs (id_base) → official preferred name, for the grouping."""
    client = get_aas_client()
    if client is None or not concepts:
        return {}
    rows = client.execute_clause(
        "UNWIND $bases AS b MATCH (cd:ConceptDescription {id_base: b}) "
        "WHERE cd.displayName_text IS NOT NULL "
        "RETURN b AS concept, cd.displayName_text[0] AS name",
        params={"bases": list(concepts)}) or []
    return {r["concept"]: r["name"] for r in rows}


@lru_cache(maxsize=1)
def _index():
    docs = _corpus()
    if not docs:
        return None
    log.info("field-discovery: indexing %d distinct fields", len(docs))
    return FAISS.from_documents(docs, embeddings())


_HYDE_PROMPT = (
    "You map a user's question to the NAMES of technical data-sheet properties that would "
    "hold the answer. These are Asset Administration Shell SubmodelElement idShorts — short "
    "technical identifiers like 'ManufacturerName', 'Max_medium_temperature', "
    "'Upper_range_limit_of_temperature', 'NominalFlowRate'. Given the question, output a "
    "JSON object {\"fields\": [...]} with 3-6 plausible candidate property NAMES (CamelCase "
    "or snake_case, English and German variants welcome). Do NOT answer the question; only "
    "name the properties that could contain the answer."
)


@lru_cache(maxsize=256)
def _hyde_queries(question: str) -> tuple[str, ...]:
    """Hypothetical field names for the question (HyDE). Cached; [] on any failure."""
    try:
        model = util_model().bind(response_format={"type": "json_object"})
        msg = model.invoke([{"role": "system", "content": _HYDE_PROMPT},
                            {"role": "user", "content": question}])
        names = json.loads(msg.content).get("fields", [])
        return tuple(n for n in names if isinstance(n, str) and n.strip())[:6]
    except Exception as exc:  # noqa: BLE001 — HyDE is best-effort; fall back to raw query
        log.warning("HyDE generation failed, using raw query: %s", exc)
        return ()


def find_relevant_fields(question: str, k: int = 8) -> dict:
    """Find AAS fields whose names/semantics match the user's wording.

    Pass the user's ORIGINAL question verbatim (do not reword it). With HyDE enabled the
    tool itself generates hypothetical field names from the full question and multi-query
    retrieves with those plus the raw question, so it works even when the question wording
    is far from the real idShorts. Returns the closest distinct SubmodelElement fields
    (idShort, the Submodel type they live in, and semanticId).
    """
    index = _index()
    if index is None:
        return {"error": "No Neo4j backend / no fields to index.", "fields": []}
    question = (question or "").strip()

    # Query set: the raw question + (HyDE) hypothetical field names.
    queries = [question]
    hyde = _hyde_queries(question) if HYDE else ()
    queries.extend(hyde)

    # Multi-query: keep the best (smallest L2) distance seen per field across all queries.
    best: dict[tuple, tuple[float, dict]] = {}
    for q in queries:
        for doc, score in index.similarity_search_with_score(q, k=k):
            m = doc.metadata
            key = (m["field"], m.get("semanticId"))
            if key not in best or score < best[key][0]:
                best[key] = (score, m)
    ranked = sorted(best.values(), key=lambda x: x[0])[:k]
    fields = [{"field": m["field"], "submodel_type": m.get("submodel_type"),
               "semanticId": m.get("semanticId"), "concept": m.get("concept")}
              for _, m in ranked]

    # ECLASS concept grouping: fields sharing a concept IRDI are the SAME thing (even when
    # spelled differently, e.g. Ambient_Temperature / Umgebungstemperatur) — exact, not
    # fuzzy. Group the hits by concept and label each group with the ECLASS name.
    by_concept: dict[str, list[str]] = {}
    for f in fields:
        if f["concept"]:
            by_concept.setdefault(f["concept"], [])
            if f["field"] not in by_concept[f["concept"]]:
                by_concept[f["concept"]].append(f["field"])
    names = _concept_names(list(by_concept))
    concepts = [{"concept": c, "eclass_name": names.get(c), "fields": fs}
                for c, fs in by_concept.items()]
    return {"count": len(fields), "fields": fields, "hyde_used": list(hyde),
            "concepts": concepts}
