import hashlib
import json
import logging
import time
from collections import abc
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def rm_quotes(s: str):
    return s.replace("'", "")


def add_quotes(s: str):
    return f"'{s}'"


def is_iterable(obj):
    return isinstance(obj, abc.Iterable) and not isinstance(obj, (str, bytes, bytearray))


@dataclass
class UploadStats:
    overall_start_time: float
    total_files: int = 0
    total_batches: int = 0
    batch_size: int = 0
    total_nodes_created: int = 0
    total_relationships_created: int = 0
    total_time: float = 0.0
    total_processing_time: float = 0.0
    total_node_creation_time: float = 0.0
    total_relationship_creation_time: float = 0.0

    def __init__(self):
        super().__init__()
        self.overall_start_time = time.time()

    def finish(self):
        self.total_time = time.time() - self.overall_start_time
        logger.info(f"Total processing time: {self.total_time:.2f} seconds")
        logger.info(f"Total nodes created: {self.total_nodes_created}")
        logger.info(f"Total relationships created: {self.total_relationships_created}")
        logger.info(f"Total batches processed: {self.total_batches}")
        logger.info(f"Total files processed: {self.total_files}")
        logger.info(f"Total processing time: {self.total_processing_time:.2f} seconds")
        logger.info(f"Total node creation time: {self.total_node_creation_time:.2f} seconds")
        logger.info(f"Total relationship creation time: {self.total_relationship_creation_time:.2f} seconds")


NEO4J_INTERNAL_NODE_KEYS = frozenset({"uid", "hash", "target_id"})


def hash_dict_obj(obj: dict) -> str:
    # Sort keys to ensure deterministic hash
    json_string = json.dumps(obj, sort_keys=True)
    return hashlib.sha256(json_string.encode()).hexdigest()
