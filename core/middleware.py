class AuthenticatedCacheControlMiddleware:
    """
    Add cache-control headers to authenticated responses to prevent
    browser from restoring protected content after logout via Back button.
    Only applies to HTML responses for authenticated users on protected routes.
    Does not affect static assets or public pages.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        return self._process_response(request, response)

    def _process_response(self, request, response):
        # Only add headers to HTML responses for authenticated users
        if (
            request.user.is_authenticated
            and hasattr(request, 'user')
            and getattr(request.user, 'is_authenticated', False)
            and response.get('Content-Type', '').startswith('text/html')
        ):
            # Prevent browser from caching authenticated pages
            # so that protected content cannot be restored after logout
            # via the Back button or browser cache.
            if 'Cache-Control' not in response.headers:
                response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            if 'Pragma' not in response.headers:
                response.headers['Pragma'] = 'no-cache'
            if 'Expires' not in response.headers:
                response.headers['Expires'] = '0'
        return response