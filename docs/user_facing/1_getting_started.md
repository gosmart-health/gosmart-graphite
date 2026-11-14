# Getting Started with Gosmart.Health GRAPHITE

## Getting Ready

### Prepare the Docker Environment

We will be using Docker to launch both a local test instance of neo4j database server and the DICOM Graph Server.

If you are not yet familiar with Docker, it will run well on Windows and Linux systems. but https://docs.docker.com/engine/install/ will have installation instructions. Please visit At the time of writing Mac with Intel emulator support was able to run the containers but this may change in the future as Apple will reduce the support of Intel emulation.

### Prepare the Local Image Storage Root Directory

By default we will use `/tmp/gosmart_dicom_storage` as the DICOM image storage location. Be sure that this location is available, directory was created and write permission is given to the docker user. This is typically the user who launches the docker commands.

### Start the Database and DICOM Graph Server

You will find `docker-compose.yaml` file on the root of this project. Please inspect this file and if you already know how docker-compose works, feel free to change variables.

```shell
docker-compose up -d
```

### Check the Database

You can access `http://localhost:7474`from your browser to launch the database user interface. Use `neo4j` as both username and password to access.

**Note on Database Storage**: We are using the Docker managed volume for the database.