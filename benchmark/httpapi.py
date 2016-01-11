# Copyright ClusterHQ Inc.  See LICENSE file for details.
"""
A HTTP REST API for storing benchmark results.
"""

import sys

from json import dumps, loads
from uuid import uuid4
from urlparse import urljoin

from twisted.application.internet import StreamServerEndpointService
from twisted.application.service import MultiService, Service
from twisted.internet.defer import Deferred, fail, succeed
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.internet.task import react
from twisted.python.log import startLogging, err, msg
from twisted.python.usage import Options, UsageError
from twisted.web.http import (
    BAD_REQUEST, CREATED, NO_CONTENT, NOT_FOUND, INTERNAL_SERVER_ERROR
)
from twisted.web.resource import Resource
from twisted.web.server import Site

from bson.errors import InvalidId
from bson.objectid import ObjectId

from dateutil import parser as timestamp_parser

from klein import Klein

from sortedcontainers import SortedList

from txmongo import MongoConnectionPool
from txmongo.filter import DESCENDING, sort as orderby

from zope.interface import implementer

from ._interfaces import IBackend


class ResultNotFound(Exception):
    """
    Exception indicating that a result with a given identifier is not found.
    """


class BadResultId(ResultNotFound):
    """
    The identifier is not recognized as a valid ID by a backend.
    """


class BadRequest(Exception):
    """
    Bad request parameters or content.
    """


@implementer(IBackend)
class InMemoryBackend(object):
    """
    The backend that keeps the results in the memory.
    """
    def __init__(self, *args, **kwargs):
        def get_timestamp(result):
            return timestamp_parser.parse(result['timestamp'])

        self._results = dict()
        self._sorted = SortedList(key=get_timestamp)

    def disconnect(self):
        return succeed(None)

    def store(self, result):
        """
        Store a single benchmarking result and return its identifier.

        :param dict result: The result in the JSON compatible format.
        :return: A Deferred that produces an identifier for the stored
            result.
        """
        id = uuid4().hex
        self._results[id] = result
        self._sorted.add(result)
        return succeed(id)

    def retrieve(self, id):
        """
        Retrive a result by the given identifier.
        """
        try:
            return succeed(self._results[id])
        except KeyError:
            return fail(ResultNotFound(id))

    def query(self, filter, limit=None):
        """
        Return matching results.
        """
        matching = []
        for result in reversed(self._sorted):
            if len(matching) == limit:
                break
            if filter.viewitems() <= result.viewitems():
                matching.append(result)
        return succeed(matching)

    def delete(self, id):
        """
        Delete a result by the given identifier.
        """
        try:
            result = self._results.pop(id)
            self._sorted.remove(result)
            return succeed(None)
        except KeyError:
            return fail(ResultNotFound(id))


