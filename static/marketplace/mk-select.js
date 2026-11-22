/* SmartPOS Marketplace - branded dropdowns.
 * Same component (markup, classes, behaviour, look) that main.js applies on the
 * dashboard pages, so every <select> looks identical across the whole system.
 * The native <select> stays in the DOM (hidden), so forms, `required`,
 * `onchange="..."` and the `form="..."` attribute keep working unchanged.
 * Opt out for a single select with class="custom-select-native". */
(function () {
    'use strict';
    if (window.__mkCustomSelect) return;
    window.__mkCustomSelect = true;

    var SVGNS = 'http://www.w3.org/2000/svg';
    var PENDING = 'select:not(.custom-select-native)';

    function svg(cls, d, sw) {
        var s = document.createElementNS(SVGNS, 'svg');
        s.setAttribute('class', cls);
        s.setAttribute('viewBox', '0 0 24 24');
        s.setAttribute('fill', 'none');
        s.setAttribute('stroke', 'currentColor');
        s.setAttribute('stroke-width', sw);
        s.setAttribute('stroke-linecap', 'round');
        s.setAttribute('stroke-linejoin', 'round');
        s.setAttribute('aria-hidden', 'true');
        var p = document.createElementNS(SVGNS, 'path');
        p.setAttribute('d', d);
        s.appendChild(p);
        return s;
    }

    function eligible(select) {
        return !select.closest('.custom-select') && select.dataset.customized !== 'true' && !select.multiple;
    }

    function enhanceAll(root) {
        (root || document).querySelectorAll(PENDING).forEach(function (select) {
            if (eligible(select)) create(select);
        });
    }

    function create(select) {
        select.dataset.customized = 'true';
        var parent = select.parentNode;
        var next = select.nextSibling;

        var wrapper = document.createElement('div');
        wrapper.className = 'custom-select custom-select-auto';
        select.className.split(/\s+/).filter(Boolean).forEach(function (cls) {
            if (cls !== 'form-input' && cls !== 'custom-select-native') wrapper.classList.add(cls);
        });

        var trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'custom-select-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.disabled = select.disabled;
        if (select.getAttribute('aria-label')) trigger.setAttribute('aria-label', select.getAttribute('aria-label'));

        var label = document.createElement('span');
        label.className = 'custom-select-label truncate';
        trigger.append(label, svg('custom-select-chevron', 'm6 9 6 6 6-6', '2'));

        var menu = document.createElement('div');
        var menuId = (select.id || select.name || 'select') + '-mk-menu-' + Math.random().toString(36).slice(2, 8);
        menu.id = menuId;
        menu.className = 'custom-select-menu';
        menu.setAttribute('role', 'listbox');
        if (select.getAttribute('aria-label')) menu.setAttribute('aria-label', select.getAttribute('aria-label'));
        trigger.setAttribute('aria-controls', menuId);

        Array.from(select.options).forEach(function (nativeOption, index) {
            var option = document.createElement('button');
            option.type = 'button';
            option.className = 'custom-option';
            option.setAttribute('role', 'option');
            option.dataset.value = nativeOption.value;
            option.dataset.index = String(index);
            option.setAttribute('aria-selected', nativeOption.selected ? 'true' : 'false');
            option.disabled = nativeOption.disabled;
            var text = document.createElement('span');
            text.className = 'truncate';
            text.textContent = nativeOption.textContent;
            option.append(text, svg('custom-option-check', 'm5 12 4 4L19 6', '2.5'));
            menu.appendChild(option);

            option.addEventListener('click', function (event) {
                event.stopPropagation();
                if (nativeOption.disabled) return;
                select.selectedIndex = index;
                sync(select, wrapper, true);
                close(wrapper);
                trigger.focus();
                select.dispatchEvent(new Event('change', { bubbles: true }));
            });
        });

        wrapper.append(select, trigger, menu);
        select.classList.add('custom-select-native');
        select.setAttribute('aria-hidden', 'true');
        select.tabIndex = -1;
        if (next) parent.insertBefore(wrapper, next); else parent.appendChild(wrapper);

        trigger.addEventListener('click', function (event) {
            event.stopPropagation();
            if (select.disabled) return;
            wrapper.classList.contains('open') ? close(wrapper) : open(wrapper);
        });
        select.addEventListener('change', function () { sync(select, wrapper, false); });
        // Reflect programmatic resets (e.g. form.reset()).
        if (select.form) select.form.addEventListener('reset', function () {
            window.setTimeout(function () { sync(select, wrapper, false); }, 0);
        });
        sync(select, wrapper, false);
    }

    function sync(select, wrapper, focusSelected) {
        var trigger = wrapper.querySelector('.custom-select-trigger');
        var label = wrapper.querySelector('.custom-select-label');
        var options = Array.from(wrapper.querySelectorAll('.custom-option'));
        var idx = select.selectedIndex;
        var selected = select.options[idx];
        label.textContent = selected ? selected.textContent : 'Select an option';
        wrapper.classList.toggle('is-placeholder', !!selected && selected.value === '');
        options.forEach(function (o, i) {
            var active = i === idx;
            o.classList.toggle('selected', active);
            o.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        wrapper.classList.toggle('is-disabled', select.disabled);
        trigger.disabled = select.disabled;
        if (focusSelected && options[idx]) options[idx].focus();
    }

    function open(wrapper) {
        document.querySelectorAll('.custom-select.open').forEach(function (o) { if (o !== wrapper) close(o); });
        wrapper.classList.add('open');
        wrapper.querySelector('.custom-select-trigger').setAttribute('aria-expanded', 'true');
        var sel = wrapper.querySelector('.custom-option.selected:not(:disabled)');
        if (sel) sel.focus({ preventScroll: false });
    }

    function close(wrapper) {
        wrapper.classList.remove('open');
        var t = wrapper.querySelector('.custom-select-trigger');
        if (t) t.setAttribute('aria-expanded', 'false');
    }

    function init() {
        enhanceAll(document);

        if (window.MutationObserver && document.body) {
            new MutationObserver(function (mutations) {
                var found = mutations.some(function (m) {
                    return Array.from(m.addedNodes).some(function (n) {
                        return n.nodeType === 1 && ((n.matches && n.matches(PENDING)) || (n.querySelector && n.querySelector(PENDING)));
                    });
                });
                if (found) window.requestAnimationFrame(function () { enhanceAll(document); });
            }).observe(document.body, { childList: true, subtree: true });
        }

        document.addEventListener('click', function (event) {
            document.querySelectorAll('.custom-select.open').forEach(function (w) {
                if (!w.contains(event.target)) close(w);
            });
        });

        document.addEventListener('keydown', function (event) {
            var inside = event.target.closest && event.target.closest('.custom-select');
            if (!inside) return;
            var wrapper = inside;
            var trigger = wrapper.querySelector('.custom-select-trigger');
            var options = Array.from(wrapper.querySelectorAll('.custom-option:not(:disabled)'));
            if (!options.length) return;

            if (event.key === 'Escape') {
                event.preventDefault(); close(wrapper); trigger.focus(); return;
            }
            if (event.target.closest('.custom-select-trigger') && (event.key === 'Enter' || event.key === ' ')) {
                event.preventDefault();
                wrapper.classList.contains('open') ? close(wrapper) : open(wrapper);
                return;
            }
            if (event.key === 'Tab' && wrapper.classList.contains('open')) { close(wrapper); return; }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (!wrapper.classList.contains('open')) open(wrapper);
                var cur = options.indexOf(document.activeElement);
                if (cur < 0) cur = options.findIndex(function (o) { return o.classList.contains('selected'); });
                var delta = event.key === 'ArrowDown' ? 1 : -1;
                options[(Math.max(0, cur) + delta + options.length) % options.length].focus();
            }
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();
})();
