# Local Tailwind build

`static/css/tailwind.css` is already built and linked from templates/base.html (V50.48).
Rebuild it whenever you add new Tailwind classes to templates/JS (run from project root):

    # download once: https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17 (standalone CLI)
    ./tailwindcss -c tools/tailwind/tailwind.config.js -i tools/tailwind/input.css -o static/css/tailwind.css --minify
    # or: npx tailwindcss@3.4.17 -c ... (same flags)

Then bump the `?v=` in base.html's <link> and run collectstatic.
`input.css` holds the custom component CSS that used to live in base.html's `<style type="text/tailwindcss">`.
Note: templates/daily_reports/report.html and expired.html (standalone public pages) still use the CDN.
