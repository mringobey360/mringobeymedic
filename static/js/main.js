// ============================================================
// SmartPOS - shared frontend behaviors
// Toasts, button loading states, and a reusable confirm modal.
// ============================================================

// Small defensive runner: one optional UI enhancement must never break the rest of the app.
// This is especially important for sidebar navigation, mobile drawer and toggles.
function safeRun(fn) {
    if (typeof fn !== 'function') return;
    try {
        fn();
    } catch (error) {
        console.warn('[SmartPOS UI] enhancement failed:', error);
    }
}

// Run page-specific code on both the first page load and SPA content swaps.
window.onPharmacyContentReady = window.onPharmacyContentReady || function (fn) {
    if (typeof fn !== 'function') return;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fn, { once: true });
    } else {
        setTimeout(fn, 0);
    }
};

// Resolve each enhancement by name. An undefined function used to throw a
// ReferenceError while evaluating the argument (outside safeRun's try/catch),
// which silently aborted every enhancement after it (e.g. the notification bell).
document.addEventListener('DOMContentLoaded', function () {
    [
        'showDjangoMessagesAsToasts',
        'attachFormLoadingStates',
        'attachConfirmModals',
        'attachPasswordToggles',
        'attachSidebarToggle',
        'attachNestedSidebarNavigation',
        'attachSidebarCollapse',
        'attachPharmacySwitcher',
        'attachFastBranchSwitch',
        'attachAppNavigation',
        'registerServiceWorker',
        'attachInstallPrompt',
        'attachNotificationBell',
        'attachGlobalCustomSelects',
        'initConnectionManager',
    ].forEach(function (name) {
        const fn = window[name];
        if (typeof fn !== 'function') {
            console.warn('[SmartPOS UI] missing enhancement:', name);
            return;
        }
        safeRun(fn);
    });
});


// ---------- Professional connection/reconnect manager ----------
// navigator.onLine is only a local hint. We also probe the application's
// /health/ endpoint so 'internet available' and 'server available' are not
// incorrectly treated as the same state.
let pharmacyConnectionState = 'unknown';
let pharmacyReconnectTimer = null;
let pharmacyReconnectAttempt = 0;

function setConnectionState(state) {
    if (pharmacyConnectionState === state) return;
    pharmacyConnectionState = state;
    document.documentElement.dataset.connectionState = state;
    window.dispatchEvent(new CustomEvent('pharmacy:connection', { detail: { state: state } }));
    updateConnectionIndicator(state);
}

function updateConnectionIndicator(state) {
    const el = document.getElementById('pharmacy-connection-status');
    if (!el) return;
    const labels = {
        online: 'Online',
        reconnecting: 'Reconnecting…',
        offline: 'No internet connection',
        server_unavailable: 'Server unavailable',
    };
    el.textContent = labels[state] || '';
    // Stay silent when the system is healthy; only surface degraded connectivity.
    el.hidden = !labels[state] || state === 'online';
    el.dataset.state = state;
}

async function checkPharmacyBackend() {
    if (!navigator.onLine) {
        setConnectionState('offline');
        return false;
    }
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 7000);
        const response = await fetch('/health/?client_check=1', {
            method: 'GET',
            cache: 'no-store',
            credentials: 'same-origin',
            signal: controller.signal,
            headers: { 'X-Pharmacy-Health-Check': '1' },
        });
        clearTimeout(timeout);
        if (response.ok) {
            setConnectionState('online');
            return true;
        }
        setConnectionState('server_unavailable');
        return false;
    } catch (error) {
        setConnectionState(navigator.onLine ? 'server_unavailable' : 'offline');
        return false;
    }
}

function schedulePharmacyReconnect() {
    if (pharmacyReconnectTimer) return;
    const delays = [2000, 5000, 10000, 20000, 30000];
    const delay = delays[Math.min(pharmacyReconnectAttempt, delays.length - 1)];
    pharmacyReconnectAttempt += 1;
    pharmacyReconnectTimer = setTimeout(async function () {
        pharmacyReconnectTimer = null;
        const ok = await checkPharmacyBackend();
        if (ok) {
            pharmacyReconnectAttempt = 0;
            // Notify pages/components that may need to refresh read-only data.
            window.dispatchEvent(new CustomEvent('pharmacy:connection-restored'));
        } else {
            schedulePharmacyReconnect();
        }
    }, delay);
}

function initConnectionManager() {
    // Initial probe is intentionally delayed a little so it does not compete
    // with the page's first render.
    setTimeout(async function () {
        const ok = await checkPharmacyBackend();
        if (!ok) schedulePharmacyReconnect();
    }, 350);

    window.addEventListener('offline', function () {
        setConnectionState('offline');
        pharmacyReconnectAttempt = 0;
        schedulePharmacyReconnect();
    });

    window.addEventListener('online', async function () {
        setConnectionState('reconnecting');
        pharmacyReconnectAttempt = 0;
        const ok = await checkPharmacyBackend();
        if (!ok) schedulePharmacyReconnect();
        else window.dispatchEvent(new CustomEvent('pharmacy:connection-restored'));
    });

    // If a normal fetch/XHR fails elsewhere in the app, components can call
    // this helper instead of forcing the whole application into Offline mode.
    window.pharmacyConnectionCheck = checkPharmacyBackend;
}



// ---------- Global professional dropdowns ----------
// Converts every native <select> outside an already-custom dropdown into the
// same branded dropdown used by the POS/Cart payment selector.
function attachGlobalCustomSelects() {
    document.querySelectorAll('select:not(.custom-select-native)').forEach(function (select) {
        if (select.closest('.custom-select')) return;
        if (select.dataset.customized === 'true') return;
        if (select.multiple) return; // Keep native multi-selects accessible.
        createProfessionalSelect(select);
    });

    // Watch dynamically-rendered sheets/modals (including barcode quick-register).
    // The scan form is injected after the page has loaded, so a one-time initial
    // scan alone leaves its selects native. Re-enhance added content safely.
    if (!window.__pharmacyCustomSelectObserver && window.MutationObserver && document.body) {
        window.__pharmacyCustomSelectObserver = new MutationObserver(function (mutations) {
            let found = false;
            mutations.forEach(function (mutation) {
                mutation.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if ((node.matches && node.matches('select:not(.custom-select-native)')) ||
                        (node.querySelector && node.querySelector('select:not(.custom-select-native)'))) {
                        found = true;
                    }
                });
            });
            if (found) {
                window.requestAnimationFrame(function () {
                    document.querySelectorAll('select:not(.custom-select-native)').forEach(function (select) {
                        if (select.closest('.custom-select') || select.dataset.customized === 'true' || select.multiple) return;
                        createProfessionalSelect(select);
                    });
                });
            }
        });
        window.__pharmacyCustomSelectObserver.observe(document.body, { childList: true, subtree: true });
    }

    // One delegated outside-click handler keeps all dropdowns synchronized.
    if (window.__pharmacyCustomSelectDocumentHandler) return;
    window.__pharmacyCustomSelectDocumentHandler = true;
    document.addEventListener('click', function (event) {
        document.querySelectorAll('.custom-select.open').forEach(function (wrapper) {
            if (!wrapper.contains(event.target)) closeProfessionalSelect(wrapper);
        });
    });

    document.addEventListener('keydown', function (event) {
        const trigger = event.target.closest('.custom-select-trigger');
        if (!trigger) return;
        const wrapper = trigger.closest('.custom-select');
        if (!wrapper) return;
        const options = Array.from(wrapper.querySelectorAll('.custom-option:not(:disabled)'));
        if (!options.length) return;

        if (event.key === 'Escape') {
            event.preventDefault();
            closeProfessionalSelect(wrapper);
            trigger.focus();
            return;
        }
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            wrapper.classList.contains('open') ? closeProfessionalSelect(wrapper) : openProfessionalSelect(wrapper);
            return;
        }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!wrapper.classList.contains('open')) openProfessionalSelect(wrapper);
            const current = options.findIndex(function (o) { return o.classList.contains('selected'); });
            const delta = event.key === 'ArrowDown' ? 1 : -1;
            const next = options[(Math.max(0, current) + delta + options.length) % options.length];
            next.focus();
        }
    });
}

