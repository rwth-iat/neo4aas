"""Core AAS <-> Neo4j mapping: the client, serialization paths and AASQL compiler.

Depends only on the neo4j driver — no basyx, and nothing here imports an app
package. See tests/test_layering.py, which enforces both.
"""
