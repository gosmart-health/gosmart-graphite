# Cypher Query Guide

## Introduction

This document provides a comprehensive guide to writing Cypher queries for interacting with a graph database. It covers various aspects of Cypher syntax, query structure, and best practices for efficient and effective querying.

This is not a Cypher documentation. We assume you already know how to read the 
Cypher query language and have a basic understanding of graph databases.

## Core Data Entity Schema

Most of the entities are subclasses of the "Auditable" entity and share the 
following common properties.

- ActedAt:
- Acted By:
- Action
- CreatedAt:

## Soft Deletion Semantics - The `NOT_` Relationship

Related nodes can indicate soft deletion by having the relationship label 
with the name starting with `NOT_` of the original relationship.

Generally once a Node is created it is considered immutable, and changes are 
implemented by cloning the node, soft-deleting the old node etc.

For example if there is `:HAS_STUDY` relationship, then the soft deleted 
version of it will be `:NOT_HAS_STUDY`.

> **IMPORTANT**
> - When a soft deletion is performed the existing Node or Relationship is not 
> deleted from the graph. Instead, a new relationship with the name starting 
> with `NOT_` and the original node is cloned.
> - Multiple deleted nodes or relationships may be found in the graph, 
> preserving the entire history of deletion (or updates)

## FHIR Nodes

FHIR nodes are based on `FHIRDomainResourceNode(AuditableNode)` and every FHIR 
node has the following properties, in addition to the properties inherited from 
`AuditableNode`.

- **label**: The label of the node is the name of the FHIR resource, for 
  example: `Encounter`, `Patient`, `Observation`, etc.
- **uid**: System assigned UID at the time of node creation in the form of 
  `urn:uuid: usually including a canonical reference to the FHIR server.
- **resource_id**: The FHIR specific resource ID. For example, if this is 
  `12345` and the node label is `Patient`, then the resource is `Patient/12345`.
- **status**: Status of the resource copied from the FHIR object.
- **resource**: The Raw JSON of the resource received.
- **resourceUri**: If the resource is more than 4K in size, then the URI of the 
  resource is stored in this property.

> ### Why 4K
>
> The FHIR specification allows for resources to be up to 1MB in size. However, 
> Neo4j's performance is dependent on memory. Therefore, if a resource is larger 
> than 4K (a comon page size), it is stored in a separate file and the URI of 
> the file is stored in the `resourceUri` property. 
> 
> The memory `resource` property may have stripped version of the raw resource, 
> for example, a large PDF or image fields removed.
> 

### FHIR Node Properties

- Values that are often searched are exposed as the Properties on the node and 
  if used very often, we will also create indexes.

#### Coded Value Representation

They will follow the FHIR query format and are in string arrays.

```
["http://loinc.org|4544-3|Hematocrit [Volume Fraction] of Blood by Automated count"]
```

##### Parallel Arrays to Represent Codes, Numeric Values and Units

Because Cypher does not support the search of nested JSON, we will store the 
numeric and unit values in parallel arrays to the coded value array.

- Numeric values are stored in a parallel array to the coded value array.
- Units are stored in a parallel array to the coded value array.

## Query Examples

### Find Specific Observation

#### Starting with Erythrocyte Count

- This finds the erythrocyte count observations found in the graph.

```cypher
MATCH (obs:Observation)
WHERE any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0')
RETURN obs
```

- Expanding the above query, we can get the patient and show the observation text and value.

```
MATCH (obs:Observation)-[:SUBJECT]->(p:FHIR_Patient)
WHERE
  any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0') AND
  any(item IN obs.numericValues WHERE item > 12.0)
RETURN p.name[0] AS Patient, obs.text as Observation, obs.numericValues[0] AS 
Count
```

```
╒═════════════════════════════════════════╤═════════════════════════════════════════════════════╤══════╕
│Patient                                  │Observation                                          │Count │
╞═════════════════════════════════════════╪═════════════════════════════════════════════════════╪══════╡
│"Mr. Allen322 Norris589 Ebert178"        │"Erythrocyte [DistWidth] in Blood by Automated count"│14.23 │
├─────────────────────────────────────────┼─────────────────────────────────────────────────────┼──────┤
│"Mr. Carroll471 Murray856"               │"Erythrocyte [DistWidth] in Blood by Automated count"│13.309│
├─────────────────────────────────────────┼─────────────────────────────────────────────────────┼──────┤
│"Mr. Alden634 Frankie174 Hettinger594 JD"│"Erythrocyte [DistWidth] in Blood by Automated count"│13.694│
├─────────────────────────────────────────┼─────────────────────────────────────────────────────┼──────┤
│"Mr. Bruno518 Virgil85 Rippin620"        │"Erythrocyte [DistWidth] in Blood by Automated count"│15.205│
└─────────────────────────────────────────┴─────────────────────────────────────────────────────┴──────┘
```

- Finding the ImagingStudies that Triggered the Erythrocyte Count of > 12.
  
```
MATCH (obs:Observation)-[:PERFORMED_IN]->(e:Encounter)<-[:PERFORMED_IN]-(img:ImagingStudy)
WHERE any(item IN obs.code WHERE item STARTS WITH 'http://loinc.org|788-0')
  AND any(item IN obs.numericValues WHERE item > 15.0)
RETURN DISTINCT img.procedureCodeText, img.studyUid, img.seriesUid

```

```
╒══════════════════════════════════╤═════════════════════════════════════════╤═════════════════════════════════════════════╕
│img.procedureCodeText             │img.studyUid                             │img.seriesUid                                │
╞══════════════════════════════════╪═════════════════════════════════════════╪═════════════════════════════════════════════╡
│"Plain X-ray of chest (procedure)"│"1.2.840.99999999.34264206.1037082020670"│["1.2.840.99999999.1.78527067.1037082020670"]│
├──────────────────────────────────┼─────────────────────────────────────────┼─────────────────────────────────────────────┤
│"Echocardiography (procedure)"    │"1.2.840.99999999.64375590.1037082020670"│["1.2.840.99999999.1.11957029.1037082020670"]│
└──────────────────────────────────┴─────────────────────────────────────────┴─────────────────────────────────────────────┘
```
