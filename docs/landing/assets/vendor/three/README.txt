three@0.185.0 (MIT) — vendored so the page makes no third-party request. Files copied from the npm
tarball; addons keep their examples/jsm layout. One edit: each addon's bare `from 'three'` import is
rewritten to a relative path to three.module.min.js, because an import map is an inline script
and the production CSP (script-src 'self') blocks it.
The two build files sit directly in this folder rather than under build/: the monorepo .gitignore
and the mirror's rsync both drop any directory named build, which would publish a page with no scene.
