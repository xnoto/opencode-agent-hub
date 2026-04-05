Fix test patches for module-level imports

The tests were patching config.ORIENTATION_RETRY_MAX/DELAY but the
sessions module imports these at module level. The patches need to
target the sessions module directly, not the config module.

Also fixed test_orient_session_skips_coordinator_by_session_id to patch
COORDINATOR_SESSION_ID in the sessions module where it's used.