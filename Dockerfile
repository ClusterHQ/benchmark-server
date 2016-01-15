FROM ubuntu:latest

RUN apt-get update
RUN apt-get install -y git python2.7 python2.7-dev python-pip

RUN mkdir /benchmark-server
ADD . /benchmark-server

WORKDIR /benchmark-server

EXPOSE 8080
RUN pip install --process-dependency-links .
ENTRYPOINT ["./bin/start_server.sh"]
