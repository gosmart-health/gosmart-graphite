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



