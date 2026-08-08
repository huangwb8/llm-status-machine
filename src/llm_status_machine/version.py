"""Application and independently versioned data contracts."""

__version__ = "2.0.1"

STUDY_SCHEMA_VERSION = 2
TRIAL_PLAN_SCHEMA_VERSION = 2
RUN_SCHEMA_VERSION = 1
RAW_BUNDLE_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
EVALUATION_SCHEMA_VERSION = 2
ANALYSIS_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 2

# Backwards-compatible name for callers that historically meant RawBundle.
SCHEMA_VERSION = RAW_BUNDLE_SCHEMA_VERSION
