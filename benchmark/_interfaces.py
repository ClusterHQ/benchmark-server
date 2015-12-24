# Copyright 2015 ClusterHQ Inc.  See LICENSE file for details.
"""
Interfaces for the benchmarking results server.
"""

from zope.interface import Interface


class IBackend(Interface):
    """
    A backend for storing and querying the results.
    """

    def store(result):
        """
        Store a single benchmarking result.

        :param dict result: The result in the JSON compatible format.
        :return: A Deferred that produces an identifier for the stored
            result.
        """

    def retrieve(id):
        """
        Retrieve a previously stored result by its identifier.

        :param id: The identifier of the result.
        :return: A Deferred that fires with the result in the JSON format.
        """

    def query(filter):
        """
        Retrieve previously stored results that match the given filter.

        The returned results will have the same values as specified in the
        filter for the fields that are specified in the filter.

        :param dict filter: The filter in the JSON compatible format.
        :return: A Deferred that fires with the results in the JSON format.
        """

    def delete(id):
        """
        Delete a previously stored result by its identifier.

        :param id: The identifier of the result.
        :return: A Deferred that fires when the result is removed.
        """