function createProfessionalSelect(select) {
    select.dataset.customized = 'true';

    const originalParent = select.parentNode;
    const originalNextSibling = select.nextSibling;
    const wrapper = document.createElement('div');
    wrapper.className = 'custom-select custom-select-auto';
    // Preserve useful layout classes such as max-w-* while replacing the
    // native .form-input styling with the shared dropdown styling.
    select.className.split(/\s+/).filter(Boolean).forEach(function (cls) {
        if (cls !== 'form-input' && cls !== 'custom-select-native') wrapper.classList.add(cls);
    });

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'custom-select-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.disabled = select.disabled;

    const label = document.createElement('span');
    label.className = 'custom-select-label truncate';
    const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevron.setAttribute('class', 'custom-select-chevron');
    chevron.setAttribute('viewBox', '0 0 24 24');
    chevron.setAttribute('fill', 'none');
    chevron.setAttribute('stroke', 'currentColor');
    chevron.setAttribute('stroke-width', '2');
    chevron.setAttribute('stroke-linecap', 'round');
    chevron.setAttribute('stroke-linejoin', 'round');
    chevron.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', 'm6 9 6 6 6-6');
    chevron.appendChild(path);
    trigger.append(label, chevron);

    const menu = document.createElement('div');
    const menuId = (select.id || 'select') + '-professional-menu-' + Math.random().toString(36).slice(2, 8);
    menu.id = menuId;
    menu.className = 'custom-select-menu';
    menu.setAttribute('role', 'listbox');
    if (select.getAttribute('aria-label')) menu.setAttribute('aria-label', select.getAttribute('aria-label'));
    trigger.setAttribute('aria-controls', menuId);

    Array.from(select.options).forEach(function (nativeOption, index) {
        const option = document.createElement('button');
        option.type = 'button';
        option.className = 'custom-option';
        option.setAttribute('role', 'option');
        option.dataset.value = nativeOption.value;
        option.dataset.index = String(index);
        option.setAttribute('aria-selected', nativeOption.selected ? 'true' : 'false');
        option.disabled = nativeOption.disabled;

        const text = document.createElement('span');
        text.className = 'truncate';
        text.textContent = nativeOption.textContent;
        const check = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        check.setAttribute('class', 'custom-option-check');
        check.setAttribute('viewBox', '0 0 24 24');
        check.setAttribute('fill', 'none');
        check.setAttribute('stroke', 'currentColor');
        check.setAttribute('stroke-width', '2.5');
        check.setAttribute('stroke-linecap', 'round');
        check.setAttribute('stroke-linejoin', 'round');
        check.setAttribute('aria-hidden', 'true');
        const checkPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        checkPath.setAttribute('d', 'm5 12 4 4L19 6');
        check.appendChild(checkPath);
        option.append(text, check);
        menu.appendChild(option);

        option.addEventListener('click', function (event) {
            event.stopPropagation();
            if (nativeOption.disabled) return;
            select.selectedIndex = index;
            syncProfessionalSelect(select, wrapper, true);
            closeProfessionalSelect(wrapper);
            trigger.focus();
            select.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });

    wrapper.append(select, trigger, menu);
    select.classList.add('custom-select-native');
    select.setAttribute('aria-hidden', 'true');
    select.tabIndex = -1;
    if (originalNextSibling) originalParent.insertBefore(wrapper, originalNextSibling);
    else originalParent.appendChild(wrapper);

    trigger.addEventListener('click', function (event) {
        event.stopPropagation();
        if (select.disabled) return;
        wrapper.classList.contains('open') ? closeProfessionalSelect(wrapper) : openProfessionalSelect(wrapper);
    });

    select.addEventListener('change', function () {
        syncProfessionalSelect(select, wrapper, false);
    });
    syncProfessionalSelect(select, wrapper, false);
}

function syncProfessionalSelect(select, wrapper, focusSelected) {
    const trigger = wrapper.querySelector('.custom-select-trigger');
    const label = wrapper.querySelector('.custom-select-label');
    const options = Array.from(wrapper.querySelectorAll('.custom-option'));
    const selectedIndex = select.selectedIndex;
    const selected = select.options[selectedIndex];
    label.textContent = selected ? selected.textContent : 'Select an option';
    options.forEach(function (option, index) {
        const active = index === selectedIndex;
        option.classList.toggle('selected', active);
        option.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    wrapper.classList.toggle('is-disabled', select.disabled);
    trigger.disabled = select.disabled;
    if (focusSelected && options[selectedIndex]) options[selectedIndex].focus();
}

function openProfessionalSelect(wrapper) {
    document.querySelectorAll('.custom-select.open').forEach(function (other) {
        if (other !== wrapper) closeProfessionalSelect(other);
    });
    wrapper.classList.add('open');
    const trigger = wrapper.querySelector('.custom-select-trigger');
    trigger.setAttribute('aria-expanded', 'true');
    const selected = wrapper.querySelector('.custom-option.selected:not(:disabled)');
    if (selected) selected.focus();
}

function closeProfessionalSelect(wrapper) {
    wrapper.classList.remove('open');
    const trigger = wrapper.querySelector('.custom-select-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
}

// ---------- CSRF helper (supports HttpOnly csrftoken via the DOM token) ----------
function getCookie(name) {
    const match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    if (match) return decodeURIComponent(match[2]);
    if (name === 'csrftoken') {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content && meta.content !== 'NOTPROVIDED') return meta.content;
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) return input.value;
    }
    return '';
}


// ---------- Launch splash (same SmartPOS splash used at sign-in) ----------
// Shown once per session (inline script in base.html starts it; class "sp-no-launch" skips it).
// Stays a short minimum so it never flickers, completes to 100% and fades out once the page
// has loaded. The timeout is a hard safety net: it can never block the user.
const SP_LAUNCH_MIN_MS = 600;
const SP_LAUNCH_MAX_MS = 3000;
const spLaunchStart = Date.now();
let spLaunchClosing = false;
function dismissSmartPOSLaunchLoader(force) {
    const splash = window.__spLaunch;
    if (!splash || spLaunchClosing) return;
    spLaunchClosing = true;
    const wait = force === true ? 0 : Math.max(0, SP_LAUNCH_MIN_MS - (Date.now() - spLaunchStart));
    window.setTimeout(() => {
        splash.finish();
        window.setTimeout(() => splash.fadeOut(), 400);
    }, wait);
}
if (window.__spLaunch) {
    window.setTimeout(() => dismissSmartPOSLaunchLoader(true), SP_LAUNCH_MAX_MS);
    if (document.readyState === 'complete') dismissSmartPOSLaunchLoader();
    else window.addEventListener('load', () => dismissSmartPOSLaunchLoader(), {once:true});
}

// ---------- Sign-in handoff: the login splash continues, completes, then fades straight into the dashboard ----------
(function initSignInHandoff() {
    const root = document.documentElement;
    const splash = window.__spHandoff;
    if (!root.classList.contains('sp-handoff') || !splash) { root.classList.remove('sp-handoff'); return; }
    const MAX_MS = 6000;
    let done = false;
    function release() {
        if (done) return; done = true;
        splash.finish();                                   // 100% · "Your pharmacy is ready"
        window.setTimeout(() => {
            splash.fadeOut();
            window.setTimeout(() => root.classList.remove('sp-handoff'), 600);
        }, 450);
    }
    function ready() {
        const fontsReady = (document.fonts && document.fonts.ready) ? document.fonts.ready : Promise.resolve();
        fontsReady.then(release);
    }
    window.setTimeout(release, MAX_MS);                    // safety net: never block the user
    if (document.readyState === 'complete') ready(); else window.addEventListener('load', ready, {once:true});
})();

// ---------- In-app navigation feedback (thin top bar, delayed) ----------
// Replaces the full-screen splash for every page change inside the account. The bar only
// appears if the next page takes longer than 150 ms, so fast pages show no loader at all.
(function initSmartPOSNavFeedback() {
    let timer = null;
    function start() {
        if (timer) return;
        timer = window.setTimeout(function () {
            showAppNavigationProgress(true);
            document.documentElement.classList.add('sp-navigating');
            // Safety: downloads / target-less responses never unload the page, so don't hang.
            window.setTimeout(stop, 12000);
        }, 150);
    }
    function stop() {
        if (timer) { window.clearTimeout(timer); timer = null; }
        showAppNavigationProgress(false);
        document.documentElement.classList.remove('sp-navigating');
    }
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        const a = e.target.closest ? e.target.closest('a[href]') : null;
        if (!a || a.hasAttribute('download') || a.hasAttribute('data-confirm') || a.dataset.noProgress === 'true') return;
        if (a.target && a.target !== '_self') return;
        const href = a.getAttribute('href') || '';
        if (!href || href.startsWith('#') || /^(javascript:|mailto:|tel:|sms:|blob:)/i.test(href)) return;
        let u; try { u = new URL(href, window.location.href); } catch (err) { return; }
        if (u.origin !== window.location.origin) return;
        if (u.pathname === window.location.pathname && u.search === window.location.search) return;
        // Let other handlers (confirm modals, SPA code) run first; only start if still navigating.
        window.setTimeout(function () { if (!e.defaultPrevented) start(); }, 0);
    });
    document.addEventListener('submit', function (e) {
        const f = e.target;
        if (!(f instanceof HTMLFormElement) || f.hasAttribute('data-no-global-loading') || f.target === '_blank') return;
        window.setTimeout(function () { if (!e.defaultPrevented) start(); }, 0);
    });
    // Back/forward cache restore, or navigation that ended without unloading (downloads, errors).
    window.addEventListener('pageshow', stop);
    window.addEventListener('pagehide', function () { if (timer) { window.clearTimeout(timer); timer = null; } });
    window.addEventListener('beforeunload', function () { /* bar keeps running until the new page paints */ });
})();

// ---------- PWA service worker ----------
// SmartPOS is a PWA. The "Install App" buttons are wired in attachInstallPrompt().
function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
}

