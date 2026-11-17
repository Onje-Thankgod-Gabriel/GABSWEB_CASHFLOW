"""Canonical admin branding values.

These values mirror the verified Gabsweb CashFlow brand tokens. The Phase 1
visual layer consumes them through ``templates/admin`` and
``static/admin_ui`` without importing the main application styles.

Phase 2 will call :func:`apply_branding` when the explicit UserAdmin mixin is
integrated, keeping the runtime AdminSite configuration in one place.
"""

BRAND_NAME = "Gabsweb CashFlow"
BRAND_SECTION = "Administration"
BRAND_HEADER = f"{BRAND_NAME} Admin"
BRAND_PRIMARY = "#6366f1"
BRAND_VIOLET = "#8b5cf6"
BRAND_GRADIENT = f"linear-gradient(135deg, {BRAND_PRIMARY}, {BRAND_VIOLET})"
BRAND_BG_LIGHT = "#fafbfc"
BRAND_BG_DARK = "#0c0d11"


def apply_branding(admin_site):
    """Apply native AdminSite branding attributes.

    Kept side-effect free until explicitly called; Phase 1 templates already
    render the same names without touching ``core.admin``.
    """
    admin_site.site_header = BRAND_HEADER
    admin_site.site_title = BRAND_HEADER
    admin_site.index_title = "Control center"


def apply_admin_logout_redirect(admin_site):
    """Redirect Django Admin logout to the Admin login page only.

    The application keeps its global ``LOGOUT_REDIRECT_URL`` (the public
    application login). Django Admin uses a different, admin-scoped logout
    destination so an administrator stays inside the admin interface.
    """
    import types

    from django.contrib.auth.views import LogoutView
    from django.urls import reverse_lazy

    def admin_logout(self, request, extra_context=None):
        defaults = {
            "next_page": reverse_lazy("admin:login"),
            "extra_context": {
                **self.each_context(request),
                "has_permission": False,
                **(extra_context or {}),
            },
        }
        if self.logout_template is not None:
            defaults["template_name"] = self.logout_template
        request.current_app = self.name
        return LogoutView.as_view(**defaults)(request)

    admin_site.logout = types.MethodType(admin_logout, admin_site)
