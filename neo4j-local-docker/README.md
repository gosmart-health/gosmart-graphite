# Neo4j Docker Test Server Setup

## What is This?

This directory contains Docker Compose file to get your Neo4j Graph Database running for development and testing. Using the community version, you can launch and use it for free for non-commercial use.

## Getting Started

- Download and set up Docker Desktop
- Database persistence is automatically managed via a Docker named volume (`neo4j_data`), eliminating the need for host file sharing or directory permission configurations across macOS, Linux, and Windows.

## Authentication is Disabled

- To facilitate development and testing, authentication is disabled in the docker-compose.yaml file with NEO4J_AUTH=none
- Do not use this feature in production!
- **IMPORTANT** For the web access the user and password remain as neo4j/neo4j, If auth is enabled, you will be asked to change the username and password after logging in.

## Ports Need to be Exposed

- 7474: This is the web UI access point
- 7687: This is the bolt protocol access point

Be sure that your firewall inbound settings are open if you are sharing this instance with other computers in your development 
environment.

## Starting

```shell
docker compose up -d
```

## Cloud Server Alternative

Neo4j Aura offers a free tier called AuraDB Free, which allows users to create a graph database without any costs or credit card requirements. This free tier supports up to 200,000 nodes and 400,000 relationships.

## References

- [Neo4j Docker Hub](https://hub.docker.com/_/neo4j/)
- [Neo4j Aura](https://neo4j.com/videos/getting-started-with-aura-free-tier/)