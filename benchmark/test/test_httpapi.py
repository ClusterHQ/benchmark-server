from json import dumps, loads
from urlparse import urljoin

from twisted.application.internet import StreamServerEndpointService
from twisted.internet import reactor, endpoints
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
                    reactor,
                    addr.host,
                    addr.port,
                ),
                reactor,
            )

        listening = Deferred()
        listening.addCallback(make_client)
        endpoint = TestEndpoint(reactor, listening)
        self.service = StreamServerEndpointService(endpoint, site)
        self.service.startService()
        return listening

    def tearDown(self):
        super(BenchmarkAPITests, self).tearDown()
        return self.service.stopService()

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

    def test_submit_persists(self):
        """
        Submitted result is stored in the backend.
        """
        req = self.submit(self.RESULT)
        req.addCallback(client.readBody)
        req.addCallback(self.parse_submit_response_body)

        def check_backend(id):
            self.assertIn(id, self.backend._results)
            self.assertEqual(self.RESULT, self.backend._results[id])

        req.addCallback(check_backend)
        return req
