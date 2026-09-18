from enum import StrEnum


class PostStatus(StrEnum):
    OPEN = "open"
    FULL = "full"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class MediaType(StrEnum):
    IMAGE = "image"
    AUDIO = "audio"


class JoinRequestStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RecommendationAction(StrEnum):
    IMPRESSION = "impression"
    OPEN = "open"
    INTERESTED = "interested"
    DISMISSED = "dismissed"


class ReportTargetType(StrEnum):
    POST = "post"
    MESSAGE = "message"
    USER = "user"


class ReportStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"
