from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", "admin")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    ROLES = [("admin", "Admin"), ("resource", "Resource")]
    senior_for = models.TextField(blank=True, default="")   # comma separated project names this senior covers
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    role = models.CharField(max_length=10, choices=ROLES, default="resource")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return f"{self.name} <{self.email}>"


class Project(models.Model):
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "name"]

    def __str__(self):
        return self.name


class Capture(models.Model):
    NEW, ASSIGNED, PICKED, WORKING, COMPLETED, TRANSFER = "new", "assigned", "picked", "working", "completed", "transfer"
    STATUSES = [(NEW, "Unassigned"), (ASSIGNED, "Assigned"), (PICKED, "Picked"),
                (WORKING, "Working"), (COMPLETED, "Completed"), (TRANSFER, "With admin")]
    OPEN = [ASSIGNED, PICKED, WORKING]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="captures")
    name = models.CharField(max_length=200)
    priority = models.PositiveSmallIntegerField(default=2)  # 1 = high, 2 = medium, 3 = low
    status = models.CharField(max_length=12, choices=STATUSES, default=NEW)
    owner = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="owned_captures")
    total_splits = models.PositiveIntegerField(null=True, blank=True)
    sheet_row = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    picked_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    ref = models.CharField(max_length=100, blank=True, default="")   # JOB ID column of the sheet
    expanded = models.BooleanField(default=False)      # sheet row was turned into one row per split
    capture_path = models.CharField(max_length=500, blank=True, default="")   # "Capture path" column (written by captureautomate)
    captured_on = models.CharField(max_length=40, blank=True, default="")     # "Date of Capture" column
    wrapped_up = models.BooleanField(default=False)    # pipeline / remarks / sub-links submitted
    pipeline = models.CharField(max_length=100, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    sublinks_json = models.TextField(blank=True, default="")

    class Meta:
        unique_together = [("project", "name", "ref")]
        ordering = ["priority", "created_at", "id"]

    def __str__(self):
        return f"{self.project.name} / {self.name}"


class Split(models.Model):
    PENDING, WORKING, DONE = "pending", "working", "done"
    STATUSES = [(PENDING, "Pending"), (WORKING, "Working"), (DONE, "Done")]

    capture = models.ForeignKey(Capture, on_delete=models.CASCADE, related_name="splits")
    number = models.PositiveIntegerField()
    assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="splits")
    handed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")  # who handed this split over (overflow)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    row_name = models.CharField(max_length=250, blank=True, default="")   # text in the sheet's Structure column
    sheet_row = models.PositiveIntegerField(null=True, blank=True)
    orig_fill = models.CharField(max_length=12, blank=True, default="")   # row colour before we coloured it ("none" = no fill)
    named = models.BooleanField(default=False)    # Resource cell already written
    logged = models.BooleanField(default=False)   # written to the monthly night log

    class Meta:
        unique_together = [("capture", "number")]
        ordering = ["number"]


class DailyRoster(models.Model):
    date = models.DateField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="rosters")

    class Meta:
        unique_together = [("date", "user")]


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=300)
    kind = models.CharField(max_length=20, default="info")
    capture = models.ForeignKey(Capture, null=True, blank=True, on_delete=models.SET_NULL)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class AppState(models.Model):
    """Single row: the working day the data belongs to + today's sheet settings typed by the admin."""
    day = models.DateField()
    captures_url = models.CharField(max_length=1000, blank=True, default="")   # production workbook link
    captures_tab = models.CharField(max_length=100, blank=True, default="")
    start_row = models.PositiveIntegerField(null=True, blank=True)
    end_row = models.PositiveIntegerField(null=True, blank=True)
    auto_allocate = models.BooleanField(default=False)    # "Automatic" mode: new captures are allocated as they arrive
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_new = models.PositiveIntegerField(default=0)
    last_sync_error = models.CharField(max_length=300, blank=True, default="")


class Transfer(models.Model):
    """An employee sends a capture back to the admin, who hands it to a senior."""
    capture = models.ForeignKey(Capture, on_delete=models.CASCADE, related_name="transfers")
    from_user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.TextField()
    status = models.CharField(max_length=10, default="open")   # open | done
    to_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class Outbox(models.Model):
    """Log rows that could not be written to the workbook; retried before every end-of-shift wipe."""
    tab = models.CharField(max_length=50)
    row = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class MsToken(models.Model):
    """Microsoft sign-in done once from the admin dashboard (device code). The MSAL token cache is stored
    encrypted, so deploys/restarts keep the connection; MSAL refreshes it silently."""
    cache = models.TextField(blank=True, default="")
    account = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=12, default="none")   # none | connected | expired
    last_error = models.CharField(max_length=300, blank=True, default="")
    connected_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
