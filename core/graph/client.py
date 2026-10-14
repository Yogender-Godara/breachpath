"""FalkorDB connection and query helpers. Every query takes the graph name; params are always passed separately."""
from functools import lru_cache

from falkordb import FalkorDB
from redis.exceptions import ResponseError

from core import config
from core.graph.schema import INDEX_QUERIES


@lru_cache(maxsize=1)
def get_db():
    return FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, password=config.FALKORDB_PASSWORD)


def get_graph(name):
    return get_db().select_graph(name)


def ro_query(graph_name, cypher, params=None):
    return get_graph(graph_name).ro_query(cypher, params, timeout=config.QUERY_TIMEOUT_MS).result_set


def query(graph_name, cypher, params=None):
    return get_graph(graph_name).query(cypher, params, timeout=config.QUERY_TIMEOUT_MS).result_set


def create_indexes(graph_name):
    for cypher in INDEX_QUERIES:
        try:
            query(graph_name, cypher)
        except ResponseError as e:
            if "already indexed" not in str(e).lower():
                raise


def delete_graph(graph_name):
    if graph_name in get_db().list_graphs():
        get_graph(graph_name).delete()
