/*
 * AngkorAuth: customer sign-in with Telegram.
 *
 * - Inside the Telegram Mini App, the page checks /api/me on load and, only if the
 *   customer is not signed in yet, posts initData to /auth/telegram/webapp (silently).
 *   A success is remembered for the page.
 * - In a normal browser, checkout opens Telegram's login popup by itself
 *   (AngkorAuth.popupSignIn); the Login Widget button (templates/frontend/pages/sign_in.html)
 *   stays as a fallback and calls AngkorAuth.onWidgetAuth(user). Both post to /auth/telegram/widget.
 *
 * Load after telegram-web-app.js and before any page script that uses it.
 */
(function () {
    'use strict';

    var webAppSignIn = null;  // memoised Promise<bool> for the Mini App sign-in

    function webApp() {
        return (window.Telegram && window.Telegram.WebApp) || null;
    }

    function isMiniApp() {
        var app = webApp();
        return !!(app && app.initData);
    }

    function postJSON(url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        });
    }

    function readJSON(response) {
        return response.json().catch(function () { return {}; });
    }

    /* Translated text for `key` in the page's current language, or '' if unknown. */
    function t(key) {
        try {
            var all = JSON.parse(document.getElementById('translations-json').textContent || '{}');
            var lang = document.documentElement.getAttribute('lang') || 'en';
            return (all[lang] && all[lang][key]) || (all.en && all.en[key]) || '';
        } catch (e) {
            return '';
        }
    }

    /* Show a translated message in every sign-in partial on the page. */
    function showSignInError(key) {
        var text = t(key) || t('err_sign_in_failed');
        document.querySelectorAll('[data-sign-in-pending]').forEach(function (el) {
            el.classList.add('hidden');
        });
        document.querySelectorAll('[data-sign-in-error]').forEach(function (el) {
            el.textContent = text;
            el.classList.remove('hidden');
        });
    }

    var lastKnownSignedIn = null;  // result of the latest /api/me, or null before the first one

    /* GET /api/me; resolves {signed_in: false} on any failure. */
    function me() {
        return fetch('/api/me', { credentials: 'same-origin', cache: 'no-store' })
            .then(function (response) { return response.ok ? readJSON(response) : {}; })
            .then(function (data) { return data && typeof data === 'object' ? data : {}; })
            .catch(function () { return {}; })
            .then(function (data) {
                if (!data.signed_in) data.signed_in = false;
                lastKnownSignedIn = data.signed_in === true;
                return data;
            });
    }

    /* True only when the latest /api/me said the customer is signed out. */
    function knownSignedOut() {
        return lastKnownSignedIn === false;
    }

    function botId() {
        var meta = document.querySelector('meta[name="telegram-bot-id"]');
        var id = meta ? parseInt(meta.getAttribute('content'), 10) : NaN;
        return isNaN(id) ? null : id;
    }

    /* Telegram's login popup (from telegram-widget.js) can be opened from script. */
    function canPopupSignIn() {
        return !isMiniApp() && !!botId()
            && !!(window.Telegram && window.Telegram.Login && typeof window.Telegram.Login.auth === 'function');
    }

    /* Open Telegram's login popup and sign in with the result, without reloading the page.
       Call it straight from a click handler (before any await) so the popup is not blocked.
       Resolves true when the customer is signed in. */
    function popupSignIn() {
        if (!canPopupSignIn()) return Promise.resolve(false);
        return new Promise(function (resolve) {
            try {
                window.Telegram.Login.auth(
                    { bot_id: botId(), request_access: 'write', lang: document.documentElement.getAttribute('lang') || 'en' },
                    function (user) {
                        if (!user) {
                            resolve(false);
                            return;
                        }
                        postJSON('/auth/telegram/widget', user)
                            .then(function (response) {
                                return readJSON(response).then(function (data) {
                                    var ok = response.ok && data.ok === true;
                                    if (ok) {
                                        lastKnownSignedIn = true;
                                    } else {
                                        api.lastError = data.error || 'sign_in_failed';
                                    }
                                    resolve(ok);
                                });
                            })
                            .catch(function () {
                                api.lastError = 'sign_in_failed';
                                resolve(false);
                            });
                    }
                );
            } catch (e) {
                resolve(false);
            }
        });
    }

    /* POST the Mini App's initData; resolves true when the server signed the customer in. */
    function postInitData() {
        return postJSON('/auth/telegram/webapp', { init_data: webApp().initData })
            .then(function (response) {
                return readJSON(response).then(function (data) {
                    if (response.ok && data.ok === true) return true;
                    api.lastError = data.error || 'sign_in_failed';
                    return false;
                });
            })
            .catch(function () {
                api.lastError = 'sign_in_failed';
                return false;
            });
    }

    function ensureSignedIn() {
        if (!isMiniApp()) {
            return me().then(function (data) { return data.signed_in === true; });
        }
        // A valid cookie is checked first (GET /api/me), so initData is only posted (a database
        // write) when the customer is not signed in yet.
        // Only a success is remembered. After a failure (network blip, or initData older than
        // 24h because the Mini App stayed open) an existing cookie may still be valid, and the
        // next user action (e.g. "Place order") retries once. Never retried automatically.
        if (!webAppSignIn) {
            api.lastError = null;
            webAppSignIn = me()
                .then(function (data) {
                    return data.signed_in === true ? true : postInitData();
                })
                .then(function (ok) {
                    if (ok) return true;
                    return me().then(function (data) {
                        if (data.signed_in === true) {
                            api.lastError = null;
                            return true;
                        }
                        webAppSignIn = null;  // let the next call try again
                        return false;
                    });
                });
        }
        return webAppSignIn;
    }

    var WRITE_ACCESS_DECLINED_KEY = 'angkorkey_write_access_declined';

    function writeAccessDeclined() {
        try {
            return window.localStorage.getItem(WRITE_ACCESS_DECLINED_KEY) === '1';
        } catch (e) {
            return false;
        }
    }

    function rememberWriteAccessAnswer(allowed) {
        try {
            if (allowed) {
                window.localStorage.removeItem(WRITE_ACCESS_DECLINED_KEY);
            } else {
                window.localStorage.setItem(WRITE_ACCESS_DECLINED_KEY, '1');
            }
        } catch (e) { /* storage blocked: they may be asked again next order */ }
    }

    /* In the Mini App, ask once for permission to receive bot messages (order updates).
       A "no" is remembered on this device, so later orders do not ask again. */
    function requestWriteAccessIfNeeded(data) {
        var app = webApp();
        if (!isMiniApp() || !data || !data.signed_in || data.can_message || writeAccessDeclined()
                || typeof app.requestWriteAccess !== 'function'
                || (typeof app.isVersionAtLeast === 'function' && !app.isVersionAtLeast('6.9'))) {
            return Promise.resolve();
        }
        return new Promise(function (resolve) {
            var settled = false;
            function finish() {
                if (!settled) {
                    settled = true;
                    resolve();
                }
            }
            // Never let a lost callback block checkout.
            var timer = setTimeout(finish, 30000);
            try {
                app.requestWriteAccess(function (allowed) {
                    rememberWriteAccessAnswer(!!allowed);
                    postJSON('/api/me/write-access', { allowed: !!allowed })
                        .catch(function () {})
                        .then(function () {
                            clearTimeout(timer);
                            finish();
                        });
                });
            } catch (e) {
                clearTimeout(timer);
                finish();
            }
        });
    }

    /* Login Widget callback (data-onauth). */
    function onWidgetAuth(user) {
        postJSON('/auth/telegram/widget', user)
            .then(function (response) {
                return readJSON(response).then(function (data) {
                    if (response.ok && data.ok === true) {
                        window.location.reload();
                    } else {
                        showSignInError(data.error === 'auth_unavailable' ? 'err_auth_unavailable' : 'err_sign_in_failed');
                    }
                });
            })
            .catch(function () { showSignInError('err_sign_in_failed'); });
    }

    var api = {
        lastError: null,  // 'auth_unavailable' or 'sign_in_failed' after a failed Mini App sign-in
        isMiniApp: isMiniApp,
        ensureSignedIn: ensureSignedIn,
        requestWriteAccessIfNeeded: requestWriteAccessIfNeeded,
        onWidgetAuth: onWidgetAuth,
        canPopupSignIn: canPopupSignIn,
        popupSignIn: popupSignIn,
        knownSignedOut: knownSignedOut,
        me: me,
        showSignInError: showSignInError
    };
    window.AngkorAuth = api;

    // Silent sign-in when opened as a Telegram Mini App.
    var app = webApp();
    if (app && typeof app.ready === 'function') {
        try { app.ready(); } catch (e) { /* not inside Telegram */ }
    }
    if (isMiniApp()) {
        ensureSignedIn();
    }
})();
