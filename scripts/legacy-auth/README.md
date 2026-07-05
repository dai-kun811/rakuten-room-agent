# Legacy authentication helper

These files are retained from the earlier Playwright storage-state login flow.
The supported login flow is `src/room_auth_setup.py`, which uses the persistent
Chrome profile under `.local/room-worker/chrome-profile`.

Do not commit files created under `.local/`; they contain local authentication
state and are ignored by Git.
