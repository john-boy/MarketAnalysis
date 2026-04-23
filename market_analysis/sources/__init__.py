"""External-source adapters.

One module per source.  Adapters are thin: HTTP, auth, pagination,
typed return values.  They do not touch the database — services
are responsible for persistence.
"""
