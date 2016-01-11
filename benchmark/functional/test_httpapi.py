from testtools import TestCase

from ..httpapi import TxMongoBackend
from ..test.test_httpapi import BenchmarkAPITestsMixin


class TxMongoBenchmarkAPITests(BenchmarkAPITestsMixin, TestCase):
    def setUp(self):
        self.backend = TxMongoBackend()
        self.addCleanup(self.backend.disconnect)
        super(TxMongoBenchmarkAPITests, self).setUp()
