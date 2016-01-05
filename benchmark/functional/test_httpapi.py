from testtools import TestCase

from ..httpapi import TxMongoBackend
from ..test.test_httpapi import BenchmarkAPITestsMixin


class TxMongoBenchmarkAPITests(BenchmarkAPITestsMixin, TestCase):
    def setUp(self):
        self.backend = TxMongoBackend()
        self.addCleanup(self.backend.disconnect)
        super(TxMongoBenchmarkAPITests, self).setUp()

    def submit(self, result):
        """
        Submit a result.
        """
        req = super(TxMongoBenchmarkAPITests, self).submit(result)

        def add_cleanup(response):
            location = response.headers.getRawHeaders(b'Location')[0]
            self.addCleanup(lambda: self.agent.request("DELETE", location))
            return response

        req.addCallback(add_cleanup)
        return req
