"""Bound the accounting upload before JSON parsing, including chunked requests."""
from starlette.responses import JSONResponse


class AnalyticsBodyLimit:
    def __init__(self, app, limit=2 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/v1/analytics-agents/'):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] != 'http.request':
                return
            body = message.get('body', b'')
            size += len(body)
            if size > self.limit:
                response = JSONResponse({'detail':'accounting payload too large'}, status_code=413,
                    headers={'Cache-Control':'no-store','Pragma':'no-cache'})
                return await response(scope, receive, send)
            chunks.append(body)
            if not message.get('more_body', False):
                break
        consumed = False
        async def bounded_receive():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {'type':'http.request','body':b''.join(chunks),'more_body':False}
        await self.app(scope, bounded_receive, send)
