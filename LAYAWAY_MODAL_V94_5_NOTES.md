# v94.5 - Layaway modal opening fix

Root cause: base.html `#app-content{animation:spPageIn ... both}` kept a transform animation in effect,
so #app-content became the containing block for position:fixed modals inside it (modal clipped under the
header and pushed below the screen on phones).

Fix: templates/base.html -> animation fill-mode changed from `both` to `backwards`.
