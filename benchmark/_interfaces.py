# Copyright ClusterHQ Inc.  See LICENSE file for details.
"""
Interfaces for the benchmarking results server.
"""

from zope.interface import Interface


class IBackend(Interface):
    """
    A backend for storing and querying the results.
    """

    def disconnect():
        """
        Perform necessary disconnect and cleanup actions.

        :return: A Deferred that fires when the cleanup is done.
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

    def query(filter, limit):
        """
        Retrieve previously stored results that match the given filter.

        The returned results will have the same values as specified in the
        filter for the fields that are specified in the filter.

        :param dict filter: The filter in the JSON compatible format.
        :param int limit: The number of the *latest* results to return.
        :return: A Deferred that fires with a list of the results
            in the JSON compatible format.
        """

    def delete(id):
        """
        Delete a previously stored result by its identifier.

        :param id: The identifier of the result.
        :return: A Deferred that fires when the result is removed.
        """