// ---------- Nested sidebar navigation ----------
const NAV_GROUP_STATE_KEY = 'pharmacy_manager_nav_groups_v2';

function attachNestedSidebarNavigation() {
    const groups = Array.from(document.querySelectorAll('#sidebar .nav-group'));
    if (!groups.length) return;

    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(NAV_GROUP_STATE_KEY) || '{}') || {}; } catch (_) {}

    function setGroup(group, open, persist = true) {
        group.classList.toggle('open', open);
        const btn = group.querySelector(':scope > .nav-group-toggle');
        if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (persist) {
            const key = group.dataset.navGroup;
            if (key) { saved[key] = open; try { localStorage.setItem(NAV_GROUP_STATE_KEY, JSON.stringify(saved)); } catch (_) {} }
        }
    }

    groups.forEach(function (group) {
        const btn = group.querySelector(':scope > .nav-group-toggle');
        if (!btn || btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', function () {
            const willOpen = !group.classList.contains('open');
            // Keep the sidebar visually calm: only one top-level group is open at once.
            if (willOpen) groups.forEach(function (other) { if (other !== group) setGroup(other, false, false); });
            setGroup(group, willOpen, true);
            // Opened groups near the bottom (e.g. Management) must scroll into view, otherwise
            // the sub-links open below the visible area and it looks like nothing happened.
            if (willOpen) {
                const reveal = function () {
                    try { group.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (_) { group.scrollIntoView(); }
                };
                if (window.requestAnimationFrame) window.requestAnimationFrame(reveal); else reveal();
            }
        });

        const hasActive = !!group.querySelector('.nav-sublink.active, .nav-subgroup-toggle.is-active');
        const key = group.dataset.navGroup;
        setGroup(group, hasActive || saved[key] === true, false);
    });

    document.querySelectorAll('#sidebar .nav-subgroup').forEach(function (subgroup) {
        const btn = subgroup.querySelector(':scope > .nav-subgroup-toggle');
        if (!btn || btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        const key = 'sub_' + (subgroup.dataset.navSubgroup || 'default');
        const hasActive = !!subgroup.querySelector('.nav-sublink.active') || btn.classList.contains('is-active');
        let open = hasActive;
        try { open = saved[key] === true || hasActive; } catch (_) {}
        function apply(v, persist = true) {
            subgroup.classList.toggle('open', v);
            btn.setAttribute('aria-expanded', v ? 'true' : 'false');
            if (persist) { saved[key] = v; try { localStorage.setItem(NAV_GROUP_STATE_KEY, JSON.stringify(saved)); } catch (_) {} }
        }
        apply(open, false);
        btn.addEventListener('click', function () { apply(!subgroup.classList.contains('open')); });
    });
}

// ---------- Desktop sidebar collapse ----------
const SIDEBAR_COLLAPSE_KEY = 'pharmacy_manager_sidebar_collapsed';

// ---------- PWA install prompt ----------
// Any element with [data-pwa-install] becomes an "Install App" button.
function attachInstallPrompt() {
    const isStandalone = function () {
        return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
               window.navigator.standalone === true;
    };
    const buttons = function () { return document.querySelectorAll('[data-pwa-install]'); };
    const refresh = function () {
        buttons().forEach(function (el) { el.classList.toggle('hidden', isStandalone()); });
    };
    refresh();

    if (window.__pharmacyInstallBound) return;
    window.__pharmacyInstallBound = true;

    window.addEventListener('beforeinstallprompt', function (event) {
        event.preventDefault();
        window.pharmacyInstallPrompt = event;
        refresh();
    });
    window.addEventListener('appinstalled', function () {
        window.pharmacyInstallPrompt = null;
        refresh();
        if (typeof showToast === 'function') showToast('SmartPOS is now installed on this device.', 'success', {title:'App installed', duration:5000});
    });

    function closeInstallGuide() {
        const modal = document.getElementById('sp-install-guide');
        if (modal) modal.remove();
    }
    function openInstallGuide(kind) {
        closeInstallGuide();
        const isIOS = kind === 'ios';
        const title = isIOS ? 'Install SmartPOS on iPhone' : 'Install SmartPOS as an app';
        const body = isIOS
            ? '<p class="sp-install-lead">SmartPOS works as a secure Home Screen web app on iPhone and iPad.</p><ol class="sp-install-steps"><li><b>Open this page in Safari.</b></li><li>Tap the <b>Share</b> button.</li><li>Choose <b>Add to Home Screen</b>.</li><li>Turn on <b>Open as Web App</b>, then tap <b>Add</b>.</li></ol><div class="sp-install-note">After installation, open SmartPOS from the Home Screen for the full app experience and notifications.</div>'
            : '<p class="sp-install-lead">Install SmartPOS for a faster, app-like experience with its own Home Screen icon.</p><ol class="sp-install-steps"><li>Use the browser menu or the install prompt.</li><li>Select <b>Install app</b> or <b>Add to Home screen</b>.</li><li>Confirm the installation.</li></ol><div class="sp-install-note">The same SmartPOS account and business data remain online and protected by your normal login and security controls.</div>';
        const modal=document.createElement('div');
        modal.id='sp-install-guide';
        modal.innerHTML='<div class="sp-install-backdrop" data-install-close></div><section class="sp-install-dialog" role="dialog" aria-modal="true" aria-labelledby="sp-install-title"><button type="button" class="sp-install-close" data-install-close aria-label="Close">&times;</button><div class="sp-install-icon"><img src="/static/img/icons/icon-192.png?v=3" alt="SmartPOS"></div><div class="sp-install-kicker">SMARTPOS APP</div><h2 id="sp-install-title">'+title+'</h2>'+body+'<button type="button" class="sp-install-done" data-install-close>Got it</button></section>';
        document.body.appendChild(modal);
        modal.querySelectorAll('[data-install-close]').forEach(function(el){el.addEventListener('click',closeInstallGuide);});
        modal.addEventListener('keydown',function(e){if(e.key==='Escape')closeInstallGuide();});
        const done=modal.querySelector('.sp-install-done'); if(done) done.focus();
    }
    function notify(text) {
        if (typeof showToast === 'function') showToast(text, 'info', { title: 'Install SmartPOS', duration: 9000 });
        else window.alert(text);
    }

    window.installSmartPOS = async function () {
        const deferred = window.pharmacyInstallPrompt;
        if (deferred) {
            deferred.prompt();
            try { await deferred.userChoice; } catch (_) {}
            window.pharmacyInstallPrompt = null;
            return;
        }
        const ua = window.navigator.userAgent || '';
        if (/iphone|ipad|ipod/i.test(ua)) {
            openInstallGuide('ios');
            return;
        }
        if (/android/i.test(ua)) {
            openInstallGuide('android');
            return;
        }
        openInstallGuide('desktop');
    };

    document.addEventListener('click', function (event) {
        const el = event.target.closest && event.target.closest('[data-pwa-install]');
        if (!el) return;
        event.preventDefault();
        window.installSmartPOS();
    });
}


// ---------- Sidebar branch switcher dropdown ----------
function attachPharmacySwitcher() {
    const btn = document.getElementById('pharmacy-switcher-btn');
    const menu = document.getElementById('pharmacy-switcher-menu');
    if (!btn || !menu) return;
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';

    btn.setAttribute('aria-haspopup', 'true');
    btn.setAttribute('aria-expanded', 'false');

    const setOpen = (open) => {
        menu.classList.toggle('hidden', !open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    };

    btn.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        setOpen(menu.classList.contains('hidden'));
    });

    // Clicks inside the menu (branch buttons, "Manage branches") must work normally.
    menu.addEventListener('click', function (event) { event.stopPropagation(); });

    document.addEventListener('click', function () { setOpen(false); });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') setOpen(false);
    });
}

function attachSidebarCollapse() {
    const sidebar = document.getElementById('sidebar');
    const btn = document.getElementById('sidebar-collapse-btn');
    if (!sidebar || !btn) return;

    const apply = (collapsed) => {
        sidebar.classList.toggle('sidebar-collapsed', collapsed);
        btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        btn.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
        btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    };

    let collapsed = false;
    try { collapsed = localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === '1'; } catch (_) {}
    apply(collapsed);

    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', function () {
        collapsed = !sidebar.classList.contains('sidebar-collapsed');
        apply(collapsed);
        try { localStorage.setItem(SIDEBAR_COLLAPSE_KEY, collapsed ? '1' : '0'); } catch (_) {}
    });
}

