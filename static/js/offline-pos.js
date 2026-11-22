// ============================================================
// SmartPOS — Offline POS support
// IndexedDB-backed catalog cache + offline sale queue + sync.
//
// How it fits together with pos.html:
// - When online, pos.html's product search hits the server API as normal.
//   This module also refreshes the local IndexedDB catalog in the
//   background so it stays reasonably fresh for the next offline moment.
// - When the server search fails (no connection), pos.html falls back to
//   OfflinePOS.searchCatalog() against the local cache.
// - When checkout's normal POST fails (no connection), pos.html calls
//   OfflinePOS.queueSale() instead — the sale is saved locally immediately
//   and the cashier sees "Saved — will sync when online", never a lost sale.
// - Whenever the browser regains connectivity (or periodically, or via the
//   manual "Sync Now" button), queued sales are POSTed to the server in
//   order. The server is the final authority on real stock — see
//   sync_offline_sales_view in sales/views.py for how conflicts are handled.
// ============================================================

const OfflinePOS = (() => {
    const DB_NAME_PREFIX = 'pharmacy-manager-offline-v3';
    const DB_VERSION = 1;
    let scopeKey = 'unscoped';
    const STORE_CATALOG = 'catalog';
    const STORE_PENDING = 'pending_sales';

    let dbPromise = null;
    let activeDbName = `${DB_NAME_PREFIX}-${scopeKey}`;

    function configureScope(newScopeKey) {
        const normalized = String(newScopeKey || 'unscoped').replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 120);
        if (normalized === scopeKey) return;
        if (dbPromise) {
            try { dbPromise.then(db => db.close()).catch(() => {}); } catch (_) {}
        }
        dbPromise = null;
        scopeKey = normalized || 'unscoped';
        activeDbName = `${DB_NAME_PREFIX}-${scopeKey}`;
    }

    function openDB() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise((resolve, reject) => {
            const request = indexedDB.open(activeDbName, DB_VERSION);
            request.onupgradeneeded = (event) => {
                const db = event.target.result;
                if (!db.objectStoreNames.contains(STORE_CATALOG)) {
                    db.createObjectStore(STORE_CATALOG, { keyPath: 'id' });
                }
                if (!db.objectStoreNames.contains(STORE_PENDING)) {
                    db.createObjectStore(STORE_PENDING, { keyPath: 'client_uuid' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        return dbPromise;
    }

    function tx(storeName, mode) {
        return openDB().then((db) => db.transaction(storeName, mode).objectStore(storeName));
    }

    function promisifyRequest(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    // ---------- Catalog cache ----------
    async function refreshCatalog(catalogUrl) {
        if (!navigator.onLine) return false;
        try {
            const resp = await fetch(catalogUrl);
            if (!resp.ok) return false;
            const data = await resp.json();
            const store = await tx(STORE_CATALOG, 'readwrite');
            await promisifyRequest(store.clear());
            for (const product of data.products || []) {
                store.put(product);
            }
            return true;
        } catch (err) {
            return false;
        }
    }

    async function searchCatalog(query) {
        const store = await tx(STORE_CATALOG, 'readonly');
        const all = await promisifyRequest(store.getAll());
        const q = (query || '').trim().toLowerCase();
        const filtered = q ? all.filter((p) => p.name.toLowerCase().includes(q) || (p.barcode || '').toLowerCase().includes(q)) : all;
        return filtered.slice(0, 15);
    }

    // Exact barcode / SKU match from the offline snapshot (handles EAN-13 <-> GTIN-14 zero padding).
    async function findByBarcode(code) {
        const c = String(code || '').trim();
        if (!c) return null;
        const wanted = (window.PMScanner ? window.PMScanner.variants(c) : [c]).map((v) => v.toLowerCase());
        const store = await tx(STORE_CATALOG, 'readonly');
        const all = await promisifyRequest(store.getAll());
        return all.find((p) => wanted.indexOf(String(p.barcode || '').toLowerCase()) !== -1) || null;
    }

    async function getCachedProduct(productId) {
        const store = await tx(STORE_CATALOG, 'readonly');
        return promisifyRequest(store.get(productId));
    }

    // ---------- Offline sale queue ----------
    function generateUUID() {
        if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
        // Fallback for older browsers
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    async function queueSale(saleData) {
        const client_uuid = generateUUID();
        const record = {
            scope_key: scopeKey,
            client_uuid,
            cart: saleData.cart,
            payment_method: saleData.payment_method,
            customer_name: saleData.customer_name || '',
            customer_phone: saleData.customer_phone || '',
            deposit_amount: saleData.deposit_amount || 0,
            deposit_method: saleData.deposit_method || 'CASH',
            cash_paid: saleData.cash_paid || 0,
            queued_at: new Date().toISOString(),
            business_date: new Date().toLocaleDateString('en-CA'),
        };
        const store = await tx(STORE_PENDING, 'readwrite');
        await promisifyRequest(store.put(record));
        return client_uuid;
    }

    async function getPendingSales() {
        const store = await tx(STORE_PENDING, 'readonly');
        return promisifyRequest(store.getAll());
    }

    async function getPendingCount() {
        const store = await tx(STORE_PENDING, 'readonly');
        return promisifyRequest(store.count());
    }

    async function removePendingSale(client_uuid) {
        const store = await tx(STORE_PENDING, 'readwrite');
        return promisifyRequest(store.delete(client_uuid));
    }

    // ---------- Sync ----------
    let syncing = false;

    async function syncPendingSales(syncUrl, csrfToken, onProgress) {
        if (syncing || !navigator.onLine) return { synced: 0, failed: 0, needsReview: 0 };
        syncing = true;
        try {
            const pending = await getPendingSales();
            if (pending.length === 0) return { synced: 0, failed: 0, needsReview: 0 };

            const response = await fetch(syncUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                body: JSON.stringify({ sales: pending, branch_id: window.POS_BRANCH_ID || null }),
            });

            if (response.status === 409) {
                // Page belongs to another branch than the active session: keep everything queued, tell the page.
                try { window.dispatchEvent(new CustomEvent('pos-branch-changed')); } catch (_) {}
                return { synced: 0, failed: pending.length, needsReview: 0 };
            }
            if (!response.ok) return { synced: 0, failed: pending.length, needsReview: 0 };

            const data = await response.json();
            let synced = 0, failed = 0, needsReview = 0;

            for (const result of data.results || []) {
                if (result.status === 'ok' || result.status === 'already_synced') {
                    await removePendingSale(result.client_uuid);
                    synced++;
                } else if (result.status === 'needs_review') {
                    await removePendingSale(result.client_uuid);
                    needsReview++;
                } else if (result.status === 'blocked_historical_closed') {
                    failed++;
                    // Keep the sale safely queued. It must be resolved by a manager
                    // through the audited cash-day correction workflow before sync.
                    if (onProgress) onProgress({ synced, failed, needsReview, total: pending.length, blockedHistorical: true, message: result.message });
                } else if (result.status === 'blocked_workday_closed') {
                    failed++;
                    // Keep the sale queued: it was made after the workday was closed, so only a
                    // Boss/manager reopening can release it.
                    if (onProgress) onProgress({ synced, failed, needsReview, total: pending.length, blockedHistorical: true, message: result.message });
                } else if (result.status === 'blocked_historical_business_date') {
                    failed++;
                    if (onProgress) onProgress({ synced, failed, needsReview, total: pending.length, blockedHistorical: true, message: result.message });
                } else {
                    failed++; // left in the queue, will retry next sync
                }
            }

            if (onProgress) onProgress({ synced, failed, needsReview, total: pending.length });
            return { synced, failed, needsReview };
        } catch (err) {
            return { synced: 0, failed: 0, needsReview: 0, error: err.message };
        } finally {
            syncing = false;
        }
    }

    return {
        configureScope,
        refreshCatalog, searchCatalog, findByBarcode, getCachedProduct,
        queueSale, getPendingSales, getPendingCount, removePendingSale,
        syncPendingSales,
    };
})();
