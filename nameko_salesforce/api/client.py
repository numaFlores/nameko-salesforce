from contextlib import contextmanager

from nameko.utils.retry import retry
import simple_salesforce


def get_client(config):
    pool = ClientPool(config)
    return ClientProxy(pool)


class MethodProxy(object):
    """
    A proxy to an method inside a :class:`~simple_salesforce.Salesforce`
    client.

    Fetches and then invokes a method from a client that is checked out of
    a pool. If the method raises a `SalesforceExpiredSession` the client is
    discarded from the pool and the method is retried on a new client.

    `Salesforce` clients support querying directly with the client
    and via a "resource" attribute, so the method may be on the client
    or a resource within it. The method to invoke is fetched by calling
    :attr:`self.get_method_ref`.
    """

    def __init__(self, pool, get_method_ref):
        self.pool = pool
        self.get_method_ref = get_method_ref

    @retry(
        max_attempts=None,
        for_exceptions=simple_salesforce.SalesforceExpiredSession)
    def __call__(self, *args, **kwargs):

        with self.pool.get() as client:
            try:
                method = self.get_method_ref(client)
                return method(*args, **kwargs)
            except simple_salesforce.SalesforceExpiredSession:
                self.pool.discard(client)
                raise


class ClientAttributeProxy(MethodProxy):
    """
    A proxy to an attribute on a :class:`~simple_salesforce.Salesforce`
    client.

    Since `Salesforce` clients support querying via a "resource"
    attribute, our `__getattr__` returns a `MethodProxy` for that resource.

    Otherwise, if the attribute is a method it can be invoked directly via
    the `__call__` method inherited from :class:`MethodProxy`.
    """

    def __init__(self, attr_name, *args):
        self.attr_name = attr_name
        super().__init__(*args)

    def __getattr__(self, name):

        def get_method_ref(client):
            attr = getattr(client, self.attr_name)
            return getattr(attr, name)

        return MethodProxy(self.pool, get_method_ref)


class ClientProxy(object):
    """ A proxy to a :class:`~simple_salesforce.Salesforce` client.

    Provides passthroughs to methods and attributes of
    :class:`~simple_salesforce.Salesforce`.

    In combination with :class:`MethodProxy`, methods are invoked on
    a client that is first checked out of a pool.
    """

    def __init__(self, pool):
        self.pool = pool

    def __getattr__(self, name):

        def get_method_ref(client):
            return getattr(client, name)

        return ClientAttributeProxy(
            name, self.pool, get_method_ref
        )


class ClientPool(object):
    """ A pool of :class:`~simple_salesforce.Salesforce` clients.

    Allows callers to discard clients that are checked out of the pool,
    for example if they discover that the client's session has expired.
    """

    def __init__(self, config):
        self.config = config
        self.free = set()
        self.busy = set()

    @contextmanager
    def get(self):
        try:
            client = self.free.pop()
        except KeyError:
            client = self.create()

        self.busy.add(client)
        try:
            yield client
        finally:
            try:
                self.busy.remove(client)
                self.free.add(client)
            except KeyError:
                pass  # client was discarded

    def create(self):
        client = simple_salesforce.Salesforce(
            username=self.config['SALESFORCE']['USERNAME'],
            password=self.config['SALESFORCE']['PASSWORD'],
            security_token=self.config['SALESFORCE']['SECURITY_TOKEN'],
            sandbox=self.config['SALESFORCE']['SANDBOX'],
            version=self.config['SALESFORCE']['API_VERSION'],
        )
        return client

    def discard(self, client):
        self.busy.discard(client)