// ---------- Sidebar SPA navigation ----------
let pharmacyNavigationBusy = false;

function normalizePath(path) {
    try {
        const u = new URL(path, window.location.origin);
        let p = u.pathname || '/';
        if (p.length > 1) p = p.replace(/\/+$/, '');
        return p || '/';
    } catch (_) {
        return path || '/';
    }
}

// Mobile bottom bar: highlight the best-matching quick link (it stays visible on every page, POS included).
function updateBottomNavState(path) {
    const nav = document.getElementById('sp-bottom-nav');
    if (!nav) return;
    const current = normalizePath(path);
    let best = null;
    let bestLen = -1;
    nav.querySelectorAll('a.bn-item').forEach(function (a) {
        a.classList.remove('active');
        const own = normalizePath(a.dataset.bnPath || '');
        const group = a.dataset.bnGroup || '';
        let len = -1;
        if (a.dataset.bnExact === '1') { if (current === own) len = own.length + 1; }
        else if (current === own || current.startsWith(own + '/')) len = own.length + 1;
        else if (group && current.startsWith(group)) len = group.length;
        if (a.classList.contains('bn-sell') && current.indexOf('/sales/pos') !== 0) len = -1;
        if (len > bestLen) { best = a; bestLen = len; }
    });
    if (best) best.classList.add('active');
    nav.querySelectorAll('a.bn-item').forEach(function (a) {
        if (a.classList.contains('active')) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    // No quick link matches: the person is on a page that lives under "More", so tint it as the current section.
    const more = document.getElementById('sp-bn-more');
    if (more) more.classList.toggle('is-current', !best);
    placeBottomNavGlider();
}

// The sliding pill behind the active bottom-bar icon.
function placeBottomNavGlider() {
    const nav = document.getElementById('sp-bottom-nav');
    const glider = nav && nav.querySelector('.bn-glider');
    if (!glider) return;
    const item = nav.querySelector('a.bn-item.active:not(.bn-sell)');
    if (!item || !nav.offsetWidth) { glider.style.setProperty('--go', '0'); return; }
    const ico = item.querySelector('.bn-ico');
    const nr = nav.getBoundingClientRect();
    const ir = ico.getBoundingClientRect();
    const gw = glider.offsetWidth, gh = glider.offsetHeight;
    glider.style.setProperty('--gx', (ir.left - nr.left - nav.clientLeft + ir.width / 2 - gw / 2) + 'px');
    glider.style.setProperty('--gy', (ir.top - nr.top - nav.clientTop + ir.height / 2 - gh / 2) + 'px');
    glider.style.setProperty('--go', '1');
}

function updateSidebarActiveState(path) {
    updateBottomNavState(path);
    const current = normalizePath(path);
    const links = Array.from(document.querySelectorAll('#sidebar nav a[href]'));
    let best = null;
    let bestLength = -1;

    links.forEach(function (link) {
        link.classList.remove('active');
        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
        let linkPath;
        try { linkPath = normalizePath(new URL(href, window.location.origin).pathname); } catch (_) { return; }
        const exact = current === linkPath;
        const prefix = linkPath !== '/' && current.startsWith(linkPath + '/');
        if ((exact || prefix) && linkPath.length > bestLength) {
            best = link;
            bestLength = linkPath.length;
        }
    });
    if (best) best.classList.add('active');

    // Keep the nested hierarchy open when SPA navigation changes the page.
    document.querySelectorAll('#sidebar .nav-group').forEach(function (group) {
        const hasActive = !!group.querySelector('.nav-sublink.active, .nav-subgroup-toggle.is-active');
        if (hasActive) {
            group.classList.add('open');
            const btn = group.querySelector(':scope > .nav-group-toggle');
            if (btn) btn.setAttribute('aria-expanded', 'true');
        }
    });
    document.querySelectorAll('#sidebar .nav-subgroup').forEach(function (subgroup) {
        const hasActive = !!subgroup.querySelector('.nav-sublink.active');
        if (hasActive) {
            subgroup.classList.add('open');
            const btn = subgroup.querySelector(':scope > .nav-subgroup-toggle');
            if (btn) btn.setAttribute('aria-expanded', 'true');
        }
    });
}

function showAppNavigationProgress(show) {
    let el = document.getElementById('app-navigation-progress');
    if (show) {
        if (!el) {
            el = document.createElement('div');
            el.id = 'app-navigation-progress';
            el.className = 'app-navigation-progress';
            document.body.appendChild(el);
        }
    } else if (el) {
        el.remove();
    }
}

async function executePageScripts(doc, responseUrl) {
    const scripts = Array.from(doc.querySelectorAll('script'));
    for (const script of scripts) {
        // Shell scripts (header menu, modals, idle lock, feedback...) already ran when the
        // page first loaded. Re-running them on every sidebar navigation stacked duplicate
        // listeners (e.g. the account menu opened and closed in the same click).
        if (script.hasAttribute('data-spa-shell')) continue;
        const src = script.getAttribute('src');
        if (src) {
            // Do not reload global application scripts on every navigation.
            if (/tailwind|main\.js|assistant/i.test(src)) continue;
            const absolute = new URL(src, responseUrl).href;
            if (Array.from(document.scripts).some(function (s) { return s.dataset.spaSrc === absolute; })) continue;
            await new Promise(function (resolve) {
                const s = document.createElement('script');
                s.src = absolute;
                s.async = false;
                s.dataset.spaSrc = absolute;
                s.onload = s.onerror = resolve;
                document.body.appendChild(s);
            });
        } else if (script.textContent.trim()) {
            try {
                // Function scope prevents page-level const/let declarations from
                // colliding when a user revisits a section.
                new Function(script.textContent)();
            } catch (err) {
                console.error('SmartPOS page script error:', err);
            }
        }
    }
}

async function navigateAppContent(url, pushState, init) {
    if (pharmacyNavigationBusy) return false;
    const target = new URL(url, window.location.origin);
    if (target.origin !== window.location.origin) return;
    if (target.pathname.startsWith('/admin/')) {
        window.location.href = target.href;
        return;
    }

    const main = document.getElementById('app-content');
    if (!main) {
        window.location.href = target.href;
        return;
    }

    pharmacyNavigationBusy = true;
    showAppNavigationProgress(true);
    main.classList.add('app-content-loading');

    try {
        const response = await fetch(target.href, {
            method: (init && init.method) || 'GET',
            body: (init && init.body) || undefined,
            credentials: 'same-origin',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-Pharmacy-Partial': '1',
                'Accept': 'text/html'
            },
            cache: 'no-store'
        });
        // A POST (e.g. branch switch) is answered with a redirect that fetch follows,
        // so the page we actually received may live at a different URL.
        let finalUrl = target;
        try { finalUrl = new URL(response.url || target.href); } catch (_) {}

        if (!response.ok) throw new Error('Navigation failed: ' + response.status);
        const html = await response.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const nextMain = doc.querySelector('#app-content') || doc.querySelector('main');
        if (!nextMain) throw new Error('The destination page has no application content.');

        // Replace page-scoped head styles as part of the SPA swap. Without this,
        // a page opened from the sidebar would get its HTML but not its dedicated
        // responsive styling. Only styles explicitly marked as SPA page styles
        // are touched; the global application CSS remains intact.
        // 1) Remove the page styles of the page we are leaving.
        document.querySelectorAll('style[data-spa-page-style]').forEach(function (style) { style.remove(); });
        // 2) Add the destination page styles. Styles explicitly marked with data-spa-page-style
        //    are always copied. Any OTHER <style>/<link rel=stylesheet> the destination page puts
        //    in its <head> (block extra_head) is copied too unless the shell already has it, so a
        //    page can never lose its layout just because the developer forgot the marker.
        var knownCss = {};
        document.querySelectorAll('head style').forEach(function (st) { knownCss[st.textContent.trim()] = true; });
        var knownLinks = {};
        document.querySelectorAll('head link[rel="stylesheet"]').forEach(function (ln) { knownLinks[ln.href] = true; });
        doc.head.querySelectorAll('style').forEach(function (style) {
            var css = style.textContent.trim();
            var marked = style.hasAttribute('data-spa-page-style');
            if (!css || (!marked && knownCss[css])) return;
            var copy = style.cloneNode(true);
            if (!marked) copy.setAttribute('data-spa-page-style', 'auto');
            document.head.appendChild(copy);
            knownCss[css] = true;
        });
        doc.head.querySelectorAll('link[rel="stylesheet"]').forEach(function (ln) {
            var abs; try { abs = new URL(ln.getAttribute('href'), response.url).href; } catch (_) { return; }
            if (knownLinks[abs]) return;
            var copy = document.createElement('link');
            copy.rel = 'stylesheet'; copy.href = abs; copy.setAttribute('data-spa-page-style', 'auto-link');
            document.head.appendChild(copy);
            knownLinks[abs] = true;
        });

        main.innerHTML = nextMain.innerHTML;
        main.setAttribute('data-page-url', finalUrl.pathname + finalUrl.search);
        if (doc.title) document.title = doc.title;
        safeRun(function () { syncShellFromDoc(doc); });

        if (pushState) history.pushState({ pharmacyApp: true }, '', finalUrl.href);
        updateSidebarActiveState(finalUrl.pathname);
        window.scrollTo({ top: 0, behavior: 'smooth' });

        // Re-run page-specific scripts and refresh global enhancements for
        // controls inserted into the new content.
        await executePageScripts(doc, response.url);
        safeRun(attachPasswordToggles);
        safeRun(attachGlobalCustomSelects);
        safeRun(attachFormLoadingStates);
        window.dispatchEvent(new CustomEvent('pharmacy:content-loaded', {
            detail: { url: finalUrl.href, main: main }
        }));
        if (init && init.showMessages) safeRun(window.showDjangoMessagesAsToasts);
    } catch (error) {
        console.error('SmartPOS navigation error:', error);
        if (init && typeof init.fallback === 'function') init.fallback();
        else window.location.href = target.href;
        return false;
    } finally {
        main.classList.remove('app-content-loading');
        showAppNavigationProgress(false);
        pharmacyNavigationBusy = false;
    }

    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (window.matchMedia('(max-width: 1023px)').matches && sidebar && backdrop) {
        sidebar.classList.add('-translate-x-full');
        backdrop.classList.add('hidden');
    }
    return true;
}

// Keep the persistent shell in step with the new page: the sidebar branch panel
// and the subscription banner both depend on the ACTIVE BRANCH, so they must
// follow a branch switch without a full reload.
function syncShellFromDoc(doc) {
    const curPanel = document.querySelector('#sidebar .sidebar-branch-panel');
    const newPanel = doc.querySelector('#sidebar .sidebar-branch-panel');
    if (curPanel && newPanel && curPanel.innerHTML.trim() !== newPanel.innerHTML.trim()) {
        curPanel.innerHTML = newPanel.innerHTML;
        safeRun(attachPharmacySwitcher);
    }
    const curSlot = document.getElementById('sp-banner-slot');
    const newSlot = doc.getElementById('sp-banner-slot');
    if (curSlot && newSlot && curSlot.innerHTML.trim() !== newSlot.innerHTML.trim()) {
        curSlot.innerHTML = newSlot.innerHTML; // its inline script is re-run by executePageScripts
    }
}

// ---------- Fast branch switching (no full page reload) ----------
// POST the switch, follow the redirect, and swap only the page content + the
// branch-aware parts of the shell. Falls back to the normal form post on any failure.
function attachFastBranchSwitch() {
    if (window.__spFastBranchSwitch) return;
    window.__spFastBranchSwitch = true;
    document.addEventListener('submit', function (event) {
        const form = event.target;
        if (!(form instanceof HTMLFormElement) || form.dataset.branchSwitch !== 'true') return;
        if (!window.fetch || !document.getElementById('app-content')) return; // native submit
        event.preventDefault();

        const btn = form.querySelector('button[type="submit"]');
        const label = btn ? btn.textContent.replace(/\s+/g, ' ').trim() : '';
        const menu = document.getElementById('pharmacy-switcher-menu');
        const nameEl = document.querySelector('#pharmacy-switcher-btn .truncate');
        const previous = nameEl ? nameEl.textContent : '';
        if (menu) menu.classList.add('hidden');
        if (nameEl && label) nameEl.textContent = label;        // instant feedback

        const restore = function () {
            form.querySelectorAll('button[type="submit"]').forEach(function (b) {
                b.disabled = false; b.removeAttribute('aria-busy');
                if (b.dataset.originalHtml) b.innerHTML = b.dataset.originalHtml;
            });
        };
        navigateAppContent(form.action, true, {
            method: 'POST',
            body: new FormData(form),
            showMessages: true,
            fallback: function () { restore(); form.dataset.branchSwitch = 'false'; form.submit(); }
        }).then(function (ok) {
            if (!ok) { if (nameEl && previous) nameEl.textContent = previous; }
            restore();
        });
    }, true);
}

function attachAppNavigation() {
    if (window.__pharmacyAppNavigationBound) return;
    window.__pharmacyAppNavigationBound = true;
    if (!history.state || !history.state.pharmacyApp) {
        try { history.replaceState({ pharmacyApp: true }, '', window.location.href); } catch (_) {}
    }

    // Delegate from document so the persistent sidebar never needs to be rebuilt.
    // Only the central #app-content is swapped; shell, sidebar, header and footer stay put.
    document.addEventListener('click', function (event) {
        const link = event.target.closest('#sidebar nav a[href], #sp-bottom-nav a[href]');
        if (!link || event.defaultPrevented || event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (link.hasAttribute('download') || link.target === '_blank' || link.dataset.noSpa === 'true') return;

        const href = link.getAttribute('href');
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
        let target;
        try { target = new URL(href, window.location.origin); } catch (_) { return; }
        if (target.origin !== window.location.origin || !/^https?:$/.test(target.protocol)) return;
        // Keep Django admin, auth transitions, and non-page actions as normal navigations.
        if (target.pathname.startsWith('/admin/') || /\/(logout|login|pin|device-pin)(?:\/|$)/i.test(target.pathname)) return;

        event.preventDefault();
        navigateAppContent(target.href, true);
    });

    window.addEventListener('popstate', function (event) {
        if (event.state && event.state.pharmacyApp) {
            navigateAppContent(window.location.href, false);
        } else {
            // A non-SPA history entry (e.g. login/admin) should retain normal browser behavior.
            window.location.reload();
        }
    });

    document.querySelectorAll('#sidebar nav a.nav-link').forEach(function (link) {
        if (!link.getAttribute('title')) {
            const label = link.querySelector('.sidebar-text, .flex-1');
            const text = label ? label.textContent.trim() : link.textContent.trim();
            if (text) link.setAttribute('title', text);
        }
    });

    const main = document.getElementById('app-content');
    if (main) updateSidebarActiveState(main.dataset.pageUrl || window.location.pathname);
}

// ---------- Mobile sidebar drawer ----------
function attachSidebarToggle() {
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const openBtn = document.getElementById('sidebar-open-btn');
    const closeBtn = document.getElementById('sidebar-close-btn');
    if (!sidebar || !backdrop) return;

    function openSidebar() {
        sidebar.classList.remove('-translate-x-full');
        backdrop.classList.remove('hidden');
    }
    function closeSidebar() {
        sidebar.classList.add('-translate-x-full');
        backdrop.classList.add('hidden');
    }

    if (openBtn) openBtn.addEventListener('click', openSidebar);
    // Bottom-bar "More": toggles the drawer (tap again to close) and mirrors its state.
    const bnMore = document.getElementById('sp-bn-more');
    function isDrawerOpen() { return !sidebar.classList.contains('-translate-x-full'); }
    function syncMoreState() {
        if (!bnMore) return;
        const open = isDrawerOpen() && window.matchMedia('(max-width: 1023px)').matches;
        bnMore.setAttribute('aria-expanded', open ? 'true' : 'false');
        const bar = document.getElementById('sp-bottom-nav');
        if (bar) bar.classList.toggle('drawer-open', open);
    }
    if (bnMore) {
        bnMore.addEventListener('click', function () {
            try { if (navigator.vibrate) navigator.vibrate(8); } catch (_) {}
            if (isDrawerOpen()) closeSidebar(); else openSidebar();
        });
        new MutationObserver(syncMoreState).observe(sidebar, { attributes: true, attributeFilter: ['class'] });
    }
    // Quick links: squeeze/pop feedback, and always close an open drawer (even when the page does not change).
    const bnBar = document.getElementById('sp-bottom-nav');
    if (bnBar) {
        bnBar.addEventListener('click', function (e) {
            const item = e.target.closest ? e.target.closest('a.bn-item') : null;
            if (!item) return;
            try { if (navigator.vibrate) navigator.vibrate(8); } catch (_) {}
            item.classList.remove('bn-tap');
            void item.offsetWidth; // restart the animation on rapid taps
            item.classList.add('bn-tap');
            window.setTimeout(function () { item.classList.remove('bn-tap'); }, 700);
            if (isDrawerOpen()) closeSidebar();
        });
    }
    if (bnMore) {
        window.addEventListener('resize', syncMoreState);
        syncMoreState();
    }
    if (closeBtn) closeBtn.addEventListener('click', closeSidebar);
    backdrop.addEventListener('click', closeSidebar);
    // Place the glider once the bar has laid out, and keep it right on resize / rotation / late font load.
    requestAnimationFrame(placeBottomNavGlider);
    window.addEventListener('load', placeBottomNavGlider);
    window.addEventListener('resize', placeBottomNavGlider);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(placeBottomNavGlider);
    // The mobile menu is a bottom sheet: Escape closes it and focus returns to the "More" button.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !window.matchMedia('(max-width: 1023px)').matches) return;
        if (isDrawerOpen()) { closeSidebar(); if (bnMore) bnMore.focus(); }
    });
    // Rotating a tablet or resizing past the desktop breakpoint must never leave the sheet's dimmer behind.
    window.addEventListener('resize', function () {
        if (window.matchMedia('(min-width: 1024px)').matches) backdrop.classList.add('hidden');
    });
}

