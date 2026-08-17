"""Live-value simulator for OperationalData submodels (aas-demonstrator).

Every UPDATE_INTERVAL seconds, run one Cypher random-walk over every
OperationalData Property in the pumpwerk Neo4j store so the demo looks like a
live plant. The Repository server + chatbot read live from Neo4j, so writes
appear immediately on REST GET / AAS web UI / chatbot (no re-import).
"""

import logging
import os
import time

from neo4j import GraphDatabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("operational_simulator")

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "12345678")
UPDATE_INTERVAL = float(os.environ.get("UPDATE_INTERVAL", "5"))

# One round-trip; rand() is evaluated per row server-side. CurrentValue does a
# clamped random walk in a per-asset range (prefix of the asset code in sm.id);
# discrete states (Started/Open/Closed/nonEmpty) hold and flip ~8% of ticks.
UPDATE_CYPHER = """
MATCH (sm:Submodel {idShort:'OperationalData'})-[:submodelElements]->(e:Property)
WITH e, left(split(sm.id,'/')[-2], 1) AS p
WITH e,
  CASE p WHEN 'F' THEN [0.0,150.0,8.0,80.0]
         WHEN 'L' THEN [0.0,100.0,5.0,50.0]
         WHEN 'Q' THEN [200.0,800.0,30.0,500.0]
         WHEN 'T' THEN [10.0,40.0,1.5,25.0]
         WHEN 'P' THEN [0.0,10.0,0.5,5.0]
         ELSE        [0.0,100.0,5.0,50.0] END AS rng
WITH e, rng, (coalesce(toFloat(e.value), rng[3]) + (rand()-0.5)*2*rng[2]) AS raw
SET e.value = CASE
  // AAS Property.value must be a STRING for basyx read-back; toString() keeps the
  // value column homogeneous (the AASQL compiler/tools cast back with toFloat).
  WHEN e.idShort = 'CurrentValue'
    THEN toString(round((CASE WHEN raw < rng[0] THEN rng[0]
                              WHEN raw > rng[1] THEN rng[1] ELSE raw END) * 100) / 100.0)
  ELSE
    toString(CASE WHEN rand() < 0.08 THEN 1 - coalesce(toInteger(e.value), 0)
                  ELSE coalesce(toInteger(e.value), 0) END)
END
RETURN count(e) AS updated
"""


def main():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    log.info("connected to %s, ticking every %ss", NEO4J_URI, UPDATE_INTERVAL)
    while True:
        try:
            with driver.session() as session:
                updated = session.run(UPDATE_CYPHER).single()["updated"]
            log.info("tick: updated %d OperationalData properties", updated)
        except Exception as exc:  # keep the loop alive across transient DB blips
            log.warning("tick failed: %s", exc)
        time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    main()
