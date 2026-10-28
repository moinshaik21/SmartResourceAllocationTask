from django.contrib import admin
from .models import User, Project, Capture, Split, DailyRoster, Notification

for m in (User, Project, Capture, Split, DailyRoster, Notification):
    admin.site.register(m)