// ---------- Toasts ----------
// showToast / showDjangoMessagesAsToasts now live in static/js/ui-kit.js (queue, grouping, pause on hover).

// ---------- Button loading states on form submit ----------
function attachFormLoadingStates() {
    // Superseded in V54.28: the single site-wide handler in base.html (window.SPButtons) locks submit
    // buttons only for submits that really proceed and always releases them (pageshow / fail-safe).
    // Kept as a no-op so existing callers do not break.
}

// ---------- Show / hide password (eye icon) ----------
// Automatically adds a professional eye toggle to EVERY <input type="password">
// on every page (login, change/reset password, ...). To opt a field out, add
// the attribute data-no-pw-toggle to the input.
const PW_ICON_EYE = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
const PW_ICON_EYE_OFF = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" x2="22" y1="2" y2="22"/></svg>';

function enhancePasswordInput(input) {
    if (!input || input.dataset.pwToggle === 'done' || input.hasAttribute('data-no-pw-toggle')) return;
    if (!input.parentNode) return;
    input.dataset.pwToggle = 'done';

    const hadFocus = document.activeElement === input;
    const wrap = document.createElement('div');
    wrap.className = 'pw-field';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    // Keep typed text from running underneath the icon.
    input.style.paddingRight = '2.75rem';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'pw-toggle';
    btn.innerHTML = PW_ICON_EYE;
    wrap.appendChild(btn);

    function setVisible(visible) {
        input.type = visible ? 'text' : 'password';
        btn.innerHTML = visible ? PW_ICON_EYE_OFF : PW_ICON_EYE;
        btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
        const label = visible ? 'Hide password' : 'Show password';
        btn.setAttribute('aria-label', label);
        btn.title = label;
    }
    setVisible(false);

    // Don't steal focus from the input (keeps the mobile keyboard open).
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); });
    btn.addEventListener('click', function () {
        const start = input.selectionStart, end = input.selectionEnd;
        setVisible(input.type === 'password');
        input.focus();
        try { input.setSelectionRange(start, end); } catch (e) { /* not supported */ }
    });

    // Never leave a password visible after the form is submitted or the page is restored.
    if (input.form) input.form.addEventListener('submit', function () { setVisible(false); });
    window.addEventListener('pageshow', function () { setVisible(false); });

    if (hadFocus) input.focus();
}

