from datetime import datetime
from json import dumps, loads
from urllib import urlencode
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

    RESULT = {u"userdata": {u"branch": "master"}, u"run": 1, u"result": 1,
              u"timestamp": datetime.now().isoformat(), }

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
        """
        Response has the expected reponse code.
        """
        self.assertEqual(
            response.code, expected_code, "Incorrect response code")
        return response

    def parse_submit_response_body(self, body):
        """
        Check that response to a submit request has the expected
        structure and version.
        Returns an identifier assigned to the submitted object.
        """
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
        req = self.submit(self.RESULT)

        def check_location(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            base_uri = response.request.absoluteURI + '/'
            d = client.readBody(response)
            d.addCallback(lambda body: loads(body)['id'])
            d.addCallback(lambda id: urljoin(base_uri, id))
            d.addCallback(
                lambda expected: self.assertEqual(expected, location)
            )
            return d

        req.addCallback(check_location)
        return req

    def check_received_result(self, response, expected_result):
        """
        Response body contains the expected result.
        If it does, return the JSON decoded response body.
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

    def test_get_nonexistent(self):
        """
        Getting non-existent resource is correctly handled.
        """
        location = "/benchmark-results/foobar"
        req = self.agent.request("GET", location)
        req.addCallback(self.check_response_code, http.NOT_FOUND)
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

    def test_delete_nonexistent(self):
        """
        Getting non-existent resource is correctly handled.
        """
        location = "/benchmark-results/foobar"
        req = self.agent.request("DELETE", location)
        req.addCallback(self.check_response_code, http.NOT_FOUND)
        return req

    BRANCH1_RESULT1 = {u"userdata": {u"branch": u"1"}, u"value": 100,
                       u"timestamp": datetime.now().isoformat()}
    BRANCH1_RESULT2 = {u"userdata": {u"branch": u"1"}, u"value": 120,
                       u"timestamp": datetime.now().isoformat()}
    BRANCH2_RESULT1 = {u"userdata": {u"branch": u"2"}, u"value": 110,
                       u"timestamp": datetime.now().isoformat()}

    def setup_results(self):
        """
        Submit some results for testing various queries against them.
        """
        results = [
            self.BRANCH1_RESULT1, self.BRANCH1_RESULT2, self.BRANCH2_RESULT1
        ]

        def chained_submit(_, result):
            """
            Discard result of a previous submit and do a new one.
            """
            return self.submit(result)

        # Sequentially submit the results.
        d = succeed(None)
        for result in results:
            d.addCallback(chained_submit, result)
        return d

    def run_query(self, ignored, filter=None, limit=None):
        """
        Invoke the query interface of the HTTP API.

        :param dict filter: The data that the results must include.
        :param int limit: The limit on how many results to turn.
        :return" Deferred that fires with content of a response.
        """
        query = {}
        if filter:
            query = filter.copy()
        if limit:
            query["limit"] = limit
        if query:
            query_string = "?" + urlencode(query)
        else:
            query_string = ""
        req = self.agent.request("GET", "/benchmark-results" + query_string)
        req.addCallback(self.check_response_code, 200)
        req.addCallback(client.readBody)
        return req

    def check_query_result(self, body, expected):
        """
        Check that the given response content is valid JSON
        that contains the expect result.

        :param str body: The content to check.
        :param expected: The expected results.
        :type expected: list of dict
        """
        data = loads(body)
        self.assertIn('version', data)
        self.assertEqual(data['version'], 1)
        self.assertIn('results', data)
        results = data['results']
        self.assertItemsEqual(expected, results)

    def test_query_no_filter_no_limit(self):
        """
        All results are returned if no filter and no limit are given.
        """
        d = self.setup_results()
        d.addCallback(self.run_query)
        d.addCallback(
            self.check_query_result,
            expected=[
                self.BRANCH1_RESULT1, self.BRANCH1_RESULT2,
                self.BRANCH2_RESULT1
            ],
        )
        return d

    def test_query_with_filter(self):
        """
        All matching results are returned if filter is given.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"branch": u"1"})
        d.addCallback(
            self.check_query_result,
            expected=[
                self.BRANCH1_RESULT1, self.BRANCH1_RESULT2,
            ],
        )
        d.addCallback(self.run_query, filter={u"branch": u"2"})
        d.addCallback(
            self.check_query_result,
            expected=[
                self.BRANCH2_RESULT1
            ],
        )
        return d

    def test_query_with_limit(self):
        """
        The latest ``limit`` results are returned if no filter is set
        but the limit is specified and the total number of the results
        is greater than the limit.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, limit=2)
        d.addCallback(
            self.check_query_result,
            expected=[
                self.BRANCH1_RESULT2,
                self.BRANCH2_RESULT1
            ],
        )
        return d

    def test_query_with_filter_and_limit(self):
        """
        The expected number of matching results is returned
        if the total number of such results is greater than
        the limit.  The returned results are the latest among
        the matching results.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"branch": u"1"}, limit=1)
        d.addCallback(
            self.check_query_result,
            expected=[
                self.BRANCH1_RESULT2,
            ],
        )
        return d
