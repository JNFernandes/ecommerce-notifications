"""HTTP layer: FastAPI routers for health checks and read-only notification queries.

Routers handle I/O only and call exactly one application service method. They contain no
business logic and expose no endpoint that creates, changes or deletes data.
"""
