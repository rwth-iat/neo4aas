"""chatbot_v2 — LangGraph agent over the AAS repository (Flask + SSE UI).

Deliberately empty of imports: pulling in ``app`` here would drag flask, langgraph
and langchain into every ``import`` of the package, including the ones that only
want a helper. Import the pieces directly (``from .tools import build_tools``), or
run the server with ``python -m neo4aas.chatbot``.
"""
