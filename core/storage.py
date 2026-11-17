import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible(path="core.storage.PrivateProfileImageStorage")
class PrivateProfileImageStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None

    def url(self, name):
        raise ValueError("Private profile images are served through their owner view.")


private_profile_image_storage = PrivateProfileImageStorage()