function attachPasswordToggles() {
    document.querySelectorAll('input[type="password"]').forEach(enhancePasswordInput);
    // Pick up password fields that are added to the page later.
    if (window.MutationObserver) {
        new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.matches && node.matches('input[type="password"]')) enhancePasswordInput(node);
                    if (node.querySelectorAll) node.querySelectorAll('input[type="password"]').forEach(enhancePasswordInput);
                });
            });
        }).observe(document.body, { childList: true, subtree: true });
    }
}


// ---------- Confirm modal ----------
// Usage: add data-confirm="Are you sure?" data-confirm-detail="..." to a <form> or <a>.
// For forms: submission is intercepted and only proceeds after confirmation.
// For links: navigation is intercepted the same way.
function attachConfirmModals() {
    const modal = document.getElementById('confirm-modal');
    if (!modal) return;

    const titleEl = document.getElementById('confirm-modal-title');
    const bodyEl = document.getElementById('confirm-modal-body');
    const acceptBtn = document.getElementById('confirm-modal-accept');
    const cancelBtn = document.getElementById('confirm-modal-cancel');

    let pendingAction = null;

    function openModal(title, detail, onAccept) {
        titleEl.textContent = title || 'Are you sure?';
        bodyEl.textContent = detail || 'This action cannot be undone.';
        pendingAction = onAccept;
        acceptBtn.disabled = false;
        acceptBtn.textContent = 'Yes, continue';
        acceptBtn.dataset.confirmAccept = 'Yes, continue';
        acceptBtn.dataset.busyLabel = '';
        modal.classList.remove('hidden');
        modal.classList.add('flex');
    }

    function closeModal() {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        pendingAction = null;
        acceptBtn.disabled = false;
    }

    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

    acceptBtn.addEventListener('click', function () {
        acceptBtn.disabled = true;
        acceptBtn.innerHTML = `<span class="spinner"></span> ${acceptBtn.dataset.busyLabel || 'Processing\u2026'}`;
        // The confirmed action may not replace the page (download, error, offline): never stay stuck.
        if (window.SPButtons) window.SPButtons.arm(12000);
        if (pendingAction) pendingAction();
    });
    document.addEventListener('sp:buttons-restored', function () {
        acceptBtn.disabled = false;
        acceptBtn.textContent = 'Yes, continue';
        closeModal();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) closeModal();
    });

    // Delegated handlers: work for forms/links added later (e.g. live-search results).
    document.addEventListener('submit', function (e) {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-confirm')) return;
        if (form.dataset.confirmed === 'true') return; // already confirmed, let it through
        e.preventDefault();
        openModal(form.dataset.confirm, form.dataset.confirmDetail, () => {
            form.dataset.confirmed = 'true';
            form.submit();
        });
        if (form.dataset.confirmAccept) acceptBtn.textContent = form.dataset.confirmAccept;
        if (form.dataset.confirmBusy) acceptBtn.dataset.busyLabel = form.dataset.confirmBusy;
    });

    document.addEventListener('click', function (e) {
        const link = e.target.closest ? e.target.closest('a[data-confirm]') : null;
        if (!link) return;
        e.preventDefault();
        openModal(link.dataset.confirm, link.dataset.confirmDetail, () => {
            window.location.href = link.href;
        });
        if (link.dataset.confirmAccept) acceptBtn.textContent = link.dataset.confirmAccept;
        if (link.dataset.confirmBusy) acceptBtn.dataset.busyLabel = link.dataset.confirmBusy;
    });

    // Reset any "Please wait..." buttons when the page is restored via Back/Forward.
    window.addEventListener('pageshow', function (e) {
        acceptBtn.disabled = false;
        acceptBtn.textContent = 'Yes, continue';
        document.querySelectorAll('form[data-confirmed]').forEach(f => delete f.dataset.confirmed);
        document.querySelectorAll('button[disabled]').forEach(btn => {
            if (/Please wait/i.test(btn.textContent)) {
                btn.disabled = false;
                btn.textContent = btn.dataset.originalText || btn.dataset.originalHtml || 'Submit';
                if (btn.dataset.originalHtml) btn.innerHTML = btn.dataset.originalHtml;
            }
        });
    });
}

// ---------- Notification sound + native app notifications ----------
// Production-grade notification audio uses two paths:
// 1) a bundled, real audio file (preferred by browsers/devices), and
// 2) Web Audio as a fallback.
// Browsers still require one user gesture before a site may make sound; we unlock
// both paths from the first interaction and retry/resume the context on every alert.
let notificationAudioContext = null;
let notificationSoundUnlocked = false;
let notificationAudioElement = null;
const NOTIFICATION_SOUND_URL = '/static/audio/notification-chime.wav?v=1';

function getNotificationAudioContext() {
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return null;
        if (!notificationAudioContext) notificationAudioContext = new AudioCtx();
        return notificationAudioContext;
    } catch (err) {
        console.debug('Notification audio context unavailable:', err);
        return null;
    }
}

function getNotificationAudioElement() {
    try {
        if (!notificationAudioElement) {
            notificationAudioElement = new Audio(NOTIFICATION_SOUND_URL);
            notificationAudioElement.preload = 'auto';
            notificationAudioElement.volume = 0.78;
            notificationAudioElement.setAttribute('aria-hidden', 'true');
        }
        return notificationAudioElement;
    } catch (err) {
        console.debug('Notification audio element unavailable:', err);
        return null;
    }
}

