import os
from falkordb import FalkorDB

db = FalkorDB(
    host=os.environ["FALKORDB_HOST"],
    port=int(os.environ["FALKORDB_PORT"]),
    password=os.environ["FALKORDB_PASSWORD"],
)
g = db.select_graph("ping")
result = g.query("RETURN 1 AS ok")
print(result.header[0][1], "=", result.result_set[0][0])
g.delete()
