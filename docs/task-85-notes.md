# Task #85: [GH#12] Add request logging middleware

---FILE: app/middleware.py---
```python
import logging
import time
from fastapi import Request, Response
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, request: Request):
        start_time = time.time()
        response = await self.app(request)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms")
        return response
```

---FILE: app/main.py---
```python
# ... existing code ...

from app.middleware import RequestLoggingMiddleware

# ... existing code ...

app.add_middleware(RequestLoggingMiddleware)

# ... existing code ...
```

Alternatively, you can also register the middleware in the `main.py` file directly without creating a separate `middleware.py` file:

---FILE: app/main.py---
```python
# ... existing code ...

logger = logging.getLogger(__name__)

class RequestLoggingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, request: Request):
        start_time = time.time()
        response = await self.app(request)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(f"{request.method} {request.url.path} {response.status_code} {duration_ms}ms")
        return response

app.add_middleware(RequestLoggingMiddleware)

# ... existing code ...
```

Both approaches will achieve the desired logging functionality. The middleware will record the request start time, log the request method, path, status code, and duration in milliseconds at the `INFO` level after the response is sent, and format the log message as specified.