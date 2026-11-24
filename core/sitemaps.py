from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class PublicPagesSitemap(Sitemap):
    """Only the public legal pages belong in the search index."""

    changefreq = "monthly"

    def items(self):
        return [
            {"name": "core:privacy_policy", "priority": 0.5},
            {"name": "core:terms", "priority": 0.5},
        ]

    def location(self, item):
        return reverse(item["name"])

    def priority(self, item):
        return item["priority"]

    def get_protocol(self, protocol=None):
        return "https"

    def get_domain(self, site=None):
        return urlsplit(settings.SITE_BASE_URL).netloc


sitemaps = {"public": PublicPagesSitemap()}
