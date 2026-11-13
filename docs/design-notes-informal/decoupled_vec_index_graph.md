# Decoupled Vector Index Graph Pattern Design

## Why We Need This?

In Vector Embeddings, Vectors contain a lot of information, and we need a way 
to efficiently store
and retrieve them. The decoupled vector index graph pattern is a design that allows us to do this.

Instead of storing vectors along with the target nodes, which may already have a lot of information, the decoupled vector index graph pattern separates the vectors from the target nodes. This allows for more efficient storage and retrieval of vectors, as well as easier management of the data.

Because we are using Graph databases, the decoupled vector index graph pattern 
provides a flexible and scalable solution for storing and retrieving vector 
embeddings. By separating the vectors from the target nodes, we can optimize 
the storage and retrieval of vectors, while still maintaining the relationships 
between the nodes in the graph.

In addition, vectors are highly specific to the embedding models which can 
evolve over time. By separating the vectors from the target nodes, we can 
easily update the vectors without affecting the relationships between 
the nodes in the graph.

## The Architecture

### Embedding Nodes

It should derived from the AuditableNode.

#### Properties

In addition to the values from the AuditableNode, each Embedding node should have:

- label: `:EMBEDDING`
- modelTag: System specific short string describing the model used.
- size: Size of the vector
- vector: Normalized vector representation, an array of floats.
  - Index of this vector
- targetFieldTag: Tag identifying the field in the target node that the vector is associated with.

### Relations - `:EMBEDS`

The relations which will be based on AuditableRelation. 

- aspect: Tag identifying the aspect of the target node that the vector is 
  associated with, for example `IMPRESSION`, `FINDINGS`

## Specific Vectorization of `DiagnosticReport` Nodes

One of the key interest in semantic search of the DiagnosticReport nodes the 
text of the reports is to allow natural text search of contents
of text reports.

These reports are usually much longer than the context window, and vectorizing 
the entire report can be computationally expensive. Therefore, we can vectorize 
only the relevant sections of the report and here is how we will approach this
specific issue.

The text reports are usually Base64 encoded in the FHIR `presentedForm` array
like this:

```text
"presentedForm": [ {
    "contentType": "text/plain; charset=utf-8",
    "data": "CjIwMTYtMDEtMTAKCiMgQ2hpZW..."
  } ]
```
Given this is not a PDF or other document, we will use the text/plain content 
type and decode the Base64 encoded data to get the actual text of the report.

Next we will find sections with the following headings (we need to later
consider multiple languages, for now, we will try this in English):

## Per-Resource Vector Nodes: Granular Vector Nodes Per Resource

I will be using the `Observation` as an example to describe the approach.

> ### Why Concatenating Strings of all observations is not a good idea?
> - Semantic Dilution & "Lost in the Middle": An encounter with 30 observations 
> (labs, vitals, social history, findings) dilutes specific signals. 
> A query for "elevated diastolic pressure" gets drowned out by unrelated lab panels and notes.
> - Loss of Granular Clinical Context: An Observation is not just text—it 
> contains status (e.g. preliminary, amended), interpretation (e.g. HIGH, 
> CRITICAL), 
> values with units (150 mmHg), and timestamps. Simple concatenation strips 
> these semantics.
> Update Costs: If a single new lab or observation is added or amended 
> to an Encounter, you have to re-read all observations and regenerate the 
> entire concatenated embedding.

Text to Embed: Form a structured sentence rather than raw pipe concatenation: 
"Observation: Blood Pressure. Value: 150/95 mmHg. Interpretation: High. 
Note: Patient reports morning dizziness."

```text
(:Patient)-[:HAS_ENCOUNTER]->(:Encounter)-[:HAS_OBSERVATION]->(:Observation)
                                                                   ▲
                                                                   │ :INDEXES
                                                    (:ObservationVectorIndex)
```

#### Query Example to Return Related Encounters given Observation Search

```cyper
CALL db.index.vector.queryNodes('observation_vector_index', 10, $queryVector)
     YIELD node AS vecNode, score
     MATCH (vecNode)<-[:INDEXES]-(obs:Observation)<-[:HAS_OBSERVATION]-(enc:Encounter)<-[:HAS_ENCOUNTER]-(p:Patient)
     RETURN p.id, enc.id, obs.codeText, obs.valueString, score
```

- We may need to de-dupe results by patient and encounter.