async function primeNotificationAudio() {
    let ready = false;
    const ctx = getNotificationAudioContext();
    if (ctx) {
        try {
            if (ctx.state !== 'running') await ctx.resume();
            ready = ctx.state === 'running';
            notificationSoundUnlocked = ready;
        } catch (err) {
            console.debug('Notification AudioContext resume failed:', err);
        }
    }

    // Explicitly unlock the browser's media element policy as well. The tiny
    // play/pause happens only from a real user gesture, so later alerts can play.
    const audio = getNotificationAudioElement();
    if (audio) {
        try {
            audio.muted = true;
            audio.currentTime = 0;
            await audio.play();
            audio.pause();
            audio.currentTime = 0;
            audio.muted = false;
            ready = true;
        } catch (err) {
            audio.muted = false;
            console.debug('Notification media unlock deferred:', err);
        }
    }

    if (ready) {
        try { localStorage.setItem('pharmacy_notification_sound_enabled', '1'); } catch (_) {}
    }
    return ready;
}

async function playNotificationAudioFile() {
    const audio = getNotificationAudioElement();
    if (!audio) return false;
    try {
        audio.pause();
        audio.currentTime = 0;
        audio.muted = false;
        audio.volume = 0.78;
        await audio.play();
        return true;
    } catch (err) {
        console.debug('Notification media playback failed:', err);
        return false;
    }
}

function playNotificationChimeWebAudio(testOnly = false) {
    const ctx = getNotificationAudioContext();
    if (!ctx || ctx.state !== 'running') return false;
    try {
        const now = ctx.currentTime + 0.015;
        const notes = testOnly ? [880] : [880, 1174.66];
        const spacing = testOnly ? 0 : 0.18;
        notes.forEach((frequency, index) => {
            const start = now + index * spacing;
            const duration = testOnly ? 0.52 : 0.48;
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.setValueAtTime(frequency, start);
            osc.frequency.exponentialRampToValueAtTime(frequency * 0.985, start + duration);
            gain.gain.setValueAtTime(0.0001, start);
            gain.gain.exponentialRampToValueAtTime(testOnly ? 0.22 : 0.20, start + 0.018);
            gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(start);
            osc.stop(start + duration + 0.02);

            const shimmer = ctx.createOscillator();
            const shimmerGain = ctx.createGain();
            shimmer.type = 'sine';
            shimmer.frequency.setValueAtTime(frequency * 2, start);
            shimmerGain.gain.setValueAtTime(0.0001, start);
            shimmerGain.gain.exponentialRampToValueAtTime(0.045, start + 0.012);
            shimmerGain.gain.exponentialRampToValueAtTime(0.0001, start + 0.22);
            shimmer.connect(shimmerGain);
            shimmerGain.connect(ctx.destination);
            shimmer.start(start);
            shimmer.stop(start + 0.24);
        });
        return true;
    } catch (err) {
        console.debug('Web Audio notification chime failed:', err);
        return false;
    }
}

async function playNotificationChime(testOnly = false) {
    // A notification may arrive after the browser suspended AudioContext while
    // the tab was backgrounded. Always resume before deciding that sound failed.
    const ctx = getNotificationAudioContext();
    if (ctx && ctx.state !== 'running') {
        try { await ctx.resume(); } catch (_) {}
    }

    if (!testOnly) {
        const mediaPlayed = await playNotificationAudioFile();
        if (mediaPlayed) return true;
    }
    return playNotificationChimeWebAudio(testOnly);
}

async function unlockNotificationSoundFromGesture(showTestTone = false) {
    const ready = await primeNotificationAudio();
    if (ready && showTestTone) {
        // A bell click is an intentional gesture, so always give the user one
        // short confirmation tone. This removes ambiguity about whether sound is enabled.
        await playNotificationChime(true);
    }
    return ready;
}

async function requestNativeNotifications() {
    if (!('Notification' in window)) return;
    try {
        if (Notification.permission === 'default') await Notification.requestPermission();
        // A bell tap is a user gesture. When permission is granted, use that
        // gesture to also establish a persistent Web Push subscription so
        // notifications can reach the user when this page/tab is hidden or closed.
        if (Notification.permission === 'granted' && typeof window.smartPOSEnsurePushSubscription === 'function') {
            await window.smartPOSEnsurePushSubscription();
        }
    } catch (err) {
        console.debug('Notification permission request failed:', err);
    }
}

function vibrateNotification(pattern = [180, 80, 180, 80, 300]) {
    try {
        if (navigator.vibrate) navigator.vibrate(pattern);
    } catch (_) {}
}

function ringNotificationBell() {
    const btn = document.getElementById('notification-bell-btn');
    if (!btn) return;
    btn.classList.remove('notification-bell-ring');
    void btn.offsetWidth;
    btn.classList.add('notification-bell-ring');
    window.setTimeout(() => btn.classList.remove('notification-bell-ring'), 900);
}

window.pharmacyHandleIncomingChatMessage = async function (message) {
    if (!message || message.is_mine) return;
    ringNotificationBell();
    vibrateNotification();
    await playNotificationChime(false);
};

function installNotificationAudioGestureUnlock() {
    const unlock = function () {
        primeNotificationAudio();
    };
    ['pointerdown', 'touchstart', 'keydown'].forEach((eventName) => {
        document.addEventListener(eventName, unlock, { once: true, passive: true, capture: true });
    });
}

installNotificationAudioGestureUnlock();
window.primeNotificationAudio = primeNotificationAudio;

async function showNativeAppNotification(n) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    const options = {
        body: n.message || n.title,
        icon: '/static/img/icons/icon-192.png',
        badge: '/static/img/icons/icon-192.png',
        tag: 'pharmacy-manager-' + n.id,
        renotify: true,
        silent: false,
        vibrate: [120, 70, 120],
        data: { url: n.link_url || '/notifications/' },
    };
    try {
        const registration = await navigator.serviceWorker.ready;
        if (registration && registration.showNotification) {
            await registration.showNotification(n.title, options);
        } else {
            new Notification(n.title, options);
        }
    } catch (err) {
        try { new Notification(n.title, options); } catch (_) {}
    }
}

// ---------- Shared polling helper ----------
// Polls politely: slower when the tab is hidden, immediate refresh when it becomes
// visible again or the network returns, exponential back-off (with jitter) while the
// server is failing, and never two requests in flight at once. This is what keeps
// hundreds of open browsers from hammering the server.
//   task():  async function; return false (or throw) to signal a failed poll.
//   opts:    intervalMs (visible tab), hiddenIntervalMs (hidden tab, 0 = pause),
//            maxBackoffMs (default 60000).
window.createPoller = function (task, opts) {
    const base = opts.intervalMs;
    const hiddenMs = opts.hiddenIntervalMs || 0;
    const maxBackoff = opts.maxBackoffMs || 60000;
    let timer = null, failures = 0, running = false, stopped = true;

    function nextDelay() {
        const d = document.hidden ? hiddenMs : base;
        if (!d) return 0;                                   // paused while hidden
        const backed = Math.min(d * Math.pow(2, Math.min(failures, 5)), maxBackoff);
        return Math.round(backed * (0.85 + Math.random() * 0.3));   // +-15% jitter
    }
    function schedule() {
        clearTimeout(timer); timer = null;
        if (stopped) return;
        const d = nextDelay();
        if (d) timer = setTimeout(run, d);
    }
    async function run() {
        timer = null;
        if (stopped || running) return;
        running = true;
        try {
            const ok = await task();
            failures = ok === false ? failures + 1 : 0;
        } catch (e) {
            failures += 1;
        } finally {
            running = false;
            schedule();
        }
    }
    function wake() {
        if (stopped) return;
        if (document.hidden) { schedule(); return; }
        failures = 0;
        clearTimeout(timer); timer = null;
        run();                                              // refresh right away
    }
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);
    return {
        start: function (runNow) { stopped = false; if (runNow) run(); else schedule(); },
        stop: function () { stopped = true; clearTimeout(timer); timer = null; },
    };
};

