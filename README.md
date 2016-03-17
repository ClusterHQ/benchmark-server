## benchmark-server

A service to store and retrieve benchmarking metrics.

### How to run

```
./bin/start_server.sh --help
Usage: httpapi.py [options]
Options:
      --port=         The port to listen on [default: 8888]
      --backend=      The persistence backend to use. One of mongodb, in-memory.
                      [default: in-memory]
      --db-hostname=  The hostname of the database
      --db-port=      The port of the database
      --version       Display Twisted version and exit.
      --help          Display this help and exit.

```

The service can be used with either an in-memory backend or a MongoDB backend.
To use the MongoDB backend, it is recommended that you use the provided docker-compose file to start up both an instance of the server and MongoDB.
This will start the service running on port 8080.

```
$ docker-compose up
```


### How to deploy

This repository contains fabric code to run this service on an AWS instance.

To deploy the service on AWS:

* Export the following environment variables:

`AWS_KEY_PAIR` (the KEY_PAIR to use)
`AWS_KEY_FILENAME` (the full path to your .pem file)
`AWS_SECRET_ACCESS_KEY`
`AWS_ACCESS_KEY_ID`

* Create a virtualenv and install dependencies:

```
$ pip install --process-dependency-links -e .[dev]
```

* Start the service:

```
$ fab start
```

You can also view additional options using:

```
$ fab help
```
