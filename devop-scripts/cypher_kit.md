# Cypher Kit

This is a collection of Cypher queries that can be used to interact with a Neo4j database.

# Cleaning Up

## Deleting Specific Nodes

Using `Patient` as an example node label.

> NOTE: This query will delete all nodes of type Patient. Use with caution.
```cypher
 MATCH (n: Patient)
DETACH DELETE n
```

## Deleting Node to the Arrow Tip of Relationship

Using :NOT_BELONGS_TO relation out of Group label nodes.

> SUPER DANGER: This query will delete all nodes and relationships connected to a specific node. Use with **extreme** caution.
```cypher
MATCH (n: Group)-[r: NOT_BELONGS_TO]-()
DETACH DELETE n, r
```

## Deleting EVERYTHING

> SUPER DANGER: This query will delete all nodes and relationships in the 
> database. Use with **extreme** caution.
```cypher
 MATCH (n)
DETACH DELETE n
```