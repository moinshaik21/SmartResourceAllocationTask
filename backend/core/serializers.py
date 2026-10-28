from rest_framework import serializers

from .models import Capture, Notification, Split, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email", "role", "is_active", "senior_for"]


class SplitSerializer(serializers.ModelSerializer):
    assignee_name = serializers.CharField(source="assignee.name", default=None, read_only=True)

    class Meta:
        model = Split
        fields = ["id", "number", "status", "assignee", "assignee_name", "started_at", "completed_at", "row_name"]


class CaptureSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    owner_name = serializers.CharField(source="owner.name", default=None, read_only=True)
    splits_done = serializers.SerializerMethodField()
    helpers = serializers.SerializerMethodField()
    minutes_taken = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Capture
        fields = ["id", "project", "project_name", "name", "priority", "status", "status_label",
                  "owner", "owner_name", "total_splits", "splits_done", "helpers",
                  "assigned_at", "picked_at", "started_at", "completed_at", "minutes_taken",
                  "wrapped_up", "pipeline", "remarks", "capture_path", "captured_on"]

    def get_splits_done(self, obj):
        return sum(1 for s in obj.splits.all() if s.status == Split.DONE)

    def get_helpers(self, obj):
        seen = {}
        for s in obj.splits.all():
            if s.assignee_id and s.assignee_id != obj.owner_id:
                seen[s.assignee_id] = s.assignee.name
        return list(seen.values())

    def get_minutes_taken(self, obj):
        start = obj.picked_at
        if not start:
            return None
        end = obj.completed_at or None
        return round((end - start).total_seconds() / 60) if end else None


class CaptureDetailSerializer(CaptureSerializer):
    splits = SplitSerializer(many=True, read_only=True)

    class Meta(CaptureSerializer.Meta):
        fields = CaptureSerializer.Meta.fields + ["splits"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "message", "kind", "capture", "is_read", "created_at"]
