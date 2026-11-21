Optional cached map

After a successful `make sample`, run `make export-map` to write
`maps/dependencies.json`. Commit that plain JSON file to reuse the exact
source-derived map in later image builds. No sample patches or test outcomes
are inputs to map generation. Do not commit a map built from a changed checkout.
