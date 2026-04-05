Fix session poller dict comparison error and add diagnostics

Fixed TypeError: '>' not supported between instances of 'dict' and 'dict'
in _verify_session_processing(). The OpenCode API returns time as a dict
{start, end} not an integer timestamp. Added type checking to handle both
formats.

Also added traceback logging to session_poller for better debugging.

Changes:
- Handle dict time format in session verification (lines 594-599, 631-636)
- Add traceback import and detailed error logging in daemon.py
- All 113 tests pass