@implementer(IBackend)
class TxMongoBackend(object):
    """
    The backend that uses txmongo driver to work with MongoDB.
    """
    def __init__(self, hostname="127.0.0.1", port=27017):
        connection = MongoConnectionPool(host=hostname, port=port)
        self.collection = connection.benchmark.results

    def disconnect(self):
        return self.collection.database.connection.disconnect()

    def store(self, result):
        """
        Store a single benchmarking result and return its identifier.

        :param dict result: The result in the JSON compatible format.
        :return: A Deferred that produces an identifier for the stored
            result.
        """
        def to_str(result):
            return str(result.inserted_id)

        # Store the timestamp field as a special hidden datetime field
        # for sorting.
        result['sort$timestamp'] = timestamp_parser.parse(result['timestamp'])
        id = self.collection.insert_one(result)
        id.addCallback(to_str)
        return id

    def retrieve(self, id):
        """
        Retrive a result by the given identifier.
        """
        try:
            object_id = ObjectId(id)
        except InvalidId:
            raise BadResultId(id)

        def post_process(result):
            if result is None:
                raise ResultNotFound(id)
            return result

        # Do not include the '_id' and 'sort$timestamp' fields in the
        # results as these are not part of the original document.
        # If we later choose to include '_id', its type is 'ObjectId'
        # which can not be serialized to JSON. Either a custom
        # JSONEncoder or bson.json_util.dumps would be needed.
        d = self.collection.find_one(
            {'_id': object_id},
            fields={'_id': False, 'sort$timestamp': False}
        )
        d.addCallback(post_process)
        return d

    def query(self, filter, limit=None):
        """
        Return matching results.
        """
        if limit == 0:
            return succeed([])

        # The txmongo API differs from pymongo with regard to sorting.
        # To sort results when making a query using txmongo, a query
        # filter needs to be created and passed to collection.find().
        sort_filter = orderby(DESCENDING('sort$timestamp'))

        # Do not include the '_id' and 'sort$timestamp' fields in the
        # results as these are not part of the original document.
        find_args = dict(
            filter=sort_filter,
            fields={'_id': False, 'sort$timestamp': False}
        )
        if limit:
            find_args['limit'] = limit

        return self.collection.find(filter, **find_args)

    def delete(self, id):
        """
        Delete a result by the given identifier.
        """
        try:
            object_id = ObjectId(id)
        except InvalidId:
            raise BadResultId(id)

        def handle_result(result):
            if result.deleted_count == 0:
                raise ResultNotFound(id)
            return None

        d = self.collection.delete_one({'_id': object_id})
        d.addCallback(handle_result)
        return d


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

    @staticmethod
    def _make_error_body(message):
        return dumps({"message": message})

    @app.handle_errors(BadResultId)
    def _bad_id(self, request, failure):
        request.setResponseCode(NOT_FOUND)
        request.setHeader(b'content-type', b'application/json')
        return self._make_error_body(
            "Result ID {} is not valid".format(failure.value.message)
        )

    @app.handle_errors(ResultNotFound)
    def _not_found(self, request, failure):
        request.setResponseCode(NOT_FOUND)
        request.setHeader(b'content-type', b'application/json')
        return self._make_error_body(
            "No result with ID {}".format(failure.value.message)
        )

    @app.handle_errors(BadRequest)
    def _bad_request(self, request, failure):
        err(failure, "Bad request")
        request.setResponseCode(BAD_REQUEST)
        request.setHeader(b'content-type', b'application/json')
        return self._make_error_body(failure.value.message)

    @app.handle_errors(Exception)
    def _unhandled_error(self, request, failure):
        err(failure, "Unhandled error")
        request.setResponseCode(INTERNAL_SERVER_ERROR)
        request.setHeader(b'content-type', b'application/json')
        return self._make_error_body(failure.value.message)

    @app.route("/benchmark-results", methods=['POST'])
    def post(self, request):
        """
        Post a new benchmarking result.

        :param twisted.web.http.Request request: The request.
        """
        request.setHeader(b'content-type', b'application/json')
        try:
            json = loads(request.content.read())
            timestamp_parser.parse(json['timestamp'])
        except KeyError as e:
            raise BadRequest("'{}' is missing".format(e.message))
        except ValueError as e:
            raise BadRequest(e.message)

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

    @app.route("/benchmark-results", methods=['GET'])
    def query(self, request):
        """
        Query the previously stored benchmarking results.

        Currently this method only supports filtering the results by the
        branch name.
        There is no support for paging of results, but a limit on the
        number of the results to return may be specified.
        The returned results are ordered by the timestamp in descending
        order.

        :param twisted.web.http.Request request: The request.
        """
        request.setHeader(b'content-type', b'application/json')
        params = self._parse_query_args(request.args)
        d = self.backend.query(**params)

        def got_results(results):
            result = {"version": self.version, "results": results}
            return dumps(result)

        d.addCallback(got_results)
        return d

    @staticmethod
    def _parse_query_args(args):
        def ensure_one_value(key, values):
            if len(values) != 1:
                raise BadRequest("'{}' should have one value".format(key))
            return values[0]

        limit = None
        filter = {}
        for k, v in args.iteritems():
            if k == 'limit':
                limit = ensure_one_value(k, v)
                try:
                    limit = int(limit)
                    if limit < 0:
                        raise BadRequest(
                            "limit is not a non-negative integer: {}".
                            format(limit)
                        )
                except ValueError:
                    raise BadRequest(
                        "limit is not an integer: '{}'".format(limit)
                    )
            elif k == 'branch':
                branch = ensure_one_value(k, v)
                filter['userdata'] = {'branch': branch}
            else:
                raise BadRequest("unexpected query argument '{}'".format(k))
        return {'filter': filter, 'limit': limit}


def create_api_service(endpoint, backend):
    """
    Create a Twisted Service that serves the API on the given endpoint.

    :param endpoint: Twisted endpoint to listen on.
    :return: Service that will listen on the endpoint using HTTP API server.
    """
    api_root = Resource()
    api = BenchmarkAPI_V1(backend)
    api_root.putChild('v1', api.app.resource())

    return StreamServerEndpointService(endpoint, Site(api_root))


class BackendService(Service):
    """
    A basic Twisted service that wraps the peristence backend.
    """
    def __init__(self, backend):
        super(Service, self).__init__()
        self.backend = backend

    def stopService(self):
        return self.backend.disconnect()


def start_services(reactor, endpoint, backend):
    top_service = MultiService()
    api_service = create_api_service(endpoint, backend)
    api_service.setServiceParent(top_service)
    backend_service = BackendService(backend)
    backend_service.setServiceParent(top_service)

    # XXX Setting _raiseSynchronously makes startService raise an exception
    # on error rather than just logging and dropping it.
    # This should be a public API, Twisted bug #8170.
    api_service._raiseSynchronously = True
    top_service.startService()
    reactor.addSystemEventTrigger(
        "before",
        "shutdown",
        lambda: top_service.stopService,
    )


class ServerOptions(Options):
    longdesc = "Run the benchmark results server"

    _BACKENDS = {
        'in-memory': InMemoryBackend,
        'mongodb': TxMongoBackend,
    }

    optParameters = [
        ['port', None, 8888, "The port to listen on", int],
        ['backend', None, 'in-memory', "The persistence backend to use. "
         "One of {}.".format(', '.join(_BACKENDS)), str],
        ['db-hostname', None, None, "The hostname of the database", str],
        ['db-port', None, None, "The port of the database", str],
    ]

    def postOptions(self):
        try:
            backend = self._BACKENDS[self['backend']]
        except KeyError:
            raise UsageError("Unknown backend {}".format(self['backend']))

        conn = dict()
        if self['db-hostname']:
            conn['hostname'] = self['db-hostname']
        if self['db-port']:
            conn['port'] = self['db-port']

        self['backend'] = backend(**conn)


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

    endpoint = TCP4ServerEndpoint(reactor, options['port'])
    backend = options['backend']
    start_services(reactor, endpoint, backend)

    # Do not quit until the reactor is stopped.
    return Deferred()


if __name__ == '__main__':
    react(main, (sys.argv[1:],))
