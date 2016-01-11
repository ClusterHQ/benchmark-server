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
from testtools.deferredruntest import (
    AsynchronousDeferredRunTest, flush_logged_errors
)

from zope.interface import implementer

from benchmark.httpapi import BenchmarkAPI_V1, InMemoryBackend, BadRequest


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


class BenchmarkAPITestsMixin(object):
    """
    Tests for BenchmarkAPI.
    """
    # The default timeout of 0.005 seconds is not always enough,
    # because we test HTTP requests via an actual TCP/IP connection.
    run_tests_with = AsynchronousDeferredRunTest.make_factory(timeout=1)

    RESULT = {u"userdata": {u"branch": "master"}, u"run": 1, u"result": 1,
              u"timestamp": datetime(2016, 1, 1, 0, 0, 5).isoformat(), }

    NO_TIMESTAMP = {u"userdata": {u"branch": "master"}, u"run": 1,
                    u"result": 1, }

    BAD_TIMESTAMP = {u"userdata": {u"branch": "master"}, u"run": 1,
                     u"result": 1, u"timestamp": "noonish", }

    def setUp(self):
        super(BenchmarkAPITestsMixin, self).setUp()

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

        def add_cleanup(response):
            if response.code == http.CREATED:
                location = response.headers.getRawHeaders(b'Location')[0]
                self.addCleanup(lambda: self.agent.request("DELETE", location))
            return response

        req.addCallback(add_cleanup)

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

    def test_no_timestamp(self):
        """
        Valid JSON with a missing timestamp is an HTTP BAD_REQUEST.
        """
        req = self.submit(self.NO_TIMESTAMP)
        req.addCallback(self.check_response_code, http.BAD_REQUEST)
        req.addCallback(lambda _: flush_logged_errors(BadRequest))
        return req

    def test_bad_timestamp(self):
        """
        Valid JSON with an invalid timestamp is an HTTP BAD_REQUEST.
        """
        req = self.submit(self.BAD_TIMESTAMP)
        req.addCallback(self.check_response_code, http.BAD_REQUEST)
        req.addCallback(lambda _: flush_logged_errors(BadRequest))
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
                       u"timestamp": datetime(2016, 1, 1, 0, 0, 5).isoformat()}
    BRANCH1_RESULT2 = {u"userdata": {u"branch": u"1"}, u"value": 120,
                       u"timestamp": datetime(2016, 1, 1, 0, 0, 7).isoformat()}
    BRANCH2_RESULT1 = {u"userdata": {u"branch": u"2"}, u"value": 110,
                       u"timestamp": datetime(2016, 1, 1, 0, 0, 6).isoformat()}
    BRANCH2_RESULT2 = {u"userdata": {u"branch": u"2"}, u"value": 110,
                       u"timestamp": datetime(2016, 1, 1, 0, 0, 8).isoformat()}

    def setup_results(self):
        """
        Submit some results for testing various queries against them.
        """

        # Shuffle the results before submitting them.
        results = [
            self.BRANCH2_RESULT1, self.BRANCH1_RESULT1, self.BRANCH2_RESULT2,
            self.BRANCH1_RESULT2
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
        :param int limit: The limit on how many results to return.
        :return: Deferred that fires with a HTTP response.
        """
        query = {}
        if filter:
            query = filter.copy()
        if limit is not None:
            query["limit"] = limit
        if query:
            query_string = "?" + urlencode(query, doseq=True)
        else:
            query_string = ""
        return self.agent.request("GET", "/benchmark-results" + query_string)

    def check_query_result(self, response, expected_results,
                           expected_code=200):
        """
        Check that the given response matches the expected response code
        and that the content is valid JSON that contains the expected
        result.

        :param response: The response to check.
        :param expected_results: The expected results that should be in
            the response.
        :type expected_results: list of dict
        :param expected_code: The expected response code.
        """
        self.check_response_code(response, expected_code)

        d = client.readBody(response)

        def check_body(body):
            data = loads(body)
            self.assertIn('version', data)
            self.assertEqual(data['version'], 1)
            self.assertIn('results', data)
            results = data['results']
            self.assertEqual(expected_results, results)

        d.addCallback(check_body)
        return d

    def test_query_no_filter_no_limit(self):
        """
        All results are returned if no filter and no limit are given.
        """
        d = self.setup_results()
        d.addCallback(self.run_query)
        d.addCallback(
            self.check_query_result,
            expected_results=[
                self.BRANCH2_RESULT2, self.BRANCH1_RESULT2,
                self.BRANCH2_RESULT1, self.BRANCH1_RESULT1
            ],
        )
        return d

    def test_query_with_filter(self):
        """
        All matching results are returned if a filter is given.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"branch": u"1"})
        d.addCallback(
            self.check_query_result,
            expected_results=[
                self.BRANCH1_RESULT2, self.BRANCH1_RESULT1,
            ],
        )
        d.addCallback(self.run_query, filter={u"branch": u"2"})
        d.addCallback(
            self.check_query_result,
            expected_results=[
                self.BRANCH2_RESULT2, self.BRANCH2_RESULT1
            ],
        )
        return d

    def test_query_with_zero_limit(self):
        """
        An empty set of results are returned for a limit of zero.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, limit=0)
        d.addCallback(
            self.check_query_result,
            expected_results=[],
        )
        return d

    def test_query_with_limit(self):
        """
        The latest ``limit`` results are returned if no filter is set
        and the specified limit is less than the total number of
        results.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, limit=2)
        d.addCallback(
            self.check_query_result,
            expected_results=[
                self.BRANCH2_RESULT2,
                self.BRANCH1_RESULT2
            ],
        )
        return d

    def test_query_with_filter_and_limit(self):
        """
        The latest ``limit`` results which match the specified filter
        are returned if the limit is less than the total number of
        results.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"branch": u"1"}, limit=1)
        d.addCallback(
            self.check_query_result,
            expected_results=[
                self.BRANCH1_RESULT2,
            ],
        )
        return d

    def test_unsupported_query_arg(self):
        """
        ``query`` raises ``BadRequest`` when an unsupported query
        argument is specified.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"unsupported": u"ignored"})
        d.addCallback(self.check_response_code, http.BAD_REQUEST)
        d.addCallback(lambda _: flush_logged_errors(BadRequest))
        return d

    def test_multiple_query_args_of_same_type(self):
        """
        ``query`` raises ``BadRequest`` when multiple values for a key
        are specified.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, filter={u"branch": [u"1", u"2"]})
        d.addCallback(self.check_response_code, http.BAD_REQUEST)
        d.addCallback(lambda _: flush_logged_errors(BadRequest))
        return d

    def test_non_integer_limit_query_arg(self):
        """
        ``query`` raises ``BadRequest`` when a non-integer value is
        is specified for the `limit` key.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, limit="one")
        d.addCallback(self.check_response_code, http.BAD_REQUEST)
        d.addCallback(lambda _: flush_logged_errors(BadRequest))
        return d

    def test_query_with_negative_limit(self):
        """
        ``query`` raises ``BadRequest`` when a negative value is
        specified for the `limit` key.
        """
        d = self.setup_results()
        d.addCallback(self.run_query, limit=-1)
        d.addCallback(self.check_response_code, http.BAD_REQUEST)
        d.addCallback(lambda _: flush_logged_errors(BadRequest))
        return d


class InMemoryBenchmarkAPITests(BenchmarkAPITestsMixin, TestCase):
    def setUp(self):
        self.backend = InMemoryBackend()
        super(InMemoryBenchmarkAPITests, self).setUp()
