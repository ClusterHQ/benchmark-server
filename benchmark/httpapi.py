# Copyright ClusterHQ Inc.  See LICENSE file for details.
"""
A HTTP REST API for storing benchmark results.
"""

import sys

from collections import OrderedDict
from json import dumps, loads
from uuid import uuid4
from urlparse import urljoin

from twisted.application.internet import StreamServerEndpointService
from twisted.internet.defer import Deferred, fail, succeed
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.internet.task import react
from twisted.python.log import startLogging, err, msg
from twisted.python.usage import Options, UsageError
from twisted.web.http import BAD_REQUEST, CREATED, NO_CONTENT, NOT_FOUND
from twisted.web.resource import Resource
from twisted.web.server import Site

from klein import Klein

from zope.interface import implementer

from ._interfaces import IBackend


class ResultNotFound(Exception):
    """
    Exception indicating that a result with a given identifier is not found.
    """


@implementer(IBackend)
class InMemoryBackend(object):
    """
    The backend that simply drops all results.

    :ivar dict results: Stored results by their identifiers.
    """
    def __init__(self):
        self._results = OrderedDict()

    def store(self, result):
        """
        Store a single benchmarking result and return its identifier.

        :param dict result: The result in the JSON compatible format.
        :return: A Deferred that produces an identifier for the stored
            result.
        """
        id = uuid4().hex
        self._results[id] = result
        return succeed(id)

    def retrieve(self, id):
        """
        Retrive a result by the given identifier.
        """
        try:
            return succeed(self._results[id])
        except KeyError:
            return fail(ResultNotFound())

    def query(self, filter, limit):
        """
        Return matching results.
        """
        matching = [
            r for r in reversed(self._results.values())
            if filter.viewitems() <= r.viewitems()
        ]
        if limit > 0:
            matching = matching[:limit]
        return succeed(matching)

    def delete(self, id):
        """
        Delete a result by the given identifier.
        """
        try:
            del self._results[id]
            return succeed(None)
        except KeyError:
            return fail(ResultNotFound())


class BenchmarkAPI_V1(object):
    """
    API for storing and accessing benchmarking results.

    :ivar IBackend backend: The backend for storing the results.
    """
    app = Klein()
    version = 1

    def __init__(self, backend):
        """
        :param IBackend backend: The backend for storing the results.
        """
        self.backend = backend

    @app.handle_errors(ResultNotFound)
    def _not_found(self, request, failure):
        request.setResponseCode(NOT_FOUND)
        return ""

    @app.route("/benchmark-results", methods=['POST'])
    def post(self, request):
        """
        Post a new benchmarking result.

        :param twisted.web.http.Request request: The request.
        """
        request.setHeader(b'content-type', b'application/json')
        try:
            json = loads(request.content.read())
        except ValueError as e:
            err(e, "failed to parse result")
            request.setResponseCode(BAD_REQUEST)
            return dumps({"message": e.message})

        d = self.backend.store(json)

        def stored(id):
            msg("stored result with id {}".format(id))
            result = {"version": self.version, "id": id}
            response = dumps(result)
            location = urljoin(request.path + '/', id)
            request.setHeader(b'Location', location)
            request.setResponseCode(CREATED)
            return response

        d.addCallback(stored)
        return d

    @app.route("/benchmark-results/<string:id>", methods=['GET'])
    def get(self, request, id):
        """
        Get a previously stored benchmarking result by its ID.

        :param twisted.web.http.Request request: The request.
        :param str id: The identifier.
        """
        request.setHeader(b'content-type', b'application/json')
        d = self.backend.retrieve(id)

        def retrieved(result):
            response = dumps(result)
            return response

        d.addCallback(retrieved)
        return d

    @app.route("/benchmark-results/<string:id>", methods=['DELETE'])
    def delete(self, request, id):
        """
        Delete a previously stored benchmarking result by its ID.

        :param twisted.web.http.Request request: The request.
        :param str id: The identifier.
        """
        request.setHeader(b'content-type', b'application/json')
        request.setResponseCode(NO_CONTENT)
        return self.backend.delete(id)

    @app.route("/query", methods=['POST'])
    def query(self, request):
        """
        Query the previously stored benchmarking results.

        Returns results that are supersets of a JSON document
        provided in the request body.
        Number of the returned results can be limited using
        "limit" query argumnet.

        :param twisted.web.http.Request request: The request.
        """
        request.setHeader(b'content-type', b'application/json')
        try:
            json = loads(request.content.read())
        except ValueError as e:
            err(e, "failed to parse filter")
            request.setResponseCode(BAD_REQUEST)
            return dumps({"message": e.message})

        filter = json.get('filter', {})
        limit = json.get('limit', '0')
        try:
            limit = int(limit)
        except ValueError as e:
            err(e, "limit is not an integer: {}".format(limit))
            request.setResponseCode(BAD_REQUEST)
            return dumps({"message": e.message})

        d = self.backend.query(filter, limit)

        def got_results(results):
            msg("got {} results (limit = {})".format(len(results), limit))
            result = {"version": self.version, "results": results}
            return dumps(result)

        d.addCallback(got_results)
        return d


def create_api_service(endpoint):
    """
    Create a Twisted Service that serves the API on the given endpoint.

    :param endpoint: Twisted endpoint to listen on.
    :return: Service that will listen on the endpoint using HTTP API server.
    """
    api_root = Resource()
    api = BenchmarkAPI_V1(InMemoryBackend())
    api_root.putChild('v1', api.app.resource())

    return StreamServerEndpointService(endpoint, Site(api_root))


class ServerOptions(Options):
    longdesc = "Run the benchmark results server"

    optParameters = [
        ['port', None, 8888, "The port to listen on", int],
    ]


def main(reactor, args):
    try:
        options = ServerOptions()
        options.parseOptions(args)
    except UsageError as e:
        sys.stderr.write(e.args[0])
        sys.stderr.write('\n\n')
        sys.stderr.write(options.getSynopsis())
        sys.stderr.write('\n')
        sys.stderr.write(options.getUsage())
        raise SystemExit(1)

    startLogging(sys.stderr)
    service = create_api_service(TCP4ServerEndpoint(reactor, options['port']))

    # XXX Make startService() raise an exception on an error
    # instead of just logging and dropping it.
    service._raiseSynchronously = True
    service.startService()
    reactor.addSystemEventTrigger(
        "before",
        "shutdown",
        lambda: service.stopService,
    )
    # Do not quit until the reactor is stopped.
    return Deferred()


if __name__ == '__main__':
    react(main, (sys.argv[1:],))
