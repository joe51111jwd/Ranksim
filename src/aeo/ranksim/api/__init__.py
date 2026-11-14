"""The Studio JSON API, split by area. Each module has register(app, ui); studio.register calls them in this order.

session   /api/v1/session, login, logout
projects  list, create, delete, settings, import, setup, overview, report, the demo
sites     /api/v1/sites (a site in, a report out, streamed) and the pull stream
editor    page, fetch (any URL and a project's), compare, test, tests history
common    helpers every route uses (and plugin routes should use): _error, _json_body, _text, _check_header_csrf,
          _not_public, google_status, mode_of ...
"""
