# Retired 2026-11-25: [chauffeur partner] previews

`corporatetraveldc-ccw-demo` (:8085) and `corporatetraveldc-ccw-preview1` (:8086) were password-gated nginx
previews of the [chauffeur partner] site (content under the operator's `~/demos/corporate-car-worldwide*/`).
The operator retired them on 2026-11-25 ("we can destroy/remove the CCW previews going forward").

The Quadlets are kept here as `*.container.retired`: Quadlet also reads subdirectories, so a file still named
`.container` would become a live unit again if this folder were ever copied into the live directory.
Nothing replaces them. The `nginx:alpine` image stays: the client-demo and demo-portal units use it too.
