from json import dumps, loads
from urlparse import urljoin

from twisted.application.internet import StreamServerEndpointService
from twisted.internet import endpoints
from twisted.internet.defer import Deferred, succeed
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.web import client, http, server
from twisted.web.iweb import IBodyProducer

from testtools import TestCase
from testtools.deferredruntest import AsynchronousDeferredRunTest

from zope.interface import implementer

from benchmark.httpapi import BenchmarkAPI_V1, InMemoryBackend


@implementer(IBodyProducer)
class StringProducer(object):
    def __init__(self, body):
        self.body = body
        self.length = len(body)

    def startProducing(self, consumer):
        consumer.write(self.body)
        return succeed(None)

    def pauseProducing(self):
        pass

    def stopProducing(self):
        pass


class TestEndpoint(TCP4ServerEndpoint):
    def __init__(self, reactor, deferred):
        super(TestEndpoint, self).__init__(reactor, 0, interface='127.0.0.1')
        self.deferred = deferred

    def listen(self, protocolFactory):
        d = super(TestEndpoint, self).listen(protocolFactory)

        def invoke_callback(listening_port):
            self.deferred.callback(listening_port)
            return listening_port

        d.addCallback(invoke_callback)
        return d


class BenchmarkAPITests(TestCase):
    """
    Tests for BenchmarkAPI.
    """
    # The default timeout of 0.005 seconds is not always enough,
    # because we test HTTP requests via an actual TCP/IP connection.
    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    RESULT = {'branch': 'branch1', 'run': 1, 'result': 1}

    def setUp(self):
        super(BenchmarkAPITests, self).setUp()

        self.backend = InMemoryBackend()
        api = BenchmarkAPI_V1(self.backend)
        site = server.Site(api.app.resource())

        def make_client(listening_port):
            addr = listening_port.getHost()
            self.agent = client.ProxyAgent(
                endpoints.TCP4ClientEndpoint(
                    self.reactor,
                    addr.host,
                    addr.port,
                ),
                self.reactor,
            )

        listening = Deferred()
        listening.addCallback(make_client)
        endpoint = TestEndpoint(self.reactor, listening)
        self.service = StreamServerEndpointService(endpoint, site)
        self.service.startService()
        self.addCleanup(self.service.stopService)
        return listening

    def submit(self, result):
        """
        Submit a result.
        """
        json = dumps(result)
        body = StringProducer(json)
        req = self.agent.request("POST", "/benchmark-results",
                                 bodyProducer=body)
        return req

    def check_response_code(self, response, expected_code):
        self.assertEqual(
            response.code, expected_code, "Incorrect response code")
        return response

    def parse_submit_response_body(self, body):
        data = loads(body)
        self.assertIn('version', data)
        self.assertEqual(data['version'], 1)
        self.assertIn('id', data)
        return data['id']

    def test_submit_success(self):
        """
        Valid JSON can be successfully submitted.
        """
        req = self.submit(self.RESULT)
        req.addCallback(self.check_response_code, http.CREATED)
        return req

    def test_submit_response_format(self):
        """
        Returned content is the expected JSON.
        """
        req = self.submit(self.RESULT)
        req.addCallback(client.readBody)
        req.addCallback(self.parse_submit_response_body)
        return req

    def test_submit_response_location_header(self):
        """
        Returned Location header has the expected value.
        """
        # This is not a real array, but a well-known trick
        # to set an outer variable from an inner function.
        response = [None]
        req = self.submit(self.RESULT)

        def save_response(_response):
            response[0] = _response
            return _response

        req.addCallback(save_response)
        req.addCallback(client.readBody)
        req.addCallback(self.parse_submit_response_body)

        def check_location(id):
            expected_location = urljoin(
                response[0].request.absoluteURI + '/',
                id
            )
            self.assertEqual(
                expected_location,
                response[0].headers.getRawHeaders(b'Location')[0]
            )

        req.addCallback(check_location)
        return req

    def check_received_result(self, response, expected_result):
        """
        Response body contains the expected result.
        """
        got_body = client.readBody(response)

        def compare(body):
            result = loads(body)
            self.assertEqual(expected_result, result)
            return result

        return got_body.addCallback(compare)

    def test_submit_persists(self):
        """
        Submitted result is stored in the backend and it can be retrieved
        using a URI in the Location header.
        """
        req = self.submit(self.RESULT)

        def retrieve(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            return self.agent.request("GET", location)

        req.addCallback(retrieve)
        req.addCallback(self.check_response_code, http.OK)
        req.addCallback(self.check_received_result, self.RESULT)
        return req

    def test_get_idempotent(self):
        """
        Retrieving a result does not modify or remove it.
        """
        req = self.submit(self.RESULT)

        def retrieve_twice(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            got1 = self.agent.request("GET", location)
            got1.addCallback(self.check_response_code, http.OK)
            got1.addCallback(self.check_received_result, self.RESULT)
            got2 = got1.addCallback(
                lambda _: self.agent.request("GET", location)
            )
            got2.addCallback(self.check_response_code, http.OK)
            got2.addCallback(self.check_received_result, self.RESULT)
            return got2

        req.addCallback(retrieve_twice)
        return req

    def test_delete(self):
        """
        Submitted result is stored in the backend and it can be deleted
        using a URI in the Location header.
        """
        req = self.submit(self.RESULT)

        def delete(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            deleted = self.agent.request("DELETE", location)
            deleted.addCallback(self.check_response_code, http.NO_CONTENT)
            return deleted

        req.addCallback(delete)
        return req

    def test_get_deleted(self):
        """
        Deleted result can not be retrieved.
        """
        req = self.submit(self.RESULT)

        def delete_and_get(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            deleted = self.agent.request("DELETE", location)
            got = deleted.addCallback(
                lambda _: self.agent.request("GET", location)
            )
            got.addCallback(self.check_response_code, http.NOT_FOUND)
            return got

        req.addCallback(delete_and_get)
        return req

    def test_delete_deleted(self):
        """
        Deleted result can not be deleted again.
        """
        req = self.submit(self.RESULT)

        def delete_twice(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            deleted1 = self.agent.request("DELETE", location)
            deleted2 = deleted1.addCallback(
                lambda _: self.agent.request("DELETE", location)
            )
            deleted2.addCallback(self.check_response_code, http.NOT_FOUND)
            return deleted2

        req.addCallback(delete_twice)
        return req
