"""Fixed, anonymous browser events for the HISTOR desk."""

SOURCES = frozenset({"direct", "devto", "x", "bluesky", "blog", "github", "dataset", "catalog", "other", "qa"})
EVENTS = frozenset({
    "record_viewed", "check_viewed", "watch_viewed", "check_started", "check_succeeded",
    "check_repeated", "sample_started", "sample_succeeded", "watch_feed_clicked", "pilot_contact_clicked",
})
