from json import dumps, loads

from twisted.application.internet import StreamServerEndpointService
from twisted.internet import reactor, endpoints
from twisted.internet.defer import Deferred, succeed
from twisted.internet.endpoints import TCP4ServerEndpoint
from twisted.web import server, client
from twisted.web.iweb import IBodyProducer
from twisted.trial import unittest

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
    RESULT = {'branch': 'branch1', 'run': 1, 'result': 1}
    RESULT_JSON = dumps(RESULT)

    def setUp(self):
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
        return self.service.stopService()

    def check_response_code(self, response, expected_code):
        self.assertEqual(
            response.code, expected_code, "Incorrect response code")
        return response

    def check_submit_response_body(self, body):
        data = loads(body)
        self.assertIn('version', data)
        self.assertEqual(data['version'], 1)
        self.assertIn('id', data)
        return data['id']

    def test_submit_success(self):
        """
        Valid JSON can be successfully submitted.
        """
        result = StringProducer(self.RESULT_JSON)
        req = self.agent.request("POST", "/submit", bodyProducer=result)
        req.addCallback(self.check_response_code, 200)
        return req

    def test_submit_response_format(self):
        """
        Returned content is the expected JSON.
        """
        req = self.test_submit_success()
        req.addCallback(client.readBody)
        req.addCallback(self.check_submit_response_body)
        return req

    def test_submit_persists(self):
        """
        Submitted result is stored in the backend.
        """
        req = self.test_submit_response_format()

        def check_backend(id):
            self.assertIn(id, self.backend._results)
            self.assertEqual(self.RESULT, self.backend._results[id])

        req.addCallback(check_backend)
        return req