// ---------- Notification bell (polling-based "live" notifications) ----------
function attachNotificationBell() {
    // Mobile browsers require a user gesture before they allow audible playback.
    // The first bell tap explicitly unlocks the audio context and plays one short
    // confirmation tone so the owner can immediately verify that sound is enabled.
    const wrapper = document.getElementById('notification-bell-wrapper');
    const btn = document.getElementById('notification-bell-btn');
    const dropdown = document.getElementById('notification-bell-dropdown');
    const countBadge = document.getElementById('notification-bell-count');
    const itemsContainer = document.getElementById('notification-bell-items');
    const markAllBtn = document.getElementById('notification-mark-all-btn');
    const closeBtn = document.getElementById('notification-panel-close');
    const backdrop = document.getElementById('notification-bell-backdrop');
    const panelCount = document.getElementById('notification-panel-count');
    if (!wrapper || !btn || !dropdown) return; // bell isn't on this page (e.g. logged out)

    const FEED_URL = '/notifications/api/feed/';
    // Chat pages already poll their own thread, so the bell can be lazier there.
    const POLL_INTERVAL_MS = window.location.pathname.startsWith('/chat/') ? 30000 : 15000;

    // A manager can switch branches without logging out. Keep the bell's
    // "last seen" marker separate for every branch so notifications from
    // Branch A can never affect the unread/toast state of Branch B.
    let notificationScopeKey = 'global';
    let lastSeenId = 0;

    function setNotificationScope(activePharmacyId) {
        notificationScopeKey = activePharmacyId ? String(activePharmacyId) : 'global';
        lastSeenId = parseInt(
            sessionStorage.getItem('nb_last_seen_id:' + notificationScopeKey) || '0',
            10
        );
    }

    function saveLastSeenId(id) {
        sessionStorage.setItem('nb_last_seen_id:' + notificationScopeKey, String(id));
    }

    const levelDot = { DANGER: 'bg-red-500', WARNING: 'bg-amber-500', SUCCESS: 'bg-emerald-500', INFO: 'bg-brand-500' };
    const levelToastTag = { DANGER: 'error', WARNING: 'warning', SUCCESS: 'success', INFO: 'info' };

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    const INITIAL_VISIBLE = 3;      // the panel opens with the 3 newest; the rest sit behind "Show more"
    let expanded = false;
    let lastItems = [];

    function renderItems(items) {
        lastItems = items;
        const prevScroll = itemsContainer.scrollTop;    // the feed refreshes every ~15s — don't jump the list back to the top
        if (!items.length) {
            itemsContainer.innerHTML = '<div class="notif-empty">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 01-3.46 0"/></svg>' +
                'You\'re all caught up.<br><span style="font-size:.75rem">No notifications yet.</span></div>';
            return;
        }
        const visible = expanded ? items : items.slice(0, INITIAL_VISIBLE);
        const hiddenCount = items.length - INITIAL_VISIBLE;
        let html = visible.map(function (n) {
            const dot = levelDot[n.level] || levelDot.INFO;
            const msg = n.message ? '<p class="notif-msg">' + escapeHtml(n.message) + '</p>' : '';
            const href = n.link_url || '/notifications/';
            return '<a href="' + escapeHtml(href) + '" data-notification-id="' + n.id + '" class="notif-item ' + (n.is_read ? '' : 'is-unread') + '">' +
                '<span class="notif-icon" style="background:#f1f5f9"><i class="' + dot + '"></i></span>' +
                '<div class="min-w-0 flex-1"><p class="notif-title">' + escapeHtml(n.title) + '</p>' + msg +
                '<p class="notif-time">' + escapeHtml(n.created_at_display) + '</p></div></a>';
        }).join('');
        if (hiddenCount > 0) {
            html += '<button type="button" data-notification-toggle class="notif-more">' +
                (expanded ? 'Show less' : 'Show ' + hiddenCount + ' more') + '</button>';
        }
        itemsContainer.innerHTML = html;
        itemsContainer.scrollTop = prevScroll;
        itemsContainer.querySelectorAll('[data-notification-id]').forEach(function (item) {
            item.addEventListener('click', function () {
                fetch('/notifications/api/' + item.dataset.notificationId + '/read/', {
                    method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken') }
                }).catch(() => {});
            });
        });
    }

    itemsContainer.addEventListener('click', function (e) {
        if (!e.target.closest('[data-notification-toggle]')) return;
        expanded = !expanded;
        renderItems(lastItems);
        if (!expanded) itemsContainer.scrollTop = 0;
    });

    function updateBadge(count) {
        if (count > 0) {
            countBadge.textContent = count > 99 ? '99+' : String(count);
            countBadge.classList.remove('hidden');
        } else {
            countBadge.classList.add('hidden');
        }
        if (panelCount) {
            panelCount.textContent = count > 99 ? '99+ new' : count + ' new';
            panelCount.classList.toggle('hidden', !(count > 0));
        }
    }

    const SEVERITY_RANK = { DANGER: 3, WARNING: 2, INFO: 1, SUCCESS: 0 };
    // 1-2 new items: show them. 3 or more: show a single grouped toast with a "View all" action,
    // so a burst (e.g. many stock alerts at once) can never flood or flash across the screen.
    function announceNotifications(items) {
        if (items.length <= 2) {
            items.forEach(function (n) {
                showToast(n.message || '', levelToastTag[n.level] || 'info', {
                    title: n.title, key: 'n:' + n.id,
                    actionLabel: n.link_url ? 'Open' : '', onAction: function () { window.location.href = n.link_url; }
                });
            });
            return;
        }
        const top = items.slice().sort((a, b) => (SEVERITY_RANK[b.level] || 0) - (SEVERITY_RANK[a.level] || 0))[0];
        const lines = items.slice(0, 3).map(n => n.title).join(' \u2022 ');
        showToast(lines + (items.length > 3 ? ' \u2022 +' + (items.length - 3) + ' more' : ''),
                  levelToastTag[top.level] || 'info', {
            title: items.length + ' new notifications', key: 'batch:' + items[0].id + ':' + items.length,
            duration: 12000, icon: 'bell', actionLabel: 'View all',
            onAction: function () { setPanelOpen(true); }
        });
    }

    async function fetchFeed(notifyNewOnes) {
        try {
            const res = await fetch(FEED_URL, { cache: 'no-store' });
            if (!res.ok) return false;
            const data = await res.json();

            // The server is authoritative about the currently active branch.
            // If the manager switched branches in another tab/request, move
            // the bell to the new branch's independent last-seen state first.
            if (data.active_pharmacy_id !== undefined) {
                const newScope = data.active_pharmacy_id ? String(data.active_pharmacy_id) : 'global';
                if (newScope !== notificationScopeKey) {
                    setNotificationScope(data.active_pharmacy_id);
                }
            }

            updateBadge(data.unread_count);
            renderItems(data.notifications);

            if (notifyNewOnes && lastSeenId && data.latest_id > lastSeenId) {
                const isChatPage = window.location.pathname.startsWith('/chat/');
                // Chat pages already give an immediate message-specific bell, chime and vibration
                // from the chat feed. Avoid a duplicate alert for the matching notification.
                const fresh = data.notifications
                    .filter(n => n.id > lastSeenId && !(isChatPage && n.category === 'CHAT'))
                    .reverse();
                if (fresh.length) {
                    announceNotifications(fresh);
                    // One bell ring, one vibration, one chime per batch - never one per item.
                    ringNotificationBell();
                    vibrateNotification();
                    playNotificationChime(false).catch(() => {});
                    if (document.visibilityState !== 'visible') {
                        if (fresh.length === 1) showNativeAppNotification(fresh[0]);
                        else showNativeAppNotification({
                            title: fresh.length + ' new notifications',
                            message: fresh.slice(0, 3).map(n => n.title).join(' \u2022 '),
                            link_url: '/notifications/', id: fresh[fresh.length - 1].id, level: 'INFO', category: 'GENERAL'
                        });
                    }
                }
            }
            if (data.latest_id) {
                lastSeenId = data.latest_id;
                saveLastSeenId(lastSeenId);
            }
            return true;
        } catch (err) {
            console.error('Notification bell fetch error:', err);
            return false;
        }
    }

    function setPanelOpen(open) {
        dropdown.classList.toggle('hidden', !open);
        if (backdrop) backdrop.classList.toggle('hidden', !open);   // only visible on phones (CSS)
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            itemsContainer.scrollTop = 0;
        } else if (expanded) {          // next time it opens, start with the 3 newest again
            expanded = false;
            renderItems(lastItems);
        }
    }

    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        unlockNotificationSoundFromGesture(true);
        requestNativeNotifications();
        setPanelOpen(dropdown.classList.contains('hidden'));
    });
    document.addEventListener('click', function (e) {
        if (dropdown.classList.contains('hidden')) return;
        // composedPath() is captured at dispatch time, so a tap on an element the panel re-rendered
        // during the same click (e.g. "Show more") still counts as a tap *inside* the panel.
        const path = e.composedPath ? e.composedPath() : [];
        const inside = path.indexOf(dropdown) !== -1 || dropdown.contains(e.target);
        const onBell = path.indexOf(btn) !== -1 || btn.contains(e.target);
        if (!inside && !onBell) setPanelOpen(false);
    });
    // iOS Safari does not bubble taps on plain <div>s to `document`, so the backdrop gets its own handler.
    if (backdrop) backdrop.addEventListener('click', function () { setPanelOpen(false); });
    if (closeBtn) closeBtn.addEventListener('click', function () { setPanelOpen(false); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !dropdown.classList.contains('hidden')) { setPanelOpen(false); btn.focus(); }
    });

    if (markAllBtn) {
        markAllBtn.addEventListener('click', function () {
            fetch('/notifications/api/read-all/', {
                method: 'POST', headers: { 'X-CSRFToken': getCookie('csrftoken') },
            }).then(() => fetchFeed(false));
        });
    }

    fetchFeed(false); // don't toast on first load of a page
    window.createPoller(() => fetchFeed(true), { intervalMs: POLL_INTERVAL_MS, hiddenIntervalMs: 45000 }).start(false);
}
