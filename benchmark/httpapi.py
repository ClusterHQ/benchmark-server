# Copyright ClusterHQ Inc.  See LICENSE file for details.
"""
A HTTP REST API for storing benchmark results.
"""

import sys

from uuid import uuid4
from json import dumps, loads

from twisted.application.internet import StreamServerEndpointService
from twisted.internet.defer import Deferred, fail, succeed
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.internet.task import react
from twisted.python.log import startLogging, err, msg
from twisted.python.usage import Options, UsageError
from twisted.web.server import Site
from twisted.web.resource import Resource

from klein import Klein

from zope.interface import implementer

from ._interfaces import IBackend


class ResultNotFound(Exception):
    """
    Exception indicating that a result with a given identifier is not found.
    """


@implementer(IBackend)
class DummyBackend(object):
    """
    The backend that simply drops all results.
    """
    def store(self, result):
        """
        Pretend to store a single benchmarking result.

        :param dict result: The result in the JSON compatible format.
        :return: A Deferred that produces an identifier for the stored
            result.
        """
        return succeed(uuid4().hex)

    def retrieve(self, id):
        """
        Just fail.
        """
        return fail(ResultNotFound())

    def query(self, filter):
        """
        Return an empty result.
        """
        return succeed(list())

    def delete(self, id):
        """
        Just fail.
        """
        return fail(ResultNotFound())


@implementer(IBackend)
class InMemoryBackend(object):
    """
    The backend that simply drops all results.

    :ivar dict results: Stored results by their identifiers.
    """
    def __init__(self):
        self._results = {}

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
            return self._results[id]
        except KeyError:
            return fail(ResultNotFound())

    def query(self, filter):
        """
        Return matching results.
        """
        matching = [
            r for r in self._results.viewvalues()
            if filter.viewitems() <= r.viewitems()
        ]
        return succeed(matching)

    def delete(self, id):
        """
        Delete a result by the given identifier.
        """
        try:
            del self._results[id]
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
        request.setResponseCode(404)
        return ""

    @app.route("/submit", methods=['POST'])
    def submit(self, request):
        """
        Store a new benchmarking result.

        :param twisted.web.http.Request request: The request.
        """
        request.setHeader(b'content-type', b'application/json')
        try:
            json = loads(request.content.read())
        except ValueError as e:
            err(e, "failed to parse result")
            request.setResponseCode(400)
            return dumps({"message": e.message})

        d = self.backend.store(json)

        def stored(id):
            msg("stored result with id {}".format(id))
            result = {"version": self.version, "id": id}
            return dumps(result)

        d.addCallback(stored)
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
